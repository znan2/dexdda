"""Bounded background workers; API reads never make external requests."""

import asyncio
import json
import logging
from collections import Counter
from decimal import Decimal
from uuid import uuid4

import httpx
from websockets.asyncio.client import connect

from app.adapters.base import AdapterError, Failure
from app.adapters.prices import (
    WS_URLS,
    CexPrices,
    OkxPrices,
    decode_ws,
    parse_orderbook,
    parse_tickers,
)
from app.balances import WalletBalances
from app.config import PROJECT_ROOT, Credentials, Settings
from app.logos import Logos
from app.preferences import Preferences
from app.registry.loader import load_config
from app.registry.models import ChainMap, Registry
from app.smoke import quiet_transport_logs

from .comparison import coin_groups
from .filters import liquidity_warnings, suspected
from .gap import decimal_text, surface_gap
from .liquidity import liquidity_reference
from .state import Clock, Quotes
from .wallets import WalletStatus


def token_key(token):
    return token.chain_index, token.address


def batches(values, size):
    values = list(values)
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


class Collector:
    def __init__(
        self,
        registry: Registry,
        settings: Settings,
        credentials: Credentials,
        *,
        clock=None,
        client=None,
        ws_connect=connect,
        preferences_path=None,
        logos_path=None,
    ):
        self.registry, self.settings = registry, settings
        self.chain_names = {
            str(c["chain_index"]): c["name"] for c in getattr(registry, "chains", [])
        }
        self.clock = clock or Clock()
        self.preferences = Preferences(registry, preferences_path)
        self.network_map = {
            (n.exchange, n.net_type): n.chain_index
            for n in load_config(PROJECT_ROOT / "config/chain_map.toml", ChainMap).networks
        }
        self.wallet_status = WalletStatus(self)
        self.logos = Logos(self, logos_path)
        self.balances = WalletBalances(self)
        self.dry_run = credentials.dry_run
        self.client = client
        self.owns_client = client is None
        self.credentials = credentials
        self.ws_connect = ws_connect
        self.tasks = []
        self.running = False
        self.dex, self.liquidity = Quotes(self.clock), Quotes(self.clock)
        self.cex, self.fx = Quotes(self.clock, source_age=False), Quotes(self.clock)
        self.main_routes, self.bridge_groups = [], {}
        self.markets = {"upbit": set(), "bithumb": set()}
        self.active_keys = set()
        self.stats = {}
        self.cycles = {}
        for coin in registry.coins:
            if coin.excluded_reason:
                continue
            for token in coin.tokens:
                statuses = set(token.status.values())
                if "tradable" in statuses:
                    self.main_routes.append((coin, token))
                elif "bridge_candidate" in statuses:
                    self.bridge_groups.setdefault(coin.coin_id, []).append((coin, token))
                else:
                    continue
                self.active_keys.add(token_key(token))
                for exchange, listing in coin.exchanges.items():
                    if token.status.get(exchange) != "excluded":
                        self.markets[exchange].update(
                            market for market in listing.markets if market.startswith("KRW-")
                        )
        self.main_keys = {token_key(token) for _, token in self.main_routes}

    def record(self, name, error=None):
        entry = self.stats.setdefault(
            name,
            {
                "successes": 0,
                "failures": 0,
                "last_error": None,
                "last_success_at_ms": None,
                "last_failure_at_ms": None,
            },
        )
        if error:
            entry["failures"] += 1
            entry["last_failure_at_ms"] = self.clock.milliseconds()
        else:
            entry["successes"] += 1
            entry["last_success_at_ms"] = self.clock.milliseconds()
        entry["last_error"] = error

    def selected_bridges(self):
        selected, pending = [], []
        for group in self.bridge_groups.values():
            # Previously observed liquidity remains useful for route discovery.
            if not all(self.liquidity_reference(token_key(t))["usable"] for _, t in group):
                pending.append(group)
                continue
            selected.append(
                min(
                    group,
                    key=lambda pair: (
                        -self.liquidity.values[token_key(pair[1])].price.value,
                        int(pair[1].chain_index),
                        pair[1].address,
                    ),
                )
            )
        return selected, pending

    async def start(self):
        if self.running:
            return
        quiet_transport_logs()
        logger = logging.getLogger("websockets")
        logger.setLevel(logging.CRITICAL + 1)
        logger.propagate = False
        self.client = self.client or httpx.AsyncClient(
            timeout=self.settings.http.timeout_seconds,
            trust_env=False,
            limits=httpx.Limits(max_connections=16),
        )
        self.okx = OkxPrices(
            self.client, self.credentials, self.settings.pricing.okx_interval_seconds
        )
        self.cex_adapters = {
            ex: CexPrices(self.client, ex, self.settings.pricing.rest_interval_seconds)
            for ex in self.markets
        }
        self.running = True
        self.tasks = [
            asyncio.create_task(self.periodic("wallet_balances", self.balances.refresh, 60)),
            asyncio.create_task(
                self.periodic(
                    "wallet_status",
                    self.wallet_status.refresh,
                    self.settings.pricing.wallet_poll_seconds,
                )
            ),
            asyncio.create_task(
                self.periodic("okx_price", self.poll_prices, self.settings.pricing.poll_seconds)
            ),
            asyncio.create_task(
                self.periodic(
                    "okx_liquidity",
                    self.poll_liquidity,
                    self.settings.pricing.liquidity_poll_seconds,
                )
            ),
        ]
        if self.logos.path:
            self.tasks.append(asyncio.create_task(self.periodic("logos", self.logos.refresh, 3600)))
        for exchange, markets in self.markets.items():
            groups = list(batches(sorted(markets), self.settings.pricing.ws_batch_size))
            if exchange == "upbit" and not groups:
                groups = [[]]
            for i, group in enumerate(groups):
                self.tasks.append(
                    asyncio.create_task(
                        self.websocket(
                            exchange,
                            group,
                            include_fx=exchange == "upbit" and i == 0,
                            index=i,
                        )
                    )
                )
            self.tasks.append(asyncio.create_task(self.rest_loop(exchange)))

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        await self.wallet_status.stop()
        await self.balances.stop()
        self.running = False
        if self.owns_client and self.client:
            await self.client.aclose()
            self.client = None

    async def periodic(self, name, operation, interval):
        while True:
            began = self.clock.monotonic()
            try:
                await operation()
            except AdapterError as exc:
                self.record(name, exc.category.value)
            except Exception:  # noqa: BLE001 - isolate workers and sanitize raw exceptions.
                # Worker boundary: keep the process alive, never expose raw exceptions.
                self.record(name, Failure.INTERNAL.value)
            elapsed = self.clock.monotonic() - began
            self.cycles[name] = {
                "last_duration_seconds": round(elapsed, 3),
                "configured_interval_seconds": interval,
                "interval_exceeded": elapsed > interval,
            }
            await asyncio.sleep(max(0.1, interval - elapsed))

    async def poll_okx(self, keys, *, liquidity=False):
        quotes = self.liquidity if liquidity else self.dex
        name = "okx_liquidity" if liquidity else "okx_price"
        for batch in batches(sorted(keys), self.settings.pricing.okx_batch_size):
            started = self.clock.monotonic()
            try:
                result = await self.okx.fetch(batch, liquidity=liquidity)
            except AdapterError as exc:
                quotes.fail(batch, exc.category.value, started_at=started)
                self.record(name, exc.category.value)
                continue
            for key, price in result.items():
                quotes.put(key, price, name)
            for key in set(batch) - result.keys():
                reason = getattr(result, "errors", {}).get(key, "missing_response")
                quotes.fail([key], reason, started_at=started)
            self.record(name)

    def comparison_price_keys(self):
        return {
            (g["routes"][0]["chain_index"], g["routes"][0]["address"])
            for g in coin_groups(self)
            if g["routes"][0]["liquidity_usd"]["usable"]
        }

    async def poll_prices(self):
        selected, _ = self.selected_bridges()
        await self.poll_okx(
            self.main_keys | {token_key(t) for _, t in selected} | self.comparison_price_keys()
        )

    async def poll_liquidity(self):
        await self.poll_okx(self.active_keys, liquidity=True)

    async def rest_loop(self, exchange):
        # Give WS snapshots the first opportunity; REST fills missing/stale markets only.
        await asyncio.sleep(1)
        await self.periodic(
            exchange + "_rest_loop",
            lambda: self.poll_rest(exchange),
            self.settings.pricing.poll_seconds,
        )

    async def poll_rest(self, exchange):
        ttl = self.settings.pricing.stale_seconds
        adapter = self.cex_adapters[exchange]
        if exchange == "upbit" and not self.fx.fresh(
            "usdt", self.settings.pricing.usdt_stale_seconds
        ):
            started = self.clock.monotonic()
            try:
                self.fx.put("usdt", await adapter.usdt_ask(), "upbit_rest_orderbook")
                self.record("upbit_rest_orderbook")
            except AdapterError as exc:
                self.fx.fail(["usdt"], exc.category.value, started_at=started)
                self.record("upbit_rest_orderbook", exc.category.value)
        needed = sorted(m for m in self.markets[exchange] if not self.cex.fresh((exchange, m), ttl))
        for group in batches(needed, self.settings.pricing.rest_batch_size):
            # A WS receipt may have arrived while waiting for an earlier REST batch.
            group = [m for m in group if not self.cex.fresh((exchange, m), ttl)]
            if not group:
                continue
            started = self.clock.monotonic()
            name = exchange + "_rest"
            try:
                result = await adapter.tickers(group)
            except AdapterError as exc:
                self.cex.fail(
                    [(exchange, m) for m in group], exc.category.value, started_at=started
                )
                self.record(name, exc.category.value)
                continue
            for market, price in result.items():
                self.cex.put((exchange, market), price, name, started_at=started)
            self.cex.fail(
                [(exchange, m) for m in set(group) - result.keys()],
                "missing_response",
                started_at=started,
            )
            self.record(name)

    def ws_message(self, exchange, markets, raw, *, include_fx=False):
        data = decode_ws(raw)
        if data.get("type") == "ticker":
            result = parse_tickers([data], set(markets), websocket=True)
            if not result:
                raise AdapterError(Failure.SCHEMA)
            for market, price in result.items():
                self.cex.put((exchange, market), price, exchange + "_ws")
        elif include_fx and data.get("type") == "orderbook":
            self.fx.put("usdt", parse_orderbook(data, websocket=True), "upbit_ws_orderbook")
        elif data.get("status") == "UP":
            return
        else:
            raise AdapterError(Failure.SCHEMA)

    async def websocket(self, exchange, markets, *, include_fx=False, index=0):
        name = f"{exchange}_ws_{index}"
        delay = 1
        await asyncio.sleep(index * 0.3)
        while True:
            opened = self.clock.monotonic()
            try:
                async with self.ws_connect(
                    WS_URLS[exchange],
                    proxy=None,
                    open_timeout=10,
                    close_timeout=2,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2**20,
                ) as socket:
                    subscription = [{"ticket": str(uuid4())}]
                    if markets:
                        subscription.append({"type": "ticker", "codes": markets})
                    if include_fx:
                        subscription.append({"type": "orderbook", "codes": ["KRW-USDT"]})
                    subscription.append({"format": "DEFAULT"})
                    await socket.send(json.dumps(subscription))
                    async for raw in socket:
                        self.ws_message(exchange, markets, raw, include_fx=include_fx)
                        self.record(name)
                error = Failure.NETWORK.value
            except AdapterError as exc:
                error = exc.category.value
            except Exception:  # noqa: BLE001 - isolate workers and sanitize raw exceptions.
                error = Failure.NETWORK.value
            self.record(name, error)
            # A WS disconnect does not invalidate a successful REST replacement.
            self.cex.fail(
                [
                    (exchange, m)
                    for m in markets
                    if (receipt := self.cex.values.get((exchange, m))) is None
                    or receipt.source == exchange + "_ws"
                ],
                error,
            )
            receipt = self.fx.values.get("usdt")
            if include_fx and (receipt is None or receipt.source == "upbit_ws_orderbook"):
                self.fx.fail(["usdt"], error)
            if self.clock.monotonic() - opened >= 30:
                delay = 1
            await asyncio.sleep(delay)
            delay = min(30, delay * 2)

    def fx_reference(self):
        data = self.fx.public("usdt", self.settings.pricing.usdt_stale_seconds)
        receipt = self.fx.values.get("usdt")
        usable = bool(receipt and receipt.price.value.is_finite() and receipt.price.value > 0)
        return {
            **data,
            "usable": usable,
            "reused": usable and data["stale"],
            "fresh_seconds": self.settings.pricing.usdt_stale_seconds,
        }

    def liquidity_reference(self, key):
        return liquidity_reference(
            self.liquidity, key, self.settings.pricing.liquidity_stale_seconds
        )

    def row(self, coin, token, section):
        ttl = self.settings.pricing.stale_seconds
        key = token_key(token)
        dex = self.dex.public(key, ttl)
        liq = self.liquidity_reference(key)
        liquidity = self.liquidity.values[key].price.value if liq["usable"] else None
        warnings = liquidity_warnings(liquidity, token.community_recognized, self.settings.filters)
        if liq["reused"]:
            warnings.append("liquidity_cached")
        if section == "bridge_candidates":
            warnings.append("bridge_required_view_only")
        fx = self.fx_reference()
        quotes, gaps = {}, []
        eligible_status = "tradable" if section == "main" else "bridge_candidate"
        for exchange, listing in coin.exchanges.items():
            status = token.status.get(exchange, "excluded")
            markets = sorted(m for m in listing.markets if m.startswith("KRW-"))
            market = markets[0] if markets else None
            cex = self.cex.public((exchange, market), ttl)
            reasons = []
            if (
                status not in {"tradable", "bridge_candidate"}
                if section == "comparison"
                else status != eligible_status
            ):
                reasons.append("route_not_eligible")
            if dex["stale"]:
                reasons.append("dex_stale")
            if cex["stale"]:
                reasons.append("cex_stale")
            if not fx["usable"]:
                reasons.append("usdt_ask_missing")
            gap = None
            if not reasons:
                gap = surface_gap(
                    self.cex.values[(exchange, market)].price.value,
                    self.dex.values[key].price.value,
                    self.fx.values["usdt"].price.value,
                )
                gaps.append(gap)
            quotes[exchange] = {
                "market": market,
                "route_status": status,
                "price_krw": cex,
                "gap": decimal_text(gap),
                "gap_percent": decimal_text(gap * 100) if gap is not None else None,
                "stale": dex["stale"] or cex["stale"] or not fx["usable"],
                "fx_reused": fx["reused"],
                "unavailable_reasons": reasons,
                "deposit_networks": self.wallet_status.networks(coin, exchange),
            }
        for exchange, quote in quotes.items():
            networks = quote["deposit_networks"]
            relevant = [
                n
                for n in networks
                if quote["route_status"] == "bridge_candidate"
                or self.network_map.get((exchange, n["net_type"])) == token.chain_index
            ]
            for network in networks:
                network["applies_to_route"] = network in relevant
            allowed = [n for n in relevant if not n["manual_excluded"]]
            quote["manual_excluded"] = bool(relevant) and not allowed
            quote["deposit_possible"] = (
                True
                if any(n["wallet_state"] in {"working", "deposit_only"} for n in allowed)
                else False
                if relevant
                and (
                    not allowed
                    or all(
                        n["wallet_state"] in {"paused", "withdraw_only", "unsupported"}
                        for n in allowed
                    )
                )
                else None
            )
        return {
            "coin_id": coin.coin_id,
            "symbol": coin.symbol,
            "names": coin.names,
            "chain_index": token.chain_index,
            "chain_name": self.chain_names.get(token.chain_index, token.chain_index),
            "token_name": token.name,
            "address": token.address,
            "native": token.native,
            "dex_usd": dex,
            "liquidity_usd": liq,
            "community_recognized": token.community_recognized,
            "exchanges": quotes,
            "best_gap_percent": decimal_text(max(gaps) * 100) if gaps else None,
            "stale": not gaps,
            "warnings": warnings,
            "origin_section": section,
            "execution_enabled": False,
        }, gaps

    def snapshot(self):
        selected, pending = self.selected_bridges()
        sections = {"main": [], "bridge_candidates": [], "suspected": []}
        hidden = 0
        for section, routes in (("main", self.main_routes), ("bridge_candidates", selected)):
            for coin, token in routes:
                if self.preferences.blocked(coin.coin_id):
                    continue
                row, gaps = self.row(coin, token, section)
                if suspected(gaps, self.settings.filters):
                    row["warnings"].append("gap_out_of_range")
                    sections["suspected"].append(row)
                elif self.settings.filters.unrecognized_action == "hide" and any(
                    w in {"community_unrecognized", "community_recognition_unknown"}
                    for w in row["warnings"]
                ):
                    hidden += 1
                else:
                    sections[section].append(row)
        for group in pending:
            coin = group[0][0]
            if self.preferences.blocked(coin.coin_id):
                continue
            sections["bridge_candidates"].append(
                {
                    "coin_id": coin.coin_id,
                    "symbol": coin.symbol,
                    "names": coin.names,
                    "chain_index": None,
                    "address": None,
                    "exchanges": {
                        ex: {
                            "deposit_networks": self.wallet_status.networks(coin, ex),
                            "route_status": "pending",
                            "gap": None,
                            "gap_percent": None,
                            "stale": True,
                        }
                        for ex, listing in coin.exchanges.items()
                    },
                    "candidate_chain_count": len(group),
                    "best_gap_percent": None,
                    "stale": True,
                    "warnings": ["liquidity_selection_pending", "bridge_required_view_only"],
                    "origin_section": "bridge_candidates",
                    "execution_enabled": False,
                }
            )
        for rows in sections.values():
            rows.sort(
                key=lambda row: (
                    row["best_gap_percent"] is None,
                    -Decimal(row["best_gap_percent"]) if row["best_gap_percent"] is not None else 0,
                    row["coin_id"],
                    row["chain_index"] or "",
                )
            )
        ttl = self.settings.pricing.stale_seconds
        groups = coin_groups(self)
        liquidity_rows = [r["liquidity_usd"] for g in groups for r in g["routes"]]
        return {
            "coin_groups": groups,
            "wallet_status": {ex: self.wallet_status.status(ex) for ex in self.markets},
            "wallet_poll_seconds": self.settings.pricing.wallet_poll_seconds,
            "wallet_stale_seconds": self.settings.pricing.wallet_stale_seconds,
            "preferences": self.preferences.public(),
            "schema_version": 1,
            "milestone": "M3",
            "ui_poll_seconds": self.settings.pricing.ui_poll_seconds,
            "default_amount_usdt": self.settings.pricing.default_amount_usdt,
            "filters": self.settings.filters.model_dump(),
            "generated_at_ms": self.clock.milliseconds(),
            "running": self.running,
            "execution_enabled": False,
            "dry_run": self.dry_run,
            "basis": {
                "fx": "upbit_KRW-USDT_best_ask",
                "usd_usdt_assumption": "1 USD ≈ 1 USDT",
                "cex_fees_included": False,
                "label": "업비트 USDT 기준 · 거래소 수수료 제외",
                "formula": "cex_krw / (dex_usd * upbit_usdt_krw_ask) - 1",
            },
            "usdt_krw_ask": self.fx_reference(),
            **sections,
            "counts": {**{name: len(rows) for name, rows in sections.items()}, "hidden": hidden},
            "diagnostics": {
                "liquidity": {
                    "fresh": sum(q["usable"] and not q["reused"] for q in liquidity_rows),
                    "reused": sum(q["reused"] for q in liquidity_rows),
                    "never_observed": sum(not q["usable"] for q in liquidity_rows),
                    "reasons": dict(Counter(q["error"] for q in liquidity_rows if q["error"])),
                },
                "providers": {key: dict(value) for key, value in self.stats.items()},
                "cycles": {key: dict(value) for key, value in self.cycles.items()},
                "active_tokens": len(self.active_keys),
                "main_tokens": len(self.main_keys),
                "selected_bridge_coins": len(selected),
                "pending_bridge_coins": len(pending),
                "price_query_tokens": len(
                    self.main_keys
                    | {token_key(t) for _, t in selected}
                    | self.comparison_price_keys()
                ),
                "cex_markets": {key: len(value) for key, value in self.markets.items()},
                "cex_quote_sources": dict(
                    Counter(value.source for value in self.cex.values.values())
                ),
                "stale_seconds": ttl,
            },
        }
