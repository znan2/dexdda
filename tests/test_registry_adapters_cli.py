import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.adapters.base import AdapterError, Failure
from app.adapters.bithumb import BithumbAdapter
from app.adapters.catalog import Pace, Wallet, parse_rows
from app.adapters.coingecko import CoinGeckoAdapter
from app.adapters.okx import OkxAdapter
from app.adapters.upbit import UpbitAdapter
from app.config import Settings
from app.registry import builder, cli
from app.registry.builder import prepare
from app.registry.loader import load_registry
from app.registry.models import ChainMap, Overrides


@pytest.mark.parametrize(
    "adapter_class,host", [(UpbitAdapter, "api.upbit.com"), (BithumbAdapter, "api.bithumb.com")]
)
async def test_exchange_catalog_requests(adapter_class, host, credentials):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.method == "GET" and request.url.host == host
        if request.url.path == "/v1/market/all":
            assert "authorization" not in request.headers
            return httpx.Response(
                200,
                json=[
                    {
                        "market": "KRW-AI",
                        "korean_name": "젠신",
                        "english_name": "Gensyn",
                        "private": "secret",
                    },
                    {"market": "BTC-AI", "korean_name": "젠신", "english_name": "Gensyn"},
                ],
            )
        assert request.url.path == "/v1/status/wallet"
        assert request.headers["authorization"].startswith("Bearer ")
        return httpx.Response(
            200,
            json=[
                {
                    "currency": "AI",
                    "net_type": "ETH",
                    "network_name": "Ethereum",
                    "wallet_state": "working",
                    "secret": "must-not-persist",
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = adapter_class(client, credentials)
        assert [r["market"] for r in await adapter.markets()] == ["KRW-AI"]
        assert "secret" not in (await adapter.wallets())[0]
    assert calls == ["/v1/market/all", "/v1/status/wallet"]


async def test_coingecko_pages_until_empty_stable_order(credentials):
    pages = []

    def handler(request):
        assert (
            request.headers["x-cg-demo-api-key"] == credentials.coingecko_api_key.get_secret_value()
        )
        assert request.url.params["order"] == "base_target"
        page = int(request.url.params["page"])
        pages.append(page)
        rows = (
            [{"base": f"T{page}", "target": "KRW", "coin_id": f"id-{page}", "secret": "discard"}]
            if page < 3
            else []
        )
        return httpx.Response(200, json={"tickers": rows})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = CoinGeckoAdapter(client, credentials)
        adapter.interval = 0
        rows = await adapter.tickers("upbit")
    assert pages == [1, 2, 3] and len(rows) == 2
    assert all("secret" not in row for row in rows)


async def test_coingecko_repeated_page_fails_instead_of_truncating(credentials):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"tickers": [{"base": "AI", "target": "KRW", "coin_id": "gensyn"}]}
            )
        )
    ) as client:
        adapter = CoinGeckoAdapter(client, credentials)
        adapter.interval = 0
        with pytest.raises(AdapterError, match="응답 형식"):
            await adapter.tickers("upbit")


async def test_coingecko_catalog_whitelist_and_platform_parameter(credentials):
    fixture = json.loads((Path(__file__).parent / "fixtures/registry_sources.json").read_text())
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/coins/list"):
            assert request.url.params["include_platform"] == "true"
            return httpx.Response(200, json=fixture["coins"])
        if request.url.path.endswith("/asset_platforms"):
            return httpx.Response(200, json=fixture["platforms"])
        return httpx.Response(200, json=[{"id": "upbit"}, {"id": "bithumb"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = CoinGeckoAdapter(client, credentials)
        adapter.interval = 0
        assert {"upbit", "bithumb"} == await adapter.exchange_ids()
        assert len(await adapter.coins()) == len(fixture["coins"])
        assert len(await adapter.platforms()) == len(fixture["platforms"])
    assert len(seen) == 3


async def test_okx_batch_body_is_exact_signed_bytes(credentials):
    requested = [{"chainIndex": "1", "tokenContractAddress": "0x" + "a" * 40}]

    def handler(request):
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == requested
        payload = (
            request.headers["ok-access-timestamp"].encode()
            + b"POST"
            + request.url.raw_path
            + request.content
        )
        signature = base64.b64encode(
            hmac.digest(
                credentials.okx_secret_key.get_secret_value().encode(), payload, hashlib.sha256
            )
        ).decode()
        assert request.headers["ok-access-sign"] == signature
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": [
                    dict(requested[0], tokenName="", tokenSymbol="X", decimal="18", tagList={})
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await OkxAdapter(client, credentials).basic_info(requested)
    assert rows[0]["tokenName"] == ""  # Builder excludes these as unrecognized, without guessing.


@pytest.mark.parametrize(
    "data",
    [
        [
            {
                "chainIndex": "1",
                "tokenContractAddress": "0x" + "b" * 40,
                "tokenName": "Wrong",
                "tokenSymbol": "W",
                "decimal": "18",
            }
        ],
        [
            {
                "chainIndex": "1",
                "tokenContractAddress": "0x" + "a" * 40,
                "tokenName": "Dup",
                "tokenSymbol": "D",
                "decimal": "18",
            }
        ]
        * 2,
    ],
)
async def test_okx_rejects_unrequested_or_duplicate_identity(credentials, data):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"code": "0", "data": data})
        )
    ) as client:
        with pytest.raises(AdapterError):
            await OkxAdapter(client, credentials).basic_info(
                [{"chainIndex": "1", "tokenContractAddress": "0x" + "a" * 40}]
            )


