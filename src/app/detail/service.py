"""Compose a bounded, read-only detailed quote from verified registry identities."""

import asyncio
import time
from decimal import Decimal, localcontext

from app.adapters.base import AdapterError, Failure
from app.adapters.detail import DetailAdapter, ReadRpc
from app.adapters.prices import OkxPrices, Price
from app.config import PROJECT_ROOT
from app.pricing.gap import decimal_text
from app.pricing.state import SOURCE_CLOCK_SKEW_MS
from app.registry.loader import load_config
from app.registry.models import EVM_ADDRESS, ChainMap, native_address

from .math import consume_bids, effective_result
from .models import PurchaseAssets


class DetailUnavailable(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


class DetailService:
    def __init__(self, collector, *, adapter=None, assets=None, chain_map=None):
        self.collector = collector
        configured = (
            load_config(PROJECT_ROOT / "config/quote_assets.toml", PurchaseAssets).assets
            if assets is None
            else list(assets.values())
        )
        self.assets = (
            assets
            if assets is not None
            else {a.chain_index: a for a in configured if a.family == "USDT"}
        )
        self.other_assets = {(a.chain_index, a.family): a for a in configured if a.family != "USDT"}
        self.chain_map = (
            chain_map
            if chain_map is not None
            else load_config(PROJECT_ROOT / "config/chain_map.toml", ChainMap)
        )
        self.adapter = adapter
        self.lock = asyncio.Lock()

    def purchase_asset(self, chain, symbol):
        return (
            self.assets.get(chain) if symbol == "USDT" else self.other_assets.get((chain, symbol))
        )

    def asset_catalog(self):
        return [
            {**a.model_dump(), "purchase_symbol": a.family}
            for a in [*self.assets.values(), *self.other_assets.values()]
        ]

    def select(self, request):
        if self.collector.preferences.blocked(request.coin_id):
            raise DetailUnavailable("coin_blacklisted", 409)
        matches = [
            (coin, token)
            for coin in self.collector.registry.coins
            if coin.coin_id == request.coin_id and not coin.excluded_reason
            for token in coin.tokens
            if token.chain_index == request.chain_index
            and token.address == request.address
            and token.status.get(request.exchange) in {"tradable", "bridge_candidate"}
            and request.exchange in coin.exchanges
        ]
        if len(matches) != 1:
            raise DetailUnavailable("route_not_registered", 404)
        coin, token = matches[0]
        listing = coin.exchanges[request.exchange]
        nets = [
            n.net_type
            for n in self.chain_map.networks
            if n.exchange == request.exchange
            and n.chain_index == token.chain_index
            and n.net_type in listing.net_types
        ]
        if nets and all(
            self.collector.preferences.network(coin.coin_id, request.exchange, net) for net in nets
        ):
            raise DetailUnavailable("network_manually_excluded", 409)
        amount = Decimal(request.amount_usdt)
        if not 0 < amount <= 1_000_000:
            raise DetailUnavailable("invalid_amount")
        return *matches[0], amount

    def adapters(self):
        if self.adapter is None:
            c = self.collector
            self.adapter = DetailAdapter(
                c.client,
                c.credentials,
                okx_gate=c.okx.gate,
                cex_gates={ex: a.gate for ex, a in c.cex_adapters.items()},
            )
        return self.adapter

    async def calculate(self, request):
        coin, token, amount = self.select(request)
        if self.lock.locked():
            raise DetailUnavailable("detail_busy", 429)
        async with self.lock:
            try:
                async with asyncio.timeout(45):
                    return await self._calculate(request, coin, token, amount)
            except TimeoutError:
                raise DetailUnavailable("detail_timeout", 504) from None

    async def _calculate(self, request, coin, token, amount):
        started_monotonic = time.monotonic()
        c, exchange = self.collector, request.exchange
        adapter = self.adapters()
        now = lambda: int(time.time() * 1000)
        warnings, errors, deadlines = [], [], []
        ttl = int(c.settings.pricing.stale_seconds * 1000)
        listing = coin.exchanges[exchange]
        markets = [m for m in listing.markets if m == "KRW-" + listing.symbol]
        if len(markets) != 1:
            raise DetailUnavailable("krw_market_unavailable", 404)
        asset = self.purchase_asset(token.chain_index, request.purchase_symbol)
        data = {
            "coin_id": coin.coin_id,
            "symbol": coin.symbol,
            "token_name": token.name,
            "chain_index": token.chain_index,
            "chain_name": c.chain_names.get(token.chain_index),
            "address": token.address,
            "exchange": exchange,
            "market": markets[0],
            "amount_usdt": decimal_text(amount),
            "purchase_symbol": request.purchase_symbol,
            "execution_enabled": False,
            "route_status": token.status[exchange],
            "purchase_asset": asset.model_dump() if asset else None,
            "quote": None,
            "book": None,
            "fx_krw": None,
            "fx_reference": None,
            "gas": None,
            "result": None,
            "reference_result": None,
            "reference_basis": None,
            "deposit": {
                "net_type": None,
                "wallet_state": None,
                "possible": None,
                "confirmations": None,
                "estimated_seconds": None,
                "minimum": None,
                "checked_at_ms": None,
                "reference_only": exchange == "upbit",
            },
            "warnings": warnings,
            "errors": errors,
            "basis": "업비트 USDT 매도 1호가 공통 환산 · 거래소 매매 수수료 제외 · 1 USDC = 1 USDG = 1 USDT · 1 USD ≈ 1 USDT",
        }

        def warn(code, message):
            if not any(w["code"] == code for w in warnings):
                warnings.append({"code": code, "message": message})

        def source_deadline(source_ms):
            received = now()
            if source_ms > received + SOURCE_CLOCK_SKEW_MS:
                warn(
                    "source_timestamp_invalid",
                    "가격 제공자 시각이 로컬보다 5초 넘게 빠릅니다. 시각을 확인한 뒤 갱신하세요.",
                )
                return 0
            # Accepted clock skew must not extend the lifetime of a quote.
            return min(source_ms, received) + ttl

        async def stage(name, operation):
            try:
                return await operation()
            except AdapterError as exc:
                errors.append({"stage": name, "code": exc.category.value, "message": str(exc)})
            except Exception:  # noqa: BLE001 - sanitize all provider exceptions.
                errors.append(
                    {
                        "stage": name,
                        "code": Failure.INTERNAL.value,
                        "message": "조회 결과 처리에 실패했습니다. 비밀값 보호를 위해 원문은 표시하지 않습니다.",
                    }
                )
            return None

        def finish():
            data["processing_ms"] = int((time.monotonic() - started_monotonic) * 1000)
            data["generated_at_ms"] = now()
            data["expires_at_ms"] = min(deadlines) if deadlines else now()
            if data["expires_at_ms"] <= now():
                data["result"] = None
                data["reference_result"] = None
                warn("stale", "견적 또는 가격이 만료되었습니다. 다시 조회하세요.")
            return data

        networks = [
            n.net_type
            for n in self.chain_map.networks
            if n.exchange == exchange
            and n.chain_index == token.chain_index
            and n.net_type in listing.net_types
        ]
        bridge = token.status[exchange] == "bridge_candidate"
        if bridge:
            data["basis"] = "매수 체인의 OKX 견적 기준 · 브릿지 비용·가스비·거래소 매도 미반영"
            warn(
                "bridge_required",
                "브릿지 필요 · 매수 견적의 가격 영향만 조회합니다.",
            )
        if token.community_recognized is not True:
            warn("recognition_unknown", "토큰의 커뮤니티 인증 상태를 별도로 확인하세요.")

        if not bridge:
            # Refresh wallet state even when this chain has no verified purchase asset.
            wallet_started = c.clock.monotonic()
            wallets = await stage("wallet", lambda: adapter.wallets(exchange))
            c.wallet_status.observe(
                exchange, wallets, wallet_started, None if wallets is not None else "refresh_failed"
            )
            if len(networks) == 1:
                net = networks[0]
                data["deposit"]["net_type"] = net
                matched = [
                    r
                    for r in (wallets or [])
                    if r["currency"] == listing.symbol and r["net_type"] == net
                ]
                if len(matched) == 1:
                    state = matched[0]["wallet_state"]
                    data["deposit"].update(wallet_state=state, checked_at_ms=now())
                    data["deposit"]["possible"] = state in ("working", "deposit_only")
                if exchange == "upbit":
                    chance = await stage(
                        "deposit", lambda: adapter.deposit_chance(listing.symbol, net)
                    )
                    if chance is not None:
                        data["deposit"].update(
                            possible=(data["deposit"]["possible"] is True and chance["possible"]),
                            confirmations=chance["confirmations"],
                            minimum=decimal_text(chance["minimum"]),
                            checked_at_ms=now(),
                        )
                    else:
                        data["deposit"]["possible"] = None
                else:
                    warn(
                        "confirmations_unavailable",
                        "빗썸 컨펌 수는 연결한 API에서 제공되지 않아 별도 확인이 필요합니다.",
                    )
            else:
                warn(
                    "deposit_network_unknown",
                    "현재 체인과 일치하는 거래소 입금망을 단일하게 확인하지 못했습니다.",
                )
            if data["deposit"]["possible"] is not True:
                warn("deposit_unavailable", "입금 중단 또는 입금 가능 상태 미확인입니다.")
            if exchange == "upbit":
                warn(
                    "deposit_reference",
                    "업비트 입출금 정보는 실제 상태보다 늦게 반영될 수 있는 참고 정보입니다.",
                )
        if asset is None:
            warn(
                "purchase_asset_missing",
                f"이 체인의 매수용 {request.purchase_symbol} 주소·decimals를 아직 검증하지 못했습니다.",
            )
            return finish()
        if asset.address == token.address:
            warn("same_asset", "매수 자산과 대상 토큰이 같아 스왑 견적 대상이 아닙니다.")
            return finish()
        with localcontext() as ctx:
            ctx.prec = 100
            base = amount * 10**asset.decimals
            if base != base.to_integral_value():
                raise DetailUnavailable("amount_precision")
            units = int(base)
        quote = await stage("quote", lambda: adapter.quote(token, asset, units))
        if quote is None:
            return finish()
        deadlines.append(now() + ttl)
        data["quote"] = {
            key: decimal_text(value) if isinstance(value, Decimal) else value
            for key, value in quote.items()
        }
        if quote["honeypot"] is not False or quote["source_honeypot"] is not False:
            warn("honeypot", "허니팟 의심 또는 허니팟 여부 미확인입니다.")
        if quote["tax"] is None or quote["source_tax"] is None:
            warn("tax_unknown", "토큰 세율이 제공되지 않아 정확한 수령량을 계산할 수 없습니다.")
        elif quote["tax"] > 0 or quote["source_tax"] > 0:
            warn(
                "token_tax",
                "토큰 세금이 있습니다. 수령량에 매수세를 보수적으로 반영했으며 전송세는 미확인입니다.",
            )
        if quote["price_impact_percent"] is None:
            warn("impact_unknown", "가격 영향이 제공되지 않았습니다.")
        elif abs(quote["price_impact_percent"]) >= 3:
            warn("large_impact", "가격 영향 절댓값이 3% 이상입니다.")

        if bridge:
            return finish()

        # Estimate approve + one transfer, only when all conversion inputs are known.
        rpc = None
        rpc_url = c.credentials.rpc_urls.get(token.chain_index)
        owner = c.credentials.wallet_address.get_secret_value().lower()
        gas = {
            "swap_usdt": decimal_text(quote["swap_gas_usdt"]),
            "approve_usdt": None,
            "transfer_usdt": None,
            "total_usdt": None,
            "approval_count": None,
            "transfer_gas_units": c.settings.detail.native_transfer_gas
            if token.native
            else c.settings.detail.token_transfer_gas,
            "method": "swap: OKX tradeFee · approve: OKX gasLimit × RPC gasPrice · 전송 1회: 설정 gas 예산 × RPC gasPrice",
            "complete": False,
        }
        data["gas"] = gas
        if rpc_url and EVM_ADDRESS.fullmatch(owner):
            rpc = ReadRpc(c.client, rpc_url, token.chain_index)
            checked = await stage("rpc_chain", lambda: self.check_rpc(rpc))
            if checked:
                approval = await stage("approval", lambda: adapter.approval(asset, units))
                if approval:
                    allowance = await stage(
                        "allowance",
                        lambda: rpc.allowance(owner, asset.address, approval["spender"]),
                    )
                    gas_price = await stage("gas_price", lambda: rpc.integer("eth_gasPrice", []))
                    prices = getattr(c, "okx", None) or OkxPrices(c.client, c.credentials, 1.1)
                    native_key = (token.chain_index, native_address(token.chain_index))
                    native = await stage("native_price", lambda: prices.fetch([native_key]))
                    price = (native or {}).get(native_key)
                    if allowance is not None and gas_price is not None and price:
                        deadlines.append(source_deadline(price.source_ms))
                        count = 0 if allowance >= units else 1 if allowance == 0 else 2
                        # Non-zero insufficient USDT allowance reserves a reset plus approve.
                        with localcontext() as ctx:
                            ctx.prec = 100
                            unit_cost = Decimal(gas_price) * price.value / 10**asset.native_decimals
                            approve_cost = approval["gas_limit"] * count * unit_cost
                            transfer_cost = gas["transfer_gas_units"] * unit_cost
                            gas.update(
                                approve_usdt=decimal_text(approve_cost),
                                transfer_usdt=decimal_text(transfer_cost),
                                approval_count=count,
                            )
                            if asset.simple_gas and quote["swap_gas_usdt"] is not None:
                                total = quote["swap_gas_usdt"] + approve_cost + transfer_cost
                                gas.update(total_usdt=decimal_text(total), complete=True)
                confirms = data["deposit"]["confirmations"]
                if confirms is not None:
                    block_seconds = await stage("block_time", rpc.block_seconds)
                    if block_seconds is not None:
                        data["deposit"]["estimated_seconds"] = decimal_text(
                            block_seconds * confirms
                        )
        else:
            warn(
                "rpc_or_wallet_missing",
                "RPC URL 또는 지갑 주소가 없어 allowance·가스비 추정을 완료할 수 없습니다.",
            )
        if not asset.simple_gas:
            warn(
                "additional_chain_fee",
                "이 체인의 추가 데이터/L1 비용 검증 전입니다. 일부 가스비만으로 실효갭을 계산하지 않습니다.",
            )
        if not gas["complete"]:
            warn("gas_incomplete", "스왑·승인·전송 1회 비용을 모두 추정하지 못했습니다.")
        else:
            warn(
                "gas_estimate",
                "가스는 추정치입니다. 전송 gas 예산은 실제 토큰·수신 주소에 따라 달라집니다.",
            )
        if data["deposit"]["estimated_seconds"] is not None:
            warn(
                "time_estimate",
                "입금 시간은 최근 20블록 평균 × 컨펌 수이며 거래소 처리 지연은 별도입니다.",
            )

        # Read the book and common FX last; prior stages can take several seconds.
        book = await stage("orderbook", lambda: adapter.book(exchange, markets[0]))
        fx = await stage("fx", lambda: adapter.book("upbit", "KRW-USDT"))
        if fx and fx["ask"] is not None and fx["source_ms"] <= now() + SOURCE_CLOCK_SKEW_MS:
            c.fx.put("usdt", Price(fx["ask"], fx["source_ms"]), "upbit_detail_orderbook")
            data["fx_reference"] = {
                "reused": now() - fx["source_ms"] > c.settings.pricing.usdt_stale_seconds * 1000,
                "received_at_ms": now(),
                "source_at_ms": fx["source_ms"],
            }
        else:
            cached = c.fx_reference()
            fx = {"ask": Decimal(cached["value"])} if cached["usable"] else None
            if fx:
                data["fx_reference"] = {**cached, "reused": True}
        if fx and fx["ask"] is not None:
            data["fx_krw"] = decimal_text(fx["ask"])
            if data["fx_reference"]["reused"]:
                warn(
                    "fx_cached",
                    "USDT 갱신 지연으로 마지막 정상 환산값을 사용합니다. 실행 전 최신 호가 확인이 필요합니다.",
                )
        if not fx or fx["ask"] is None:
            warn(
                "fx_unavailable",
                "USDT 정상 환산값이 아직 없습니다. 첫 정상 호가 수신 후 다시 조회하세요.",
            )
        if book:
            deadlines.append(source_deadline(book["source_ms"]))
        quantity = quote["net_quantity"]
        if book and quantity and quantity > 0:
            fill = consume_bids(quantity, book["bids"])
            data["book"] = fill.public()
            if not fill.complete:
                warn(
                    "insufficient_depth",
                    "조회된 매수 호가 잔량으로 전량 매도할 수 없습니다. 일부 체결액으로 실효갭을 계산하지 않습니다.",
                )
            minimum = data["deposit"]["minimum"]
            if minimum is not None and quantity < Decimal(minimum):
                data["deposit"]["possible"] = False
                warn("below_minimum", "예상 입금 수량이 거래소 최소 입금 수량보다 작습니다.")
            eligible = (
                not bridge
                and gas["complete"]
                and fx
                and fx["ask"] is not None
                and quote["tax"] == 0
                and quote["source_tax"] == 0
                and quote["honeypot"] is False
                and quote["source_honeypot"] is False
            )
            if eligible:
                calculated = effective_result(amount, fill, fx["ask"], Decimal(gas["total_usdt"]))
                if data["deposit"]["possible"] is True:
                    data["result"] = calculated
                else:
                    data["reference_result"] = calculated
                    data["reference_basis"] = "입금 가능을 가정한 참고 실효갭 · 현재 실행 불가"
        return finish()

    @staticmethod
    async def check_rpc(rpc):
        await rpc.check_chain()
        return True
