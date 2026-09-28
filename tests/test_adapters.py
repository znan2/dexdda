import json

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.base import AdapterError, Failure, request_json
from app.adapters.coingecko import CoinGeckoAdapter
from app.adapters.okx import OkxAdapter
from app.adapters.rpc import RpcAdapter
from app.config import Credentials, Settings
from app.smoke import run_checks


@pytest.mark.parametrize("dry_run", [True, False])
async def test_all_read_only_requests_with_no_private_key(credentials, responses, dry_run):
    seen = []
    mapping = {
        "web3.okx.com": ("okx", "/api/v6/dex/aggregator/supported/chain"),
        "api.upbit.com": ("upbit", "/v1/deposits/coin_addresses"),
        "api.bithumb.com": ("bithumb", "/v1/deposits/coin_addresses"),
        "api.coingecko.com": ("coingecko", "/api/v3/ping"),
        "rpc.invalid": ("rpc", "/sentinel-rpc-key"),
    }

    def handler(request):
        name, path = mapping[request.url.host]
        seen.append(name)
        assert request.url.path == path
        assert not request.url.query
        if name == "rpc":
            assert request.method == "POST"
            assert json.loads(request.content) == {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_chainId",
                "params": [],
            }
            assert "authorization" not in request.headers
        else:
            assert request.method == "GET"
            assert not request.content
        if name == "coingecko":
            assert (
                request.headers["x-cg-demo-api-key"]
                == credentials.coingecko_api_key.get_secret_value()
            )
        if name == "okx":
            assert request.headers.get("ok-access-sign")
        if name in ("upbit", "bithumb"):
            assert request.headers["authorization"].startswith("Bearer ")
        return httpx.Response(200, json=responses[name])

    results = await run_checks(
        Settings(),
        credentials.model_copy(update={"dry_run": dry_run}),
        transport=httpx.MockTransport(handler),
    )
    assert len(results) == 5 and all(result.ok for result in results)
    assert seen == ["okx", "upbit", "bithumb", "coingecko", "rpc"]
    assert credentials.wallet_private_key.get_secret_value() == ""


@pytest.mark.parametrize(
    "status,payload,category",
    [
        (401, {"error": {"name": "no_authorization_ip"}}, Failure.IP),
        (401, {"error": {"name": "NotAllowIP"}}, Failure.IP),
        (401, {"error": {"name": "out_of_scope"}}, Failure.PERMISSION),
        (403, {"error": {"name": "out_of_scope"}}, Failure.PERMISSION),
        (401, {"error": {"name": "jwt_verification"}}, Failure.SIGNATURE),
        (401, {"code": "50113"}, Failure.SIGNATURE),
        (200, {"error": {"name": "jwt_verification"}}, Failure.SIGNATURE),
        (429, {}, Failure.LIMIT),
        (418, {}, Failure.LIMIT),
        (402, {}, Failure.PAYMENT),
        (401, {"status": {"error_code": 10002}}, Failure.AUTH),
        (403, {"error": {"name": "unknown"}}, Failure.HTTP),
    ],
)
async def test_service_error_categories(status, payload, category):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(status, json=payload),
        )
    ) as client:
        with pytest.raises(AdapterError) as caught:
            await request_json(client, "GET", "https://service.invalid")
    assert caught.value.category == category


async def test_okx_http_200_business_error(credentials):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"code": "50113", "msg": "secret"}),
        )
    ) as client:
        with pytest.raises(AdapterError) as caught:
            await OkxAdapter(client, credentials).supported_chains()
    assert caught.value.category == Failure.SIGNATURE
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "status,body,category",
    [
        (200, "not json", Failure.SCHEMA),
        (500, "<html>error</html>", Failure.HTTP),
        (302, "", Failure.HTTP),
    ],
)
async def test_bad_json_and_redirects_not_followed(status, body, category):
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(status, text=body, headers={"Location": "https://other.invalid"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        with pytest.raises(AdapterError) as caught:
            await request_json(client, "GET", "https://service.invalid")
    assert seen == ["service.invalid"]
    assert caught.value.category == category


@pytest.mark.parametrize(
    "error,category",
    [
        (httpx.ReadTimeout, Failure.TIMEOUT),
        (httpx.ConnectError, Failure.NETWORK),
    ],
)
async def test_transport_errors_sanitized(error, category):
    def handler(request):
        raise error("secret https://rpc.invalid/key", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AdapterError) as caught:
            await request_json(client, "GET", "https://service.invalid")
    assert caught.value.category == category
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "payload,category",
    [
        ({"jsonrpc": "2.0", "id": 1, "result": "0x89"}, Failure.CHAIN),
        ({"jsonrpc": "2.0", "id": 1, "result": 1}, Failure.SCHEMA),
        ({"jsonrpc": "2.0", "id": True, "result": "0x1"}, Failure.SCHEMA),
        ({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "secret"}}, Failure.RPC),
    ],
)
async def test_rpc_response_validation(payload, category):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=payload),
        )
    ) as client:
        with pytest.raises(AdapterError) as caught:
            await RpcAdapter(client, SecretStr("https://rpc.invalid/key"), "1").check_chain()
    assert caught.value.category == category


@pytest.mark.parametrize(
    "url",
    ["http://public.invalid/key", "file:///secret", "https://", "https://rpc.invalid/#secret"],
)
async def test_invalid_rpc_url_never_requests(url):
    def handler(request):
        pytest.fail("Invalid URL must not make a request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AdapterError) as caught:
            await RpcAdapter(client, SecretStr(url), "1").check_chain()
    assert caught.value.category == Failure.CONFIG


async def test_missing_keys_make_no_requests():
    def handler(request):
        pytest.fail("Missing keys must not make a request")

    results = await run_checks(Settings(), Credentials(), transport=httpx.MockTransport(handler))
    assert len(results) == 4
    assert all(result.category == "missing_config" for result in results)


async def test_coingecko_pro_explicit_host_header(credentials, responses):
    def handler(request):
        assert request.url.host == "pro-api.coingecko.com"
        assert (
            request.headers["x-cg-pro-api-key"] == credentials.coingecko_api_key.get_secret_value()
        )
        assert "x-cg-demo-api-key" not in request.headers
        return httpx.Response(200, json=responses["coingecko"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await CoinGeckoAdapter(client, credentials, "pro").ping()


@pytest.mark.parametrize("chain_index", ["1", 1, "42161", 42161])
async def test_okx_chain_index_string_and_integer(credentials, chain_index):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"code": "0", "data": [{"chainIndex": chain_index}]}
            ),
        )
    ) as client:
        assert await OkxAdapter(client, credentials).supported_chains() == 1


@pytest.mark.parametrize(
    "chain_index", [True, False, None, 1.0, 0, -1, "0", "-1", "", "solana", "１"]
)
async def test_okx_rejects_invalid_chain_index(credentials, chain_index):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"code": "0", "data": [{"chainIndex": chain_index}]}
            ),
        )
    ) as client:
        with pytest.raises(AdapterError) as caught:
            await OkxAdapter(client, credentials).supported_chains()
    assert caught.value.category == Failure.SCHEMA
