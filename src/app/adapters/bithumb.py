import hashlib
import time
import uuid
from urllib.parse import urlencode

import httpx
import jwt

from app.config import Credentials

from .base import request_json, require_config
from .upbit import address_count

URL = "https://api.bithumb.com/v1/deposits/coin_addresses"


def auth_headers(credentials: Credentials, params: dict | None = None) -> dict[str, str]:
    require_config(credentials, "bithumb_access_key", "bithumb_secret_key")
    payload = {
        "access_key": credentials.bithumb_access_key.get_secret_value(),
        "nonce": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
    }
    if params:
        payload["query_hash"] = hashlib.sha512(urlencode(params).encode()).hexdigest()
        payload["query_hash_alg"] = "SHA512"
    token = jwt.encode(
        payload, credentials.bithumb_secret_key.get_secret_value(), algorithm="HS256"
    )
    return {"Authorization": "Bearer " + token}


class BithumbAdapter:
    def __init__(self, client: httpx.AsyncClient, credentials: Credentials):
        self.client = client
        self.credentials = credentials

    async def deposit_addresses(self) -> int:
        return address_count(
            await request_json(self.client, "GET", URL, headers=auth_headers(self.credentials))
        )

    async def markets(self) -> list[dict]:
        from .catalog import Market, parse_rows

        data = await request_json(self.client, "GET", "https://api.bithumb.com/v1/market/all")
        return [
            row
            for row in parse_rows(Market, data, nonempty=True)
            if row["market"].startswith("KRW-")
        ]

    async def wallets(self) -> list[dict]:
        from .catalog import Wallet, parse_rows

        data = await request_json(
            self.client,
            "GET",
            "https://api.bithumb.com/v1/status/wallet",
            headers=auth_headers(self.credentials),
        )
        return parse_rows(Wallet, data, nonempty=True)
