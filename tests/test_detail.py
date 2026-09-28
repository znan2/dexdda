import hashlib
import json
import time
from decimal import Decimal as D
from urllib.parse import urlencode

import httpx
import jwt
import pytest
from detail_fixtures import attach_detail
from pydantic import ValidationError
from test_pricing import make_collector, token

from app.adapters.base import AdapterError, Failure
from app.adapters.detail import DetailAdapter, ReadRpc, parse_quote
from app.adapters.upbit import auth_headers
from app.detail.math import consume_bids, effective_result
from app.detail.models import DetailRequest, PurchaseAsset
from app.detail.service import DetailUnavailable
from app.main import create_app


def request(**changes):
    return DetailRequest(
        coin_id="coin",
        chain_index="1",
        address=token().address,
        exchange="upbit",
        amount_usdt="1000",
        **changes,
    )


@pytest.fixture
async def setup(credentials):
    collector = make_collector(credentials)
    service, adapter, state = attach_detail(collector)
    yield collector, service, adapter, state
    await collector.client.aclose()


def test_bid_fill_consumes_best_then_partial_and_excludes_exchange_fee():
    fill = consume_bids(D("1.5"), [(D(100), D(5)), (D(120), D(1))])
    assert fill.complete and fill.proceeds == 170
    assert fill.public()["filled"] == "1.5"
    result = effective_result(D(100), fill, D("1.5"), D(2))
    assert abs(D(result["profit_usdt"]) - D("11.33333333333333333333333333")) < D("1e-25")


def test_insufficient_book_never_extrapolates_or_reports_profit():
    fill = consume_bids(D(4), [(D(100), D(1)), (D(90), D(2))])
    assert not fill.complete and fill.filled == 3 and fill.proceeds == 280
    assert effective_result(D(100), fill, D(1), D(0)) is None
    assert not consume_bids(D(1), []).complete


async def test_non_numeric_bid_price_is_schema_failure_without_echo(setup):
    # Upbit orderbook shape with one corrupted bid; the raw text must never reach the client.
    units = [
        {"bid_price": "abc", "bid_size": "1", "ask_price": "1310", "ask_size": "1"},
        {"bid_price": "1290", "bid_size": "2", "ask_price": "1320", "ask_size": "1"},
    ]

    def handler(request):
        row = {"market": "KRW-COIN", "timestamp": int(time.time() * 1000)}
        return httpx.Response(200, json=[{**row, "orderbook_units": units}])

    with pytest.raises(AdapterError) as caught:
        consume_bids(D(1), [("abc", D(1))])
    assert caught.value.category is Failure.SCHEMA and "abc" not in str(caught.value)

    collector, service, adapter, _ = setup
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        real = DetailAdapter(client, collector.credentials)
        with pytest.raises(AdapterError) as caught:
            await real.book("upbit", "KRW-COIN")
        assert caught.value.category is Failure.SCHEMA and "abc" not in str(caught.value)

        adapter.book = real.book
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(collector, service)),
            base_url="http://127.0.0.1",
        ) as api:
            r = await api.post("/api/detail", json=request().model_dump())
    assert r.status_code == 200 and "abc" not in r.text
    data = r.json()
    assert data["book"] is None and data["result"] is None
    # fx also fails as SCHEMA: the handler answers KRW-USDT with the KRW-COIN book (market mismatch).
    assert {e["stage"]: e["code"] for e in data["errors"]} == {
        "orderbook": "unexpected_response",
        "fx": "unexpected_response",
    }


@pytest.mark.parametrize(
    "amount", ["0", "-1", "NaN", "1e3", "0.0000001", "1000001", "01", "9" * 200]
)
async def test_amount_validation(setup, amount):
    _, service, _, _ = setup
    with pytest.raises((ValidationError, DetailUnavailable)):
        req = (
            request().model_copy(update={"amount_usdt": amount})
            if amount in ("0", "1000001")
            else DetailRequest(**{**request().model_dump(), "amount_usdt": amount})
        )
        await service.calculate(req)


