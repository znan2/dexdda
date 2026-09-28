"""Build the backend-free static demo in dist/ (no credentials, no network, never signs).

The real FastAPI app runs in-process against synthetic providers (the offline test fakes plus a
demo adapter below). Every response the dashboard needs is captured into dist/data/demo.json, and
assets/static-api.js answers the page's /api/* calls from that file. Coins, prices, order books
and contract addresses are fictional; addresses use the obviously synthetic 0xde70… prefix.

Usage: uv run scripts/build_static.py [--out dist]
"""

import argparse
import json
import shutil
import sys
import time
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))  # offline fakes shared with the browser fixtures

from detail_fixtures import attach_detail
from fastapi.testclient import TestClient
from test_swap import FakeRpc, FakeSigner, FakeSwapAdapter

from app.adapters.prices import Price
from app.balances import WalletBalances
from app.config import Credentials, Settings
from app.deposit.addresses import AddressBook
from app.main import create_app
from app.pricing.collector import Collector
from app.registry.loader import load_config
from app.registry.models import ChainMap, RegistryCoin, Token
from app.swap.service import SwapService

D = Decimal
WEB = ROOT / "src/app/web"
STATIC = ROOT / "scripts/static"
FX = D(1391)
AMOUNT = "1000"

# 한 곳에서 정책을 만들고 _headers와 meta 태그에 같이 쓴다. meta는 frame-ancestors를 무시한다.
CSP = [
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "require-trusted-types-for 'script'",
]
HEADER_ONLY_CSP = ["frame-ancestors 'none'"]
HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}

CHAINS = {"1": "Ethereum", "8453": "Base", "42161": "Arbitrum", "56": "BNB Chain"}
# 실제 config/chain_map.toml의 거래소별 입금망 표기. 업비트에는 BNB Chain 입금망이 없다.
NET_TYPES = {
    "upbit": {"1": "ETH", "8453": "BASENET", "42161": "ARBITRUM"},
    "bithumb": {"1": "ETH", "8453": "BASE_ETH", "42161": "ARB_ETH", "56": "BSC"},
}
PLATFORMS = {"1": "ethereum", "8453": "base", "42161": "arbitrum-one", "56": "binance-smart-chain"}

# (coin_id, symbol, 한글명, 영문명, [(chain, dex_usd, liquidity_usd)], {exchange: (gap%, wallet)},
#  deposit_chain, recognized). deposit_chain은 거래소가 지원하는 입금망 체인이며 다르면 브릿지 후보.
COINS = [
    ("lumen", "LUMN", "루멘", "Lumen Protocol", [("1", "2.418", 18_400_000)],
     {"upbit": ("3.42", "working"), "bithumb": ("2.87", "working")}, "1", True),
    ("orbit", "ORBT", "오르빗", "Orbit Finance", [("1", "0.8421", 7_200_000)],
     {"upbit": ("1.96", "working"), "bithumb": ("2.31", "working")}, "1", True),
    ("keel", "KEEL", "킬", "Keel Network", [("8453", "0.06318", 3_100_000)],
     {"upbit": ("1.12", "working"), "bithumb": ("0.84", "working")}, "8453", True),
    ("vesta", "VEST", "베스타", "Vesta Labs", [("42161", "14.73", 22_000_000)],
     {"upbit": ("0.38", "working"), "bithumb": ("-0.21", "working")}, "42161", True),
    ("tidal", "TIDE", "타이달", "Tidal Swap", [("1", "1.2045", 9_800_000)],
     {"upbit": ("-0.64", "working"), "bithumb": ("-1.18", "working")}, "1", True),
    ("quill", "QUIL", "퀼", "Quill", [("56", "0.331", 4_400_000)],
     {"bithumb": ("-1.92", "working")}, "56", False),
    ("fathom", "FTHM", "패덤", "Fathom Data", [("8453", "0.5274", 5_500_000), ("1", "0.5281", 2_100_000)],
     {"upbit": ("2.24", "working"), "bithumb": ("1.65", "working")}, "1", True),
    ("cobalt", "CBLT", "코발트", "Cobalt", [("1", "3.906", 6_300_000)],
     {"bithumb": ("0.95", "deposit_only")}, "1", True),
    ("ember", "EMBR", "엠버", "Ember", [("1", "0.0942", 3_800_000)],
     {"upbit": ("4.81", "withdraw_only")}, "1", True),
    ("prism", "PRSM", "프리즘", "Prism", [("1", "0.01274", 1_900_000)],
     {"upbit": ("41.5", "working"), "bithumb": ("38.9", "working")}, "1", True),
    ("sable", "SABL", "세이블", "Sable", [("42161", "0.7712", 640_000)],
     {"upbit": ("2.75", "working"), "bithumb": ("2.4", "working")}, "42161", True),
]  # fmt: skip
BALANCES = {
    ("1", "USDT"): "12480.5", ("1", "USDC"): "3200",
    ("4663", "USDG"): "0",
    ("8453", "USDT"): "850", ("8453", "USDC"): "1540.25",
    ("42161", "USDT0"): "2310", ("42161", "USDC"): "0",
    ("56", "USDT"): "640", ("56", "USDC"): "95.5",
}  # fmt: skip


