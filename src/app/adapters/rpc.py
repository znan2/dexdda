import re
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from .base import AdapterError, Failure, request_json


class RpcAdapter:
    def __init__(self, client: httpx.AsyncClient, url: SecretStr, chain_id: str):
        self.client = client
        self.url = url
        self.chain_id = chain_id

    async def check_chain(self) -> None:
        try:
            parsed = urlsplit(self.url.get_secret_value())
            valid = bool(parsed.hostname) and (
                parsed.scheme == "https"
                or (
                    parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
                )
            )
            valid = valid and not parsed.fragment
        except ValueError:
            valid = False
        if not valid:
            raise AdapterError(Failure.CONFIG)
        data = await request_json(
            self.client,
            "POST",
            self.url.get_secret_value(),
            check_api_error=False,
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
        )
        if (
            not isinstance(data, dict)
            or data.get("jsonrpc") != "2.0"
            or type(data.get("id")) is not int
            or data["id"] != 1
        ):
            raise AdapterError(Failure.SCHEMA)
        if data.get("error") is not None:
            raise AdapterError(Failure.RPC)
        result = data.get("result")
        if not isinstance(result, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", result):
            raise AdapterError(Failure.SCHEMA)
        if int(result, 16) != int(self.chain_id):
            raise AdapterError(Failure.CHAIN)
