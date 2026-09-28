"""Read-only quote, orderbook, deposit status and RPC adapters. No signing/sending."""

import re
import time
from decimal import ROUND_DOWN, Decimal, localcontext
from urllib.parse import urlencode

from app.registry.models import EVM_ADDRESS

from .base import AdapterError, Failure, classify, request_json
from .bithumb import BithumbAdapter
from .okx import BASE_URL, auth_headers
from .prices import Gate, number, timestamp
from .rpc import RpcAdapter
from .upbit import UpbitAdapter
from .upbit import auth_headers as upbit_headers


def uint(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise AdapterError(Failure.SCHEMA)
    text = str(value)
    if not re.fullmatch(r"[0-9]{1,78}", text) or int(text) >= 2**256:
        raise AdapterError(Failure.SCHEMA)
    return int(text)


def optional_number(value):
    return None if value in (None, "") else number(value, zero=True)


def token_info(row, address, decimals):
    if not isinstance(row, dict) or str(row.get("tokenContractAddress", "")).lower() != address:
        raise AdapterError(Failure.SCHEMA)
    if uint(row.get("decimal")) != decimals:
        raise AdapterError(Failure.SCHEMA)
    tax = optional_number(row.get("taxRate"))
    if tax is not None and tax > 1:
        raise AdapterError(Failure.SCHEMA)
    honey = row.get("isHoneyPot")
    if honey is not None and type(honey) is not bool:
        raise AdapterError(Failure.SCHEMA)
    return tax, honey


def parse_quote(row, token, asset, units):
    if not isinstance(row, dict) or str(row.get("chainIndex")) != token.chain_index:
        raise AdapterError(Failure.SCHEMA)
    if uint(row.get("fromTokenAmount")) != units:
        raise AdapterError(Failure.SCHEMA)
    source_tax, source_honey = token_info(row.get("fromToken"), asset.address, asset.decimals)
    tax, honey = token_info(row.get("toToken"), token.address, token.decimals)
    output = uint(row.get("toTokenAmount"))
    if not output:
        raise AdapterError(Failure.SCHEMA)
    impact = row.get("priceImpactPercent")
    if impact in (None, ""):
        impact = None
    else:
        # Price impact may be negative; use the unsigned strict numeric parser.
        negative = isinstance(impact, str) and impact.startswith("-")
        impact = number(impact[1:] if negative else impact, zero=True) * (-1 if negative else 1)
    routes = []
    raw_routes = row.get("dexRouterList", [])
    if not isinstance(raw_routes, list):
        raise AdapterError(Failure.SCHEMA)
    for hop in raw_routes:
        if not isinstance(hop, dict):
            raise AdapterError(Failure.SCHEMA)
        protocol = hop.get("dexProtocol", {})
        if not isinstance(protocol, dict):
            raise AdapterError(Failure.SCHEMA)
        name = protocol.get("dexName")
        if isinstance(name, str):
            labels = []
            for key in ("fromToken", "toToken"):
                info = hop.get(key, {})
                symbol = info.get("tokenSymbol") if isinstance(info, dict) else None
                labels.append(symbol[:30] if isinstance(symbol, str) else "?")
            percent = optional_number(protocol.get("percent"))
            if percent is not None and percent > 100:
                raise AdapterError(Failure.SCHEMA)
            share = "" if percent is None else f" ({percent}%)"
            routes.append(name[:80] + " · " + " → ".join(labels) + share)
    with localcontext() as ctx:
        ctx.prec = 400
        # Deliberately conservative tax adjustment; never round up a base-unit quantity.
        adjusted = (
            None
            if tax is None
            else int((Decimal(output) * (1 - tax)).to_integral_value(rounding=ROUND_DOWN))
        )
        return {
            "quantity": Decimal(output) / 10**token.decimals,
            "net_quantity": None if adjusted is None else Decimal(adjusted) / 10**token.decimals,
            "tax": tax,
            "source_tax": source_tax,
            "honeypot": honey,
            "source_honeypot": source_honey,
            "price_impact_percent": impact,
            "swap_gas_usdt": optional_number(row.get("tradeFee")),
            "routes": list(dict.fromkeys(routes))[:30],
        }


class ReadRpc(RpcAdapter):
    async def call(self, method, params):
        if method not in {"eth_call", "eth_gasPrice", "eth_blockNumber", "eth_getBlockByNumber"}:
            raise AdapterError(Failure.CONFIG)
        data = await request_json(
            self.client,
            "POST",
            self.url.get_secret_value(),
            check_api_error=False,
            json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
        )
        if not isinstance(data, dict) or data.get("id") != 2 or data.get("jsonrpc") != "2.0":
            raise AdapterError(Failure.SCHEMA)
        if data.get("error") is not None:
            raise AdapterError(Failure.RPC)
        return data.get("result")

    async def integer(self, method, params):
        value = await self.call(method, params)
        if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{1,64}", value):
            raise AdapterError(Failure.SCHEMA)
        return int(value, 16)

    async def allowance(self, owner, token, spender):
        if not all(EVM_ADDRESS.fullmatch(a) for a in (owner, token, spender)):
            raise AdapterError(Failure.CONFIG)
        data = "0xdd62ed3e" + owner[2:].zfill(64) + spender[2:].zfill(64)
        return await self.integer("eth_call", [{"to": token, "data": data}, "latest"])

    async def block_seconds(self):
        current = await self.integer("eth_blockNumber", [])
        if current < 20:
            raise AdapterError(Failure.SCHEMA)
        times = []
        for height in (current - 20, current):
            block = await self.call("eth_getBlockByNumber", [hex(height), False])
            if not isinstance(block, dict) or block.get("number") != hex(height):
                raise AdapterError(Failure.SCHEMA)
            value = block.get("timestamp")
            if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{1,16}", value):
                raise AdapterError(Failure.SCHEMA)
            times.append(int(value, 16))
        delta = times[1] - times[0]
        if not 0 < delta < 86400:
            raise AdapterError(Failure.SCHEMA)
        return Decimal(delta) / 20


class DetailAdapter:
    def __init__(self, client, credentials, *, okx_gate=None, cex_gates=None):
        self.client, self.credentials = client, credentials
        self.okx_gate = okx_gate or Gate(1.1)
        self.cex_gates = cex_gates or {ex: Gate(0.21) for ex in ("upbit", "bithumb")}

    async def okx(self, endpoint, params):
        path = "/api/v6/dex/aggregator/" + endpoint + "?" + urlencode(params)

        async def operation():
            payload = await request_json(
                self.client,
                "GET",
                BASE_URL + path,
                headers=auth_headers(self.credentials, "GET", path),
                decimal_numbers=True,
            )
            if not isinstance(payload, dict) or "code" not in payload:
                raise AdapterError(Failure.SCHEMA)
            if str(payload["code"]) != "0":
                raise AdapterError(classify(200, payload))
            rows = payload.get("data")
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise AdapterError(Failure.SCHEMA)
            return rows[0]

        return await self.okx_gate.run(operation)

    async def quote(self, token, asset, units):
        row = await self.okx(
            "quote",
            {
                "chainIndex": token.chain_index,
                "amount": str(units),
                "fromTokenAddress": asset.address,
                "toTokenAddress": token.address,
                "swapMode": "exactIn",
            },
        )
        return parse_quote(row, token, asset, units)

    async def approval(self, asset, units):
        row = await self.okx(
            "approve-transaction",
            {
                "chainIndex": asset.chain_index,
                "tokenContractAddress": asset.address,
                "approveAmount": str(units),
            },
        )
        spender = str(row.get("dexContractAddress", "")).lower()
        if not EVM_ADDRESS.fullmatch(spender) or int(spender, 16) == 0:
            raise AdapterError(Failure.SCHEMA)
        # Unsigned calldata is never executed or exposed.
        return {"spender": spender, "gas_limit": number(row.get("gasLimit"))}

    async def book(self, exchange, market):
        async def operation():
            params = {"markets": market}
            if exchange == "upbit":
                params.update({"level": "0", "count": "30"})
            rows = await request_json(
                self.client,
                "GET",
                f"https://api.{exchange}.com/v1/orderbook",
                params=params,
                decimal_numbers=True,
            )
            if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("market") != market:
                raise AdapterError(Failure.SCHEMA)
            row = rows[0]
            source = timestamp(row.get("timestamp"))
            # Both live orderbook APIs use UTC epoch milliseconds (unlike Bithumb ticker).
            now = int(time.time() * 1000)
            if source > now + 2000:
                raise AdapterError(Failure.CLOCK)
            levels = row.get("orderbook_units")
            if not isinstance(levels, list) or not 1 <= len(levels) <= 100:
                raise AdapterError(Failure.SCHEMA)
            bids, asks = [], []
            for level in levels:
                if not isinstance(level, dict):
                    raise AdapterError(Failure.SCHEMA)
                bids.append(
                    (number(level.get("bid_price")), number(level.get("bid_size"), zero=True))
                )
                price, size = (
                    number(level.get("ask_price")),
                    number(level.get("ask_size"), zero=True),
                )
                if size:
                    asks.append(price)
            return {"bids": bids, "ask": min(asks) if asks else None, "source_ms": source}

        return await self.cex_gates[exchange].run(operation)

    async def wallets(self, exchange):
        cls = UpbitAdapter if exchange == "upbit" else BithumbAdapter
        return await self.cex_gates[exchange].run(
            lambda: cls(self.client, self.credentials).wallets()
        )

    async def deposit_chance(self, symbol, network):
        params = {"currency": symbol, "net_type": network}

        async def operation():
            data = await request_json(
                self.client,
                "GET",
                "https://api.upbit.com/v1/deposits/chance/coin",
                params=params,
                headers=upbit_headers(self.credentials, params),
                decimal_numbers=True,
            )
            if (
                not isinstance(data, dict)
                or data.get("currency") != symbol
                or data.get("net_type") != network
            ):
                raise AdapterError(Failure.SCHEMA)
            possible = data.get("is_deposit_possible")
            confirms = data.get("minimum_deposit_confirmations")
            if (
                type(possible) is not bool
                or type(confirms) is not int
                or not 0 <= confirms <= 1_000_000
            ):
                raise AdapterError(Failure.SCHEMA)
            return {
                "possible": possible,
                "confirmations": confirms,
                "minimum": number(data.get("minimum_deposit_amount"), zero=True),
            }

        return await self.cex_gates["upbit"].run(operation)