async def test_full_result_amount_requote_common_fx_and_approve_cost(setup):
    _, service, adapter, state = setup
    first = await service.calculate(request())
    assert first["errors"] == [] and first["result"]
    assert first["gas"]["approval_count"] == 1
    assert D(first["gas"]["approve_usdt"]) == D("0.13")
    assert D(first["gas"]["transfer_usdt"]) == D("0.2")
    assert D(first["result"]["profit_usdt"]) == D("98.67")
    assert first["deposit"]["estimated_seconds"] == "144"
    second = await service.calculate(request().model_copy(update={"amount_usdt": "2000"}))
    assert adapter.quote_calls == [1000000000, 2000000000]
    assert D(second["book"]["average_price_krw"]) == 1365
    assert D(second["result"]["gap_percent"]) < D(first["result"]["gap_percent"])
    state["allowance"] = 10**15
    third = await service.calculate(request().model_copy(update={"exchange": "bithumb"}))
    assert third["fx_krw"] == "1300"
    assert D(third["gas"]["approve_usdt"]) == 0
    assert third["deposit"]["confirmations"] is None
    assert third["execution_enabled"] is False
    state["allowance"] = 5
    assert (await service.calculate(request()))["gas"]["approval_count"] == 2


@pytest.mark.parametrize(
    "scenario",
    ["depth", "tax", "honeypot", "stale", "rpc", "minimum", "deposit", "bridge", "asset", "fee"],
)
async def test_unavailable_inputs_never_become_zero_or_profit(setup, scenario):
    collector, service, adapter, state = setup
    if scenario == "depth":
        adapter.depth = D(1)
    if scenario == "tax":
        adapter.tax = D(".01")
    if scenario == "honeypot":
        adapter.honeypot = True
    if scenario == "stale":
        adapter.age = 100000
    if scenario == "rpc":
        state["rpc_failure"] = True
    if scenario == "minimum":
        adapter.minimum = D(1001)
    if scenario == "deposit":
        collector.registry.coins[0].exchanges["upbit"].networks[0]["wallet_state"] = "withdraw_only"
    if scenario == "bridge":
        collector.registry.coins[0].tokens[0].status["upbit"] = "bridge_candidate"
    if scenario == "asset":
        service.assets = {}
    if scenario == "fee":
        service.assets["1"] = service.assets["1"].model_copy(update={"simple_gas": False})
    result = await service.calculate(request())
    assert result["result"] is None and result["execution_enabled"] is False
    assert result["warnings"]
    assert "sentinel-secret" not in json.dumps(result)


async def test_exact_identity_rejected_before_provider_request(setup):
    _, service, adapter, _ = setup
    for changes in [
        {"address": "0x" + "d" * 40},
        {"coin_id": "same-symbol"},
        {"chain_index": "56"},
    ]:
        with pytest.raises(DetailUnavailable):
            await service.calculate(request().model_copy(update=changes))
    assert adapter.quote_calls == []


async def test_quote_failure_preserves_deposit_info_and_sanitized_error(setup):
    _, service, adapter, _ = setup
    adapter.fail_quote = True
    data = await service.calculate(request())
    assert data["quote"] is None and data["result"] is None
    assert data["deposit"]["possible"] is True
    assert data["errors"][0]["code"] == "rate_limited"


async def test_busy_rejected_without_queueing(setup):
    _, service, _, _ = setup
    async with service.lock:
        with pytest.raises(DetailUnavailable, match="detail_busy"):
            await service.calculate(request())


def quote_payload():
    return {
        "chainIndex": "1",
        "fromTokenAmount": "1000000000",
        "toTokenAmount": "12345",
        "fromToken": {
            "tokenContractAddress": "0x" + "c" * 40,
            "decimal": "6",
            "taxRate": "0",
            "isHoneyPot": False,
        },
        "toToken": {
            "tokenContractAddress": token().address,
            "decimal": "18",
            "taxRate": "0.01",
            "isHoneyPot": False,
        },
        "tradeFee": "1.2",
        "estimateGasFee": "999999999999999999999",
        "priceImpactPercent": "-3.2",
        "dexRouterList": [
            {
                "dexProtocol": {"dexName": "DEX A", "percent": "100"},
                "fromToken": {"tokenSymbol": "USDT"},
                "toToken": {"tokenSymbol": "TOKEN"},
            }
        ],
    }


