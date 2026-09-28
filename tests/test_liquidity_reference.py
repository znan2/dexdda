"""Per-token diagnostics and last normal observations used only for discovery."""

from decimal import Decimal

import httpx
import pytest
from test_pricing import coin, make_collector, seed, token

from app.adapters.base import AdapterError, Failure
from app.adapters.prices import OkxPrices, Price, PriceBatch


@pytest.mark.parametrize(
    "reason",
    [
        "rate_limited",
        "timeout",
        "token_not_returned",
        "value_missing",
        "value_invalid",
        "source_time_invalid",
    ],
)
def test_failure_preserves_observation_and_filter_basis(credentials, reason):
    c = make_collector(credentials)
    seed(c, liquidity="49999")
    key = next(iter(c.active_keys))
    original = c.liquidity_reference(key)
    c.clock.advance(120)
    c.liquidity.fail([key], reason)
    q = c.liquidity_reference(key)
    assert q["usable"] and q["reused"] and q["stale"]
    assert q["value"] == "49999" and q["age_seconds"] == 120
    assert q["source_at_ms"] == original["source_at_ms"]
    assert q["received_at_ms"] == original["received_at_ms"]
    assert q["last_attempt_at_ms"] == c.clock.milliseconds()
    assert q["error"] == reason and q["error_message"]
    row = c.snapshot()["coin_groups"][0]["routes"][0]
    assert row["low_liquidity"] and "liquidity_cached" in row["warnings"]
    assert c.snapshot()["diagnostics"]["liquidity"]["reused"] == 1
    # Unchanged spot-price freshness still prevents a stale price gap.
    assert row["exchanges"]["upbit"]["gap"] is None
    c.clock.advance(1)
    c.liquidity.put(key, Price(Decimal(0), c.clock.milliseconds()), "okx")
    q = c.liquidity_reference(key)
    assert q["value"] == "0" and q["usable"]
    assert not q["reused"] and q["error"] is None
    assert q["received_at_ms"] > original["received_at_ms"]


async def test_expired_main_pool_keeps_priority_and_price_poll(credentials):
    c = make_collector(credentials, [coin([token(), token("56", direct=False)])])
    seed(c, liquidity="1000000")
    bsc = ("56", token("56").address)
    c.liquidity.put(bsc, Price(Decimal(9000000), c.clock.milliseconds()), "okx")
    c.clock.advance(86400)
    group = c.snapshot()["coin_groups"][0]
    assert group["routes"][0]["chain_index"] == "56"
    assert group["liquidity_reused"] and group["liquidity_comparison_complete"]
    assert group["routes"][0]["liquidity_usd"]["error"] == "stale"
    calls = []

    async def poll(keys, **_):
        calls.extend(keys)

    c.poll_okx = poll
    await c.poll_prices()
    assert bsc in calls


def test_first_failure_never_fabricates_a_value(credentials):
    c = make_collector(credentials)
    key = next(iter(c.active_keys))
    c.liquidity.fail([key], "token_not_returned")
    q = c.liquidity_reference(key)
    assert not q["usable"] and not q["reused"]
    assert q["value"] is None and q["received_at_ms"] is None
    assert q["age_seconds"] is None and q["error"] == "token_not_returned"


async def test_batch_distinguishes_missing_token_value_and_time(credentials):
    keys = [(str(i), token(str(i)).address) for i in range(1, 9)]
    rows = [
        {
            "chainIndex": k[0],
            "tokenContractAddress": k[1],
            "liquidity": "10",
            "time": "1790000000000",
        }
        for k in keys[:-1]
    ]
    rows[1].pop("liquidity")
    rows[2]["liquidity"] = "-1"
    rows[3].pop("time")
    rows[4]["time"] = "bad"
    rows[5]["liquidity"] = "0"
    rows[6]["time"] = "1890000000000"  # Valid format, future clock rejected by Quotes.
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": "0", "data": rows})
        )
    ) as client:
        result = await OkxPrices(client, credentials, 0).fetch(keys, liquidity=True)
    assert set(result) == {keys[0], keys[5], keys[6]}
    assert result.errors == {
        keys[1]: "value_missing",
        keys[2]: "value_invalid",
        keys[3]: "source_time_missing",
        keys[4]: "source_time_invalid",
        keys[7]: "token_not_returned",
    }
    c = make_collector(credentials, [coin([token(str(i)) for i in range(1, 9)])])
    seed(c)

    class Adapter:
        async def fetch(self, *_args, **_kwargs):
            return result

    c.okx = Adapter()
    await c.poll_liquidity()
    for key, reason in result.errors.items():
        assert c.liquidity_reference(key)["error"] == reason
        assert c.liquidity_reference(key)["reused"]
    assert c.liquidity_reference(keys[6])["error"] == "timestamp_invalid"
    assert c.liquidity_reference(keys[6])["value"] == "100000"
    assert c.stats["okx_liquidity"]["failures"] == 0


async def test_request_failure_distinct_from_partial_success(credentials):
    c = make_collector(credentials)
    seed(c)

    class Adapter:
        async def fetch(self, *_args, **_kwargs):
            raise AdapterError(Failure.LIMIT)

    c.okx = Adapter()
    await c.poll_liquidity()
    key = next(iter(c.active_keys))
    assert c.stats["okx_liquidity"]["failures"] == 1
    assert c.liquidity_reference(key)["error"] == "rate_limited"

    class Recovered:
        async def fetch(self, *_args, **_kwargs):
            result = PriceBatch()
            result[key] = Price(Decimal(1200000), c.clock.milliseconds())
            return result

    c.okx = Recovered()
    await c.poll_liquidity()
    assert c.liquidity_reference(key)["error"] is None
    assert not c.liquidity_reference(key)["reused"]


def test_cached_recognized_token_is_not_hidden_as_unrecognized(credentials):
    from app.config import Settings

    c = make_collector(credentials, settings=Settings(filters={"unrecognized_action": "hide"}))
    seed(c)
    c.liquidity.fail(c.active_keys, "timeout")
    data = c.snapshot()
    assert len(data["main"]) == 1
    assert "liquidity_cached" in data["main"][0]["warnings"]
