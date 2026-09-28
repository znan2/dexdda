import copy
import stat
from types import SimpleNamespace

import pytest
from test_pricing import coin, token

from app.adapters.base import AdapterError, Failure
from app.deposit.addresses import AddressBook, targets
from app.registry.models import ChainMap


class FakeAddresses:
    def __init__(self):
        self.known = {}
        self.creates = 0
        self.fail = False
        self.pending = False
        self.missing = False

    async def list(self):
        if self.fail:
            raise AdapterError(Failure.PERMISSION)
        return copy.deepcopy(self.known)

    async def create(self, currency, network):
        self.creates += 1
        row = {
            "currency": currency,
            "net_type": network,
            "deposit_address": "0x" + "a" * 40,
            "secondary_address": "123",
        }
        if not self.missing:
            self.known[(currency, network)] = row
        return None if self.pending else row

    async def get(self, currency, network):
        return self.known.get((currency, network))


@pytest.fixture
def setup(tmp_path, credentials):
    registry = SimpleNamespace(coins=[coin([token()])])
    mapping = ChainMap(
        networks=[
            {
                "exchange": ex,
                "net_type": "ETH",
                "chain_index": "1",
                "platform_id": "ethereum",
                "evidence": "fixture",
            }
            for ex in ("upbit", "bithumb")
        ]
    )
    book = AddressBook(tmp_path / "addresses.json", credentials, registry, mapping)
    return book, {ex: FakeAddresses() for ex in ("upbit", "bithumb")}


async def test_whole_registry_sync_idempotent_private_and_exact_network(setup):
    book, adapters = setup
    assert len(targets(book.registry, book.chain_map)) == 2
    first = await book.sync(adapters, poll_seconds=0)
    content = book.store.path.read_bytes()
    second = await book.sync(adapters, poll_seconds=0)
    assert first == second == {"total": 2, "counts": {"ready": 2}, "errors": {}}
    assert book.store.path.read_bytes() == content
    assert sum(a.creates for a in adapters.values()) == 2
    assert stat.S_IMODE(book.store.path.stat().st_mode) == 0o600
    assert len(book.for_route("coin", "1", token().address)) == 2
    data = book.read()
    data["entries"]["upbit:COIN:ETH"]["chain_index"] = "56"
    book.store.write(data)
    assert (
        next(r for r in book.for_route("coin", "1", token().address) if r["exchange"] == "upbit")[
            "deposit_address"
        ]
        is None
    )
    assert book.for_route("coin", "56", token().address) == []


async def test_async_creation_and_restart_only_reads_ready_addresses(setup):
    book, adapters = setup
    for a in adapters.values():
        a.pending = True
    result = await book.sync(adapters, poll_seconds=0)
    assert result["counts"] == {"ready": 2}
    await book.sync(adapters, poll_seconds=0)
    assert all(a.creates == 1 for a in adapters.values())


async def test_permission_failure_prevents_creating_and_cache_account_change_hides(setup):
    book, adapters = setup
    adapters["upbit"].fail = True
    result = await book.sync(adapters, poll_seconds=0)
    assert adapters["upbit"].creates == 0
    assert result["errors"] == {"permission_denied": 1}
    from pydantic import SecretStr

    book.credentials = book.credentials.model_copy(
        update={"bithumb_access_key": SecretStr("different")}
    )
    assert all(r["deposit_address"] is None for r in book.for_route("coin", "1", token().address))


async def test_read_only_never_creates_and_bridge_never_targets(setup):
    book, adapters = setup
    result = await book.sync(adapters, create=False)
    assert result["counts"] == {"missing": 2}
    assert all(a.creates == 0 for a in adapters.values())
    book.registry.coins[0].tokens[0].status["upbit"] = "bridge_candidate"
    assert len(targets(book.registry, book.chain_map)) == 1
