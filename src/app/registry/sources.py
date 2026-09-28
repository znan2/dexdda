"""Read-only collection, preserving only normalized public registry fields."""

import hashlib
import json
from pathlib import Path

import httpx

from app.adapters.bithumb import BithumbAdapter
from app.adapters.catalog import (
    Chain,
    Coin,
    Market,
    Platform,
    Ticker,
    TokenInfo,
    Wallet,
    parse_rows,
)
from app.adapters.coingecko import CoinGeckoAdapter
from app.adapters.okx import OkxAdapter
from app.adapters.upbit import UpbitAdapter
from app.config import ConfigError, Credentials, Settings
from app.smoke import quiet_transport_logs


def normalize_sources(data: dict) -> dict:
    schemas = {
        "upbit_markets": Market,
        "bithumb_markets": Market,
        "upbit_wallets": Wallet,
        "bithumb_wallets": Wallet,
        "upbit_tickers": Ticker,
        "bithumb_tickers": Ticker,
        "coins": Coin,
        "platforms": Platform,
        "swap_chains": Chain,
        "market_chains": Chain,
    }
    if not isinstance(data, dict) or set(data) != set(schemas):
        raise ConfigError("원본 스냅샷의 필수 데이터셋을 확인하세요.")
    return {
        name: sorted(parse_rows(model, data[name]), key=lambda r: json.dumps(r, sort_keys=True))
        for name, model in schemas.items()
    }


async def collect(
    settings: Settings,
    credentials: Credentials,
    extra_coin_ids: set[str],
    progress=print,
    transport=None,
) -> dict:
    quiet_transport_logs()
    data = {}
    async with httpx.AsyncClient(
        timeout=settings.http.timeout_seconds,
        trust_env=False,
        follow_redirects=False,
        transport=transport,
    ) as client:
        for name, adapter in [
            ("upbit", UpbitAdapter(client, credentials)),
            ("bithumb", BithumbAdapter(client, credentials)),
        ]:
            data[name + "_markets"] = await adapter.markets()
            data[name + "_wallets"] = await adapter.wallets()
            progress(f"{name}: KRW 마켓·네트워크 수집 완료")
        okx = OkxAdapter(client, credentials)
        okx.interval = settings.registry.okx_interval_seconds
        data["swap_chains"] = await okx.chain_catalog()
        data["market_chains"] = await okx.chain_catalog(market=True)
        progress("OKX: Market·Swap 지원 체인 수집 완료")
        cg = CoinGeckoAdapter(client, credentials, settings.coingecko.plan)
        cg.interval = settings.registry.coingecko_interval_seconds
        if not {"upbit", "bithumb"}.issubset(await cg.exchange_ids()):
            raise ConfigError("CoinGecko 거래소 ID를 확인할 수 없습니다.")
        for name in ("upbit", "bithumb"):
            data[name + "_tickers"] = await cg.tickers(name)
            progress(f"CoinGecko {name}: 전체 티커 페이지 수집 완료")
        data["platforms"] = await cg.platforms()
        wanted = extra_coin_ids | {
            r["coin_id"]
            for name in ("upbit", "bithumb")
            for r in data[name + "_tickers"]
            if r["coin_id"]
        }
        data["coins"] = [r for r in await cg.coins() if r["id"] in wanted]
        progress("CoinGecko: 플랫폼·컨트랙트 수집 완료")
    return normalize_sources(data)


def read_snapshot(path: Path) -> dict:
    try:
        return normalize_sources(json.loads(path.read_text()))
    except (OSError, ValueError):
        raise ConfigError("원본 스냅샷 파일을 읽을 수 없습니다.") from None


def digest(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def fetch_metadata(
    settings: Settings,
    credentials: Credentials,
    keys: list[tuple[str, str]],
    progress=print,
    transport=None,
) -> list[dict]:
    quiet_transport_logs()
    result = []
    batch_size = settings.registry.basic_info_batch_size
    async with httpx.AsyncClient(
        timeout=settings.http.timeout_seconds,
        trust_env=False,
        follow_redirects=False,
        transport=transport,
    ) as client:
        adapter = OkxAdapter(client, credentials)
        adapter.interval = settings.registry.okx_interval_seconds
        for offset in range(0, len(keys), batch_size):
            batch = [
                {"chainIndex": chain, "tokenContractAddress": address}
                for chain, address in keys[offset : offset + batch_size]
            ]
            result.extend(await adapter.basic_info(batch))
            if (
                offset == 0
                or (offset // batch_size + 1) % 10 == 0
                or offset + batch_size >= len(keys)
            ):
                progress(
                    f"OKX 메타데이터: {min(offset + batch_size, len(keys))}/{len(keys)} 조회 완료"
                )
    return sorted(result, key=lambda r: (int(r["chainIndex"]), r["tokenContractAddress"].lower()))


def read_metadata(path: Path):
    try:
        return parse_rows(TokenInfo, json.loads(path.read_text()))
    except (OSError, ValueError):
        raise ConfigError("메타데이터 스냅샷 형식을 확인하세요.") from None
