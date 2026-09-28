"""Exact chain/network deposit targets and resumable whole-registry address preparation."""

import asyncio
import hashlib
import time
from collections import Counter

from app.adapters.base import AdapterError, Failure, request_json
from app.adapters.bithumb import auth_headers as bithumb_headers
from app.adapters.prices import Gate
from app.adapters.upbit import auth_headers as upbit_headers
from app.private_store import PrivateStore, StoreError


def account_scope(credentials, exchange):
    key = getattr(credentials, exchange + "_access_key").get_secret_value()
    return hashlib.sha256((exchange + ":" + key).encode()).hexdigest() if key else None


def targets(registry, chain_map):
    result = {}
    for coin in registry.coins:
        if coin.excluded_reason:
            continue
        for exchange, listing in coin.exchanges.items():
            for token in coin.tokens:
                if token.status.get(exchange) != "tradable":
                    continue
                for mapping in chain_map.networks:
                    if (
                        mapping.exchange != exchange
                        or mapping.chain_index != token.chain_index
                        or mapping.net_type not in listing.net_types
                    ):
                        continue
                    key = exchange + ":" + listing.symbol + ":" + mapping.net_type
                    item = {
                        "key": key,
                        "exchange": exchange,
                        "currency": listing.symbol,
                        "coin_id": coin.coin_id,
                        "chain_index": token.chain_index,
                        "net_type": mapping.net_type,
                    }
                    if key in result and result[key] != item:
                        raise StoreError("ambiguous_deposit_target")
                    result[key] = item
    return sorted(result.values(), key=lambda r: r["key"])


def parse_address(row, currency=None, network=None):
    if not isinstance(row, dict):
        raise AdapterError(Failure.SCHEMA)
    if currency is not None and (row.get("currency") != currency or row.get("net_type") != network):
        raise AdapterError(Failure.SCHEMA)
    if not all(
        isinstance(row.get(k), str) and 0 < len(row[k]) <= 128 for k in ("currency", "net_type")
    ):
        raise AdapterError(Failure.SCHEMA)
    address, secondary = row.get("deposit_address"), row.get("secondary_address")
    if address in (None, ""):
        return None
    if (
        not isinstance(address, str)
        or not 8 <= len(address) <= 256
        or any(c.isspace() for c in address)
    ):
        raise AdapterError(Failure.SCHEMA)
    if secondary is not None and (not isinstance(secondary, str) or len(secondary) > 256):
        raise AdapterError(Failure.SCHEMA)
    return {k: row.get(k) for k in ("currency", "net_type", "deposit_address", "secondary_address")}


class AddressAdapter:
    def __init__(self, client, credentials, exchange, gate=None):
        self.client, self.credentials, self.exchange = client, credentials, exchange
        self.gate = gate or Gate(0.6)
        self.headers = upbit_headers if exchange == "upbit" else bithumb_headers

    async def request(self, method, endpoint, params=None):
        async def operation():
            kwargs = {"headers": self.headers(self.credentials, params)}
            if params:
                kwargs["json" if method == "POST" else "params"] = params
            return await request_json(
                self.client,
                method,
                f"https://api.{self.exchange}.com/v1/deposits/" + endpoint,
                **kwargs,
            )

        return await self.gate.run(operation)

    async def list(self):
        rows = await self.request("GET", "coin_addresses")
        if not isinstance(rows, list):
            raise AdapterError(Failure.SCHEMA)
        result = {}
        for raw in rows:
            row = parse_address(raw)
            if row:
                key = (row["currency"], row["net_type"])
                if key in result and result[key] != row:
                    raise AdapterError(Failure.SCHEMA)
                result[key] = row
        return result

    async def get(self, currency, network):
        row = await self.request("GET", "coin_address", {"currency": currency, "net_type": network})
        return parse_address(row, currency, network)

    async def create(self, currency, network):
        row = await self.request(
            "POST", "generate_coin_address", {"currency": currency, "net_type": network}
        )
        if isinstance(row, dict) and row.get("success") is True and "deposit_address" not in row:
            return None
        return parse_address(row, currency, network)


