"""Bounded read-only quote input and explicitly verified purchase assets."""

from typing import Literal

from pydantic import Field, model_validator

from app.config import Model


class DetailRequest(Model):
    coin_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")
    chain_index: str = Field(max_length=20, pattern=r"^[1-9][0-9]*$")
    address: str = Field(pattern=r"^0x[0-9a-f]{40}$")
    exchange: Literal["upbit", "bithumb"]
    purchase_symbol: Literal["USDT", "USDC", "USDG"] = "USDT"
    # Input token units; all supported stablecoins are valued 1:1 by user policy.
    amount_usdt: str = Field(max_length=30, pattern=r"^(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,6})?$")


class PurchaseAsset(Model):
    chain_index: str = Field(pattern=r"^[1-9][0-9]*$")
    address: str = Field(pattern=r"^0x[0-9a-f]{40}$")
    decimals: int = Field(ge=0, le=18)
    symbol: Literal["USDT", "USDT0", "USDC", "USDG"]
    evidence: str
    label: str | None = None

    @property
    def family(self):
        return "USDT" if self.symbol == "USDT0" else self.symbol

    # Only reviewed chains may use EVM execution gas without additional fee components.
    simple_gas: bool = False
    native_decimals: int = Field(default=18, ge=0, le=18)


class PurchaseAssets(Model):
    assets: list[PurchaseAsset]

    @model_validator(mode="after")
    def unique_chain(self):
        chains = [(a.chain_index, a.family) for a in self.assets]
        if len(chains) != len(set(chains)):
            raise ValueError("duplicate purchase asset chain/symbol")
        return self
