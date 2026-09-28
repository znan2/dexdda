"""Offline M5–M7 browser fixture. All balances, signatures, receipts and addresses are synthetic."""

import atexit
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from detail_fixtures import attach_detail
from pydantic import SecretStr
from test_swap import FakeRpc, FakeSigner, FakeSwapAdapter
from ui_server import collector

from app.deposit.addresses import AddressBook, account_scope, targets
from app.main import create_app
from app.swap.service import SwapService

temp = TemporaryDirectory(prefix="dexdda-ui-")
atexit.register(temp.cleanup)
root = Path(temp.name)
for coin in collector.registry.coins:
    if coin.coin_id == "alpha":
        for listing in coin.exchanges.values():
            for network in listing.networks:
                network["wallet_state"] = "working"
detail, adapter, _state = attach_detail(collector)
collector.credentials = collector.credentials.model_copy(
    update={
        "upbit_access_key": SecretStr("fixture-upbit"),
        "bithumb_access_key": SecretStr("fixture-bithumb"),
    }
)
rpc = FakeRpc()
signer = FakeSigner()
swap = SwapService(
    collector,
    detail,
    path=root / "swap_history.json",
    contracts={"1": {"router": "0x" + "d" * 40, "spender": "0x" + "b" * 40}},
    adapter=FakeSwapAdapter(adapter),
    rpc_factory=lambda _: rpc,
    signer=signer,
)
book = AddressBook(
    root / "addresses.json", collector.credentials, collector.registry, detail.chain_map
)
data = book.empty()
data["accounts"] = {ex: account_scope(collector.credentials, ex) for ex in ("upbit", "bithumb")}
for target in targets(collector.registry, detail.chain_map):
    data["entries"][target["key"]] = {
        **target,
        "status": "ready",
        "deposit_address": "0x" + "a" * 40,
        "secondary_address": "123456",
        "checked_at_ms": int(time.time() * 1000),
        "error": None,
    }
book.store.write(data)
# A historical mock success lets the UI test receipt/addresses without sending a real transaction.
token = next(c.tokens[0] for c in collector.registry.coins if c.coin_id == "alpha")
completed = {
    "id": "e" * 32,
    "status": "success",
    "created_at_ms": int(time.time() * 1000) - 1000,
    "expires_at_ms": 0,
    "request": {
        "coin_id": "alpha",
        "chain_index": "1",
        "address": token.address,
        "exchange": "upbit",
        "amount_usdt": "1000",
    },
    "owner": "0x" + "a" * 40,
    "dry_run": False,
    "actual_quantity": "999.5",
    "error": None,
    "transactions": [{"kind": "swap", "hash": "0x" + "f" * 64, "nonce": 0, "status": "confirmed"}],
    "summary": {
        "coin_id": "alpha",
        "token_name": "Alpha",
        "symbol": "ALPHA",
        "chain_index": "1",
        "chain_name": "Ethereum",
        "address": token.address,
        "exchange": "upbit",
        "amount_usdt": "1000",
        "slippage_percent": "0.5",
        "expected_quantity": "1000",
        "minimum_quantity": "995",
        "approval_count": 1,
        "approval_amount": "5000",
        "approval_policy": "capped",
        "gas_budget_native": "0.001",
        "gap_percent": "9.8",
    },
}
swap.store.write({"schema_version": 1, "intents": {completed["id"]: completed}})


class FixtureOperations:
    def start(self):
        pass

    async def stop(self):
        pass

    def snapshot(self):
        return {
            "markets": {
                "upbit": {"new": ["KRW-NEW"], "error": None},
                "bithumb": {
                    "new": [],
                    "mapping_pending": [{"market": "KRW-HELD", "reason": "코인 ID 연결 정보 없음"}],
                    "error": None,
                },
            },
            "reference_fx": {"date": "2026-09-18", "delayed": True},
            "reference_premium_percent": "1.23",
            "does_not_affect_gap": True,
        }


app = create_app(collector, detail, swap, FixtureOperations(), book)