class AddressBook:
    def __init__(self, path, credentials, registry, chain_map):
        self.store = PrivateStore(path)
        self.credentials, self.registry, self.chain_map = credentials, registry, chain_map

    def empty(self):
        return {"schema_version": 1, "accounts": {}, "entries": {}}

    def read(self):
        data = self.store.read(self.empty())
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != 1
            or not isinstance(data.get("entries"), dict)
        ):
            raise StoreError("invalid_address_cache")
        return data

    async def sync(self, adapters, *, create=True, poll_attempts=3, poll_seconds=2, progress=None):
        with self.store.lock():
            data = self.read()
            desired = targets(self.registry, self.chain_map)
            # Remove obsolete routes rather than exposing stale registry mappings.
            keys = {t["key"] for t in desired}
            data["entries"] = {k: v for k, v in data["entries"].items() if k in keys}
            for exchange in ("upbit", "bithumb"):
                scope = account_scope(self.credentials, exchange)
                selected = [t for t in desired if t["exchange"] == exchange]
                if data["accounts"].get(exchange) != scope:
                    data["entries"] = {
                        k: v for k, v in data["entries"].items() if not k.startswith(exchange + ":")
                    }
                data["accounts"][exchange] = scope
                try:
                    known = await adapters[exchange].list()
                    if not scope:
                        raise AdapterError(Failure.MISSING)
                except AdapterError as exc:
                    for target in selected:
                        data["entries"][target["key"]] = {
                            **target,
                            "status": "error",
                            "error": exc.category.value,
                        }
                    self.store.write(data)
                    continue
                unavailable = None
                for target in selected:
                    key = target["key"]
                    row = known.get((target["currency"], target["net_type"]))
                    previous = data["entries"].get(key, {})
                    entry = {**target, "status": "missing", "error": None}
                    if row is None and previous.get("status") == "generating":
                        try:
                            row = await adapters[exchange].get(
                                target["currency"], target["net_type"]
                            )
                        except AdapterError:
                            pass
                    if row is None and create and unavailable is None:
                        # Persist pending BEFORE the mutating API call; reruns first reconcile.
                        entry.update(status="generating", requested_at_ms=int(time.time() * 1000))
                        data["entries"][key] = entry
                        self.store.write(data)
                        try:
                            row = await adapters[exchange].create(
                                target["currency"], target["net_type"]
                            )
                            for _ in range(poll_attempts):
                                if row is not None:
                                    break
                                await asyncio.sleep(poll_seconds)
                                try:
                                    row = await adapters[exchange].get(
                                        target["currency"], target["net_type"]
                                    )
                                except AdapterError as exc:
                                    if exc.category in (
                                        Failure.AUTH,
                                        Failure.IP,
                                        Failure.PERMISSION,
                                        Failure.PAYMENT,
                                    ):
                                        raise
                            if row is None:
                                entry["error"] = "address_pending"
                        except AdapterError as exc:
                            # Ambiguous POST timeout remains reconcilable, never a false success.
                            entry["error"] = exc.category.value
                            if exc.category in (
                                Failure.AUTH,
                                Failure.IP,
                                Failure.PERMISSION,
                                Failure.PAYMENT,
                            ):
                                unavailable = exc.category.value
                                entry["status"] = "error"
                    elif row is None and unavailable:
                        entry.update(status="error", error=unavailable)
                    if row:
                        checked = parse_address(row, target["currency"], target["net_type"])
                        # EVM destination must have an EVM address, not another chain's address.
                        from app.registry.models import EVM_ADDRESS

                        if not EVM_ADDRESS.fullmatch(checked["deposit_address"].lower()):
                            entry.update(status="error", error="address_network_mismatch")
                        else:
                            entry.pop("requested_at_ms", None)
                            entry.update(checked, status="ready", error=None)
                            entry["checked_at_ms"] = (
                                previous.get("checked_at_ms", int(time.time() * 1000))
                                if previous.get("deposit_address") == checked["deposit_address"]
                                else int(time.time() * 1000)
                            )
                    data["entries"][key] = entry
                    self.store.write(data)
                    if progress:
                        progress(
                            {
                                "exchange": exchange,
                                "completed": len(data["entries"]),
                                "total": len(desired),
                            }
                        )
            return {
                "total": len(desired),
                "counts": dict(Counter(v["status"] for v in data["entries"].values())),
                "errors": dict(
                    Counter(v["error"] for v in data["entries"].values() if v.get("error"))
                ),
            }

    def for_route(self, coin_id, chain, address):
        pairs = [
            (c, t)
            for c in self.registry.coins
            if c.coin_id == coin_id and not c.excluded_reason
            for t in c.tokens
            if t.chain_index == chain and t.address == address
        ]
        if len(pairs) != 1:
            return []
        coin, token = pairs[0]
        data = self.read()
        result = []
        for target in targets(self.registry, self.chain_map):
            exchange = target["exchange"]
            if (
                target["coin_id"] != coin.coin_id
                or target["chain_index"] != chain
                or token.status.get(exchange) != "tradable"
            ):
                continue
            row = data["entries"].get(target["key"], {})
            matching = all(row.get(k) == v for k, v in target.items())
            current = (
                data["accounts"].get(exchange) == account_scope(self.credentials, exchange)
                and account_scope(self.credentials, exchange) is not None
            )
            valid = matching and current and row.get("status") == "ready"
            result.append(
                {
                    **target,
                    "status": row.get("status", "missing") if current else "account_changed",
                    "deposit_address": row.get("deposit_address") if valid else None,
                    "secondary_address": row.get("secondary_address") if valid else None,
                    "checked_at_ms": row.get("checked_at_ms") if valid else None,
                    "error": None if valid else row.get("error") or "address_not_ready",
                }
            )
        return result