def parse(data):
    asset = PurchaseAsset(
        chain_index="1", address="0x" + "c" * 40, decimals=6, symbol="USDT", evidence="fixture"
    )
    return parse_quote(data, token(), asset, 1000000000)


def test_quote_units_fractional_tax_round_down_and_fee_units():
    data = parse(quote_payload())
    assert data["quantity"] == D("0.000000000000012345")
    assert data["net_quantity"] == D("0.000000000000012221")
    assert data["swap_gas_usdt"] == D("1.2")
    assert data["price_impact_percent"] == D("-3.2")
    assert "DEX A" in data["routes"][0]


@pytest.mark.parametrize(
    "field,value", [("chainIndex", "56"), ("fromTokenAmount", "2"), ("toTokenAmount", "0")]
)
def test_quote_rejects_wrong_identity_or_amount(field, value):
    row = quote_payload()
    row[field] = value
    with pytest.raises(AdapterError):
        parse(row)


def test_quote_rejects_wrong_decimals_ca_and_invalid_tax():
    for field, value in [
        ("decimal", "6"),
        ("tokenContractAddress", "0x" + "f" * 40),
        ("taxRate", "1.1"),
        ("isHoneyPot", "false"),
    ]:
        row = quote_payload()
        row["toToken"][field] = value
        with pytest.raises(AdapterError):
            parse(row)
    row = quote_payload()
    row["toToken"].pop("taxRate")
    assert parse(row)["net_quantity"] is None
    row = quote_payload()
    row.pop("tradeFee")
    assert parse(row)["swap_gas_usdt"] is None


def test_upbit_query_hash_covers_actual_query(credentials):
    params = {"currency": "ETH", "net_type": "ETH"}
    headers = auth_headers(credentials, params)
    payload = jwt.decode(
        headers["Authorization"].split()[1],
        credentials.upbit_secret_key.get_secret_value(),
        algorithms=["HS512"],
    )
    assert payload["query_hash"] == hashlib.sha512(urlencode(params).encode()).hexdigest()
    assert payload["query_hash_alg"] == "SHA512"


async def test_detail_api_no_secret_echo_and_no_execution_routes(setup):
    collector, service, _, _ = setup
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(collector, service)),
        base_url="http://127.0.0.1",
    ) as client:
        r = await client.post("/api/detail", json=request().model_dump())
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        r = await client.post(
            "/api/detail", json={**request().model_dump(), "amount_usdt": "sentinel-private-key"}
        )
        assert r.status_code == 422 and "sentinel-private-key" not in r.text
        for path in ("/api/swap", "/api/approve", "/api/transfer"):
            assert (await client.post(path, json={})).status_code == 404


async def test_rpc_write_methods_prohibited(setup):
    collector, _, _, _ = setup
    rpc = ReadRpc(collector.client, collector.credentials.rpc_urls["1"], "1")
    with pytest.raises(AdapterError):
        await rpc.call("eth_sendRawTransaction", ["secret"])


