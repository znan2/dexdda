"""Prepare all tradable exchange/network deposit addresses; never move funds."""

import argparse
import asyncio
import json

import httpx

from app.config import PROJECT_ROOT, load_credentials
from app.deposit.addresses import AddressAdapter, AddressBook
from app.registry.loader import load_config, load_registry
from app.registry.models import ChainMap
from app.smoke import quiet_transport_logs


async def run(args):
    quiet_transport_logs()
    credentials = load_credentials()
    registry = load_registry(PROJECT_ROOT / "data/registry.json")
    mapping = load_config(PROJECT_ROOT / "config/chain_map.toml", ChainMap)
    book = AddressBook(PROJECT_ROOT / "data/deposit_addresses.json", credentials, registry, mapping)
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        adapters = {ex: AddressAdapter(client, credentials, ex) for ex in ("upbit", "bithumb")}

        def progress(data):
            if data["completed"] % 25 == 0:
                print(json.dumps({"progress": data}), flush=True)

        result = await book.sync(adapters, create=not args.read_only, progress=progress)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["counts"].get("ready", 0) == result["total"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--read-only", action="store_true", help="기존 주소만 동기화; 생성하지 않음"
    )
    args = parser.parse_args()
    try:
        code = asyncio.run(run(args))
    except KeyboardInterrupt:
        code = 130
    except Exception:  # noqa: BLE001 - no provider secrets.
        print('{"error":"address_sync_failed","detail":"원문·주소·비밀값을 출력하지 않습니다."}')
        code = 2
    raise SystemExit(code)
