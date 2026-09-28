import httpx

from app.config import Credentials

from .base import AdapterError, Failure, request_json, require_config


class CoinGeckoAdapter:
    def __init__(self, client: httpx.AsyncClient, credentials: Credentials, plan: str = "demo"):
        self.client = client
        self.credentials = credentials
        if plan not in ("demo", "pro"):
            raise AdapterError(Failure.CONFIG)
        self.plan = plan

    async def ping(self) -> None:
        require_config(self.credentials, "coingecko_api_key")
        host = "api.coingecko.com" if self.plan == "demo" else "pro-api.coingecko.com"
        data = await request_json(
            self.client,
            "GET",
            f"https://{host}/api/v3/ping",
            headers={
                f"x-cg-{self.plan}-api-key": self.credentials.coingecko_api_key.get_secret_value()
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("gecko_says"), str):
            raise AdapterError(Failure.SCHEMA)

    async def _get(self, path: str, params=None):
        from .catalog import Pace

        require_config(self.credentials, "coingecko_api_key")
        if not hasattr(self, "_pace"):
            self._pace = Pace(getattr(self, "interval", 2.2))
        host = "api.coingecko.com" if self.plan == "demo" else "pro-api.coingecko.com"
        return await self._pace.run(
            lambda: request_json(
                self.client,
                "GET",
                f"https://{host}/api/v3{path}",
                params=params,
                headers={
                    f"x-cg-{self.plan}-api-key": self.credentials.coingecko_api_key.get_secret_value()
                },
            )
        )

    async def exchange_ids(self) -> set[str]:
        data = await self._get("/exchanges/list")
        if not isinstance(data, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in data
        ):
            raise AdapterError(Failure.SCHEMA)
        return {item["id"] for item in data}

    async def tickers(self, exchange: str) -> list[dict]:
        import json

        from .catalog import Ticker, parse_rows

        if exchange not in ("upbit", "bithumb"):
            raise AdapterError(Failure.CONFIG)
        result, pages = [], set()
        for page in range(1, 101):
            data = await self._get(
                f"/exchanges/{exchange}/tickers", {"page": page, "order": "base_target"}
            )
            if not isinstance(data, dict):
                raise AdapterError(Failure.SCHEMA)
            rows = parse_rows(Ticker, data.get("tickers"))
            if not rows:
                return result
            signature = json.dumps(
                sorted(rows, key=lambda r: (r["base"], r["target"], r["coin_id"] or "")),
                sort_keys=True,
            )
            if signature in pages:
                raise AdapterError(Failure.SCHEMA)
            pages.add(signature)
            result.extend(rows)
        # Never quietly publish a truncated page set.
        raise AdapterError(Failure.SCHEMA)

    async def coins(self) -> list[dict]:
        from .catalog import Coin, parse_rows

        return parse_rows(
            Coin, await self._get("/coins/list", {"include_platform": "true"}), nonempty=True
        )

    async def platforms(self) -> list[dict]:
        from .catalog import Platform, parse_rows

        return parse_rows(Platform, await self._get("/asset_platforms"), nonempty=True)
