"""Independent live exchange wallet snapshots; registry networks are identities only."""

import asyncio

from app.adapters.base import AdapterError
from app.adapters.bithumb import BithumbAdapter
from app.adapters.upbit import UpbitAdapter


class WalletStatus:
    def __init__(self, collector):
        self.collector = collector
        self.clock = collector.clock
        self.states = {}
        self.task = None
        self.last_refresh = None

    def observe(self, exchange, rows, started, error=None):
        old = self.states.get(exchange)
        if old and old["started"] > started:
            return
        indexed = {}
        for row in rows or []:
            key = (row["currency"], row["net_type"])
            if key in indexed:
                error = "unexpected_response"
                break
            indexed[key] = dict(row)
        self.states[exchange] = {
            "rows": old["rows"] if error and old else indexed,
            "started": started,
            "checked_at_ms": old["checked_at_ms"]
            if error and old
            else (None if error else self.clock.milliseconds()),
            "received": old["received"]
            if error and old
            else (None if error else self.clock.monotonic()),
            "attempted_at_ms": self.clock.milliseconds(),
            "error": error,
        }

    def status(self, exchange):
        data = self.states.get(exchange, {})
        received = data.get("received")
        age = max(0, self.clock.monotonic() - received) if received is not None else None
        stale = (
            age is None
            or age > self.collector.settings.pricing.wallet_stale_seconds
            or bool(data.get("error"))
        )
        return {
            "checked_at_ms": data.get("checked_at_ms"),
            "attempted_at_ms": data.get("attempted_at_ms"),
            "age_seconds": age,
            "stale": stale,
            "error": data.get("error")
            or ("missing" if age is None else "stale" if stale else None),
        }

    def networks(self, coin, exchange):
        listing = coin.exchanges[exchange]
        state = self.status(exchange)
        rows = self.states.get(exchange, {}).get("rows", {})
        result = []
        for network in listing.networks:
            current = rows.get((listing.symbol, network["net_type"]))
            exclusion = self.collector.preferences.network(
                coin.coin_id, exchange, network["net_type"]
            )
            result.append(
                {
                    **network,
                    **state,
                    "wallet_state": current["wallet_state"]
                    if current and not state["stale"]
                    else None,
                    "previous_wallet_state": current["wallet_state"] if current else None,
                    "error": state["error"]
                    or ("network_not_returned" if current is None else None),
                    "manual_excluded": exclusion is not None,
                    "exclusion_reason": exclusion.reason if exclusion else None,
                }
            )
        return result

    async def poll(self, exchange):
        c = self.collector
        started = self.clock.monotonic()
        try:
            adapter = UpbitAdapter if exchange == "upbit" else BithumbAdapter
            rows = await c.cex_adapters[exchange].gate.run(
                lambda: adapter(c.client, c.credentials).wallets()
            )
            self.observe(exchange, rows, started)
        except AdapterError as exc:
            self.observe(exchange, None, started, exc.category.value)
        except Exception:  # noqa: BLE001 - never expose credentials or remote error text.
            self.observe(exchange, None, started, "internal_error")

    async def refresh(self):
        if self.task and not self.task.done():
            await asyncio.shield(self.task)
            return
        if self.last_refresh is not None and self.clock.monotonic() - self.last_refresh < 10:
            return

        async def run():
            self.last_refresh = self.clock.monotonic()
            await asyncio.gather(*(self.poll(ex) for ex in self.collector.markets))

        self.task = asyncio.create_task(run())
        await asyncio.shield(self.task)

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
