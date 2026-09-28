import base64
import hashlib
import hmac
from datetime import UTC, datetime

import httpx

from app.config import Credentials

from .base import AdapterError, Failure, classify, request_json, require_config

BASE_URL = "https://web3.okx.com"
CHAINS_PATH = "/api/v6/dex/aggregator/supported/chain"


def auth_headers(
    credentials: Credentials,
    method: str,
    path_with_query: str,
    body: str = "",
    timestamp: str | None = None,
) -> dict[str, str]:
    require_config(credentials, "okx_api_key", "okx_secret_key", "okx_passphrase")
    timestamp = timestamp or datetime.now(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
    prehash = timestamp + method.upper() + path_with_query + body
    signature = base64.b64encode(
        hmac.new(
            credentials.okx_secret_key.get_secret_value().encode(),
            prehash.encode(),
            hashlib.sha256,
        ).digest()
    ).decode()
    return {
        "OK-ACCESS-KEY": credentials.okx_api_key.get_secret_value(),
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-PASSPHRASE": credentials.okx_passphrase.get_secret_value(),
        "OK-ACCESS-TIMESTAMP": timestamp,
    }


class OkxAdapter:
    def __init__(self, client: httpx.AsyncClient, credentials: Credentials):
        self.client = client
        self.credentials = credentials

    async def supported_chains(self) -> int:
        data = await request_json(
            self.client,
            "GET",
            BASE_URL + CHAINS_PATH,
            headers=auth_headers(self.credentials, "GET", CHAINS_PATH),
        )
        if not isinstance(data, dict) or "code" not in data:
            raise AdapterError(Failure.SCHEMA)
        if str(data["code"]) != "0":
            raise AdapterError(classify(200, data))
        chains = data.get("data")
        if not isinstance(chains, list) or not chains:
            raise AdapterError(Failure.SCHEMA)
        for chain in chains:
            index = chain.get("chainIndex") if isinstance(chain, dict) else None
            # Docs say string; live v6 responses also use JSON integers.
            if type(index) is int:
                valid = index > 0
            elif isinstance(index, str):
                valid = index.isascii() and index.isdecimal() and int(index) > 0
            else:
                valid = False
            if not valid:
                raise AdapterError(Failure.SCHEMA)
        return len(chains)

    async def _catalog_request(self, path: str, tokens: list[dict] | None = None):
        import json

        from .catalog import Pace

        if not hasattr(self, "_pace"):
            self._pace = Pace(getattr(self, "interval", 1.1))
        method = "GET" if tokens is None else "POST"
        body = "" if tokens is None else json.dumps(tokens, separators=(",", ":"))

        async def operation():
            headers = auth_headers(self.credentials, method, path, body)
            if tokens is not None:
                headers["Content-Type"] = "application/json"
            data = await request_json(
                self.client,
                method,
                BASE_URL + path,
                headers=headers,
                content=body.encode() if body else None,
            )
            if not isinstance(data, dict) or "code" not in data:
                raise AdapterError(Failure.SCHEMA)
            if str(data["code"]) != "0":
                raise AdapterError(classify(200, data))
            return data.get("data")

        return await self._pace.run(operation)

    async def chain_catalog(self, market: bool = False) -> list[dict]:
        from .catalog import Chain, parse_rows

        path = "/api/v6/dex/market/supported/chain" if market else CHAINS_PATH
        rows = parse_rows(Chain, await self._catalog_request(path), nonempty=True)
        for row in rows:
            index = row["chainIndex"]
            if not index.isascii() or not index.isdecimal():
                raise AdapterError(Failure.SCHEMA)
        return rows

    async def basic_info(self, tokens: list[dict]) -> list[dict]:
        from .catalog import TokenInfo, parse_rows

        if not tokens:
            return []
        data = await self._catalog_request("/api/v6/dex/market/token/basic-info", tokens)
        rows = parse_rows(TokenInfo, data)
        expected = {(str(r["chainIndex"]), r["tokenContractAddress"].lower()) for r in tokens}
        seen = set()
        for row in rows:
            key = (row["chainIndex"], row["tokenContractAddress"].lower())
            if key not in expected or key in seen:
                raise AdapterError(Failure.SCHEMA)
            seen.add(key)
        return rows
