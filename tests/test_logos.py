"""Optional logo metadata: identity, durable reuse, outages and URL boundaries."""

import json
from types import SimpleNamespace

import httpx
import pytest
from test_pricing import coin, make_collector, token

from app.adapters.prices import Gate
from app.logos import WEEK, Logos, safe_url
from app.main import create_app

CG = "https://coin-images.coingecko.com/coins/images/1/small/coin.png"
CHAIN = "https://coin-images.coingecko.com/asset_platforms/images/1/small/chain.png"
OKX = "https://static.oklink.com/cdn/token.png"


def setup(credentials, path=None):
    c = make_collector(credentials, [coin([token()], "first"), coin([token("56")], "second")])
    c.registry.chains = [{"chain_index": "1", "platform_ids": ["ethereum"]}]
    c.settings = c.settings.model_copy(
        update={
            "registry": c.settings.registry.model_copy(update={"coingecko_interval_seconds": 0})
        }
    )
    c.okx = SimpleNamespace(gate=Gate(0))
    now = [1000000]
    logos = Logos(c, path, now=lambda: now[0])
    c.logos = logos
    return c, logos, now


@pytest.mark.parametrize(
    "url",
    [
        "http://coin-images.coingecko.com/a",
        "https://coin-images.coingecko.com.evil.test/a",
        "https://x:secret@coin-images.coingecko.com/a",
        "javascript:alert(1)",
        "https://127.0.0.1/a",
        "https://coin-images.coingecko.com:4000/a",
        "https://coin-images.coingecko.com/a\n",
        None,
    ],
)
def test_unsafe_logo_url(url):
    assert safe_url(url) is None


async def test_bulk_identity_fallback_persistence_and_retry(credentials, tmp_path):
    c, logos, now = setup(credentials, tmp_path / "logos.json")
    calls = []
    failure = [False]

    def handler(request):
        calls.append(request.url.path)
        if failure[0]:
            return httpx.Response(429, json={"error": "rate limited"})
        if request.url.path.endswith("asset_platforms"):
            return httpx.Response(
                200,
                json=[
                    {"id": "ethereum", "chain_identifier": 1, "image": {"small": CHAIN}},
                    {"id": "wrong", "chain_identifier": 1, "image": {"small": CG}},
                ],
            )
        if request.url.path.endswith("coins/markets"):
            return httpx.Response(
                200, json=[{"id": "first", "image": CG}, {"id": "not-requested", "image": CG}]
            )
        payload = json.loads(request.content)
        assert len(payload) == 1 and payload[0]["chainIndex"] == "56"
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": [
                    {**payload[0], "tokenLogoUrl": OKX},
                    {
                        "chainIndex": "1",
                        "tokenContractAddress": payload[0]["tokenContractAddress"],
                        "tokenLogoUrl": CG,
                    },
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        c.client = client
        await logos.refresh()
        result = logos.public()
        assert result["coins"] == {"first": CG}
        assert result["chains"] == {"1": CHAIN}
        key = "56:" + token("56").address
        assert result["tokens"] == {key: OKX}
        count = len(calls)
        await logos.refresh()
        assert len(calls) == count  # No provider call on warm cache.
        loaded = Logos(c, logos.path, now=lambda: now[0])
        assert loaded.public() == result
        now[0] += WEEK + 1
        failure[0] = True
        # Avoid provider retry backoff in this deterministic failure test.
        from app.adapters.base import AdapterError, Failure

        async def unavailable(*args, **kwargs):
            raise AdapterError(Failure.LIMIT)

        from unittest.mock import patch

        with (
            patch("app.adapters.coingecko.CoinGeckoAdapter._get", unavailable),
            patch("app.adapters.okx.OkxAdapter._catalog_request", unavailable),
        ):
            await logos.refresh()
        assert logos.public() == result
        assert Logos(c, logos.path, now=lambda: now[0]).public() == result
        assert c.stats["logos"]["last_error"] == "rate_limited"


async def test_optional_corrupt_cache_api_and_missing_image(credentials, tmp_path):
    path = tmp_path / "logos.json"
    path.write_text("broken json")
    c, logos, now = setup(credentials, path)
    logos.remember("coin:first", CG)
    logos.remember("coin:first", "https://untrusted.test/wrong.png")
    assert logos.public()["coins"]["first"] == CG
    logos.remember("coin:second", None)
    assert not logos.due("coin:second")
    now[0] += 86401
    assert logos.due("coin:second")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(c)), base_url="http://localhost"
    ) as client:
        r = await client.get("/api/logos")
        assert r.status_code == 200
        assert r.json()["coins"] == {"first": CG}
        assert "sentinel" not in r.text
