"""Bounded, read-only M2 live check. Prints counts, never credentials or raw errors."""

import argparse
import asyncio
import json

import httpx

from app.config import PROJECT_ROOT, ConfigError, load_credentials, load_settings
from app.main import create_app
from app.pricing.collector import Collector


async def check(seconds):
    from app.registry.loader import load_registry

    collector = Collector(
        load_registry(PROJECT_ROOT / "data/registry.json"), load_settings(), load_credentials()
    )
    app = create_app(collector)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(seconds)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.get("/api/gaps")
            data = response.json()
    if response.status_code != 200:
        return {"ok": False, "category": "collector_unavailable"}
    rows = data["main"] + data["bridge_candidates"] + data["suspected"]
    fresh = {
        exchange: sum(
            row.get("exchanges", {}).get(exchange, {}).get("gap") is not None for row in rows
        )
        for exchange in ("upbit", "bithumb")
    }
    return {
        "ok": not data["usdt_krw_ask"]["stale"] and all(fresh.values()),
        "read_only": True,
        "duration_seconds": seconds,
        "api_status": response.status_code,
        "counts": data["counts"],
        "fresh_gaps": fresh,
        "usdt_ask_fresh": not data["usdt_krw_ask"]["stale"],
        "diagnostics": data["diagnostics"],
        "all_workers_stopped": not collector.running and not collector.tasks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=35)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 120:
        parser.error("--seconds must be between 5 and 120")
    try:
        result = asyncio.run(check(args.seconds))
    except ConfigError:
        result = {"ok": False, "category": "invalid_config"}
    except Exception:  # noqa: BLE001 - diagnostic boundary, never print raw exceptions.
        result = {"ok": False, "category": "internal_error"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
