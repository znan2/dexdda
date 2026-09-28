"""Whitelisted public catalog fields; never persist account payloads."""

import asyncio
import time

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .base import AdapterError, Failure


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class Market(PublicModel):
    market: str
    korean_name: str
    english_name: str


class Wallet(PublicModel):
    currency: str
    net_type: str
    network_name: str
    wallet_state: str


class Ticker(PublicModel):
    base: str
    target: str
    coin_id: str | None = None


class Coin(PublicModel):
    id: str
    symbol: str
    name: str
    platforms: dict[str, str | None]


class Platform(PublicModel):
    id: str
    chain_identifier: int | None
    name: str
    native_coin_id: str | None = None


class Chain(PublicModel):
    chainIndex: str
    chainName: str


class Tags(PublicModel):
    communityRecognized: bool | None = None


class TokenInfo(PublicModel):
    chainIndex: str
    tokenContractAddress: str
    tokenName: str
    tokenSymbol: str
    decimal: str
    tagList: Tags = Field(default_factory=Tags)


def parse_rows(model: type[PublicModel], data, *, nonempty=False) -> list[dict]:
    if not isinstance(data, list) or (nonempty and not data):
        raise AdapterError(Failure.SCHEMA)
    result = []
    try:
        for raw in data:
            if model in (Chain, TokenInfo) and isinstance(raw, dict):
                raw = dict(raw)
                index = raw.get("chainIndex")
                if type(index) is int and index >= 0:
                    raw["chainIndex"] = str(index)
            row = model.model_validate(raw)
            result.append(row.model_dump())
    except (ValidationError, ValueError):
        raise AdapterError(Failure.SCHEMA) from None
    return result


class Pace:
    """Sequential per-provider pacing, including bounded rate-limit retries."""

    def __init__(self, interval: float):
        self.interval = interval
        self.last = 0.0

    async def run(self, operation):
        for attempt in range(3):
            await asyncio.sleep(max(0, self.last + self.interval - time.monotonic()))
            self.last = time.monotonic()
            try:
                return await operation()
            except AdapterError as exc:
                if exc.category != Failure.LIMIT or attempt == 2:
                    raise
                await asyncio.sleep(5 * (attempt + 1))
