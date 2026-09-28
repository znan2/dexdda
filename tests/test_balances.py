import asyncio
import json
from decimal import Decimal

import httpx
from test_pricing import make_collector

from app.adapters.base import AdapterError, Failure
from app.balances import WalletBalances
from app.config import Credentials
from app.main import create_app


def make():
    c = make_collector(Credentials(wallet_address="0x" + "a" * 40))
    calls = []
    state = {"fail": None, "bad_decimals": None, "chain_fail": None}
    assets = c.balances.assets

    class Rpc:
        def __init__(self, chain):
            self.chain = chain

        async def check_chain(self):
            calls.append((self.chain, "chain"))
            if self.chain == state["chain_fail"]:
                raise AdapterError(Failure.CHAIN)

        async def integer(self, method, params):
            assert method == "eth_call"
            addr, data = params[0]["to"], params[0]["data"]
            a = next(a for a in assets if a.chain_index == self.chain and a.address == addr)
            calls.append((self.chain, a.symbol, data[:10]))
            if data == "0x313ce567":
                return 1 if a.address == state["bad_decimals"] else a.decimals
            assert data == "0x70a08231" + c.credentials.wallet_address.get_secret_value()[2:].zfill(
                64
            )
            if a.address == state["fail"]:
                raise RuntimeError("sentinel-secret-rpc-url")
            return 0 if a.family == "USDC" else 123456789 * 10 ** (a.decimals - 6)

    service = WalletBalances(c, rpc_factory=Rpc, public_rpcs={})
    c.balances = service
    return c, service, calls, state


def tokens(service):
    return [t for chain in service.snapshot()["chains"] for t in chain["tokens"]]


async def test_balances_five_chains_decimals_zero_and_cooldown():
    c, s, calls, _ = make()
    await s.refresh()
    assert len(s.snapshot()["chains"]) == 5
    rows = tokens(s)
    assert len(rows) == 9
    assert all(
        Decimal(t["value"]) == (0 if t["symbol"] == "USDC" else Decimal("123.456789")) for t in rows
    )
    assert all(not t["stale"] and not t["error"] for t in rows)
    robinhood = s.snapshot()["chains"][1]
    assert [t["symbol"] for t in robinhood["tokens"]] == ["USDG"]
    count = len(calls)
    await asyncio.gather(s.refresh(), s.refresh())
    assert len(calls) == count
    c.clock.advance(60)
    await asyncio.gather(s.refresh(), s.refresh())
    assert len(calls) - count == 14  # Five chain checks + nine balances, metadata cached.
    assert "0x" + "a" * 40 not in json.dumps(s.snapshot())


async def test_partial_failure_retains_balance_age_and_recovers():
    c, s, _, state = make()
    await s.refresh()
    before = tokens(s)[0]
    state["fail"] = before["address"]
    c.clock.advance(180)
    await s.refresh()
    failed = tokens(s)[0]
    assert failed["value"] == before["value"]
    assert failed["observed_at_ms"] == before["observed_at_ms"]
    assert failed["stale"] and failed["error"] == "rpc_failed"
    assert sum(bool(t["error"]) for t in tokens(s)) == 1
    assert "sentinel" not in json.dumps(s.snapshot())
    state["fail"] = None
    c.clock.advance(60)
    await s.refresh()
    assert not tokens(s)[0]["stale"]
    assert tokens(s)[0]["observed_at_ms"] > before["observed_at_ms"]


async def test_rpc_chain_or_decimals_failure_is_unknown_not_zero():
    _, s, _, state = make()
    state["chain_fail"] = "4663"
    state["bad_decimals"] = s.selected("1")[0].address
    await s.refresh()
    failed = [t for t in tokens(s) if t["error"]]
    assert len(failed) == 2
    assert all(t["value"] is None for t in failed)
    assert {t["error"] for t in failed} == {"unexpected_response", "chain_mismatch"}


async def test_missing_config_and_changed_wallet_never_show_previous_wallet():
    c, s, _, _ = make()
    await s.refresh()
    c.credentials = Credentials()
    c.clock.advance(60)
    await s.refresh()
    assert all(t["value"] is None and t["error"] == "wallet_missing" for t in tokens(s))
    c.credentials = Credentials(wallet_address="0x" + "b" * 40)
    s.rpc_factory = None
    c.clock.advance(60)
    await s.refresh()
    assert all(t["value"] is None and t["error"] == "rpc_missing" for t in tokens(s))


async def test_balance_get_is_cached_refresh_is_session_protected():
    c, _, calls, _ = make()
    c.running = True
    app = create_app(c)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        assert (await client.get("/api/balances")).status_code == 200
        assert calls == []
        assert (await client.post("/api/balances/refresh")).status_code == 403
        csrf = (await client.get("/api/runtime")).json()["csrf"]
        headers = {"X-Dexdda-Session": csrf}
        assert (
            await client.post(
                "/api/balances/refresh", headers={**headers, "Origin": "https://untrusted.invalid"}
            )
        ).status_code == 403
        response = await client.post("/api/balances/refresh", headers=headers)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        count = len(calls)
        await client.get("/api/balances")
        assert len(calls) == count


async def test_robinhood_uses_public_rpc_when_no_configured_rpc(monkeypatch):
    from app import balances

    c, _, _, _ = make()
    seen = []

    class PublicRpc:
        def __init__(self, client, url, chain):
            seen.append((chain, url.get_secret_value()))

        async def check_chain(self):
            pass

        async def integer(self, method, params):
            return 6 if params[0]["data"] == "0x313ce567" else 1000000

    monkeypatch.setattr(balances, "ReadRpc", PublicRpc)
    service = WalletBalances(c)
    await service.refresh()
    assert seen == [("4663", "https://rpc.mainnet.chain.robinhood.com")]
    assert service.snapshot()["chains"][1]["tokens"][0]["value"] == "1"
    assert "https://" not in json.dumps(service.snapshot())
