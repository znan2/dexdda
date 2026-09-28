import hashlib
import json
from decimal import Decimal as D

import pytest
from detail_fixtures import attach_detail
from test_pricing import make_collector, token

from app.detail.models import DetailRequest
from app.swap.models import SwapError
from app.swap.service import SwapService


def req():
    return DetailRequest(
        coin_id="coin",
        chain_index="1",
        address=token().address,
        exchange="upbit",
        amount_usdt="1000",
    )


class FakeRpc:
    def __init__(self):
        self.allow = 0
        self.balance = 10**15
        self.native = 10**20
        self.nonce = 0
        self.sent = []
        self.receipts = {}
        self.revert = False
        self.timeout = False
        self.ambiguous = False

    async def check_chain(self):
        pass

    async def call(self, method, params):
        if method == "eth_getCode":
            return "0x"
        if method == "eth_getTransactionReceipt":
            return self.receipts.get(params[0])
        raise AssertionError(method)

    async def integer(self, method, params):
        return {
            "eth_getTransactionCount": self.nonce,
            "eth_gasPrice": 10**9,
            "eth_getBalance": self.native,
            "eth_estimateGas": 21000,
            "eth_blockNumber": 101,
        }[method]

    async def token_balance(self, *_):
        return self.balance

    async def allowance(self, *_):
        return self.allow

    async def broadcast(self, raw, expected_hash):
        tx = json.loads(bytes.fromhex(raw[2:]))
        self.sent.append((expected_hash, tx))
        self.nonce += 1
        if tx["data"].startswith("0x095ea7b3"):
            self.allow = int(tx["data"][-64:], 16)
        if not self.timeout:
            self.receipts[expected_hash] = {
                "transactionHash": expected_hash,
                "status": "0x0" if self.revert else "0x1",
                "blockNumber": "0x64",
            }
        if self.ambiguous:
            raise TimeoutError("sentinel-secret-rpc-url")
        return expected_hash


class FakeSigner:
    def __init__(self):
        self.calls = 0

    def check(self, owner):
        pass

    def sign(self, tx, owner):
        self.calls += 1
        raw = json.dumps(tx, sort_keys=True).encode()
        return "0x" + raw.hex(), "0x" + hashlib.sha256(raw).hexdigest()


class FakeSwapAdapter:
    def __init__(self, detail_adapter):
        self.detail = detail_adapter

    async def approval(self, asset, units):
        spender = "0x" + "b" * 40
        return {
            "to": asset.address,
            "value": 0,
            "data": "0x095ea7b3" + spender[2:].zfill(64) + hex(units)[2:].zfill(64),
            "gas": 70000,
        }, spender

    async def swap(self, token, asset, units, owner, slippage):
        quote = await self.detail.quote(token, asset, units)
        return (
            {"to": "0x" + "d" * 40, "value": 0, "data": "0x12345678", "gas": 100000},
            quote,
            int(quote["quantity"] * 10**token.decimals * D(".995")),
        )

    async def history(self, *_):
        return "999.5"


@pytest.fixture
async def setup(tmp_path, credentials):
    collector = make_collector(credentials)
    detail, adapter, _state = attach_detail(collector)
    settings = collector.settings.model_copy(
        update={
            "swap": collector.settings.swap.model_copy(
                update={"receipt_timeout_seconds": 1, "receipt_poll_seconds": 0.05}
            )
        }
    )
    collector.settings = settings
    rpc = FakeRpc()
    signer = FakeSigner()
    service = SwapService(
        collector,
        detail,
        path=tmp_path / "swaps.json",
        contracts={"1": {"router": "0x" + "d" * 40, "spender": "0x" + "b" * 40}},
        adapter=FakeSwapAdapter(adapter),
        rpc_factory=lambda _: rpc,
        signer=signer,
    )
    yield collector, detail, adapter, rpc, signer, service
    await service.stop()
    await collector.client.aclose()


async def settle(service, ident):
    task = service.tasks.get(ident)
    if task:
        await task
    return await service.status(ident)


async def test_complete_dry_run_never_signs_or_broadcasts_including_reset(setup):
    _, _, _, rpc, signer, service = setup
    rpc.allow = 1
    prepared = await service.prepare(req())
    assert prepared["summary"]["approval_count"] == 2
    assert prepared["status"] == "awaiting_confirmation"
    assert signer.calls == 0 and rpc.sent == []
    await service.confirm(prepared["id"])
    result = await settle(service, prepared["id"])
    assert result["status"] == "dry_run_complete"
    assert [t["kind"] for t in result["planned_transactions"]] == [
        "approve_reset",
        "approve",
        "swap",
    ]
    assert signer.calls == 0 and rpc.sent == []
    assert "0x095ea7b3" not in json.dumps(result)


