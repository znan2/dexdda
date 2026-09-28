"""Read-only smoke checks. Never print raw responses, credentials, or URLs."""

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from app.adapters.base import MESSAGES, AdapterError, Failure
from app.adapters.bithumb import BithumbAdapter
from app.adapters.coingecko import CoinGeckoAdapter
from app.adapters.okx import OkxAdapter
from app.adapters.rpc import RpcAdapter
from app.adapters.upbit import UpbitAdapter
from app.config import ConfigError, Credentials, Settings, load_credentials, load_settings


@dataclass(frozen=True)
class Result:
    service: str
    ok: bool
    category: str
    detail: str


def quiet_transport_logs() -> None:
    # httpx INFO includes full RPC URLs, which may contain provider API keys.
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        logger.setLevel(logging.CRITICAL + 1)


async def check(service, operation) -> Result:
    try:
        await operation()
        return Result(service, True, "ok", "읽기 전용 조회 성공")
    except AdapterError as exc:
        return Result(service, False, exc.category.value, MESSAGES[exc.category])
    except Exception:  # noqa: BLE001 - CLI boundary must never expose secret-bearing exceptions.
        return Result(service, False, Failure.INTERNAL.value, MESSAGES[Failure.INTERNAL])


async def run_checks(
    settings: Settings,
    credentials: Credentials,
    transport: httpx.AsyncBaseTransport | None = None,
    include_rpc: bool = True,
) -> list[Result]:
    quiet_transport_logs()
    async with httpx.AsyncClient(
        timeout=settings.http.timeout_seconds,
        trust_env=False,
        follow_redirects=False,
        transport=transport,
    ) as client:
        operations = [
            ("OKX", OkxAdapter(client, credentials).supported_chains),
            ("Upbit", UpbitAdapter(client, credentials).deposit_addresses),
            ("Bithumb", BithumbAdapter(client, credentials).deposit_addresses),
            ("CoinGecko", CoinGeckoAdapter(client, credentials, settings.coingecko.plan).ping),
        ]
        if include_rpc:
            operations.extend(
                (f"RPC[{chain}]", RpcAdapter(client, url, chain).check_chain)
                for chain, url in sorted(
                    credentials.rpc_urls.items(), key=lambda pair: int(pair[0])
                )
            )
        # Once per service. No hidden retries, address generation or payment.
        results = []
        for service, operation in operations:
            results.append(await check(service, operation))
        return results


def render(results: list[Result], dry_run: bool, json_output: bool = False) -> str:
    if json_output:
        return json.dumps(
            {"dry_run": dry_run, "read_only": True, "results": [asdict(r) for r in results]},
            ensure_ascii=False,
            indent=2,
        )
    lines = [
        f"DRY_RUN={str(dry_run).lower()} | M0 READ-ONLY (트랜잭션 서명/전송 없음)",
        "서비스          결과    분류                   안내",
    ]
    for r in results:
        lines.append(f"{r.service:<15} {'PASS' if r.ok else 'FAIL':<7} {r.category:<22} {r.detail}")
    passed = sum(r.ok for r in results)
    lines.append(f"결과: {passed}/{len(results)} PASS")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--json", action="store_true", help="Only sanitized diagnostic results")
    parser.add_argument("--skip-rpc", action="store_true", help="Run only the four service checks")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.settings)
        credentials = load_credentials(args.env_file)
        results = asyncio.run(run_checks(settings, credentials, include_rpc=not args.skip_rpc))
        print(render(results, credentials.dry_run, args.json))
        return 0 if all(result.ok for result in results) else 1
    except ConfigError as exc:
        print(
            json.dumps(
                {"ok": False, "category": "configuration_error", "detail": str(exc)},
                ensure_ascii=False,
            )
        )
        return 2
    except KeyboardInterrupt:
        print("연결 검증을 중단했습니다.")
        return 130
    except Exception:  # noqa: BLE001 - CLI boundary must never expose secret-bearing exceptions.
        print(
            "내부 오류로 검증을 완료하지 못했습니다. 비밀값을 포함할 수 있는 예외는 출력하지 않습니다."
        )
        return 2
