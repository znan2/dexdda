import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.adapters.base import AdapterError, Failure
from app.adapters.prices import CexPrices, OkxPrices, Price, decode_ws, number
from app.config import Settings
from app.main import create_app
from app.pricing.collector import Collector
from app.pricing.gap import surface_gap
from app.pricing.state import Quotes
from app.registry.models import ExchangeListing, RegistryCoin, Token


class FakeClock:
    now = 1000.0
    epoch = 1_790_000_000_000

    def monotonic(self):
        return self.now

    def milliseconds(self):
        return self.epoch + int((self.now - 1000) * 1000)

    def advance(self, seconds):
        self.now += seconds


def token(chain="1", *, direct=True, recognized=True):
    return Token(
        chain_index=chain,
        address="0x" + chain.zfill(40),
        decimals=18,
        name="Coin",
        symbol="COIN",
        community_recognized=recognized,
        native=False,
        source="coingecko",
        status={ex: "tradable" if direct else "bridge_candidate" for ex in ("upbit", "bithumb")},
    )


def coin(tokens, name="coin"):
    return RegistryCoin(
        coin_id=name,
        symbol=name.upper(),
        names={"coingecko": name},
        exchanges={
            ex: ExchangeListing(
                symbol=name.upper(),
                markets=["KRW-" + name.upper()],
                net_types=["ETH"],
                networks=[{"net_type": "ETH", "wallet_state": "working"}],
            )
            for ex in ("upbit", "bithumb")
        },
        tokens=tokens,
        source="coingecko",
    )


def make_collector(credentials, coins=None, settings=None):
    return Collector(
        SimpleNamespace(coins=coins or [coin([token()])]),
        settings or Settings(),
        credentials,
        clock=FakeClock(),
    )


def seed(collector, *, dex="1", upbit="1300", bithumb="1300", fx="1300", liquidity="100000"):
    price = lambda value: Price(Decimal(value), collector.clock.milliseconds())
    collector.fx.put("usdt", price(fx), "upbit_ws_orderbook")
    for key in collector.active_keys:
        collector.dex.put(key, price(dex), "okx_price")
        collector.liquidity.put(key, price(liquidity), "okx_liquidity")
    for exchange, markets in collector.markets.items():
        for market in markets:
            collector.cex.put(
                (exchange, market),
                price(upbit if exchange == "upbit" else bithumb),
                exchange + "_ws",
            )


def test_one_percent_kimchi_premium_cancels_without_cex_fees():
    fiat_rate = Decimal(1300)
    usdt_ask = fiat_rate * Decimal("1.01")
    coin_krw = Decimal(2) * fiat_rate * Decimal("1.01")
    assert surface_gap(coin_krw, Decimal(2), usdt_ask) == 0


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "0", True, None, "1e100000"])
def test_price_numbers_reject_invalid(bad):
    with pytest.raises(AdapterError):
        number(bad)


def test_independent_exchange_gaps_stale_and_common_ask(credentials):
    collector = make_collector(credentials)
    seed(collector, upbit="1430", bithumb="1170")
    row = collector.snapshot()["main"][0]
    assert Decimal(row["exchanges"]["upbit"]["gap_percent"]) == 10
    assert Decimal(row["exchanges"]["bithumb"]["gap_percent"]) == -10
    collector.cex.fail([("bithumb", "KRW-COIN")], "network_error")
    row = collector.snapshot()["main"][0]
    assert row["exchanges"]["upbit"]["gap"] is not None
    assert row["exchanges"]["bithumb"]["gap"] is None
    collector.clock.advance(16)
    row = collector.snapshot()["main"][0]
    assert row["stale"]
    assert all(q["gap"] is None for q in row["exchanges"].values())


