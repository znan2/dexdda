"""Offline purchase-asset + balance fixture, synthetic dry-run execution only."""

import atexit
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from detail_fixtures import attach_detail
from test_swap import FakeRpc, FakeSigner, FakeSwapAdapter
from ui_server import FixtureCollector, fixture_coin

from app.balances import WalletBalances
from app.config import Credentials, Settings
from app.main import create_app
from app.swap.service import SwapService

registry = SimpleNamespace(
    coins=[
        fixture_coin("alpha", 1),
        fixture_coin("cap", 56, bridge=True),
        fixture_coin("hood", 4663, bridge=True),
    ],
    chains=[
        {"chain_index": c, "name": n}
        for c, n in [("1", "Ethereum"), ("56", "BNB Chain"), ("4663", "Robinhood")]
    ],
)
collector = FixtureCollector(registry, Settings(), Credentials())
detail, adapter, _ = attach_detail(collector)
for chain in ("1", "56"):
    detail.other_assets[(chain, "USDC")] = detail.assets[chain].model_copy(
        update={
            "symbol": "USDC",
            "address": "0x" + "e" * 40,
            "decimals": 18 if chain == "56" else 6,
        }
    )
detail.other_assets[("4663", "USDG")] = detail.assets.pop("4663").model_copy(
    update={"symbol": "USDG", "address": "0x" + "f" * 40}
)


class BalanceRpc:
    async def check_chain(self):
        pass

    async def integer(self, method, params):
        address = params[0]["to"]
        asset = next(a for a in collector.balances.assets if a.address == address)
        if params[0]["data"] == "0x313ce567":
            return asset.decimals
        return 12345 * 10 ** (asset.decimals - 2)


collector.balances = WalletBalances(collector, rpc_factory=lambda _: BalanceRpc())
base_start = collector.start


async def start():
    await base_start()
    await collector.balances.refresh()


collector.start = start
temp = TemporaryDirectory(prefix="dexdda-assets-ui-")
atexit.register(temp.cleanup)
rpc, signer = FakeRpc(), FakeSigner()
swap = SwapService(
    collector,
    detail,
    path=Path(temp.name) / "swaps.json",
    contracts={"1": {"router": "0x" + "d" * 40, "spender": "0x" + "b" * 40}},
    adapter=FakeSwapAdapter(adapter),
    rpc_factory=lambda _: rpc,
    signer=signer,
)
app = create_app(collector, detail, swap=swap, enable_operations=False)