@pytest.mark.parametrize(
    "case,code",
    [
        ("amount", "amount_limit"),
        ("bridge", "bridge_forbidden"),
        ("chain", "chain_not_ready"),
        ("balance", "token_balance_insufficient"),
        ("gas", "gas_balance_insufficient"),
        ("warning", "quote_warning"),
        ("gap", "gap_below_minimum"),
        ("cap", "approval_cap_too_low"),
    ],
)
async def test_each_execution_guard(setup, case, code):
    c, d, a, rpc, signer, s = setup
    request = req()
    if case == "amount":
        request = request.model_copy(update={"amount_usdt": "1001"})
    if case == "bridge":
        c.registry.coins[0].tokens[0].status["upbit"] = "bridge_candidate"
    if case == "chain":
        d.assets = {}
    if case == "balance":
        rpc.balance = 0
    if case == "gas":
        rpc.native = 0
    if case == "warning":
        a.honeypot = True
    if case == "gap":
        s.settings = s.settings.model_copy(update={"min_gap_percent": "99"})
    if case == "cap":
        s.settings = s.settings.model_copy(update={"approval_cap_usdt": "1"})
    with pytest.raises(SwapError) as caught:
        await s.prepare(request)
    assert caught.value.code == code
    assert signer.calls == 0 and not rpc.sent


async def test_mismatched_ca_is_rejected_before_quote(setup):
    _, _, a, _, _, s = setup
    from app.detail.service import DetailUnavailable

    with pytest.raises(DetailUnavailable):
        await s.prepare(req().model_copy(update={"address": "0x" + "f" * 40}))
    assert a.quote_calls == []


async def test_mock_live_success_exact_idempotency_and_actual_quantity(setup):
    c, _, _, rpc, signer, s = setup
    c.credentials = c.credentials.model_copy(update={"dry_run": False})
    p = await s.prepare(req())
    await s.confirm(p["id"])
    await s.confirm(p["id"])
    result = await settle(s, p["id"])
    assert result["status"] == "success" and result["actual_quantity"] == "999.5"
    assert [t["kind"] for t in result["transactions"]] == ["approve", "swap"]
    count = len(rpc.sent)
    await s.confirm(p["id"])
    await s.status(p["id"])
    assert len(rpc.sent) == count == 2 and signer.calls == 2


async def test_revert_stops_pipeline_and_ambiguous_submission_never_replays(setup):
    c, _, _, rpc, _signer, s = setup
    c.credentials = c.credentials.model_copy(update={"dry_run": False})
    rpc.revert = True
    p = await s.prepare(req())
    await s.confirm(p["id"])
    r = await settle(s, p["id"])
    assert r["status"] == "reverted" and len(rpc.sent) == 1
    rpc.revert = False
    rpc.ambiguous = True
    rpc.timeout = True
    p = await s.prepare(req())
    await s.confirm(p["id"])
    r = await settle(s, p["id"])
    assert r["status"] == "pending"
    before = len(rpc.sent)
    await s.confirm(p["id"])
    await s.status(p["id"])
    assert len(rpc.sent) == before and "sentinel-secret" not in json.dumps(r)
    with pytest.raises(SwapError):
        await s.prepare(req())


async def test_nonce_changed_after_modal_rejects_without_signing(setup):
    c, _, _, rpc, signer, s = setup
    c.credentials = c.credentials.model_copy(update={"dry_run": False})
    p = await s.prepare(req())
    rpc.nonce += 1
    await s.confirm(p["id"])
    r = await settle(s, p["id"])
    assert r["status"] == "rejected" and r["error"] == "nonce_conflict"
    assert signer.calls == 0 and not rpc.sent


async def test_restart_reconciles_pending_approval_requires_new_confirmation(setup):
    c, d, _, rpc, signer, s = setup
    c.credentials = c.credentials.model_copy(update={"dry_run": False})
    rpc.timeout = True
    p = await s.prepare(req())
    await s.confirm(p["id"])
    await settle(s, p["id"])
    before = len(rpc.sent)
    tx_hash = rpc.sent[-1][0]
    restarted = SwapService(
        c,
        d,
        path=s.store.path,
        contracts=s.contracts,
        adapter=s.adapter,
        rpc_factory=lambda _: rpc,
        signer=signer,
    )
    rpc.receipts[tx_hash] = {"transactionHash": tx_hash, "status": "0x1", "blockNumber": "0x64"}
    r = await restarted.status(p["id"])
    assert r["status"] == "approval_complete_reconfirm"
    assert len(rpc.sent) == before