def address(n):
    return "0xde70" + format(n, "036x")


def krw(value):
    step = D(1) if value >= 1000 else D("0.1") if value >= 10 else D("0.001")
    return value.quantize(step)


def build_registry():
    coins, prices, token_no = [], {}, 0
    for coin_id, symbol, ko, en, chains, listings, deposit_chain, recognized in COINS:
        exchanges = {}
        for ex, (_gap, wallet) in listings.items():
            net = NET_TYPES[ex].get(deposit_chain)
            exchanges[ex] = {
                "symbol": symbol,
                "markets": ["KRW-" + symbol],
                "net_types": [net],
                "networks": [
                    {"net_type": net, "network_name": CHAINS[deposit_chain], "wallet_state": wallet}
                ],
            }
        tokens = []
        for chain, dex, liquidity in chains:
            token_no += 1
            key = (chain, address(token_no))
            prices[key] = (D(dex), D(liquidity))
            tokens.append(
                Token(
                    chain_index=chain,
                    address=key[1],
                    decimals=18,
                    name=en,
                    symbol=symbol,
                    community_recognized=recognized,
                    native=False,
                    source="coingecko",
                    status={
                        ex: "tradable" if chain == deposit_chain else "bridge_candidate"
                        for ex in listings
                    },
                )
            )
        # CEX 호가는 대표(첫) 토큰의 DEX 가격에 목표 갭을 곱해 만든다.
        base = D(chains[0][1]) * FX
        for ex, (gap, _wallet) in listings.items():
            prices[(ex, "KRW-" + symbol)] = krw(base * (1 + D(gap) / 100))
        coins.append(
            RegistryCoin(
                coin_id=coin_id,
                symbol=symbol,
                names={"upbit_ko": ko, "bithumb_ko": ko, "okx": en},
                exchanges=exchanges,
                tokens=tokens,
                source="coingecko",
            )
        )
    chains = [{"chain_index": c, "name": n} for c, n in CHAINS.items()]
    chains.append({"chain_index": "4663", "name": "Robinhood"})
    return SimpleNamespace(coins=coins, chains=chains), prices


class DemoCollector(Collector):
    """Serves fixed synthetic quotes, stamped at snapshot time like a healthy live collector."""

    def __init__(self, registry, prices, **kwargs):
        super().__init__(registry, **kwargs)
        self.demo_prices = prices

    async def start(self):
        self.running = True
        await self.balances.refresh()

    async def stop(self):
        self.running = False

    def snapshot(self):
        for ex in self.markets:
            rows = [
                {"currency": listing.symbol, **network}
                for coin in self.registry.coins
                for exchange, listing in coin.exchanges.items()
                if exchange == ex
                for network in listing.networks
            ]
            self.wallet_status.observe(ex, rows, self.clock.monotonic())
        now = self.clock.milliseconds()
        self.fx.put("usdt", Price(FX, now), "demo")
        for coin in self.registry.coins:
            for token in coin.tokens:
                dex, liquidity = self.demo_prices[(token.chain_index, token.address)]
                self.dex.put((token.chain_index, token.address), Price(dex, now), "demo")
                self.liquidity.put(
                    (token.chain_index, token.address), Price(liquidity, now), "demo"
                )
            for ex, listing in coin.exchanges.items():
                market = listing.markets[0]
                self.cex.put((ex, market), Price(self.demo_prices[(ex, market)], now), "demo")
        return super().snapshot()


