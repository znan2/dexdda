"""Optional display metadata; never changes token identity or pricing eligibility."""

import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from app.adapters.base import AdapterError, Failure
from app.adapters.coingecko import CoinGeckoAdapter
from app.adapters.okx import OkxAdapter
from app.registry.loader import write_atomic

HOSTS = {
    "coin-images.coingecko.com",
    "assets.coingecko.com",
    "static.oklink.com",
    "static.coinall.ltd",
    "www.okx.com",
    "web3.okx.com",
}
WEEK = 7 * 86400


def safe_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        u = urlsplit(value)
        if (
            u.scheme == "https"
            and u.hostname in HOSTS
            and u.port in (None, 443)
            and not u.username
            and not u.password
            and not any(c.isspace() for c in value)
        ):
            return value
    except ValueError:
        pass
    return None


class Logos:
    def __init__(self, collector, path: Path | None = None, now=time.time):
        self.collector, self.path, self.now = collector, path, now
        self.entries = {}
        self.lock = asyncio.Lock()
        self.coins = {c.coin_id: c for c in collector.registry.coins if not c.excluded_reason}
        self.chains = {str(c["chain_index"]): c for c in getattr(collector.registry, "chains", [])}
        self.keys = {"coin:" + k for k in self.coins} | {"chain:" + k for k in self.chains}
        self.keys |= {self.token_key(t) for c in self.coins.values() for t in c.tokens}
        if path:
            try:
                raw = json.loads(path.read_text())
                for key, entry in raw.get("entries", {}).items():
                    if key not in self.keys or not isinstance(entry, dict):
                        continue
                    checked = entry.get("checked", 0)
                    if type(checked) not in (int, float) or not 0 <= checked <= self.now():
                        continue
                    self.entries[key] = {"url": safe_url(entry.get("url")), "checked": checked}
            except (OSError, ValueError, TypeError, AttributeError):
                pass  # A corrupt optional cache must never prevent startup.

    @staticmethod
    def token_key(token):
        return "token:" + token.chain_index + ":" + token.address.lower()

    def due(self, key):
        entry = self.entries.get(key)
        return not entry or self.now() - entry["checked"] >= (WEEK if entry["url"] else 86400)

    def remember(self, key, value):
        self.entries[key] = {
            "url": safe_url(value) or self.entries.get(key, {}).get("url"),
            "checked": self.now(),
        }

    def save(self):
        if self.path:
            write_atomic(
                self.path, json.dumps({"version": 1, "entries": self.entries}, ensure_ascii=False)
            )

    def public(self):
        result = {"chains": {}, "coins": {}, "tokens": {}, "networks": {}}
        for key, entry in self.entries.items():
            url = safe_url(entry.get("url"))
            if not url:
                continue
            kind, identity = key.split(":", 1)
            result[{"chain": "chains", "coin": "coins", "token": "tokens"}[kind]][identity] = url
        for (exchange, net), chain in self.collector.network_map.items():
            result["networks"].setdefault(exchange, {})[net] = chain
        return result

    async def refresh(self):
        async with self.lock:
            c = self.collector
            cg = CoinGeckoAdapter(c.client, c.credentials, c.settings.coingecko.plan)
            cg.interval = c.settings.registry.coingecko_interval_seconds
            failures = []
            # These few bulk calls avoid competing with frequent OKX price/liquidity calls.
            try:
                due = [k for k in self.chains if self.due("chain:" + k)]
                if due:
                    rows = await cg._get("/asset_platforms")
                    if not isinstance(rows, list):
                        raise AdapterError(Failure.SCHEMA)
                    for chain in due:
                        platforms = self.chains[chain].get("platform_ids", [])
                        matches = [
                            r
                            for r in rows
                            if isinstance(r, dict)
                            and str(r.get("chain_identifier")) == chain
                            and r.get("id") in platforms
                        ]
                        url = next(
                            (
                                safe_url((r.get("image") or {}).get("small"))
                                for r in matches
                                if isinstance(r.get("image"), dict)
                                and safe_url(r["image"].get("small"))
                            ),
                            None,
                        )
                        self.remember("chain:" + chain, url)
            except AdapterError as exc:
                failures.append(exc.category.value)
            try:
                ids = [
                    k
                    for k, coin in self.coins.items()
                    if coin.source == "coingecko" and self.due("coin:" + k)
                ]
                for offset in range(0, len(ids), 100):
                    batch = ids[offset : offset + 100]
                    rows = await cg._get(
                        "/coins/markets",
                        {
                            "vs_currency": "usd",
                            "ids": ",".join(batch),
                            "per_page": 250,
                            "sparkline": "false",
                        },
                    )
                    if not isinstance(rows, list):
                        raise AdapterError(Failure.SCHEMA)
                    by_id = {
                        r.get("id"): r.get("image")
                        for r in rows
                        if isinstance(r, dict) and r.get("id") in batch
                    }
                    for coin_id in batch:
                        self.remember("coin:" + coin_id, by_id.get(coin_id))
                    self.save()  # Preserve progress if another batch fails or shutdown arrives.
            except AdapterError as exc:
                failures.append(exc.category.value)
            # Only coins lacking a verified CoinGecko logo need address-specific OKX fallback.
            tokens = {
                self.token_key(t): t
                for coin in self.coins.values()
                if not self.entries.get("coin:" + coin.coin_id, {}).get("url")
                for t in coin.tokens
                if self.due(self.token_key(t))
            }
            okx = OkxAdapter(c.client, c.credentials)
            okx.interval = 0  # All requests below use the existing shared OKX gate.
            keys = list(tokens)
            try:
                for offset in range(0, len(keys), 50):
                    batch = keys[offset : offset + 50]
                    payload = [
                        {
                            "chainIndex": tokens[k].chain_index,
                            "tokenContractAddress": tokens[k].address,
                        }
                        for k in batch
                    ]
                    rows = await c.okx.gate.run(
                        lambda payload=payload: okx._catalog_request(
                            "/api/v6/dex/market/token/basic-info", payload
                        )
                    )
                    if not isinstance(rows, list):
                        raise AdapterError(Failure.SCHEMA)
                    found = {}
                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        key = (
                            "token:"
                            + str(r.get("chainIndex"))
                            + ":"
                            + str(r.get("tokenContractAddress", "")).lower()
                        )
                        if key in batch:
                            found[key] = r.get("tokenLogoUrl")
                    for key in batch:
                        self.remember(key, found.get(key))
                    self.save()
            except AdapterError as exc:
                failures.append(exc.category.value)
            self.save()
            c.record("logos", failures[0] if failures else None)