def test_fx_failure_reuses_last_value_but_first_observation_is_required(credentials):
    collector = make_collector(credentials)
    seed(collector)
    collector.fx.fail(["usdt"], "missing_response")
    data = collector.snapshot()
    assert data["usdt_krw_ask"]["reused"]
    assert all(q["gap"] == "0" for q in data["main"][0]["exchanges"].values())
    collector.fx.values.clear()
    row = collector.snapshot()["main"][0]
    assert all(q["gap"] is None for q in row["exchanges"].values())
    assert all("usdt_ask_missing" in q["unavailable_reasons"] for q in row["exchanges"].values())


def test_bridge_maximum_one_chain_including_coin_with_direct_route(credentials):
    collector = make_collector(
        credentials,
        [
            coin(
                [
                    token(),
                    token("2", direct=False),
                    token("3", direct=False),
                ]
            )
        ],
    )
    seed(collector)
    collector.liquidity.put(
        ("3", token("3").address), Price(Decimal(200000), collector.clock.milliseconds()), "okx"
    )
    data = collector.snapshot()
    assert len(data["main"]) == 1
    assert len(data["bridge_candidates"]) == 1
    bridge = data["bridge_candidates"][0]
    assert bridge["chain_index"] == "3"
    assert bridge["execution_enabled"] is False
    assert "bridge_required_view_only" in bridge["warnings"]
    collector.clock.advance(901)
    data = collector.snapshot()
    assert data["bridge_candidates"][0]["chain_index"] == "3"
    assert data["bridge_candidates"][0]["liquidity_usd"]["reused"]
    assert data["diagnostics"]["selected_bridge_coins"] == 1


def test_bridge_unknown_liquidity_is_pending_then_deterministic_tie(credentials):
    collector = make_collector(
        credentials,
        [
            coin(
                [
                    token("3", direct=False),
                    token("2", direct=False),
                ]
            )
        ],
    )
    assert collector.snapshot()["bridge_candidates"][0]["chain_index"] is None
    seed(collector)
    collector.liquidity.fail([("3", token("3").address)], "missing_response")
    assert collector.snapshot()["bridge_candidates"][0]["chain_index"] == "2"
    seed(collector)
    assert collector.snapshot()["bridge_candidates"][0]["chain_index"] == "2"


def test_filters_abs_threshold_warn_hide_and_unknown(credentials):
    collector = make_collector(credentials, [coin([token(recognized=False)])])
    seed(collector, liquidity="1")
    row = collector.snapshot()["main"][0]
    assert {"low_liquidity", "community_unrecognized"} <= set(row["warnings"])
    hidden_settings = Settings(filters={"unrecognized_action": "hide"})
    collector.settings = hidden_settings
    assert collector.snapshot()["counts"]["hidden"] == 1
    for upbit in ("1700", "800"):
        seed(collector, upbit=upbit)
        data = collector.snapshot()
        assert len(data["suspected"]) == 1
        assert not data["main"]
    seed(collector, upbit="1690", liquidity="100000")
    collector.settings = Settings()
    assert not collector.snapshot()["suspected"]  # Exactly +30% remains below exclusion.
    collector.liquidity.fail(list(collector.active_keys), "missing_response")
    assert "liquidity_cached" in collector.snapshot()["main"][0]["warnings"]


def test_direct_row_does_not_rank_indirect_exchange(credentials):
    t = token().model_copy(update={"status": {"upbit": "tradable", "bithumb": "bridge_candidate"}})
    collector = make_collector(credentials, [coin([t])])
    seed(collector, bithumb="999999")
    data = collector.snapshot()
    assert not data["suspected"]
    assert data["main"][0]["exchanges"]["bithumb"]["gap"] is None
    assert data["main"][0]["exchanges"]["upbit"]["gap"] == "0"