async def test_confirmation_rechecks_gas_and_allowance_and_never_exceeds_budget(setup):
    _c, _, _, rpc, signer, s = setup
    p = await s.prepare(req())
    rpc.native = 0
    await s.confirm(p["id"])
    result = await settle(s, p["id"])
    assert result["status"] == "rejected" and result["error"] == "gas_balance_insufficient"
    rpc.native = 10**20
    p = await s.prepare(req())
    rpc.allow = 1
    await s.confirm(p["id"])
    result = await settle(s, p["id"])
    assert result["status"] == "rejected" and result["error"] == "allowance_changed"
    assert signer.calls == 0 and not rpc.sent


async def test_hash_is_durable_before_broadcast_and_exact_approval_policy(setup):
    c, _, _, rpc, _, s = setup
    c.credentials = c.credentials.model_copy(update={"dry_run": False})
    s.settings = s.settings.model_copy(update={"approval_policy": "exact"})
    original = rpc.broadcast

    async def check(raw, tx_hash):
        entries = s.read()["intents"].values()
        assert any(
            t["hash"] == tx_hash and t["status"] == "pending"
            for e in entries
            for t in e["transactions"]
        )
        return await original(raw, tx_hash)

    rpc.broadcast = check
    p = await s.prepare(req())
    assert D(p["summary"]["approval_amount"]) == 1000
    await s.confirm(p["id"])
    assert (await settle(s, p["id"]))["status"] == "success"
    assert rpc.allow == 1000000000


async def test_execution_cost_counts_swap_once_and_retains_approval_costs(setup):
    _, detail, adapter, _, _, service = setup
    _, selected, amount = detail.select(req())
    quote = await adapter.quote(selected, detail.assets["1"], 1_000_000_000)
    result = await service.conservative_result(
        req(), selected, quote, amount, 270000, 1_000_000_000, 100000
    )
    # 1 USDT swap estimate (larger than RPC's 0.2) + 0.34 approval/transfer.
    assert D(result["profit_usdt"]) == D("98.66")


async def test_history_failure_keeps_confirmed_swap_success_and_never_replays(setup):
    c, _, _, rpc, signer, service = setup
    c.credentials = c.credentials.model_copy(update={"dry_run": False})

    async def unavailable(*_):
        raise TimeoutError("sentinel-secret-provider-response")

    service.adapter.history = unavailable
    prepared = await service.prepare(req())
    await service.confirm(prepared["id"])
    result = await settle(service, prepared["id"])
    assert result["status"] == "success" and result["error"] == "history_pending"
    assert result["actual_quantity"] is None
    before = (len(rpc.sent), signer.calls)
    await service.confirm(prepared["id"])
    assert (await service.status(prepared["id"]))["status"] == "success"
    assert (len(rpc.sent), signer.calls) == before


async def test_usdc_execution_uses_selected_balance_approval_and_confirmation(setup, monkeypatch):
    _, detail, _, rpc, signer, service = setup
    asset = detail.assets["1"].model_copy(update={"symbol": "USDC", "address": "0x" + "e" * 40})
    detail.other_assets[("1", "USDC")] = asset
    addresses = []

    async def selected_balance(owner, ca):
        addresses.append(ca)
        assert ca == asset.address
        return 10**15

    monkeypatch.setattr(rpc, "token_balance", selected_balance)
    prepared = await service.prepare(req().model_copy(update={"purchase_symbol": "USDC"}))
    assert prepared["summary"]["purchase_symbol"] == "USDC"
    assert prepared["summary"]["purchase_address"] == asset.address
    entry = service.read()["intents"][prepared["id"]]
    assert entry["request"]["purchase_symbol"] == "USDC"
    assert entry["approvals"][0]["tx"]["to"] == asset.address
    await service.confirm(prepared["id"])
    result = await settle(service, prepared["id"])
    assert result["status"] == "dry_run_complete"
    assert len(addresses) == 2
    assert signer.calls == 0 and not rpc.sent


async def test_selected_asset_balance_cannot_use_other_stablecoin(setup, monkeypatch):
    _, detail, _, rpc, signer, service = setup
    asset = detail.assets["1"].model_copy(update={"symbol": "USDC", "address": "0x" + "e" * 40})
    detail.other_assets[("1", "USDC")] = asset

    async def selected_balance(owner, ca):
        return 0 if ca == asset.address else 10**15

    monkeypatch.setattr(rpc, "token_balance", selected_balance)
    with pytest.raises(SwapError, match="USDC"):
        await service.prepare(req().model_copy(update={"purchase_symbol": "USDC"}))
    assert signer.calls == 0 and not rpc.sent


async def test_asset_contract_change_after_prepare_rejects_confirmation(setup):
    _, detail, _, rpc, signer, service = setup
    prepared = await service.prepare(req())
    detail.assets["1"] = detail.assets["1"].model_copy(update={"address": "0x" + "e" * 40})
    await service.confirm(prepared["id"])
    result = await settle(service, prepared["id"])
    assert result["error"] == "purchase_asset_changed"
    assert signer.calls == 0 and not rpc.sent
