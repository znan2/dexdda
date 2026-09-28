import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from test_deposit import FakeAddresses
from test_swap import req, settle
from test_swap import setup as setup  # noqa: PLC0414 - shared pytest fixture

from app.adapters.execution import ExecutionRpc, LocalSigner
from app.deposit.addresses import AddressBook
from app.main import create_app
from app.operations import EventLog, Operations, parse_ecb
from app.swap.models import SwapError


def test_reference_fx_cross_rate_delayed_and_untrusted_xml():
    xml = b'<Envelope><Cube><Cube time="2026-09-18"><Cube currency="USD" rate="1.2"/><Cube currency="KRW" rate="1560"/></Cube></Cube></Envelope>'
    result = parse_ecb(xml, today=date(2026, 9, 20))
    assert Decimal(result["usd_krw"]) == 1300 and result["delayed"] is True
    from app.adapters.base import AdapterError

    with pytest.raises(AdapterError):
        parse_ecb(xml, today=date(2026, 9, 30))
    with pytest.raises(AdapterError):
        parse_ecb(b'<!DOCTYPE x [<!ENTITY x "secret">]><x/>')
    with pytest.raises(AdapterError):
        parse_ecb(xml.replace(b"1.2", b"0"), today=date(2026, 9, 20))


def test_log_whitelist_never_serializes_secrets(tmp_path):
    path = tmp_path / "events.jsonl"
    EventLog(path).event(
        "swap_state",
        operation_id="a" * 32,
        status="pending",
        private_key="sentinel-key",
        error="https://rpc.invalid/sentinel-secret",
        headers={"Authorization": "sentinel-token"},
    )
    text = path.read_text()
    assert "sentinel" not in text and "rpc.invalid" not in text and "Authorization" not in text
    assert json.loads(text)["status"] == "pending"


async def test_dry_run_lowest_signing_and_broadcast_gates(credentials):
    creds = credentials.model_copy(update={"dry_run": True})
    signer = LocalSigner(creds)
    with pytest.raises(SwapError):
        signer.sign({}, "0x" + "a" * 40)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("network must not be called"))
    ) as client:
        rpc = ExecutionRpc(client, SecretStr("https://rpc.invalid"), "1", dry_run=True)
        with pytest.raises(SwapError):
            await rpc.broadcast("0x11", "0x" + "b" * 64)
        with pytest.raises(SwapError):
            await rpc.raw("eth_sendRawTransaction", ["0x11"])


async def test_local_session_confirmation_and_completion_network_boundary(setup, tmp_path):
    c, d, _a, rpc, signer, s = setup
    book = AddressBook(tmp_path / "addresses.json", c.credentials, c.registry, d.chain_map)
    c.credentials = c.credentials.model_copy(
        update={
            "upbit_access_key": SecretStr("fixture-key"),
            "bithumb_access_key": SecretStr("fixture-b"),
        }
    )
    book.credentials = c.credentials
    await book.sync({ex: FakeAddresses() for ex in ("upbit", "bithumb")}, poll_seconds=0)
    app = create_app(c, d, s, address_book=book)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        assert (await client.post("/api/swap/prepare", json=req().model_dump())).status_code == 403
        runtime = (await client.get("/api/runtime")).json()
        headers = {"X-Dexdda-Session": runtime["csrf"]}
        bad = {**headers, "Origin": "https://foreign.invalid"}
        assert (
            await client.post("/api/swap/prepare", headers=bad, json=req().model_dump())
        ).status_code == 403
        r = await client.post("/api/swap/prepare", headers=headers, json=req().model_dump())
        assert r.status_code == 200
        p = r.json()
        assert (
            await client.post(
                "/api/swap/confirm",
                headers=headers,
                json={"intent_id": p["id"], "confirmed": False},
            )
        ).status_code == 422
        assert signer.calls == 0 and not rpc.sent
        assert (
            await client.post(
                "/api/swap/confirm", headers=headers, json={"intent_id": p["id"], "confirmed": True}
            )
        ).status_code == 200
        assert (await settle(s, p["id"]))["status"] == "dry_run_complete"
        assert (await client.get("/api/swap/" + p["id"] + "/completion")).status_code == 409
        # Synthetic completed swap, never execute real funds in a test.
        entry = s.read()["intents"][p["id"]]
        entry.update(status="success", actual_quantity="999")
        entry["transactions"] = [
            {"kind": "swap", "hash": "0x" + "f" * 64, "nonce": 0, "status": "confirmed"}
        ]
        s.save_entry(entry)
        result = (await client.get("/api/swap/" + p["id"] + "/completion")).json()
        assert len(result["addresses"]) == 2 and all(
            x["chain_index"] == "1" for x in result["addresses"]
        )
        data = book.read()
        for row in data["entries"].values():
            row["chain_index"] = "56"
        book.store.write(data)
        result = (await client.get("/api/swap/" + p["id"] + "/completion")).json()
        assert all(x["deposit_address"] is None for x in result["addresses"])


async def test_listing_detection_and_fx_failure_do_not_change_prices(setup, tmp_path):
    c, _, _, _, _, _ = setup
    from app.adapters.prices import Gate

    def handler(request):
        if request.url.host == "www.ecb.europa.eu":
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=[
                {"market": "KRW-COIN", "korean_name": "코인", "english_name": "Coin"},
                {"market": "KRW-NEW", "korean_name": "신규", "english_name": "New"},
            ],
        )

    c.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c.cex_adapters = {ex: SimpleNamespace(gate=Gate(0)) for ex in ("upbit", "bithumb")}
    c.registry.source_markets = {ex: ["KRW-COIN"] for ex in ("upbit", "bithumb")}
    service = Operations(c, log=EventLog(tmp_path / "events.jsonl"))
    await service.listings()
    await service.reference_fx()
    snapshot = service.snapshot()
    assert all(row["new"] == ["KRW-NEW"] for row in snapshot["markets"].values())
    assert snapshot["reference_fx"] is None and snapshot["fx_error"]
    assert snapshot["does_not_affect_gap"] is True
    await c.client.aclose()
