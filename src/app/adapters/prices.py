"""Read-only market data. Retain only known identities, numbers and timestamps."""

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from app.config import Credentials

from .base import AdapterError, Failure, classify, request_json
from .okx import BASE_URL, auth_headers

TokenKey = tuple[str, str]
WS_URLS = {
    "upbit": "wss://api.upbit.com/websocket/v1",
    "bithumb": "wss://ws-api.bithumb.com/websocket/v1",
}


def number(value, *, zero=False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise AdapterError(Failure.SCHEMA)
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise AdapterError(Failure.SCHEMA) from None
    if not result.is_finite() or result < 0 or (not zero and result == 0):
        raise AdapterError(Failure.SCHEMA)
    # Bound pathological provider inputs before arithmetic/JSON formatting.
    if result and not -100 <= result.adjusted() <= 100:
        raise AdapterError(Failure.SCHEMA)
    return result


def timestamp(value) -> int:
    if type(value) is int:
        result = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        result = int(value)
    else:
        raise AdapterError(Failure.SCHEMA)
    if not 1_000_000_000_000 <= result < 10_000_000_000_000:
        raise AdapterError(Failure.SCHEMA)
    return result


@dataclass(frozen=True)
class Price:
    value: Decimal
    source_ms: int
    timestamp_offset_ms: int = 0


class Gate:
    """Serialize a provider's calls, including a shared cooldown after rate limits."""

    def __init__(self, interval: float):
        self.interval = interval
        self.lock = asyncio.Lock()
        self.next_at = 0.0
        self.failures = 0

    async def run(self, operation):
        async with self.lock:
            await asyncio.sleep(max(0, self.next_at - time.monotonic()))
            self.next_at = time.monotonic() + self.interval
            try:
                result = await operation()
            except AdapterError as exc:
                if exc.category == Failure.LIMIT:
                    self.failures = min(self.failures + 1, 5)
                    self.next_at = time.monotonic() + min(60, 2**self.failures)
                elif exc.category in (Failure.AUTH, Failure.PERMISSION, Failure.PAYMENT):
                    self.next_at = time.monotonic() + 60
                raise
            self.failures = 0
            return result


class PriceBatch(dict):
    """Valid quotes plus sanitized, per-token reasons for incomplete responses."""

    def __init__(self):
        super().__init__()
        self.errors: dict[TokenKey, str] = {}


class OkxPrices:
    def __init__(self, client: httpx.AsyncClient, credentials: Credentials, interval: float):
        self.client = client
        self.credentials = credentials
        self.gate = Gate(interval)

    async def fetch(self, keys: list[TokenKey], *, liquidity=False) -> dict[TokenKey, Price]:
        if not keys:
            return {}
        if len(keys) > 100 or len(set(keys)) != len(keys):
            raise AdapterError(Failure.SCHEMA)
        path = "/api/v6/dex/market/" + ("price-info" if liquidity else "price")
        body = json.dumps(
            [{"chainIndex": c, "tokenContractAddress": a} for c, a in keys],
            separators=(",", ":"),
        )

        async def operation():
            headers = auth_headers(self.credentials, "POST", path, body)
            headers["Content-Type"] = "application/json"
            payload = await request_json(
                self.client,
                "POST",
                BASE_URL + path,
                headers=headers,
                content=body.encode(),
                decimal_numbers=True,
            )
            if not isinstance(payload, dict) or "code" not in payload:
                raise AdapterError(Failure.SCHEMA)
            if str(payload["code"]) != "0":
                raise AdapterError(classify(200, payload))
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise AdapterError(Failure.SCHEMA)
            expected, seen, result = set(keys), set(), PriceBatch()
            for row in rows:
                if not isinstance(row, dict):
                    raise AdapterError(Failure.SCHEMA)
                address = row.get("tokenContractAddress")
                key = (
                    str(row.get("chainIndex")),
                    address.lower() if isinstance(address, str) else "",
                )
                if key not in expected or key in seen:
                    raise AdapterError(Failure.SCHEMA)
                seen.add(key)
                field = "liquidity" if liquidity else "price"
                if row.get(field) in (None, ""):
                    result.errors[key] = "value_missing"
                    continue
                try:
                    value = number(row[field], zero=liquidity)
                except AdapterError:
                    result.errors[key] = "value_invalid"
                    continue
                if row.get("time") in (None, ""):
                    result.errors[key] = "source_time_missing"
                    continue
                try:
                    source_ms = timestamp(row["time"])
                except AdapterError:
                    result.errors[key] = "source_time_invalid"
                    continue
                result[key] = Price(value, source_ms)
            for key in expected - seen:
                result.errors[key] = "token_not_returned"
            return result

        return await self.gate.run(operation)


def bithumb_rest_time(row) -> tuple[int, int]:
    """Normalize the observed KST epoch only when UTC trade fields corroborate it.

    Live v1 ticker numeric times can encode UTC+9, unlike modern WS timestamps.
    Accept a future provider fix to UTC as well; never subtract nine hours blindly.
    """
    source = timestamp(row.get("timestamp"))
    date, clock = row.get("trade_date"), row.get("trade_time")
    if date is None and clock is None:
        return source, 0
    if not isinstance(date, str) or not isinstance(clock, str):
        raise AdapterError(Failure.SCHEMA)
    try:
        utc_seconds = datetime.strptime(date + clock, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        raise AdapterError(Failure.SCHEMA) from None
    trade_ms = timestamp(row.get("trade_timestamp"))
    difference = trade_ms - int(utc_seconds.timestamp()) * 1000
    for offset in (0, 9 * 60 * 60 * 1000):
        if 0 <= difference - offset < 1000:
            return timestamp(source - offset), offset
    raise AdapterError(Failure.SCHEMA)


def parse_tickers(
    rows, expected: set[str], *, websocket=False, bithumb_rest=False
) -> dict[str, Price]:
    if not isinstance(rows, list):
        raise AdapterError(Failure.SCHEMA)
    result, seen = {}, set()
    for row in rows:
        if not isinstance(row, dict):
            raise AdapterError(Failure.SCHEMA)
        market = row.get("code" if websocket else "market")
        if not isinstance(market, str) or market not in expected or market in seen:
            raise AdapterError(Failure.SCHEMA)
        seen.add(market)
        try:
            source_ms, offset = (
                bithumb_rest_time(row) if bithumb_rest else (timestamp(row.get("timestamp")), 0)
            )
            result[market] = Price(number(row.get("trade_price")), source_ms, offset)
        except AdapterError:
            continue
    return result


def parse_orderbook(row, *, websocket=False) -> Price:
    if not isinstance(row, dict) or row.get("code" if websocket else "market") != "KRW-USDT":
        raise AdapterError(Failure.SCHEMA)
    units = row.get("orderbook_units")
    if not isinstance(units, list) or not units or not isinstance(units[0], dict):
        raise AdapterError(Failure.SCHEMA)
    # Ask 1, never the USDT last trade or the bid.
    return Price(number(units[0].get("ask_price")), timestamp(row.get("timestamp")))


def decode_ws(raw):
    try:
        value = json.loads(raw, parse_float=Decimal)
    except (ValueError, TypeError):
        raise AdapterError(Failure.SCHEMA) from None
    if not isinstance(value, dict):
        raise AdapterError(Failure.SCHEMA)
    if value.get("error"):
        raise AdapterError(classify(200, value))
    return value


class CexPrices:
    def __init__(self, client: httpx.AsyncClient, exchange: str, interval: float):
        if exchange not in WS_URLS:
            raise ValueError("unsupported exchange")
        self.client, self.exchange = client, exchange
        self.gate = Gate(interval)

    async def tickers(self, markets: list[str]) -> dict[str, Price]:
        async def operation():
            rows = await request_json(
                self.client,
                "GET",
                f"https://api.{self.exchange}.com/v1/ticker",
                params={"markets": ",".join(markets)},
                decimal_numbers=True,
            )
            return parse_tickers(rows, set(markets), bithumb_rest=self.exchange == "bithumb")

        return await self.gate.run(operation)

    async def usdt_ask(self) -> Price:
        if self.exchange != "upbit":
            raise ValueError("FX source must be upbit")

        async def operation():
            rows = await request_json(
                self.client,
                "GET",
                "https://api.upbit.com/v1/orderbook",
                params={"markets": "KRW-USDT", "level": "0", "count": "1"},
                decimal_numbers=True,
            )
            if not isinstance(rows, list) or len(rows) != 1:
                raise AdapterError(Failure.SCHEMA)
            return parse_orderbook(rows[0])

        return await self.gate.run(operation)
