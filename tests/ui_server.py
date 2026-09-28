"""Offline browser fixture: real collector snapshots, no credentials/network requests."""

from decimal import Decimal
from types import SimpleNamespace

from app.adapters.prices import Price
from app.config import Credentials, Settings
from app.main import create_app
from app.pricing.collector import Collector
from app.pricing.state import Clock
from app.registry.models import RegistryCoin, Token


def fixture_coin(name, chain, *, bridge=False, wallet="working"):
    return RegistryCoin(
        coin_id=name,
        symbol="AI" if name == "gensyn" else name.upper(),
        names={
            "upbit_ko": "젠신" if name == "gensyn" else name + " 코인",
            "okx": "Gensyn" if name == "gensyn" else name.title(),
        },
        exchanges={
            ex: {
                "symbol": name.upper(),
                "markets": ["KRW-" + name.upper()],
                "net_types": ["ETH"],
                "networks": [
                    {"net_type": "ETH", "network_name": "Ethereum", "wallet_state": wallet}
                ],
            }
            for ex in ("upbit", "bithumb")
        },
        tokens=[
            Token(
                chain_index=str(chain),
                address="0x" + str(chain).zfill(40),
                decimals=18,
                name="Gensyn" if name == "gensyn" else name.title(),
                symbol=name.upper(),
                community_recognized=True,
                native=False,
                source="coingecko",
                status={
                    ex: "bridge_candidate" if bridge else "tradable" for ex in ("upbit", "bithumb")
                },
            )
        ],
        source="coingecko",
    )


class FixtureCollector(Collector):
    async def start(self):
        self.running = True

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
        self.fx.put("usdt", Price(Decimal(1300), now), "fixture")
        for coin in self.registry.coins:
            for token in coin.tokens:
                key = (token.chain_index, token.address)
                self.dex.put(key, Price(Decimal(1), now), "fixture")
                if coin.coin_id != "pending":
                    self.liquidity.put(key, Price(Decimal(10000000), now), "fixture")
                if coin.coin_id == "stale":
                    self.dex.fail([key], "missing_response")
            for ex in coin.exchanges:
                value = (
                    1430
                    if coin.coin_id == "alpha"
                    else (
                        1560
                        if coin.coin_id == "beta" and ex == "bithumb"
                        else (
                            1170
                            if coin.coin_id == "negative"
                            else (1950 if coin.coin_id == "suspect" else 1300)
                        )
                    )
                )
                self.cex.put(
                    (ex, "KRW-" + coin.coin_id.upper()), Price(Decimal(value), now), "fixture"
                )
        return super().snapshot()


coins = [
    fixture_coin("alpha", 1, wallet="withdraw_only"),
    fixture_coin("beta", 8453, wallet="deposit_only"),
    fixture_coin("zero", 10),
    fixture_coin("negative", 56),
    fixture_coin("stale", 137),
    fixture_coin("gensyn", 42161, bridge=True),
    fixture_coin("pending", 59144, bridge=True),
    fixture_coin("suspect", 43114),
]
registry = SimpleNamespace(
    coins=coins,
    chains=[
        {"chain_index": str(i), "name": n}
        for i, n in [
            (1, "Ethereum"),
            (8453, "Base"),
            (10, "Optimism"),
            (56, "BNB Chain"),
            (137, "Polygon"),
            (42161, "Arbitrum"),
            (59144, "Linea"),
            (43114, "Avalanche"),
        ]
    ],
)
collector = FixtureCollector(
    registry,
    Settings(pricing={"ui_poll_seconds": 0.5, "stale_seconds": 3}),
    Credentials(),
    clock=Clock(),
)
app = create_app(collector)
