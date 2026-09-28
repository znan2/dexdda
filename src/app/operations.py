"""Isolated new-listing checks and reference FX. Neither can change trading inputs."""

import asyncio
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from decimal import Decimal

from app.adapters.base import AdapterError, Failure
from app.adapters.bithumb import BithumbAdapter
from app.adapters.prices import number
from app.adapters.upbit import UpbitAdapter
from app.config import PROJECT_ROOT
from app.market_watch import MarketWatch

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


class EventLog:
    """Only allow enumerated fields; never attempt to regex-redact arbitrary exceptions."""

    def __init__(self, path=None):
        self.path = path or PROJECT_ROOT / "data/operations.jsonl"

    def event(self, event, **fields):
        allowed = {"operation_id", "status", "stage", "chain_index", "error", "count"}
        record = {"time_ms": int(time.time() * 1000), "event": event}
        for key, value in fields.items():
            if key in allowed and (
                type(value) is int
                or isinstance(value, str)
                and re.fullmatch(r"[a-zA-Z0-9_:-]{1,100}", value)
            ):
                record[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, (json.dumps(record, separators=(",", ":")) + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)


def parse_ecb(content, today=None):
    if len(content) > 200_000 or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise AdapterError(Failure.SCHEMA)
    try:
        root = ET.fromstring(content)
        dated = [n for n in root.iter() if "time" in n.attrib]
        if len(dated) != 1:
            raise ValueError()
        day = date.fromisoformat(dated[0].attrib["time"])
        today = today or datetime.now(UTC).date()
        if not 0 <= (today - day).days <= 7:
            raise ValueError()
        rates = {
            n.attrib["currency"]: number(n.attrib["rate"])
            for n in dated[0]
            if "currency" in n.attrib
        }
        rate = rates["KRW"] / rates["USD"]
        return {
            "usd_krw": format(rate, "f"),
            "date": day.isoformat(),
            "source": "ECB 일일 기준환율",
            "delayed": day != today,
            "reference_only": True,
        }
    except (ValueError, KeyError, ET.ParseError):
        raise AdapterError(Failure.SCHEMA) from None


class Operations:
    def __init__(self, collector, *, log=None, listing_path=None):
        self.collector = collector
        self.log = log or EventLog()
        self.market_watch = MarketWatch(collector.registry, listing_path)
        self.markets = {
            ex: {"new": [], "mapping_pending": [], "checked_at_ms": None, "error": None}
            for ex in ("upbit", "bithumb")
        }
        self.fx = None
        self.fx_error = None
        self.tasks = []

    async def listings(self):
        c = self.collector
        for exchange, cls in (("upbit", UpbitAdapter), ("bithumb", BithumbAdapter)):
            try:
                adapter = cls(c.client, c.credentials)
                rows = await c.cex_adapters[exchange].gate.run(adapter.markets)
                incoming = {r["market"] for r in rows}
                observed = self.market_watch.observe(exchange, incoming)
                new = observed["new"]
                if new != self.markets[exchange]["new"]:
                    self.log.event("listing_change", stage=exchange, count=len(new))
                self.markets[exchange] = {
                    **observed,
                    "checked_at_ms": int(time.time() * 1000),
                    "error": None,
                }
            except Exception as exc:  # noqa: BLE001 - isolate optional data source.
                code = exc.category.value if isinstance(exc, AdapterError) else "internal_error"
                self.markets[exchange]["error"] = code
                self.log.event("listing_error", stage=exchange, error=code)

    async def reference_fx(self):
        try:
            response = await self.collector.client.get(ECB_URL, follow_redirects=False)
            if not response.is_success:
                raise AdapterError(Failure.HTTP)
            self.fx = parse_ecb(response.content)
            self.fx_error = None
        except Exception as exc:  # noqa: BLE001 - isolate optional data source.
            self.fx = None
            self.fx_error = exc.category.value if isinstance(exc, AdapterError) else "network_error"
            self.log.event("fx_error", error=self.fx_error)

    async def periodic(self, operation, interval):
        while True:
            await operation()
            await asyncio.sleep(interval)

    def start(self):
        settings = self.collector.settings.operations
        self.tasks = [
            asyncio.create_task(self.periodic(self.listings, settings.market_check_seconds))
        ]
        if settings.reference_fx_enabled:
            self.tasks.append(
                asyncio.create_task(self.periodic(self.reference_fx, settings.fx_check_seconds))
            )

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def snapshot(self):
        premium = None
        usdt = self.collector.fx_reference()
        if self.fx and usdt["usable"]:
            price = self.collector.fx.values["usdt"].price.value
            premium = format((price / Decimal(self.fx["usd_krw"]) - 1) * 100, "f")
        return {
            "markets": self.markets,
            "reference_fx": self.fx,
            "fx_error": self.fx_error,
            "reference_premium_percent": premium,
            "usdt_reused": usdt["reused"],
            "does_not_affect_gap": True,
        }
