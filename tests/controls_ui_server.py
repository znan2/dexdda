"""Offline wallet refresh and persistent exclusions browser fixture."""

import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from ui_server import FixtureCollector, fixture_coin

from app.config import Credentials, Settings
from app.main import create_app
from app.pricing.collector import Collector

coins = [
    fixture_coin(name, 1, wallet=state)
    for name, state in [
        ("fet", "working"),
        ("dual", "working"),
        ("outgoing", "deposit_only"),
        ("halted", "paused"),
        ("pending", "working"),
        ("cached", "working"),
    ]
]
coins[0].exchanges.pop("upbit")
for i, c in enumerate(coins, 1):
    c.tokens[:] = [t.model_copy(update={"address": "0x" + str(i).zfill(40)}) for t in c.tokens]
registry = SimpleNamespace(coins=coins, chains=[{"chain_index": "1", "name": "Ethereum"}])
workspace = TemporaryDirectory(prefix="dexdda-controls-")


class ControlsCollector(FixtureCollector):
    async def start(self):
        self.running = True
        for ex in self.markets:
            self.wallet_status.observe(ex, self.wallet_rows(ex), self.clock.monotonic())

        async def poll(ex):
            self.wallet_status.observe(
                ex, self.wallet_rows(ex, refreshed=True), self.clock.monotonic()
            )

        self.wallet_status.poll = poll

    def wallet_rows(self, ex, refreshed=False):
        return [
            {
                "currency": listing.symbol,
                **n,
                "wallet_state": "paused" if refreshed and c.coin_id == "fet" else n["wallet_state"],
            }
            for c in self.registry.coins
            for exchange, listing in c.exchanges.items()
            if exchange == ex
            for n in listing.networks
        ]

    def snapshot(self):
        states = copy.deepcopy(self.wallet_status.states)
        super().snapshot()
        self.wallet_status.states = states
        for c in self.registry.coins:
            if c.coin_id == "cached":
                t = c.tokens[0]
                self.liquidity.fail([(t.chain_index, t.address)], "timeout")
        return Collector.snapshot(self)


collector = ControlsCollector(
    registry,
    Settings(pricing={"ui_poll_seconds": 0.5}),
    Credentials(),
    preferences_path=Path(workspace.name) / "preferences.json",
)
app = create_app(collector, enable_execution=False, enable_operations=False)
