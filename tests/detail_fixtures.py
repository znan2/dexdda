"""Deterministic providers for API and browser checks; no external calls or real secrets."""

import time
from decimal import Decimal
from types import SimpleNamespace

import httpx

from app.adapters.base import AdapterError, Failure
from app.adapters.prices import Price
from app.config import Credentials
from app.detail.models import PurchaseAsset
from app.detail.service import DetailService
from app.registry.models import ChainMap

D = Decimal


class FakeAdapter:
    def __init__(self, registry):
        self.registry = registry
        self.quote_calls = []
        self.fail_quote = False
        self.tax = D(0)
        self.honeypot = False
        self.depth = D(100000)
        self.age = 0
        self.minimum = D(0)

    async def wallets(self, exchange):
        return [
            {"currency": c.exchanges[exchange].symbol, **n}
            for c in self.registry.coins
            if exchange in c.exchanges
            for n in c.exchanges[exchange].networks
        ]

    async def deposit_chance(self, symbol, network):
        return {"possible": True, "confirmations": 12, "minimum": self.minimum}

    async def quote(self, token, asset, units):
        self.quote_calls.append(units)
        if self.fail_quote:
            raise AdapterError(Failure.LIMIT)
        amount = D(units) / 10**asset.decimals
        return {
            "quantity": amount,
            "net_quantity": amount * (1 - self.tax),
            "tax": self.tax,
            "source_tax": D(0),
            "honeypot": self.honeypot,
            "source_honeypot": False,
            "price_impact_percent": D("-0.2"),
            "swap_gas_usdt": D(1),
            "routes": [f"Fixture DEX · {asset.symbol} → TOKEN (100%)"],
        }

    async def approval(self, asset, units):
        return {"spender": "0x" + "b" * 40, "gas_limit": D(65000)}

    async def book(self, exchange, market):
        return {
            "bids": [
                (D(1430 if exchange == "upbit" else 1560), min(self.depth, D(1000))),
                (D(1300), max(D(0), self.depth - 1000)),
            ],
            "ask": D(1300),
            "source_ms": int(time.time() * 1000) - self.age,
        }


def attach_detail(collector, adapter=None):
    state = {"allowance": 0, "rpc_failure": False}

    def handler(request):
        import json

        body = json.loads(request.content)
        method = body["method"]
        assert method in {
            "eth_chainId",
            "eth_call",
            "eth_gasPrice",
            "eth_blockNumber",
            "eth_getBlockByNumber",
        }
        if state["rpc_failure"]:
            return httpx.Response(500, json={"error": "sentinel-secret-never-expose"})
        if method == "eth_chainId":
            result = hex(int(request.url.path.strip("/")))
        elif method == "eth_call":
            result = hex(state["allowance"])
        elif method == "eth_gasPrice":
            result = hex(1_000_000_000)
        elif method == "eth_blockNumber":
            result = hex(1000)
        else:
            height = int(body["params"][0], 16)
            result = {"number": hex(height), "timestamp": hex(1_700_000_000 + height * 12)}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    chains = {t.chain_index for c in collector.registry.coins for t in c.tokens}
    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector.credentials = Credentials(
        wallet_address="0x" + "a" * 40,
        rpc_urls={chain: "https://fixture.invalid/" + chain for chain in chains},
    )

    async def prices(keys):
        return {key: Price(D(2000), int(time.time() * 1000)) for key in keys}

    collector.okx = SimpleNamespace(fetch=prices)
    assets = {
        chain: PurchaseAsset(
            chain_index=chain,
            address="0x" + "c" * 40,
            decimals=6,
            symbol="USDT",
            evidence="offline fixture",
            simple_gas=True,
        )
        for chain in chains
    }
    mapping = ChainMap(
        networks=[
            {
                "exchange": ex,
                "net_type": "ETH",
                "chain_index": "1",
                "platform_id": "ethereum",
                "evidence": "offline fixture",
            }
            for ex in ("upbit", "bithumb")
        ]
    )
    # Browser fixture labels each synthetic chain with ETH. This is fixture-only.
    adapter = adapter or FakeAdapter(collector.registry)
    return (
        DetailService(collector, adapter=adapter, assets=assets, chain_map=mapping),
        adapter,
        state,
    )
