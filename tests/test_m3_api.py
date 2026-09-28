"""M3 serving and registry-only presentation contract."""

from decimal import Decimal
from types import SimpleNamespace

import httpx
from test_pricing import FakeClock, coin, seed, token

from app.adapters.prices import Price
from app.config import Settings
from app.main import create_app
from app.pricing.collector import Collector


def collector_with_display(credentials, *, bridge=False):
    entry = coin([token(direct=not bridge)], "gensyn")
    entry = entry.model_copy(
        update={
            "symbol": "AI",
            "names": {"upbit_ko": "젠신", "okx": "Gensyn"},
            "tokens": [entry.tokens[0].model_copy(update={"name": "Gensyn"})],
        }
    )
    listing = entry.exchanges["upbit"]
    entry.exchanges["upbit"] = listing.model_copy(
        update={
            "networks": [
                {"net_type": "ETH", "network_name": "Ethereum", "wallet_state": "withdraw_only"}
            ]
        }
    )
    registry = SimpleNamespace(coins=[entry], chains=[{"chain_index": "1", "name": "Ethereum"}])
    return Collector(registry, Settings(), credentials, clock=FakeClock())


def test_ui_metadata_and_paused_deposit_kept_in_main(credentials):
    collector = collector_with_display(credentials)
    seed(collector)
    collector.wallet_status.observe(
        "upbit",
        [
            {
                "currency": "GENSYN",
                "net_type": "ETH",
                "network_name": "Ethereum",
                "wallet_state": "withdraw_only",
            }
        ],
        collector.clock.monotonic(),
    )
    data = collector.snapshot()
    row = data["main"][0]
    assert row["chain_name"] == "Ethereum"
    assert row["token_name"] == "Gensyn"
    assert row["names"]["upbit_ko"] == "젠신"
    assert row["exchanges"]["upbit"]["deposit_networks"][0]["wallet_state"] == "withdraw_only"
    assert row["exchanges"]["upbit"]["route_status"] == "tradable"
    assert data["ui_poll_seconds"] == 2
    assert data["filters"]["max_abs_gap_percent"] == "30"


def test_pending_bridge_retains_known_exchange_deposit_networks(credentials):
    data = collector_with_display(credentials, bridge=True).snapshot()
    row = data["bridge_candidates"][0]
    assert row["chain_index"] is None
    assert row["exchanges"]["upbit"]["deposit_networks"][0]["net_type"] == "ETH"
    assert row["exchanges"]["upbit"]["gap_percent"] is None
    assert row["execution_enabled"] is False


def test_foreign_same_ticker_prices_cannot_create_display_rows(credentials):
    collector = collector_with_display(credentials)
    seed(collector)
    foreign = ("1", "0x" + "f" * 40)
    collector.dex.put(foreign, Price(Decimal(999), collector.clock.milliseconds()), "okx_price")
    collector.cex.put(
        ("upbit", "KRW-ARTIFICIAL-INU"),
        Price(Decimal(999999), collector.clock.milliseconds()),
        "upbit_ws",
    )
    snapshot = collector.snapshot()
    rows = snapshot["main"] + snapshot["bridge_candidates"] + snapshot["suspected"]
    assert {(r["coin_id"], r["chain_index"], r["address"]) for r in rows} == {
        ("gensyn", "1", token().address)
    }


async def test_dashboard_assets_local_only_no_private_file_routes(credentials):
    app = create_app(collector_with_display(credentials))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        page = await client.get("/")
        assert page.status_code == 200
        assert 'src="/assets/dashboard.js"' in page.text
        for asset, mime in [("dashboard.css", "text/css"), ("dashboard.js", "javascript")]:
            response = await client.get("/assets/" + asset)
            assert response.status_code == 200
            assert mime in response.headers["content-type"]
        for url in ["/.env", "/assets/%2e%2e/%2e%2e/.env", "/assets/absent.js"]:
            assert (await client.get(url)).status_code == 404
        assert (
            await client.get("/assets/dashboard.js", headers={"Host": "foreign.invalid"})
        ).status_code == 400
        assert (await client.get("/api/gaps")).headers["cache-control"] == "no-store"
