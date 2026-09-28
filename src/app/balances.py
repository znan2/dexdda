"""Cached read-only stablecoin balances. No signing, pricing API, or persistence."""

import asyncio
from decimal import Decimal, localcontext

from pydantic import SecretStr

from app.adapters.base import AdapterError, Failure
from app.adapters.detail import ReadRpc
from app.config import PROJECT_ROOT
from app.detail.models import PurchaseAssets
from app.registry.loader import load_config
from app.registry.models import EVM_ADDRESS

CHAINS = (
    ("1", "Ethereum"),
    ("4663", "Robinhood"),
    ("8453", "Base"),
    ("42161", "Arbitrum"),
    ("56", "BNB Chain"),
)


PUBLIC_RPCS = {"4663": SecretStr("https://rpc.mainnet.chain.robinhood.com")}


class WalletBalances:
    def __init__(self, collector, *, assets=None, rpc_factory=None, public_rpcs=None):
        self.collector = collector
        self.clock = collector.clock
        self.assets = (
            assets
            if assets is not None
            else load_config(PROJECT_ROOT / "config/quote_assets.toml", PurchaseAssets).assets
        )
        self.rpc_factory = rpc_factory
        self.public_rpcs = PUBLIC_RPCS if public_rpcs is None else public_rpcs
        self.states = {}
        self.verified = set()
        self.task = None
        self.last_refresh = None
        self.owner = None
        self.semaphore = asyncio.Semaphore(2)

    def selected(self, chain):
        families = {"USDG"} if chain == "4663" else {"USDT", "USDC"}
        return sorted(
            (a for a in self.assets if a.chain_index == chain and a.family in families),
            key=lambda a: (a.family != "USDT", a.family),
        )

    def observe(self, asset, value=None, error=None):
        key = (asset.chain_index, asset.address)
        old = self.states.get(key, {})
        self.states[key] = {
            "value": old.get("value") if error else value,
            "observed_at_ms": old.get("observed_at_ms") if error else self.clock.milliseconds(),
            "received": old.get("received") if error else self.clock.monotonic(),
            "error": error,
        }

    async def poll_chain(self, chain, owner):
        assets = self.selected(chain)
        url = self.collector.credentials.rpc_urls.get(chain) or self.public_rpcs.get(chain)
        if not assets:
            return
        if not owner or (not url and not self.rpc_factory):
            for asset in assets:
                self.observe(asset, error="wallet_missing" if not owner else "rpc_missing")
            return
        async with self.semaphore:
            rpc = (
                self.rpc_factory(chain)
                if self.rpc_factory
                else ReadRpc(self.collector.client, url, chain)
            )
            try:
                async with asyncio.timeout(8):
                    await rpc.check_chain()
            except Exception as exc:  # noqa: BLE001 - remote URLs and error text never leave server.
                error = exc.category.value if isinstance(exc, AdapterError) else "rpc_failed"
                for asset in assets:
                    self.observe(asset, error=error)
                return
            for asset in assets:
                try:
                    async with asyncio.timeout(8):
                        key = (chain, asset.address, asset.decimals)
                        if key not in self.verified:
                            decimals = await rpc.integer(
                                "eth_call", [{"to": asset.address, "data": "0x313ce567"}, "latest"]
                            )
                            if decimals != asset.decimals:
                                raise AdapterError(Failure.SCHEMA)
                            self.verified.add(key)
                        raw = await rpc.integer(
                            "eth_call",
                            [
                                {"to": asset.address, "data": "0x70a08231" + owner[2:].zfill(64)},
                                "latest",
                            ],
                        )
                        with localcontext() as ctx:
                            ctx.prec = 100
                            value = format(Decimal(raw) / 10**asset.decimals, "f")
                        self.observe(asset, value=value)
                except Exception as exc:  # noqa: BLE001 - isolate per-token failure, preserve last good balance.
                    self.observe(
                        asset,
                        error=exc.category.value if isinstance(exc, AdapterError) else "rpc_failed",
                    )

    async def refresh(self):
        if self.task and not self.task.done():
            await asyncio.shield(self.task)
            return
        if self.last_refresh is not None and self.clock.monotonic() - self.last_refresh < 10:
            return

        async def run():
            self.last_refresh = self.clock.monotonic()
            owner = self.collector.credentials.wallet_address.get_secret_value().lower()
            owner = owner if EVM_ADDRESS.fullmatch(owner) and int(owner, 16) else None
            if owner != self.owner:
                self.states.clear()
                self.owner = owner
            await asyncio.gather(*(self.poll_chain(chain, owner) for chain, _ in CHAINS))

        self.task = asyncio.create_task(run())
        await asyncio.shield(self.task)

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    def snapshot(self):
        chains = []
        for chain, name in CHAINS:
            tokens = []
            for asset in self.selected(chain):
                item = self.states.get((chain, asset.address), {})
                received = item.get("received")
                stale = (
                    received is None
                    or self.clock.monotonic() - received > 120
                    or bool(item.get("error"))
                )
                tokens.append(
                    {
                        "symbol": asset.symbol,
                        "label": asset.label or asset.symbol,
                        "address": asset.address,
                        "value": item.get("value"),
                        "observed_at_ms": item.get("observed_at_ms"),
                        "stale": stale,
                        "error": item.get("error"),
                    }
                )
            chains.append({"chain_index": chain, "name": name, "tokens": tokens})
        return {
            "chains": chains,
            "refresh_seconds": 60,
            "refreshing": bool(self.task and not self.task.done()),
        }
