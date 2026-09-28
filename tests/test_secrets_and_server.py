import json
import logging
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr
from test_pricing import make_collector

from app import smoke
from app.config import Settings
from app.main import app, create_app


@pytest.mark.parametrize("failure", [False, True])
async def test_smoke_output_and_logs_never_contain_secrets(
    credentials, responses, caplog, capsys, failure
):
    credentials = credentials.model_copy(
        update={"wallet_private_key": SecretStr("sentinel-private-key")}
    )
    secret_values = [
        value.get_secret_value()
        for name, value in credentials
        if name not in ("dry_run", "rpc_urls") and value.get_secret_value()
    ] + [
        credentials.rpc_urls["1"].get_secret_value(),
        "sentinel-rpc-key",
        "synthetic-upbit-deposit-address",
        "synthetic-bithumb-deposit-address",
        "raw-remote-body",
    ]
    headers_seen = []

    def handler(request):
        headers_seen.extend(
            str(value)
            for name, value in request.headers.items()
            if name in ("authorization", "ok-access-sign")
        )
        if failure:
            return httpx.Response(
                401,
                json={
                    "error": {
                        "name": "jwt_verification",
                        "message": " ".join(secret_values),
                    }
                },
            )
        name = {
            "web3.okx.com": "okx",
            "api.upbit.com": "upbit",
            "api.bithumb.com": "bithumb",
            "api.coingecko.com": "coingecko",
            "rpc.invalid": "rpc",
        }[request.url.host]
        return httpx.Response(200, json=responses[name])

    with caplog.at_level(logging.DEBUG):
        results = await smoke.run_checks(Settings(), credentials, httpx.MockTransport(handler))
    print(smoke.render(results, credentials.dry_run))
    print(smoke.render(results, credentials.dry_run, True))
    captured = capsys.readouterr()
    visible = captured.out + captured.err + caplog.text + repr(results)
    for secret in secret_values + headers_seen:
        assert secret not in visible
    assert all(result.ok for result in results) is not failure


def test_cli_exit_codes_and_sanitized_json(monkeypatch, credentials, capsys):
    monkeypatch.setattr(smoke, "load_settings", lambda path: Settings())
    monkeypatch.setattr(smoke, "load_credentials", lambda path: credentials)

    async def checks(*args, **kwargs):
        return [smoke.Result("OKX", False, "signature_invalid", "서명 검증 실패")]

    monkeypatch.setattr(smoke, "run_checks", checks)
    assert smoke.main(["--json"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["read_only"] is True
    assert output["results"][0]["category"] == "signature_invalid"


def test_unexpected_cli_error_is_sanitized(monkeypatch, capsys):
    def fail(path):
        raise RuntimeError("raw-secret")

    monkeypatch.setattr(smoke, "load_settings", fail)
    assert smoke.main([]) == 2
    captured = capsys.readouterr()
    assert "raw-secret" not in captured.out + captured.err


async def test_server_is_local_read_only_and_has_no_cors():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        response = await client.get("/api/health", headers={"Origin": "https://foreign.invalid"})
        assert response.json() == {
            "status": "ok",
            "milestone": "M7",
            "execution_enabled": False,
            "registry_generated_at": None,
            "registry_age_hours": None,
            "registry_time_source": None,
            "registry_stale": False,
        }
        assert "access-control-allow-origin" not in response.headers
        assert (await client.get("/")).status_code == 200
        assert (await client.post("/api/swap")).status_code == 404
        assert (
            await client.get("/api/health", headers={"Host": "foreign.invalid"})
        ).status_code == 400


@pytest.mark.parametrize(
    ("age_hours", "max_age_hours", "stale"),
    [(71.5, None, False), (72.0, None, False), (72.5, None, True), (5.0, 4, True)],
)
async def test_health_reports_registry_age_against_max_age(
    credentials, age_hours, max_age_hours, stale
):
    settings = Settings() if max_age_hours is None else Settings(registry={"max_age_hours": 4})
    collector = make_collector(credentials, settings=settings)
    built = collector.clock.milliseconds() / 1000 - age_hours * 3600
    collector.registry.generated_at = datetime.fromtimestamp(built, UTC).isoformat()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(collector)), base_url="http://127.0.0.1"
    ) as client:
        payload = (await client.get("/api/health")).json()
    assert payload["registry_generated_at"] == collector.registry.generated_at
    assert payload["registry_time_source"] == "field"
    assert isinstance(payload["registry_age_hours"], float)
    assert abs(payload["registry_age_hours"] - age_hours) < 0.001
    assert payload["registry_stale"] is stale
