"""Liquidity representatives and reference-only deposit-blocked quotes."""

from decimal import Decimal

import pytest
from detail_fixtures import attach_detail
from test_detail import request
from test_pricing import coin, make_collector, seed, token

from app.adapters.prices import Price
from app.swap.models import SwapError
from app.swap.service import SwapService


def test_representative_routes_sort_by_liquidity_not_gap_and_preserve_low_routes(credentials):
    c = make_collector(credentials, [coin([token(), token("56", direct=False)], "cap")])
    seed(c)
    c.liquidity.put(("1", token().address), Price(Decimal(4711), c.clock.milliseconds()), "fixture")
    c.liquidity.put(
        ("56", token("56").address), Price(Decimal(1080000), c.clock.milliseconds()), "fixture"
    )
    c.dex.put(("1", token().address), Price(Decimal(".1"), c.clock.milliseconds()), "fixture")
    group = c.snapshot()["coin_groups"][0]
    assert [r["chain_index"] for r in group["routes"]] == ["56", "1"]
    assert group["routes"][1]["low_liquidity"] and group["routes"][1]["suspected"]
    assert not group["routes"][0]["low_liquidity"]
    assert group["routes"][0]["exchanges"]["upbit"]["route_status"] == "bridge_candidate"
    assert group["routes"][0]["exchanges"]["upbit"]["gap_percent"] is not None
    assert c.settings.filters.hide_low_liquidity
    assert group["liquidity_comparison_complete"]


async def test_unknown_alternative_does_not_prevent_known_representative_price_poll(credentials):
    c = make_collector(credentials, [coin([token("56", direct=False), token("99", direct=False)])])
    seed(c)
    c.liquidity.values.pop(("99", token("99").address))
    c.liquidity.fail([("99", token("99").address)], "missing")
    calls = []

    async def poll(keys, **_):
        calls.extend(keys)

    c.poll_okx = poll
    await c.poll_prices()
    assert ("56", token("56").address) in calls
    group = c.snapshot()["coin_groups"][0]
    assert not group["liquidity_comparison_complete"]
    assert group["routes"][0]["chain_index"] == "56"


def test_mixed_exchange_routes_keep_comparison_gaps_separate(credentials):
    t = token().model_copy(update={"status": {"upbit": "tradable", "bithumb": "bridge_candidate"}})
    c = make_collector(credentials, [coin([t])])
    seed(c)
    row = c.snapshot()["coin_groups"][0]["routes"][0]
    assert row["exchanges"]["bithumb"]["gap_percent"] == "0"
    assert row["exchanges"]["bithumb"]["route_status"] == "bridge_candidate"
    assert not row["execution_enabled"]


@pytest.mark.parametrize(
    "failure", ["stopped", "unknown", "minimum", "gas", "depth", "tax", "bridge"]
)
async def test_reference_requires_complete_costs_and_never_enables_execution(
    credentials, tmp_path, failure
):
    c = make_collector(credentials)
    detail, adapter, _ = attach_detail(c)
    if failure == "stopped":
        c.registry.coins[0].exchanges["upbit"].networks[0]["wallet_state"] = "withdraw_only"
    elif failure == "unknown":

        async def unavailable(_):
            return []

        adapter.wallets = unavailable
    elif failure == "minimum":
        adapter.minimum = Decimal(1001)
    elif failure == "gas":
        detail.assets["1"] = detail.assets["1"].model_copy(update={"simple_gas": False})
    elif failure == "depth":
        adapter.depth = Decimal(1)
    elif failure == "tax":
        adapter.tax = Decimal(".01")
    elif failure == "bridge":
        c.registry.coins[0].tokens[0].status["upbit"] = "bridge_candidate"
    try:
        result = await detail.calculate(request())
        assert result["result"] is None
        if failure in ("stopped", "unknown", "minimum"):
            assert result["reference_result"] and result["reference_basis"]
        else:
            assert result["reference_result"] is None
        service = SwapService(c, detail, path=tmp_path / "swaps.json", contracts={})
        with pytest.raises(SwapError):
            service.check_detail(result)
        assert not result["execution_enabled"]
    finally:
        await c.client.aclose()


def test_liquidity_threshold_boundary(credentials):
    c = make_collector(credentials)
    seed(c, liquidity="50000")
    assert not c.snapshot()["coin_groups"][0]["routes"][0]["low_liquidity"]
    seed(c, liquidity="49999.99")
    assert c.snapshot()["coin_groups"][0]["routes"][0]["low_liquidity"]


async def test_expired_deposit_blocked_reference_is_not_reusable(credentials):
    c = make_collector(credentials)
    detail, adapter, _ = attach_detail(c)
    c.registry.coins[0].exchanges["upbit"].networks[0]["wallet_state"] = "withdraw_only"
    adapter.age = 100000
    try:
        result = await detail.calculate(request())
        assert result["result"] is None and result["reference_result"] is None
        assert {w["code"] for w in result["warnings"]} >= {"stale", "deposit_unavailable"}
    finally:
        await c.client.aclose()


@pytest.mark.parametrize("ahead_ms,available", [(3000, True), (6000, False)])
async def test_provider_clock_skew_consistent_and_does_not_extend_quote(
    credentials, ahead_ms, available
):
    import time

    c = make_collector(credentials)
    detail, _, _ = attach_detail(c)
    c.registry.coins[0].exchanges["upbit"].networks[0]["wallet_state"] = "withdraw_only"

    async def prices(keys):
        return {key: Price(Decimal(2000), int(time.time() * 1000) + ahead_ms) for key in keys}

    c.okx.fetch = prices
    try:
        result = await detail.calculate(request())
        assert (result["reference_result"] is not None) == available
        assert result["expires_at_ms"] - result["generated_at_ms"] <= 15000
        if not available:
            assert "source_timestamp_invalid" in {w["code"] for w in result["warnings"]}
    finally:
        await c.client.aclose()