async def test_rate_limit_retry_is_bounded(monkeypatch):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.adapters.catalog.asyncio.sleep", sleep)
    calls = 0

    async def limited():
        nonlocal calls
        calls += 1
        raise AdapterError(Failure.LIMIT)

    with pytest.raises(AdapterError):
        await Pace(0).run(limited)
    assert calls == 3 and 5 in sleeps and 10 in sleeps


def test_bad_schema_never_echoes_payload():
    with pytest.raises(AdapterError) as caught:
        parse_rows(Wallet, [{"currency": "secret-echo"}])
    assert "secret-echo" not in str(caught.value)


@pytest.fixture
def cli_case(tmp_path, monkeypatch, credentials):
    source = json.loads((Path(__file__).parent / "fixtures/registry_sources.json").read_text())
    for name in ("chain_map.toml", "overrides.toml"):
        (tmp_path / name).write_text("")

    async def collect(*args, **kwargs):
        return source

    async def metas(*args, **kwargs):
        draft = prepare(source, ChainMap(), Overrides())
        return [
            {
                "chainIndex": c,
                "tokenContractAddress": a,
                "tokenName": "Fixture",
                "tokenSymbol": "F",
                "decimal": "18",
                "tagList": {"communityRecognized": True},
            }
            for c, a in draft.token_keys()
        ]

    monkeypatch.setattr(cli, "load_settings", lambda path: Settings())
    monkeypatch.setattr(cli, "load_credentials", lambda path: credentials)
    monkeypatch.setattr(cli, "collect", collect)
    monkeypatch.setattr(cli, "fetch_metadata", metas)
    args = [
        "--chain-map",
        str(tmp_path / "chain_map.toml"),
        "--overrides",
        str(tmp_path / "overrides.toml"),
        "--output-dir",
        str(tmp_path / "out"),
    ]
    return args, source, tmp_path / "out"


def test_cli_publishes_two_files_and_repeats_identically(cli_case, capsys, monkeypatch):
    args, _, out = cli_case
    assert cli.main(args) == 0
    first = load_registry(out / "registry.json")
    assert first.schema_version == 1
    # The build stamps the current UTC time; only that field may differ between runs.
    stamped = datetime.fromisoformat(first.generated_at)
    assert stamped.utcoffset() == timedelta(0) and stamped.microsecond == 0
    assert abs((datetime.now(UTC) - stamped).total_seconds()) < 60
    monkeypatch.setattr(builder, "utc_now", lambda: first.generated_at)
    assert cli.main(args) == 0
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    assert set(before) == {"registry.json", "registry_report.md"}
    assert cli.main(args) == 0
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
    assert all(json.loads(line)["ok"] for line in capsys.readouterr().out.splitlines())


def test_cli_failure_preserves_last_good_artifacts(cli_case, monkeypatch, capsys):
    args, _, out = cli_case
    assert cli.main(args) == 0
    before = {p.name: p.read_bytes() for p in out.iterdir()}

    async def fail(*args, **kwargs):
        raise AdapterError(Failure.PAYMENT)

    monkeypatch.setattr(cli, "fetch_metadata", fail)
    assert cli.main(args) == 1
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
    assert "payment_required" in capsys.readouterr().out


def test_cli_reflected_secret_never_written(cli_case, credentials, capsys):
    args, source, out = cli_case
    source["coins"][0]["name"] = credentials.okx_secret_key.get_secret_value()
    assert cli.main(args) == 2
    assert not out.exists()
    assert credentials.okx_secret_key.get_secret_value() not in capsys.readouterr().out


def test_unhandled_exception_not_exposed(cli_case, monkeypatch, capsys):
    args, _, _ = cli_case

    async def fail(*args, **kwargs):
        raise RuntimeError("secret-full-url")

    monkeypatch.setattr(cli, "collect", fail)
    assert cli.main(args) == 2
    assert "secret-full-url" not in capsys.readouterr().out


def test_offline_replay_never_loads_credentials(cli_case, monkeypatch, tmp_path):
    args, source, _ = cli_case
    draft = prepare(source, ChainMap(), Overrides())
    metas = [
        {
            "chainIndex": c,
            "tokenContractAddress": a,
            "tokenName": "Fixture",
            "tokenSymbol": "F",
            "decimal": "18",
            "tagList": {"communityRecognized": True},
        }
        for c, a in draft.token_keys()
    ]
    source_path = tmp_path / "sources.json"
    meta_path = tmp_path / "metadata.json"
    source_path.write_text(json.dumps(source))
    meta_path.write_text(json.dumps(metas))

    def forbidden(*args, **kwargs):
        pytest.fail("Offline replay must not load .env or access APIs")

    monkeypatch.setattr(cli, "load_credentials", forbidden)
    monkeypatch.setattr(cli, "collect", forbidden)
    monkeypatch.setattr(cli, "fetch_metadata", forbidden)
    assert cli.main(args + ["--sources", str(source_path), "--metadata", str(meta_path)]) == 0


async def test_okx_chain_catalog_accepts_zero_and_integer_ids(credentials):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v6/dex/market/supported/chain"
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": [
                    {"chainIndex": "0", "chainName": "Bitcoin"},
                    {"chainIndex": 1, "chainName": "Ethereum"},
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await OkxAdapter(client, credentials).chain_catalog(market=True)
    assert [r["chainIndex"] for r in rows] == ["0", "1"]
