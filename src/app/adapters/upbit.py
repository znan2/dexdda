import hashlib
import uuid
import warnings
from urllib.parse import unquote, urlencode

import httpx
import jwt
from jwt.warnings import InsecureKeyLengthWarning

from app.config import Credentials

from .base import AdapterError, Failure, request_json, require_config

URL = "https://api.upbit.com/v1/deposits/coin_addresses"


def auth_headers(credentials: Credentials, params: dict | None = None) -> dict[str, str]:
    require_config(credentials, "upbit_access_key", "upbit_secret_key")
    payload = {
        "access_key": credentials.upbit_access_key.get_secret_value(),
        "nonce": str(uuid.uuid4()),
    }
    if params:
        query = unquote(urlencode(params, doseq=True)).encode()
        payload["query_hash"] = hashlib.sha512(query).hexdigest()
        payload["query_hash_alg"] = "SHA512"
    # Upbit issues the secret; keep the documented HS512 and raw key bytes.
    # PyJWT's generic length recommendation must not pollute CLI diagnostics.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureKeyLengthWarning)
        token = jwt.encode(
            payload, credentials.upbit_secret_key.get_secret_value(), algorithm="HS512"
        )
    return {"Authorization": "Bearer " + token}


def address_count(data) -> int:
    # Do not return or log account deposit addresses in a connectivity check.
    if not isinstance(data, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("currency"), str) for item in data
    ):
        raise AdapterError(Failure.SCHEMA)
    return len(data)


class UpbitAdapter:
    def __init__(self, client: httpx.AsyncClient, credentials: Credentials):
        self.client = client
        self.credentials = credentials

    async def deposit_addresses(self) -> int:
        return address_count(
            await request_json(self.client, "GET", URL, headers=auth_headers(self.credentials))
        )

    async def markets(self) -> list[dict]:
        from .catalog import Market, parse_rows

        data = await request_json(self.client, "GET", "https://api.upbit.com/v1/market/all")
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
            "https://api.upbit.com/v1/status/wallet",
            headers=auth_headers(self.credentials),
        )
        return parse_rows(Wallet, data, nonempty=True)
