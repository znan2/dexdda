"""In-memory receipts use a monotonic clock; exchange timestamps prevent replay."""

import time
from dataclasses import dataclass

from app.adapters.prices import Price
from app.pricing.gap import decimal_text

SOURCE_CLOCK_SKEW_MS = 5_000


class Clock:
    def monotonic(self) -> float:
        return time.monotonic()

    def milliseconds(self) -> int:
        return time.time_ns() // 1_000_000


@dataclass(frozen=True)
class Receipt:
    price: Price
    received_ms: int
    received_at: float
    source: str


class Quotes:
    def __init__(self, clock: Clock, *, source_age=True):
        self.clock = clock
        self.check_source_age = source_age
        self.values = {}
        self.errors = {}
        self.attempted_at_ms = {}

    def put(self, key, price: Price, source: str, *, started_at: float | None = None) -> bool:
        old = self.values.get(key)
        if price.source_ms > self.clock.milliseconds() + SOURCE_CLOCK_SKEW_MS:
            self.fail([key], "timestamp_invalid", started_at=started_at)
            return False
        if old:
            # A REST ticker is a current snapshot, but its time may denote the last
            # trade. Prefer a WS receipt received during the in-flight REST call.
            if started_at is not None:
                if old.received_at > started_at:
                    return False
            elif old.price.source_ms > price.source_ms:
                return False
        self.attempted_at_ms[key] = self.clock.milliseconds()
        self.values[key] = Receipt(price, self.clock.milliseconds(), self.clock.monotonic(), source)
        self.errors.pop(key, None)
        return True

    def fail(self, keys, category: str, *, started_at: float | None = None):
        for key in keys:
            old = self.values.get(key)
            # A failed REST response must not invalidate a newer WS receipt.
            if started_at is None or old is None or old.received_at <= started_at:
                self.errors[key] = category
                self.attempted_at_ms[key] = self.clock.milliseconds()

    def fresh(self, key, ttl: float, *, source_age=None) -> bool:
        receipt = self.values.get(key)
        if receipt is None or key in self.errors:
            return False
        if self.clock.monotonic() - receipt.received_at > ttl:
            return False
        if source_age is None:
            source_age = self.check_source_age
        return not source_age or (
            -SOURCE_CLOCK_SKEW_MS
            <= self.clock.milliseconds() - receipt.price.source_ms
            <= ttl * 1000
        )

    def public(self, key, ttl: float, *, source_age=None) -> dict:
        receipt = self.values.get(key)
        fresh = self.fresh(key, ttl, source_age=source_age)
        return {
            "value": decimal_text(receipt.price.value) if receipt else None,
            "received_at_ms": receipt.received_ms if receipt else None,
            "source_at_ms": receipt.price.source_ms if receipt else None,
            "provider_timestamp_offset_ms": receipt.price.timestamp_offset_ms if receipt else None,
            "source": receipt.source if receipt else None,
            "stale": not fresh,
            "error": self.errors.get(key) or (None if fresh else "stale" if receipt else "missing"),
        }
