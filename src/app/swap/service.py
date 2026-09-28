"""Confirmed swap state machine with durable hashes before broadcast and no automatic replay."""

import asyncio
import hashlib
import json
import time
import uuid
from decimal import Decimal, localcontext

from app.adapters.base import AdapterError
from app.adapters.execution import ExecutionRpc, LocalSigner, SwapAdapter, address, hexdata
from app.config import PROJECT_ROOT
from app.detail.math import consume_bids, effective_result
from app.detail.models import DetailRequest
from app.operations import EventLog
from app.pricing.state import SOURCE_CLOCK_SKEW_MS
from app.private_store import PrivateStore, StoreError
from app.registry.models import native_address

from .models import SwapError

TERMINAL = frozenset(
    {"dry_run_complete", "success", "reverted", "rejected", "failed", "approval_complete_reconfirm"}
)
INFORMATIONAL = frozenset(
    {"deposit_reference", "gas_estimate", "time_estimate", "confirmations_unavailable"}
)


class SwapService:
    def __init__(
        self,
        collector,
        detail,
        *,
        path=None,
        contracts=None,
        adapter=None,
        rpc_factory=None,
        signer=None,
    ):
        self.collector, self.detail = collector, detail
        self.store = PrivateStore(path or PROJECT_ROOT / "data/swap_history.json")
        self.log = EventLog(self.store.path.with_name("operations.jsonl"))
        self.contracts = (
            contracts
            if contracts is not None
            else json.loads((PROJECT_ROOT / "config/contracts.json").read_text())["chains"]
        )
        self.adapter = adapter
        self.rpc_factory = rpc_factory
        self.signer = signer or LocalSigner(collector.credentials)
        self.tasks = {}
        self.settings = collector.settings.swap
        if not (
            0 < Decimal(self.settings.max_amount_usdt) <= 1_000_000
            and 0 < Decimal(self.settings.approval_cap_usdt) <= 10_000_000
            and 0 <= Decimal(self.settings.slippage_percent) <= 5
        ):
            raise SwapError("invalid_swap_settings", "금액·승인 상한·슬리피지 설정을 확인하세요.")
        self.config_digest = hashlib.sha256(
            json.dumps(self.settings.model_dump(), sort_keys=True).encode()
        ).hexdigest()

    def read(self):
        data = self.store.read({"schema_version": 1, "intents": {}})
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != 1
            or not isinstance(data.get("intents"), dict)
        ):
            raise StoreError("invalid_swap_history")
        return data

    def api(self):
        if self.adapter is None:
            self.adapter = SwapAdapter(self.detail.adapters(), self.contracts)
        return self.adapter

    def rpc(self, chain):
        if self.rpc_factory:
            return self.rpc_factory(chain)
        creds = self.collector.credentials
        url = creds.rpc_urls.get(chain)
        if url is None:
            raise SwapError("chain_not_ready", "체인 RPC 설정이 없습니다.")
        return ExecutionRpc(self.collector.client, url, chain, dry_run=creds.dry_run)

    def identity(self, request):
        coin, token, amount = self.detail.select(request)
        if token.status.get(request.exchange) != "tradable":
            raise SwapError("bridge_forbidden", "브릿지 후보는 실행할 수 없습니다.")
        if amount > Decimal(self.settings.max_amount_usdt):
            raise SwapError("amount_limit", "1회 최대 매수 금액을 초과했습니다.")
        asset = self.detail.purchase_asset(token.chain_index, request.purchase_symbol)
        if not asset or not asset.simple_gas or token.chain_index not in self.contracts:
            raise SwapError(
                "chain_not_ready", "매수 자산·체인 비용·컨트랙트 검증이 완료되지 않았습니다."
            )
        owner = address(self.collector.credentials.wallet_address.get_secret_value())
        if not self.collector.credentials.dry_run:
            self.signer.check(owner)
        return coin, token, amount, asset, owner

    def check_detail(self, data):
        blocking = [w["code"] for w in data["warnings"] if w["code"] not in INFORMATIONAL]
        if blocking:
            raise SwapError(
                "quote_warning", "견적의 실행 차단 항목을 먼저 해결하세요: " + ", ".join(blocking)
            )
        if data["result"] is None or data["expires_at_ms"] <= int(time.time() * 1000):
            raise SwapError("quote_unavailable", "유효한 실효갭을 계산할 수 없습니다.")
        if Decimal(data["result"]["gap_percent"]) < Decimal(self.settings.min_gap_percent):
            raise SwapError("gap_below_minimum", "직전 재견적 실효갭이 설정 최소값보다 낮습니다.")

    async def prepare(self, request):
        coin, token, amount, asset, owner = self.identity(request)
        fresh = await self.detail.calculate(request)
        self.check_detail(fresh)
        rpc = self.rpc(token.chain_index)
        await rpc.check_chain()
        if await rpc.call("eth_getCode", [owner, "latest"]) != "0x":
            raise SwapError("wallet_not_eoa", "현재 실행 계층은 일반 EOA 지갑만 지원합니다.")
        with localcontext() as ctx:
            ctx.prec = 400
            units = int(amount * 10**asset.decimals)
            cap = int(Decimal(self.settings.approval_cap_usdt) * 10**asset.decimals)
        if self.settings.approval_policy == "capped" and cap < units:
            raise SwapError("approval_cap_too_low", "승인 상한이 매수 금액보다 작습니다.")
        approval_units = units if self.settings.approval_policy == "exact" else cap
        approval, spender = await self.api().approval(asset, approval_units)
        allowance = await rpc.allowance(owner, asset.address, spender)
        approvals = []
        if allowance < units:
            if allowance > 0:
                reset, reset_spender = await self.api().approval(asset, 0)
                if reset_spender != spender:
                    raise SwapError("spender_changed", "승인 대상이 조회 중 변경되었습니다.")
                approvals.append({"kind": "approve_reset", "tx": reset})
            approvals.append({"kind": "approve", "tx": approval})
        swap, quote, minimum = await self.api().swap(
            token, asset, units, owner, self.settings.slippage_percent
        )
        self.check_swap_quote(quote)
        balance = await rpc.token_balance(owner, asset.address)
        if balance < units:
            raise SwapError(
                "token_balance_insufficient", f"매수용 {asset.symbol} 잔고가 부족합니다."
            )
        nonce = await rpc.integer("eth_getTransactionCount", [owner, "pending"])
        latest = await rpc.integer("eth_getTransactionCount", [owner, "latest"])
        if nonce != latest:
            raise SwapError(
                "pending_nonce", "이 지갑에 처리 중인 트랜잭션이 있습니다. 먼저 결과를 확인하세요."
            )
        gas_price = await rpc.integer("eth_gasPrice", [])
        native_balance = await rpc.integer("eth_getBalance", [owner, "latest"])
        gas_price = max(1, (gas_price * 3 + 1) // 2)
        transfer_gas = fresh["gas"]["transfer_gas_units"]
        total_gas = swap["gas"] + sum(a["tx"]["gas"] for a in approvals) + transfer_gas
        if native_balance < total_gas * gas_price:
            raise SwapError(
                "gas_balance_insufficient", "스왑·승인·전송 예산의 네이티브 가스 잔고가 부족합니다."
            )
        # Include a conservative transaction gas reserve in the final pre-confirmation gap.
        conservative = await self.conservative_result(
            request, token, quote, amount, total_gas, gas_price, swap["gas"]
        )
        intent_id = uuid.uuid4().hex
        now = int(time.time() * 1000)
        entry = {
            "id": intent_id,
            "status": "awaiting_confirmation",
            "created_at_ms": now,
            "expires_at_ms": now + self.settings.confirmation_seconds * 1000,
            "request": request.model_dump(),
            "owner": owner,
            "dry_run": self.collector.credentials.dry_run,
            "config_digest": self.config_digest,
            "spender": spender,
            "purchase_asset": asset.model_dump(),
            "approval_units": approval_units,
            "minimum_receive_units": minimum,
            "allowance": allowance,
            "gas_budget_wei": total_gas * gas_price,
            "nonce": nonce,
            "gas_price": gas_price,
            "approvals": approvals,
            "swap": swap,
            "transactions": [],
            "summary": {
                "coin_id": coin.coin_id,
                "token_name": token.name,
                "symbol": token.symbol,
                "chain_index": token.chain_index,
                "chain_name": self.collector.chain_names.get(token.chain_index),
                "address": token.address,
                "exchange": request.exchange,
                "amount_usdt": request.amount_usdt,
                "purchase_symbol": asset.symbol,
                "purchase_address": asset.address,
                "slippage_percent": self.settings.slippage_percent,
                "expected_quantity": format(quote["quantity"], "f"),
                "minimum_quantity": format(Decimal(minimum) / 10**token.decimals, "f"),
                "approval_amount": format(Decimal(approval_units) / 10**asset.decimals, "f"),
                "approval_count": len(approvals),
                "approval_policy": self.settings.approval_policy,
                "gas_budget_native": format(
                    Decimal(total_gas * gas_price) / 10**asset.native_decimals, "f"
                ),
                "gap_percent": conservative["gap_percent"],
            },
            "actual_quantity": None,
            "error": None,
        }
        with self.store.lock():
            data = self.read()
            self.assert_no_active(data, owner, token.chain_index)
            # Expired unconfirmed intents are inert and can be pruned.
            data["intents"] = {
                k: v
                for k, v in data["intents"].items()
                if v.get("status") != "awaiting_confirmation" or v.get("expires_at_ms", 0) > now
            }
            data["intents"][intent_id] = entry
            self.store.write(data)
        return self.public(entry)

    def check_swap_quote(self, quote):
        if (
            quote["honeypot"] is not False
            or quote["source_honeypot"] is not False
            or quote["tax"] != 0
            or quote["source_tax"] != 0
            or quote["price_impact_percent"] is None
            or abs(quote["price_impact_percent"]) >= 3
        ):
            raise SwapError(
                "swap_quote_warning",
                "실제 스왑 데이터 견적에 세금·허니팟·가격 영향 경고가 있습니다.",
            )

    async def conservative_result(
        self, request, token, quote, amount, total_gas, gas_price, swap_gas
    ):
        adapter = self.detail.adapters()
        listing = next(
            c for c in self.collector.registry.coins if c.coin_id == request.coin_id
        ).exchanges[request.exchange]
        book = await adapter.book(request.exchange, "KRW-" + listing.symbol)
        fx = await adapter.book("upbit", "KRW-USDT")
        native_key = (
            token.chain_index,
            native_address(token.chain_index),
        )
        prices = await self.collector.okx.fetch([native_key])
        native = prices.get(native_key)
        now = int(time.time() * 1000)
        ttl = self.collector.settings.pricing.stale_seconds * 1000
        if (
            not native
            or any(
                not -SOURCE_CLOCK_SKEW_MS <= now - t <= ttl
                for t in (native.source_ms, book["source_ms"], fx["source_ms"])
            )
            or fx["ask"] is None
        ):
            raise SwapError("quote_stale", "실행 비용 또는 호가가 만료되었습니다.")
        asset = self.detail.purchase_asset(token.chain_index, request.purchase_symbol)
        with localcontext() as ctx:
            ctx.prec = 400
            unit_cost = Decimal(gas_price) * native.value / 10**asset.native_decimals
            # Use the larger swap fee estimate, without counting swap gas twice.
            if quote["swap_gas_usdt"] is None:
                raise SwapError("gas_unknown", "스왑 네트워크 비용이 없습니다.")
            budget = Decimal(total_gas - swap_gas) * unit_cost + max(
                Decimal(swap_gas) * unit_cost, quote["swap_gas_usdt"]
            )
            result = effective_result(
                amount, consume_bids(quote["net_quantity"], book["bids"]), fx["ask"], budget
            )
        if result is None:
            raise SwapError("insufficient_depth", "매수 호가 잔량이 부족합니다.")
        if Decimal(result["gap_percent"]) < Decimal(self.settings.min_gap_percent):
            raise SwapError(
                "gap_below_minimum", "가스 예산을 반영한 직전 실효갭이 최소값보다 낮습니다."
            )
        return result

    @staticmethod
    def assert_no_active(data, owner, chain, except_id=None):
        for key, entry in data["intents"].items():
            if (
                key != except_id
                and entry.get("owner") == owner
                and entry["request"]["chain_index"] == chain
                and entry.get("status") not in TERMINAL | {"awaiting_confirmation"}
            ):
                raise SwapError(
                    "operation_pending", "이 지갑·체인의 이전 실행 결과부터 확인하세요.", 409
                )

    @staticmethod
    def public(entry):
        return {
            k: entry.get(k)
            for k in (
                "id",
                "status",
                "created_at_ms",
                "expires_at_ms",
                "dry_run",
                "summary",
                "transactions",
                "actual_quantity",
                "error",
            )
        }

    async def confirm(self, intent_id):
        with self.store.lock():
            data = self.read()
            entry = data["intents"].get(intent_id)
            if not entry:
                raise SwapError("intent_missing", "확인할 견적이 없습니다.", 404)
            if entry["status"] != "awaiting_confirmation":
                return self.public(entry)
            if entry["expires_at_ms"] <= int(time.time() * 1000):
                raise SwapError(
                    "intent_expired", "확인 시간이 지나 다시 견적을 준비해야 합니다.", 409
                )
            if (
                entry["config_digest"] != self.config_digest
                or entry["dry_run"] != self.collector.credentials.dry_run
            ):
                raise SwapError(
                    "configuration_changed", "설정이 바뀌었습니다. 새 견적을 확인하세요.", 409
                )
            self.assert_no_active(data, entry["owner"], entry["request"]["chain_index"], intent_id)
            entry["status"] = "rechecking"
            self.store.write(data)
        task = asyncio.create_task(self._worker(intent_id))
        self.tasks[intent_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(intent_id, None))
        return self.public(entry)

    def save_entry(self, entry):
        data = self.read()
        data["intents"][entry["id"]] = entry
        self.store.write(data)
        try:
            self.log.event(
                "swap_state",
                operation_id=entry["id"],
                status=entry["status"],
                chain_index=entry["request"]["chain_index"],
                error=entry.get("error"),
            )
        except OSError:
            pass  # Durable state remains authoritative even if optional event logging fails.

    async def _worker(self, intent_id):
        entry = None
        try:
            with self.store.lock():
                entry = self.read()["intents"][intent_id]
                req = DetailRequest.model_validate(entry["request"])
                _coin, token, amount, asset, owner = self.identity(req)
                if entry.get("purchase_asset") != asset.model_dump():
                    raise SwapError(
                        "purchase_asset_changed",
                        "매수 자산 설정이 변경되었습니다. 새 견적이 필요합니다.",
                    )
                if owner != entry["owner"]:
                    raise SwapError("wallet_changed", "지갑 주소가 변경되었습니다.")
                fresh = await self.detail.calculate(req)
                self.check_detail(fresh)
                rpc = self.rpc(token.chain_index)
                await rpc.check_chain()
                allowance = await rpc.allowance(owner, asset.address, entry["spender"])
                if await rpc.token_balance(owner, asset.address) < int(amount * 10**asset.decimals):
                    raise SwapError(
                        "token_balance_insufficient",
                        f"직전 재조회에서 {asset.symbol} 잔고가 부족합니다.",
                    )
                nonce = await rpc.integer("eth_getTransactionCount", [owner, "pending"])
                latest = await rpc.integer("eth_getTransactionCount", [owner, "latest"])
                if nonce != entry["nonce"] or latest != nonce:
                    raise SwapError(
                        "nonce_conflict", "지갑 nonce가 변경되었습니다. 새 견적이 필요합니다."
                    )
                if allowance != entry["allowance"]:
                    raise SwapError(
                        "allowance_changed",
                        "확인 후 allowance가 변경되었습니다. 새 견적이 필요합니다.",
                    )
                if await rpc.integer("eth_getBalance", [owner, "latest"]) < entry["gas_budget_wei"]:
                    raise SwapError(
                        "gas_balance_insufficient", "직전 재조회에서 전체 가스 예산이 부족합니다."
                    )
                # DRY_RUN completes the whole planned pipeline before any signer/broadcast call.
                if entry["dry_run"]:
                    swap, quote, minimum = await self.api().swap(
                        token,
                        asset,
                        int(amount * 10**asset.decimals),
                        owner,
                        self.settings.slippage_percent,
                    )
                    self.check_swap_quote(quote)
                    if swap["gas"] > entry["swap"]["gas"]:
                        raise SwapError(
                            "gas_budget_changed", "확인한 스왑 가스 상한을 초과했습니다."
                        )
                    if minimum < entry["minimum_receive_units"]:
                        raise SwapError(
                            "quote_deteriorated", "최소 수령량이 확인한 견적보다 낮아졌습니다."
                        )
                    await self.conservative_result(
                        req,
                        token,
                        quote,
                        amount,
                        swap["gas"]
                        + sum(a["tx"]["gas"] for a in entry["approvals"])
                        + fresh["gas"]["transfer_gas_units"],
                        entry["gas_price"],
                        swap["gas"],
                    )
                    entry["dry_run_transactions"] = [
                        {
                            "kind": a["kind"],
                            "to": a["tx"]["to"],
                            "gas": a["tx"]["gas"],
                            "nonce": nonce + i,
                        }
                        for i, a in enumerate(entry["approvals"])
                    ]
                    entry["dry_run_transactions"].append(
                        {
                            "kind": "swap",
                            "to": swap["to"],
                            "gas": swap["gas"],
                            "nonce": nonce + len(entry["approvals"]),
                        }
                    )
                    entry["status"] = "dry_run_complete"
                    self.save_entry(entry)
                    return
                # Recreate approvals from current allowance; no infinite approvals.
                units = int(amount * 10**asset.decimals)
                steps = []
                if allowance < units:
                    if allowance:
                        reset, _ = await self.api().approval(asset, 0)
                        steps.append(("approve_reset", reset))
                    approve, spender = await self.api().approval(asset, entry["approval_units"])
                    if spender != entry["spender"]:
                        raise SwapError("spender_changed", "승인 대상이 변경되었습니다.")
                    steps.append(("approve", approve))
                planned = {a["kind"]: a["tx"]["gas"] for a in entry["approvals"]}
                if any(kind not in planned or tx["gas"] > planned[kind] for kind, tx in steps):
                    raise SwapError("approval_changed", "승인 단계가 확인한 계획을 초과했습니다.")
                for kind, tx in steps:
                    ok = await self.send_and_wait(entry, rpc, kind, tx, nonce)
                    if not ok:
                        return
                    nonce += 1
                # Recheck deposit state, profitability, payload, allowance and balances after approvals.
                fresh = await self.detail.calculate(req)
                self.check_detail(fresh)
                swap, quote, minimum = await self.api().swap(
                    token, asset, units, owner, self.settings.slippage_percent
                )
                self.check_swap_quote(quote)
                if swap["gas"] > entry["swap"]["gas"]:
                    raise SwapError(
                        "gas_budget_changed",
                        "승인 후 스왑 가스 예산이 증가했습니다. 새 확인이 필요합니다.",
                    )
                if minimum < entry["minimum_receive_units"]:
                    raise SwapError(
                        "quote_deteriorated", "승인 중 최소 수령량이 확인한 견적보다 낮아졌습니다."
                    )
                await self.conservative_result(
                    req,
                    token,
                    quote,
                    amount,
                    swap["gas"]
                    + sum(a["tx"]["gas"] for a in entry["approvals"])
                    + fresh["gas"]["transfer_gas_units"],
                    entry["gas_price"],
                    swap["gas"],
                )
                if await rpc.allowance(owner, asset.address, entry["spender"]) < units:
                    raise SwapError("allowance_insufficient", "승인 후 allowance가 부족합니다.")
                if await rpc.token_balance(owner, asset.address) < units:
                    raise SwapError(
                        "token_balance_insufficient", f"스왑 직전 {asset.symbol} 잔고가 부족합니다."
                    )
                if not await self.send_and_wait(entry, rpc, "swap", swap, nonce):
                    return
                entry["status"] = "success"
                await self.fill_actual(entry, token)
                self.save_entry(entry)
        except asyncio.CancelledError:
            if entry:
                self.record_failure(entry, "interrupted")
            raise
        except Exception as exc:  # noqa: BLE001 - preserve durable on-chain outcome.
            if entry:
                code = (
                    exc.code
                    if isinstance(exc, SwapError)
                    else exc.category.value
                    if isinstance(exc, AdapterError)
                    else "internal_error"
                )
                self.record_failure(entry, code)

    def record_failure(self, entry, code):
        transactions = entry["transactions"]
        if any(t["status"] == "pending" for t in transactions):
            entry["status"] = "submission_unknown"
        elif any(t["kind"] == "swap" and t["status"] == "confirmed" for t in transactions):
            entry["status"] = "success"
            code = "history_pending" if entry.get("actual_quantity") is None else code
        elif any(t["status"] == "reverted" for t in transactions):
            entry["status"] = "reverted"
        else:
            entry["status"] = "rejected"
        entry["error"] = code
        self.save_entry(entry)

    async def send_and_wait(self, entry, rpc, kind, tx, nonce):
        owner = entry["owner"]
        if entry["dry_run"] or self.collector.credentials.dry_run:
            raise SwapError(
                "dry_run_broadcast_blocked", "DRY_RUN에서는 승인·스왑을 전송하지 않습니다."
            )
        if await rpc.integer("eth_getTransactionCount", [owner, "pending"]) != nonce:
            raise SwapError("nonce_conflict", "전송 직전 nonce 충돌입니다.")
        price = max(entry["gas_price"], await rpc.integer("eth_gasPrice", []))
        wire = {"from": owner, "to": tx["to"], "data": tx["data"], "value": hex(tx["value"])}
        estimated = await rpc.integer("eth_estimateGas", [wire])
        gas = max(tx["gas"], (estimated * 3 + 1) // 2)
        # Never expand beyond the gas price/limit explicitly confirmed by the user.
        if price > entry["gas_price"] or gas > tx["gas"]:
            raise SwapError(
                "gas_budget_changed",
                "가스 예산이 확인한 범위를 초과했습니다. 새 확인이 필요합니다.",
            )
        if await rpc.integer("eth_getBalance", [owner, "latest"]) < gas * price:
            raise SwapError("gas_balance_insufficient", "전송 직전 가스 잔고가 부족합니다.")
        transaction = {
            "chainId": int(entry["request"]["chain_index"]),
            "to": tx["to"],
            "value": tx["value"],
            "data": tx["data"],
            "gas": gas,
            "gasPrice": price,
            "nonce": nonce,
        }
        raw, tx_hash = self.signer.sign(transaction, owner)
        tx_hash = hexdata(tx_hash, size=32)
        record = {"kind": kind, "hash": tx_hash, "nonce": nonce, "status": "pending"}
        entry["transactions"].append(record)
        entry["status"] = "pending"
        self.save_entry(entry)  # Durable transaction identity exists BEFORE sending bytes.
        await rpc.broadcast(raw, tx_hash)
        deadline = time.monotonic() + self.settings.receipt_timeout_seconds
        while time.monotonic() < deadline:
            receipt = await self.receipt(rpc, record)
            if receipt is not None:
                record["status"] = "confirmed" if receipt else "reverted"
                entry["status"] = "rechecking" if receipt else "reverted"
                entry["error"] = None if receipt else "transaction_reverted"
                self.save_entry(entry)
                return receipt
            await asyncio.sleep(self.settings.receipt_poll_seconds)
        entry["status"] = "pending"
        entry["error"] = "receipt_timeout"
        self.save_entry(entry)
        return False

    async def receipt(self, rpc, record):
        receipt = await rpc.call("eth_getTransactionReceipt", [record["hash"]])
        if receipt is None:
            return None
        if (
            not isinstance(receipt, dict)
            or str(receipt.get("transactionHash", "")).lower() != record["hash"]
        ):
            raise SwapError("receipt_mismatch", "영수증 트랜잭션 해시가 다릅니다.")
        status = receipt.get("status")
        if status not in ("0x0", "0x1"):
            raise SwapError("receipt_invalid", "영수증 상태를 확인할 수 없습니다.")
        block = int(receipt.get("blockNumber"), 16)
        current = await rpc.integer("eth_blockNumber", [])
        if current - block + 1 < self.settings.receipt_confirmations:
            return None
        return status == "0x1"

    async def fill_actual(self, entry, token):
        record = next(t for t in reversed(entry["transactions"]) if t["kind"] == "swap")
        try:
            entry["actual_quantity"] = await self.api().history(
                token.chain_index, record["hash"], entry["owner"], token
            )
            entry["error"] = None if entry["actual_quantity"] is not None else "history_pending"
        except Exception:  # noqa: BLE001 - no provider secrets.
            entry["actual_quantity"] = None
            entry["error"] = "history_pending"

    async def status(self, intent_id):
        entry = self.read()["intents"].get(intent_id)
        if not entry:
            raise SwapError("intent_missing", "실행 기록이 없습니다.", 404)
        if intent_id not in self.tasks and entry["status"] not in {
            "awaiting_confirmation",
            "dry_run_complete",
            "rejected",
            "failed",
            "reverted",
        }:
            with self.store.lock():
                entry = self.read()["intents"][intent_id]
                req = DetailRequest.model_validate(entry["request"])
                matches = [
                    t
                    for c in self.collector.registry.coins
                    if c.coin_id == req.coin_id
                    for t in c.tokens
                    if t.chain_index == req.chain_index and t.address == req.address
                ]
                rpc = self.rpc(req.chain_index)
                await rpc.check_chain()
                for record in entry["transactions"]:
                    if record["status"] == "pending":
                        success = await self.receipt(rpc, record)
                        if success is not None:
                            record["status"] = "confirmed" if success else "reverted"
                txs = entry["transactions"]
                if any(t["status"] == "reverted" for t in txs):
                    entry["status"] = "reverted"
                    entry["error"] = "transaction_reverted"
                elif any(t["status"] == "pending" for t in txs):
                    entry["status"] = "pending"
                elif txs and txs[-1]["kind"] == "swap":
                    entry["status"] = "success"
                    if matches:
                        await self.fill_actual(entry, matches[0])
                else:
                    entry["status"] = "approval_complete_reconfirm"
                    entry["error"] = "new_confirmation_required"
                self.save_entry(entry)
        result = self.public(entry)
        if entry["status"] == "dry_run_complete":
            result["planned_transactions"] = entry.get("dry_run_transactions", [])
        return result

    async def stop(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