@pytest.mark.parametrize(
    "scenario", ["success", "quote_failure", "missing_asset", "missing_impact"]
)
async def test_bridge_only_quotes_without_wallet_books_gas_or_rpc(setup, monkeypatch, scenario):
    from unittest.mock import AsyncMock

    collector, service, adapter, _ = setup
    collector.registry.coins[0].tokens[0].status["upbit"] = "bridge_candidate"
    blocked = []
    for owner, name in [
        (adapter, "wallets"),
        (adapter, "deposit_chance"),
        (adapter, "approval"),
        (adapter, "book"),
        (collector.okx, "fetch"),
        (collector.client, "send"),
    ]:
        mock = AsyncMock(side_effect=AssertionError("bridge must only request a quote"))
        monkeypatch.setattr(owner, name, mock)
        blocked.append(mock)
    if scenario == "quote_failure":
        adapter.fail_quote = True
    elif scenario == "missing_asset":
        service.assets = {}
    elif scenario == "missing_impact":
        original = adapter.quote

        async def without_impact(*args):
            return {**await original(*args), "price_impact_percent": None}

        monkeypatch.setattr(adapter, "quote", without_impact)
    data = await service.calculate(request())
    assert all(mock.await_count == 0 for mock in blocked)
    assert data["execution_enabled"] is False
    assert all(data[key] is None for key in ["result", "reference_result", "gas", "book", "fx_krw"])
    assert "deposit_unavailable" not in {w["code"] for w in data["warnings"]}
    if scenario in {"success", "missing_impact"}:
        assert data["errors"] == []
        assert data["quote"]["quantity"] == "1000"
        assert data["quote"]["price_impact_percent"] == ("-0.2" if scenario == "success" else None)
        await service.calculate(request().model_copy(update={"amount_usdt": "100"}))
        assert adapter.quote_calls == [1000000000, 100000000]
    elif scenario == "quote_failure":
        assert data["quote"] is None and data["errors"][0]["code"] == "rate_limited"
    else:
        assert data["quote"] is None and adapter.quote_calls == []
        assert "purchase_asset_missing" in {w["code"] for w in data["warnings"]}


async def test_bsc_bridge_uses_configured_18_decimal_usdt(setup):
    from app.config import PROJECT_ROOT
    from app.detail.models import PurchaseAssets
    from app.registry.loader import load_config

    collector, service, adapter, _ = setup
    asset = next(
        a
        for a in load_config(PROJECT_ROOT / "config/quote_assets.toml", PurchaseAssets).assets
        if a.chain_index == "56"
    )
    assert asset.address == "0x55d398326f99059ff775485246999027b3197955"
    assert asset.decimals == 18 and asset.simple_gas is False
    coin = collector.registry.coins[0]
    coin.tokens[0] = coin.tokens[0].model_copy(
        update={"chain_index": "56", "status": {"upbit": "bridge_candidate"}}
    )
    service.assets = {"56": asset}
    data = await service.calculate(
        request().model_copy(update={"chain_index": "56", "amount_usdt": "100"})
    )
    assert adapter.quote_calls == [100 * 10**18]
    assert data["quote"]["quantity"] == "100"
    assert data["result"] is None


@pytest.mark.parametrize("symbol,decimals", [("USDC", 6), ("USDC", 18), ("USDG", 6)])
async def test_selected_asset_quote_units_identity_and_one_to_one_math(
    setup, monkeypatch, symbol, decimals
):
    _, service, adapter, _ = setup
    baseline = await service.calculate(request())
    asset = service.assets["1"].model_copy(
        update={"symbol": symbol, "address": "0x" + "e" * 40, "decimals": decimals}
    )
    service.other_assets[("1", symbol)] = asset
    seen = []
    original = adapter.quote

    async def quote(token, selected, units):
        seen.append((selected.address, units))
        return await original(token, selected, units)

    monkeypatch.setattr(adapter, "quote", quote)
    result = await service.calculate(request().model_copy(update={"purchase_symbol": symbol}))
    assert seen == [(asset.address, 1000 * 10**decimals)]
    assert result["result"] == baseline["result"]
    assert result["purchase_symbol"] == symbol
    assert result["purchase_asset"]["address"] == asset.address
    assert "1 USDC = 1 USDG = 1 USDT" in result["basis"]


async def test_unsupported_asset_does_not_fall_back_to_usdt(setup):
    _, service, adapter, _ = setup
    result = await service.calculate(request().model_copy(update={"purchase_symbol": "USDC"}))
    assert result["quote"] is None and adapter.quote_calls == []
    assert "purchase_asset_missing" in {w["code"] for w in result["warnings"]}


def test_purchase_catalog_allows_distinct_assets_but_not_duplicate_family():
    from app.detail.models import PurchaseAssets

    base = PurchaseAsset(
        chain_index="1", address="0x" + "c" * 40, decimals=6, symbol="USDT", evidence="fixture"
    )
    assert (
        len(PurchaseAssets(assets=[base, base.model_copy(update={"symbol": "USDC"})]).assets) == 2
    )
    with pytest.raises(ValidationError):
        PurchaseAssets(assets=[base, base.model_copy(update={"symbol": "USDT0"})])