def test_quote_replay_future_and_rest_ws_race():
    clock = FakeClock()
    quotes = Quotes(clock)
    p = Price(Decimal("1.001"), clock.milliseconds())
    quotes.put("x", p, "ws")
    started = clock.monotonic()
    clock.advance(1)
    quotes.put("x", Price(Decimal("1.002"), clock.milliseconds()), "ws")
    quotes.fail(["x"], "http_error", started_at=started)
    assert quotes.fresh("x", 15)
    assert not quotes.put("x", p, "rest")
    clock.advance(1)
    assert not quotes.put("x", Price(Decimal(1), clock.milliseconds() + 6000), "ws")
    assert not quotes.fresh("x", 15)


def test_repeated_old_provider_time_cannot_keep_quote_fresh():
    clock = FakeClock()
    quotes = Quotes(clock)
    price = Price(Decimal(1), clock.milliseconds())
    quotes.put("x", price, "rest")
    clock.advance(16)
    quotes.put("x", price, "rest")
    assert not quotes.fresh("x", 15)
    assert quotes.fresh("x", 900, source_age=False)  # Liquidity uses query receipt age.


async def test_okx_partial_response_exact_signed_body_and_liquidity(credentials):
    keys = [("1", token().address), ("2", token("2").address)]
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert json.loads(request.content) == [
            {"chainIndex": c, "tokenContractAddress": a} for c, a in keys
        ]
        assert "ok-access-sign" in request.headers
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": [
                    {
                        "chainIndex": "1",
                        "tokenContractAddress": keys[0][1],
                        "price": "1.234567890123456789",
                        "liquidity": "0",
                        "time": "1790000000000",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OkxPrices(client, credentials, 0)
        result = await adapter.fetch(keys)
        assert result[keys[0]].value == Decimal("1.234567890123456789")
        assert keys[1] not in result
        assert (await adapter.fetch(keys, liquidity=True))[keys[0]].value == 0
    assert requests[0].url.path.endswith("/price")
    assert requests[1].url.path.endswith("/price-info")


@pytest.mark.parametrize("mode", ["foreign", "duplicate", "raw_error"])
async def test_okx_untrusted_response_does_not_escape(credentials, mode):
    key = ("1", token().address)
    row = {"chainIndex": "1", "tokenContractAddress": key[1], "price": "1", "time": "1790000000000"}
    data = [row, row] if mode == "duplicate" else [{**row, "chainIndex": "2"}]
    payload = (
        {"code": "0", "data": data}
        if mode != "raw_error"
        else {"code": "50113", "msg": "sentinel-secret"}
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(AdapterError) as exc:
            await OkxPrices(client, credentials, 0).fetch([key])
    assert "sentinel" not in str(exc.value)


async def test_cex_rest_decimal_and_upbit_best_ask_only():
    seen = []

    def handler(request):
        seen.append(request)
        assert "authorization" not in request.headers
        if request.url.path.endswith("orderbook"):
            return httpx.Response(
                200,
                json=[
                    {
                        "market": "KRW-USDT",
                        "timestamp": 1790000000000,
                        "orderbook_units": [{"ask_price": 1301, "bid_price": 1299}],
                    }
                ],
            )
        return httpx.Response(
            200,
            content=b'[{"market":"KRW-COIN","trade_price":0.1234567890123456789,"timestamp":1790000000000}]',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for exchange in ("upbit", "bithumb"):
            adapter = CexPrices(client, exchange, 0)
            assert (await adapter.tickers(["KRW-COIN"]))["KRW-COIN"].value == Decimal(
                "0.1234567890123456789"
            )
        assert (await CexPrices(client, "upbit", 0).usdt_ask()).value == 1301
    assert all(r.method == "GET" for r in seen)
    assert str(seen[-1].url).startswith("https://api.upbit.com/")


async def test_partial_batch_missing_and_provider_failure_only_affect_requested_keys(credentials):
    collector = make_collector(credentials, [coin([token(), token("2")])])
    seed(collector)
    collector.settings = Settings(pricing={"okx_batch_size": 1})

    class FakeOkx:
        async def fetch(self, keys, **kwargs):
            if keys[0][0] == "1":
                raise AdapterError(Failure.TIMEOUT)
            return {keys[0]: Price(Decimal(1), collector.clock.milliseconds())}

    collector.okx = FakeOkx()
    await collector.poll_prices()
    assert collector.dex.public(("1", token().address), 15)["stale"]
    assert not collector.dex.public(("2", token("2").address), 15)["stale"]
    assert collector.stats["okx_price"]["failures"] == 1


async def test_price_poll_queries_only_main_plus_selected_bridge(credentials):
    collector = make_collector(
        credentials,
        [
            coin(
                [
                    token(),
                    token("2", direct=False),
                    token("3", direct=False),
                ]
            )
        ],
    )
    seed(collector)
    queried = []

    class FakeOkx:
        async def fetch(self, keys, **kwargs):
            queried.extend(keys)
            return {}

    collector.okx = FakeOkx()
    await collector.poll_prices()
    assert {key[0] for key in queried} == {"1", "2"}
    assert collector.dex.public(("1", token().address), 15)["error"] == "missing_response"


async def test_rest_fallback_only_missing_or_stale_not_healthy_ws(credentials):
    collector = make_collector(credentials)
    seed(collector)
    called = []

    class FakeCex:
        async def tickers(self, markets):
            called.extend(markets)
            return {
                market: Price(Decimal(1310), collector.clock.milliseconds()) for market in markets
            }

        async def usdt_ask(self):
            raise AssertionError("fresh websocket FX must not be refetched")

    collector.cex_adapters = {"upbit": FakeCex()}
    await collector.poll_rest("upbit")
    assert not called
    collector.cex.fail([("upbit", "KRW-COIN")], "network_error")
    await collector.poll_rest("upbit")
    assert called == ["KRW-COIN"]
    assert collector.cex.public(("upbit", "KRW-COIN"), 15)["source"] == "upbit_rest"


async def test_websocket_disconnect_reconnect_and_cancel(credentials):
    collector = make_collector(credentials)
    seed(collector)
    sends, disconnected, reconnected = [], asyncio.Event(), asyncio.Event()

    class Socket:
        async def send(self, message):
            sends.append(json.loads(message))

        def __aiter__(self):
            return self.frames()

        async def frames(self):
            if len(sends) == 1:
                disconnected.set()
                raise OSError("sentinel-secret")
            reconnected.set()
            yield json.dumps(
                {
                    "type": "ticker",
                    "code": "KRW-COIN",
                    "trade_price": 1310,
                    "timestamp": collector.clock.milliseconds(),
                }
            )
            await asyncio.Event().wait()

    class Context:
        async def __aenter__(self):
            return Socket()

        async def __aexit__(self, *args):
            pass

    collector.ws_connect = lambda *args, **kwargs: Context()
    worker = asyncio.create_task(collector.websocket("upbit", ["KRW-COIN"], include_fx=True))
    try:
        await asyncio.wait_for(disconnected.wait(), 1)
        await asyncio.sleep(0)
        assert collector.cex.public(("upbit", "KRW-COIN"), 15)["stale"]
        assert not collector.cex.public(("bithumb", "KRW-COIN"), 15)["stale"]
        await asyncio.wait_for(reconnected.wait(), 2)
        assert collector.cex.fresh(("upbit", "KRW-COIN"), 15)
        assert sends[0][1] == {"type": "ticker", "codes": ["KRW-COIN"]}
        assert sends[0][2] == {"type": "orderbook", "codes": ["KRW-USDT"]}
        assert "sentinel" not in json.dumps(collector.snapshot())
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


def test_websocket_normalization_and_unknown_market(credentials):
    collector = make_collector(credentials)
    collector.ws_message(
        "bithumb",
        ["KRW-COIN"],
        '{"type":"ticker","code":"KRW-COIN","trade_price":0.1234567890123456789,"timestamp":1790000000000}',
    )
    assert collector.cex.values[("bithumb", "KRW-COIN")].price.value == Decimal(
        "0.1234567890123456789"
    )
    with pytest.raises(AdapterError):
        collector.ws_message(
            "bithumb",
            ["KRW-COIN"],
            '{"type":"ticker","code":"KRW-FOREIGN","trade_price":1,"timestamp":1790000000000}',
        )
    with pytest.raises(AdapterError):
        decode_ws('{"error":{"name":"raw-secret","message":"raw-secret"}}')


async def test_api_partition_cache_and_startup_failure_sanitized(credentials):
    collector = make_collector(credentials, [coin([token(), token("2", direct=False)])])
    seed(collector)
    app = create_app(collector)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        response = await client.get("/api/gaps")
        data = response.json()
        assert response.status_code == 200
        assert set(data) >= {"main", "bridge_candidates", "suspected"}
        assert response.headers["cache-control"] == "no-store"
        assert data["execution_enabled"] is False
        assert data["basis"]["cex_fees_included"] is False

    class Broken:
        async def start(self):
            raise RuntimeError("raw-secret")

        async def stop(self):
            pass

    app = create_app(Broken())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        response = await client.get("/api/gaps")
        assert response.status_code == 503
        assert "raw-secret" not in response.text
        assert (await client.get("/api/health")).status_code == 200


async def test_shared_okx_gate_serializes_and_cools_down_without_retry():
    import time

    from app.adapters.prices import Gate

    gate = Gate(0)
    active = 0
    maximum = 0

    async def operation():
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "ok"

    assert await asyncio.gather(*(gate.run(operation) for _ in range(3))) == ["ok"] * 3
    assert maximum == 1

    async def limited():
        raise AdapterError(Failure.LIMIT)

    with pytest.raises(AdapterError):
        await gate.run(limited)
    assert gate.next_at >= time.monotonic() + 1.9


async def test_collector_lifecycle_cancels_ws_and_background_requests(credentials):
    collector = make_collector(credentials)

    def handler(request):
        return httpx.Response(200, json={"code": "0", "data": []})

    closed = []

    class Socket:
        async def send(self, message):
            pass

        def __aiter__(self):
            return self.frames()

        async def frames(self):
            await asyncio.Event().wait()
            yield b"unused"

    class Context:
        async def __aenter__(self):
            return Socket()

        async def __aexit__(self, *args):
            closed.append(True)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        collector.client = client
        collector.owns_client = False
        collector.ws_connect = lambda *args, **kwargs: Context()
        await collector.start()
        await asyncio.sleep(0.02)
        workers = list(collector.tasks)
        assert workers and collector.running
        await collector.stop()
        assert all(worker.done() for worker in workers)
        assert len(closed) == 2
        assert not collector.tasks and not collector.running
        assert not client.is_closed


async def test_rest_failure_preserves_newer_ws_update(credentials):
    collector = make_collector(credentials)
    seed(collector)
    collector.cex.fail([("upbit", "KRW-COIN")], "network_error")

    class Adapter:
        async def tickers(self, markets):
            collector.clock.advance(1)
            collector.cex.put(
                ("upbit", "KRW-COIN"),
                Price(Decimal(1350), collector.clock.milliseconds()),
                "upbit_ws",
            )
            raise AdapterError(Failure.TIMEOUT)

    collector.cex_adapters = {"upbit": Adapter()}
    await collector.poll_rest("upbit")
    assert collector.cex.fresh(("upbit", "KRW-COIN"), 15)
    assert collector.cex.values[("upbit", "KRW-COIN")].price.value == 1350


def test_excluded_tokens_are_never_price_or_ws_targets(credentials):
    excluded = token().model_copy(update={"status": {"upbit": "excluded", "bithumb": "excluded"}})
    collector = make_collector(credentials, [coin([excluded])])
    assert not collector.active_keys
    assert not collector.main_keys
    assert not collector.bridge_groups
    assert not any(collector.markets.values())
    assert collector.snapshot()["counts"] == {
        "main": 0,
        "bridge_candidates": 0,
        "suspected": 0,
        "hidden": 0,
    }


def test_pending_bridge_is_selected_after_liquidity_recovers(credentials):
    collector = make_collector(credentials, [coin([token("2", direct=False)])])
    assert collector.snapshot()["diagnostics"]["pending_bridge_coins"] == 1
    seed(collector)
    assert collector.snapshot()["diagnostics"]["selected_bridge_coins"] == 1
    assert collector.snapshot()["bridge_candidates"][0]["exchanges"]["upbit"]["gap"] == "0"


@pytest.mark.parametrize("offset", [0, 9 * 60 * 60 * 1000])
async def test_bithumb_rest_timestamp_normalizes_only_corroborated_utc_fields(credentials, offset):
    from datetime import UTC, datetime

    collector = make_collector(credentials)
    seed(collector)
    collector.cex.fail([("bithumb", "KRW-COIN")], "network_error")
    now = collector.clock.milliseconds()
    utc = datetime.fromtimestamp(now / 1000, UTC)
    row = {
        "market": "KRW-COIN",
        "trade_price": 1310,
        "timestamp": now + offset,
        "trade_timestamp": now + offset,
        "trade_date": utc.strftime("%Y%m%d"),
        "trade_time": utc.strftime("%H%M%S"),
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))
    ) as client:
        collector.cex_adapters = {"bithumb": CexPrices(client, "bithumb", 0)}
        await collector.poll_rest("bithumb")
    quote = collector.cex.public(("bithumb", "KRW-COIN"), 15)
    assert not quote["stale"]
    assert quote["source"] == "bithumb_rest"
    assert quote["source_at_ms"] == now
    assert quote["provider_timestamp_offset_ms"] == offset
    assert collector.snapshot()["main"][0]["exchanges"]["bithumb"]["gap"] is not None


def test_bithumb_inconsistent_utc_fields_are_not_guessed():
    from app.adapters.prices import bithumb_rest_time

    row = {
        "timestamp": 1790000000000,
        "trade_timestamp": 1790000000000,
        "trade_date": "20200101",
        "trade_time": "120000",
    }
    with pytest.raises(AdapterError):
        bithumb_rest_time(row)


async def test_rest_current_snapshot_refreshes_idle_market_not_last_trade_time(credentials):
    collector = make_collector(credentials)
    seed(collector)
    collector.clock.advance(16)
    # REST timestamp denotes a last trade older than the preceding WS snapshot.
    last_trade = collector.clock.milliseconds() - 120_000

    class Adapter:
        async def tickers(self, markets):
            return {m: Price(Decimal(1300), last_trade) for m in markets}

    collector.cex_adapters = {"bithumb": Adapter()}
    assert not collector.cex.fresh(("bithumb", "KRW-COIN"), 15)
    await collector.poll_rest("bithumb")
    assert collector.cex.fresh(("bithumb", "KRW-COIN"), 15)
    assert collector.cex.public(("bithumb", "KRW-COIN"), 15)["source_at_ms"] == last_trade


async def test_successful_rest_snapshot_does_not_overwrite_newer_ws_receipt(credentials):
    collector = make_collector(credentials)
    seed(collector)
    collector.cex.fail([("bithumb", "KRW-COIN")], "network_error")

    class Adapter:
        async def tickers(self, markets):
            now = collector.clock.milliseconds()
            collector.clock.advance(1)
            collector.cex.put(
                ("bithumb", "KRW-COIN"),
                Price(Decimal(1350), collector.clock.milliseconds()),
                "bithumb_ws",
            )
            return {"KRW-COIN": Price(Decimal(1200), now)}

    collector.cex_adapters = {"bithumb": Adapter()}
    await collector.poll_rest("bithumb")
    assert collector.cex.values[("bithumb", "KRW-COIN")].price.value == 1350
