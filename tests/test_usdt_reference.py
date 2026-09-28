from decimal import Decimal

import pytest
from detail_fixtures import attach_detail
from test_detail import request
from test_pricing import make_collector, seed

from app.adapters.base import AdapterError, Failure
from app.adapters.prices import Price
from app.swap.models import SwapError
from app.swap.service import SwapService


def test_old_fx_reused_but_stale_token_prices_still_block(credentials):
    c = make_collector(credentials)
    seed(c, upbit="1430")
    old = c.fx.values["usdt"].price
    c.clock.advance(3600)
    seed(c, upbit="1430")
    c.fx.values["usdt"] = c.fx.values["usdt"].__class__(
        old, c.clock.milliseconds() - 3600000, c.clock.monotonic() - 3600, "old_fixture"
    )
    d = c.snapshot()
    assert d["usdt_krw_ask"]["usable"] and d["usdt_krw_ask"]["reused"]
    assert Decimal(d["main"][0]["exchanges"]["upbit"]["gap_percent"]) == 10
    c.clock.advance(16)
    assert c.snapshot()["main"][0]["exchanges"]["upbit"]["gap_percent"] is None


def test_fx_freshness_is_separate_and_recovery_recalculates(credentials):
    c = make_collector(credentials)
    seed(c)
    c.clock.advance(20)
    assert c.fx_reference()["usable"] and not c.fx_reference()["reused"]
    c.fx.fail(["usdt"], "network_error")
    assert c.fx_reference()["reused"]
    c.fx.put("usdt", Price(Decimal(1400), c.clock.milliseconds()), "recovered")
    assert not c.fx_reference()["reused"] and c.fx_reference()["value"] == "1400"


@pytest.mark.parametrize("cached", [True, False])
async def test_detail_fx_failure_uses_cache_only_when_available_and_execution_stays_strict(
    credentials, tmp_path, cached
):
    c = make_collector(credentials)
    if cached:
        seed(c)
    detail, adapter, _ = attach_detail(c)
    original = adapter.book

    async def book(exchange, market):
        if market == "KRW-USDT":
            raise AdapterError(Failure.NETWORK)
        return await original(exchange, market)

    adapter.book = book
    try:
        result = await detail.calculate(request())
        assert (result["result"] is not None) == cached
        if cached:
            assert result["fx_krw"] == "1300" and result["fx_reference"]["reused"]
            assert "fx_cached" in {w["code"] for w in result["warnings"]}
            service = SwapService(c, detail, path=tmp_path / "swaps.json", contracts={})
            with pytest.raises(SwapError):
                service.check_detail(result)
        else:
            assert result["fx_krw"] is None
    finally:
        await c.client.aclose()
