"""Durable, registry-identified dashboard exclusions."""

import copy
import time
from typing import Literal

from pydantic import Field, ValidationError

from app.config import Model
from app.private_store import PrivateStore, StoreError


class Exclusion(Model):
    kind: Literal["coin", "network"]
    coin_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")
    exchange: Literal["upbit", "bithumb"] | None = None
    net_type: str | None = Field(default=None, min_length=1, max_length=80)
    reason: str = Field(default="", max_length=200)
    created_at_ms: int = 0

    def key(self):
        return (self.kind, self.coin_id, self.exchange, self.net_type)


class Change(Exclusion):
    excluded: bool


class Saved(Model):
    version: Literal[1] = 1
    exclusions: list[Exclusion] = Field(default_factory=list, max_length=10000)


class Preferences:
    def __init__(self, registry, path=None):
        self.registry = registry
        self.store = PrivateStore(path) if path else None
        self.data = self.read()

    def read(self):
        try:
            return Saved.model_validate(self.store.read({}) if self.store else {})
        except ValidationError:
            raise StoreError("invalid_preferences") from None

    def coin(self, coin_id):
        return next((c for c in self.registry.coins if c.coin_id == coin_id), None)

    def blocked(self, coin_id):
        return any(e.kind == "coin" and e.coin_id == coin_id for e in self.data.exclusions)

    def network(self, coin_id, exchange, net_type):
        return next(
            (
                e
                for e in self.data.exclusions
                if e.key() == ("network", coin_id, exchange, net_type)
            ),
            None,
        )

    def change(self, change):
        if change.kind == "coin" and (change.exchange or change.net_type):
            raise ValueError("invalid_identity")
        if change.kind == "network" and (not change.exchange or not change.net_type):
            raise ValueError("invalid_identity")
        coin = self.coin(change.coin_id)
        if change.excluded:
            if coin is None or coin.excluded_reason:
                raise ValueError("coin_not_registered")
            if change.kind == "network":
                listing = coin.exchanges.get(change.exchange)
                if not listing or change.net_type not in listing.net_types:
                    raise ValueError("network_not_registered")

        def update():
            data = self.read() if self.store else self.data.model_copy(deep=True)
            entries = [e for e in data.exclusions if e.key() != change.key()]
            if change.excluded:
                entries.append(
                    Exclusion(
                        **change.model_dump(exclude={"excluded", "created_at_ms"}),
                        created_at_ms=int(time.time() * 1000),
                    )
                )
            data = Saved(exclusions=entries)
            if self.store:
                self.store.write(data.model_dump())
            self.data = data

        if self.store:
            with self.store.lock():
                update()
        else:
            update()

    def public(self):
        return copy.deepcopy(self.data.model_dump())

    def catalog(self):
        return [
            {
                "coin_id": c.coin_id,
                "symbol": c.symbol,
                "name": c.names.get("upbit_ko") or c.names.get("bithumb_ko") or c.coin_id,
                "networks": [
                    {"exchange": ex, **n}
                    for ex, listing in c.exchanges.items()
                    for n in listing.networks
                ],
            }
            for c in self.registry.coins
            if not c.excluded_reason
        ]
