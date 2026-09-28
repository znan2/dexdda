"""Synthetic CAP + deposit-stopped Ethereum fixture; no .env or external APIs."""

from decimal import Decimal
from types import SimpleNamespace

from detail_fixtures import attach_detail
from ui_server import FixtureCollector, fixture_coin

from app.adapters.prices import Price
from app.config import Credentials, Settings
from app.main import create_app
from app.pricing.collector import Collector

cap = fixture_coin("cap", 1)
cap.tokens.append(fixture_coin("cap", 56, bridge=True).tokens[0])
snx = fixture_coin("snx", 1, wallet="withdraw_only")
dust = fixture_coin("dust", 1)


class FeedbackCollector(FixtureCollector):
    def snapshot(self):
        super().snapshot()
        now = self.clock.milliseconds()
        for c in self.registry.coins:
            for t in c.tokens:
                low = c.coin_id == "dust" or c.coin_id == "cap" and t.chain_index == "1"
                self.liquidity.put(
                    (t.chain_index, t.address),
                    Price(
                        Decimal("7732863.5533")
                        if c.coin_id == "snx"
                        else Decimal(4711 if low else 1080000),
                        now,
                    ),
                    "fixture",
                )
                self.dex.put(
                    (t.chain_index, t.address), Price(Decimal(".5" if low else "1"), now), "fixture"
                )
        return Collector.snapshot(self)


# Distinct contracts per coin; identical symbols never determine routing.
for i, c in enumerate([cap, snx, dust], 1):
    c.tokens[:] = [
        t.model_copy(update={"address": "0x" + str(i * 100 + int(t.chain_index)).zfill(40)})
        for t in c.tokens
    ]
registry = SimpleNamespace(
    coins=[cap, snx, dust],
    chains=[{"chain_index": "1", "name": "Ethereum"}, {"chain_index": "56", "name": "BNB Chain"}],
)
collector = FeedbackCollector(
    registry, Settings(pricing={"ui_poll_seconds": 0.5, "stale_seconds": 3}), Credentials()
)
detail, adapter, _ = attach_detail(collector)
app = create_app(collector, detail, enable_execution=False, enable_operations=False)