class DemoAdapter:
    """Detail/swap provider with per-coin prices, routes and three-level order books."""

    ROUTES = ("Uniswap V3", "Curve", "Balancer V2", "SushiSwap", "PancakeSwap V3", "Aerodrome")

    def __init__(self, registry, prices):
        self.registry, self.prices = registry, prices

    async def wallets(self, exchange):
        return [
            {"currency": c.exchanges[exchange].symbol, **n}
            for c in self.registry.coins
            if exchange in c.exchanges
            for n in c.exchanges[exchange].networks
        ]

    async def deposit_chance(self, symbol, network):
        return {"possible": True, "confirmations": 12 if network == "ETH" else 30, "minimum": D(0)}

    async def quote(self, token, asset, units):
        amount = D(units) / 10**asset.decimals
        dex, liquidity = self.prices[(token.chain_index, token.address)]
        impact = min(D("0.02"), amount / liquidity * 4)  # 얕은 풀일수록 가격 영향이 커진다
        quantity = amount / dex * (1 - impact)
        split = [("62", 0), ("27", 1), ("11", 2)] if token.chain_index == "1" else [("100", 5)]
        return {
            "quantity": quantity,
            "net_quantity": quantity,
            "tax": D(0),
            "source_tax": D(0),
            "honeypot": False,
            "source_honeypot": False,
            "price_impact_percent": -(impact * 100).quantize(D("0.01")),
            "swap_gas_usdt": D("2.4") if token.chain_index == "1" else D("0.06"),
            "routes": [
                f"{self.ROUTES[i]} · {asset.symbol} → {token.symbol} ({share}%)"
                for share, i in split
            ],
        }

    async def approval(self, asset, units):
        return {"spender": "0x" + "b" * 40, "gas_limit": D(65000)}

    async def book(self, exchange, market):
        now = int(time.time() * 1000)
        if market == "KRW-USDT":
            return {"bids": [(FX - 1, D(10**7))], "ask": FX, "source_ms": now}
        top = self.prices[(exchange, market)]
        # 매수 호가 3단: 대부분 1호가에서 체결되고 큰 금액은 아래 호가까지 내려간다.
        levels = [(top, D("0.55")), (krw(top * D("0.997")), D("0.35")), (krw(top * D("0.99")), 3)]
        depth = D(1_500_000) / top  # 1호가 잔량 ≈ 1,500,000 KRW
        return {
            "bids": [(price, depth * share) for price, share in levels],
            "ask": krw(top * D("1.002")),
            "source_ms": now,
        }


class DemoOperations:
    def start(self):
        pass

    async def stop(self):
        pass

    def snapshot(self):
        return {
            "markets": {
                "upbit": {"new": [], "error": None},
                "bithumb": {"new": [], "mapping_pending": [], "error": None},
            },
            "reference_fx": None,
            "reference_premium_percent": None,
            "does_not_affect_gap": True,
        }


def build_app(workdir):
    registry, prices = build_registry()
    collector = DemoCollector(registry, prices, settings=Settings(), credentials=Credentials())
    adapter = DemoAdapter(registry, prices)
    detail, adapter, _state = attach_detail(collector, adapter)
    detail.chain_map = load_config(ROOT / "config/chain_map.toml", ChainMap)

    class BalanceRpc:
        async def check_chain(self):
            pass

        async def integer(self, method, params):
            target = params[0]["to"]
            asset = next(a for a in collector.balances.assets if a.address == target)
            if params[0]["data"] == "0x313ce567":  # decimals()
                return asset.decimals
            value = D(BALANCES.get((asset.chain_index, asset.symbol), "0"))
            return int(value * 10**asset.decimals)

    collector.balances = WalletBalances(collector, rpc_factory=lambda _: BalanceRpc())
    rpc, signer = FakeRpc(), FakeSigner()
    swap = SwapService(
        collector,
        detail,
        path=workdir / "swaps.json",
        contracts={"1": {"router": "0x" + "d" * 40, "spender": "0x" + "b" * 40}},
        adapter=FakeSwapAdapter(adapter),
        rpc_factory=lambda _: rpc,
        signer=signer,
    )
    # 비어 있는 임시 주소록을 넘긴다. 생략하면 lifespan이 실제 data/deposit_addresses.json을 연다.
    book = AddressBook(
        workdir / "addresses.json", collector.credentials, registry, detail.chain_map
    )
    return create_app(collector, detail, swap, DemoOperations(), book), collector, signer


def request_key(body):
    # static-api.js의 String(Number(amount))와 같은 표기("1000", "12.5")
    amount = format(Decimal(body["amount_usdt"]).normalize(), "f")
    route = [body["coin_id"], body["chain_index"], body["address"], body["exchange"]]
    return "|".join([*route, body.get("purchase_symbol") or "USDT", amount])


