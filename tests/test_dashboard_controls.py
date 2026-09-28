"""Durable exclusions, exact network matching and shared wallet refresh."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from test_pricing import coin, make_collector, seed, token

from app.adapters.prices import Gate
from app.config import Settings
from app.detail.models import DetailRequest
from app.detail.service import DetailService, DetailUnavailable
from app.main import create_app
from app.preferences import Change, Preferences
from app.pricing.collector import Collector
from app.private_store import StoreError


def wallet(state="working", net="ETH"):
    return {"currency": "COIN", "net_type": net, "network_name": net, "wallet_state": state}


def test_registry_normal_is_not_live_confirmation(credentials):
    c = make_collector(credentials)
    seed(c)
    q = c.snapshot()["coin_groups"][0]["routes"][0]["exchanges"]["upbit"]
    assert q["deposit_possible"] is None
    assert q["deposit_networks"][0]["wallet_state"] is None
    assert c.registry.coins[0].exchanges["upbit"].networks[0]["wallet_state"] == "working"


def test_wallet_state_network_scope_failure_expiry_and_recovery(credentials):
    c = make_collector(credentials)
    seed(c)

    def quote():
        return c.snapshot()["coin_groups"][0]["routes"][0]["exchanges"]["upbit"]

    c.wallet_status.observe("upbit", [wallet("paused")], c.clock.monotonic())
    assert quote()["deposit_possible"] is False
    c.clock.advance(1)
    c.wallet_status.observe("upbit", [wallet("deposit_only")], c.clock.monotonic())
    assert quote()["deposit_possible"] is True  # Withdrawals are irrelevant to selling here.
    c.clock.advance(91)
    assert quote()["deposit_possible"] is None
    assert quote()["deposit_networks"][0]["wallet_state"] is None
    c.wallet_status.observe("upbit", [wallet()], c.clock.monotonic())
    c.clock.advance(1)
    c.wallet_status.observe("upbit", None, c.clock.monotonic(), "timeout")
    assert quote()["deposit_possible"] is None
    assert quote()["deposit_networks"][0]["previous_wallet_state"] == "working"
    c.wallet_status.observe("upbit", [wallet("working", "OTHER")], c.clock.monotonic())
    assert quote()["deposit_possible"] is None
    assert quote()["deposit_networks"][0]["error"] == "network_not_returned"
    c.clock.advance(1)
    c.wallet_status.observe("upbit", [wallet("withdraw_only")], c.clock.monotonic())
    c.wallet_status.observe("upbit", [wallet()], c.clock.monotonic() - 1)
    assert quote()["deposit_possible"] is False  # Older in-flight responses cannot restore green.


def test_multi_network_uses_buy_chain_and_override_wins(credentials):
    entry = coin([token()])
    ex = entry.exchanges["upbit"]
    entry.exchanges["upbit"] = ex.model_copy(
        update={
            "net_types": ["ETH", "ARBITRUM"],
            "networks": [wallet(net="ETH"), wallet(net="ARBITRUM")],
        }
    )
    c = make_collector(credentials, [entry])
    seed(c)
    c.wallet_status.observe(
        "upbit", [wallet("paused"), wallet(net="ARBITRUM")], c.clock.monotonic()
    )
    q = c.snapshot()["coin_groups"][0]["routes"][0]["exchanges"]["upbit"]
    assert q["deposit_possible"] is False
    c.preferences.change(
        Change(kind="network", coin_id="coin", exchange="upbit", net_type="ETH", excluded=True)
    )
    c.wallet_status.observe("upbit", [wallet(), wallet(net="ARBITRUM")], c.clock.monotonic())
    q = c.snapshot()["coin_groups"][0]["routes"][0]["exchanges"]["upbit"]
    assert q["manual_excluded"] and q["deposit_possible"] is False
    assert not c.snapshot()["coin_groups"][0]["routes"][0]["exchanges"]["bithumb"][
        "manual_excluded"
    ]


async def test_shared_refresh_cooldown_and_exchange_failure_isolation(credentials):
    c = make_collector(credentials)
    calls = []

    async def handler(request):
        calls.append(request.url.host)
        await asyncio.sleep(0.01)
        return (
            httpx.Response(429)
            if request.url.host == "api.upbit.com"
            else httpx.Response(200, json=[wallet("paused")])
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        c.client = client
        c.cex_adapters = {ex: SimpleNamespace(gate=Gate(0)) for ex in c.markets}
        await asyncio.gather(
            c.wallet_status.refresh(), c.wallet_status.refresh(), c.wallet_status.refresh()
        )
        assert len(calls) == 2
        await c.wallet_status.refresh()
        assert len(calls) == 2
        assert c.wallet_status.status("upbit")["error"] == "rate_limited"
        assert not c.wallet_status.status("bithumb")["stale"]
        c.clock.advance(30)
        await c.wallet_status.refresh()
        assert len(calls) == 4
    assert Settings().pricing.wallet_poll_seconds == 30


def test_preferences_persist_exact_identity_and_failed_save_keeps_old(
    tmp_path, credentials, monkeypatch
):
    first = coin([token()], "first")
    second = coin([token("56")], "second").model_copy(update={"symbol": first.symbol})
    registry = SimpleNamespace(coins=[first, second])
    path = tmp_path / "preferences.json"
    p = Preferences(registry, path)
    p.change(Change(kind="coin", coin_id="first", excluded=True, reason="브릿지 없음"))
    restored = Preferences(registry, path)
    assert restored.blocked("first") and not restored.blocked("second")
    assert restored.public()["exclusions"][0]["reason"] == "브릿지 없음"
    monkeypatch.setattr(restored.store, "write", lambda _: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        restored.change(Change(kind="coin", coin_id="first", excluded=False))
    assert restored.blocked("first")
    assert Preferences(registry, path).blocked("first")
    p.change(Change(kind="coin", coin_id="first", excluded=False))
    assert not Preferences(registry, path).blocked("first")
    path.write_text("not json")
    with pytest.raises(StoreError):
        Preferences(registry, path)


async def test_exclusion_api_csrf_validation_and_hidden_coin_guard(tmp_path, credentials):
    c = Collector(
        SimpleNamespace(coins=[coin([token()])]),
        Settings(),
        credentials,
        preferences_path=tmp_path / "prefs.json",
    )
    seed(c)
    app = create_app(c, enable_execution=False, enable_operations=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        change = {"kind": "coin", "coin_id": "coin", "excluded": True}
        assert (await client.post("/api/preferences", json=change)).status_code == 403
        csrf = (await client.get("/api/runtime")).json()["csrf"]
        headers = {"X-Dexdda-Session": csrf}
        assert (
            await client.post(
                "/api/preferences",
                json=change,
                headers={**headers, "Origin": "https://foreign.invalid"},
            )
        ).status_code == 403
        assert (
            await client.post(
                "/api/preferences", json={**change, "coin_id": "unknown"}, headers=headers
            )
        ).status_code == 422
        assert (
            await client.post("/api/preferences", json=change, headers=headers)
        ).status_code == 200
        assert not (await client.get("/api/gaps")).json()["coin_groups"]
        assert (await client.get("/api/preferences")).json()["catalog"][0]["coin_id"] == "coin"
        detail = DetailService(c)
        req = DetailRequest(
            coin_id="coin",
            chain_index="1",
            address=token().address,
            exchange="upbit",
            amount_usdt="100",
        )
        with pytest.raises(DetailUnavailable, match="coin_blacklisted"):
            detail.select(req)
        assert (
            await client.post(
                "/api/preferences", json={**change, "excluded": False}, headers=headers
            )
        ).status_code == 200
        assert (
            await client.post(
                "/api/preferences",
                json={**change, "kind": "network", "exchange": "upbit", "net_type": "ETH"},
                headers=headers,
            )
        ).status_code == 200
        with pytest.raises(DetailUnavailable, match="network_manually_excluded"):
            detail.select(req)
        detail.select(req.model_copy(update={"exchange": "bithumb"}))
        assert (await client.post("/api/wallet-status/refresh")).status_code == 403