def capture(workdir):
    app, collector, signer = build_app(workdir)
    data = {"schema_version": 1, "get": {}, "detail": {}, "swap": {}}
    with TestClient(app, base_url="http://127.0.0.1") as client:

        def get(path):
            response = client.get(path)
            assert response.status_code == 200, (path, response.status_code)
            return response.json()

        gaps = get("/api/gaps")
        data["captured_at_ms"] = gaps["generated_at_ms"]
        runtime = get("/api/runtime")
        session = {"X-Dexdda-Session": runtime["csrf"]}
        data["get"]["/api/runtime"] = {**runtime, "csrf": "static-demo"}
        for path in [
            "/api/health", "/api/preferences", "/api/purchase-assets", "/api/balances",
            "/api/logos", "/api/swaps", "/api/operations",
        ]:  # fmt: skip
            data["get"][path] = get(path)
        # 픽스처가 set에서 자산을 만들어 순서가 해시 시드에 따라 달라진다. 재빌드 diff를 줄인다.
        data["get"]["/api/purchase-assets"]["assets"].sort(key=lambda a: int(a["chain_index"]))
        data["get"]["/api/gaps"] = gaps
        for group in gaps["coin_groups"]:
            for route in group["routes"]:
                for exchange, quote in route["exchanges"].items():
                    if quote["route_status"] == "excluded":
                        continue
                    body = {
                        "coin_id": route["coin_id"],
                        "chain_index": route["chain_index"],
                        "address": route["address"],
                        "exchange": exchange,
                        "amount_usdt": AMOUNT,
                        "purchase_symbol": "USDT",
                    }
                    response = client.post("/api/detail", json=body)
                    assert response.status_code == 200, (body, response.json())
                    detail = response.json()
                    data["detail"][request_key(body)] = detail
                    executable = detail.get("result") and detail["route_status"] == "tradable"
                    if not executable or route["chain_index"] != "1":
                        continue  # 데모 실행 계약은 Ethereum만 등록돼 있다
                    prepared = client.post("/api/swap/prepare", json=body, headers=session)
                    if prepared.status_code != 200:
                        continue  # 실행 가드가 막는 경로는 데모에서도 실행 준비 버튼이 비활성이다
                    intent = prepared.json()
                    confirmed = client.post(
                        "/api/swap/confirm",
                        json={"intent_id": intent["id"], "confirmed": True},
                        headers=session,
                    )
                    assert confirmed.status_code == 200, confirmed.json()
                    final = get("/api/swap/" + intent["id"])
                    assert final["status"] == "dry_run_complete", final["status"]
                    data["swap"][request_key(body)] = {
                        "prepare": intent,
                        "confirm": confirmed.json(),
                        "final": final,
                    }
    assert collector.dry_run and signer.calls == 0, "demo capture must never sign"
    assert data["swap"], "at least one DRY_RUN flow is captured"
    return data


BANNER = (
    '<div class="demo-banner" role="note"><span><strong>정적 데모</strong> · '
    "가상의 코인과 합성 시세로 동작합니다. 실제 시세가 아니며 외부 요청·서명·전송을 하지 "
    "않습니다.</span></div>"
)


def page(html):
    csp = "; ".join(CSP)
    replacements = [
        ('<meta charset="utf-8">',
         f'<meta charset="utf-8">\n<meta http-equiv="Content-Security-Policy" content="{csp}">'),
        ("<title>dexdda · 갭 모니터</title>", "<title>dexdda · 갭 모니터 (정적 데모)</title>"),
        ('<link rel="stylesheet" href="/assets/dashboard.css">',
         '<link rel="stylesheet" href="assets/dashboard.css">\n'
         + '<script src="assets/static-api.js" defer></script>'),
        ('<a class="brand" href="/">', '<a class="brand" href="./">'),
        ('<span class="local-tag">LOCAL</span>', '<span class="local-tag">DEMO</span>'),
        ("</header>", "</header>\n" + BANNER),
        ('<a href="/api/gaps">가격 API 보기</a>', ""),
    ]  # fmt: skip
    for old, new in replacements:
        assert html.count(old) == 1, "index.html changed; update scripts/build_static.py: " + old
        html = html.replace(old, new)
    html = html.replace('src="/assets/', 'src="assets/')
    assert "/assets/" not in html and "/api/" not in html
    return html


def build(out):
    with TemporaryDirectory(prefix="dexdda-static-") as temp:
        data = capture(Path(temp))
    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    (out / "data").mkdir()
    for source in sorted(WEB.glob("*.js")) + [WEB / "dashboard.css", STATIC / "static-api.js"]:
        shutil.copyfile(source, out / "assets" / source.name)
    (out / "index.html").write_text(page((WEB / "index.html").read_text()))
    (out / "data/demo.json").write_text(json.dumps(data, ensure_ascii=False, sort_keys=True))
    policy = "; ".join(CSP + HEADER_ONLY_CSP)
    lines = ["/*", "  Content-Security-Policy: " + policy]
    lines += [f"  {name}: {value}" for name, value in HEADERS.items()]
    (out / "_headers").write_text("\n".join(lines) + "\n")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args(argv)
    data = build(args.out.resolve())
    print(
        json.dumps(
            {
                "out": str(args.out),
                "routes": sum(len(g["routes"]) for g in data["get"]["/api/gaps"]["coin_groups"]),
                "details": len(data["detail"]),
                "dry_run_flows": len(data["swap"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
