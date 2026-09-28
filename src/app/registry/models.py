"""Strict local configuration and registry artifact schemas."""

import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.config import Model

EVM_ADDRESS = re.compile(r"0x[0-9a-f]{40}")
NATIVE_ADDRESS = "0x" + "e" * 40


def native_address(chain_index: str) -> str:
    # OKX Swap FAQ documents Arc USDC as an exception to EVM's placeholder.
    return "0x3600000000000000000000000000000000000000" if chain_index == "5042" else NATIVE_ADDRESS


Exchange = Literal["upbit", "bithumb"]
Status = Literal["tradable", "bridge_candidate", "excluded"]


class Network(Model):
    exchange: Exchange
    net_type: str = Field(min_length=1)
    chain_index: str = Field(pattern=r"^[1-9][0-9]*$")
    platform_id: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class ChainMap(Model):
    networks: list[Network] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self):
        keys = [(n.exchange, n.net_type) for n in self.networks]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate network mapping")
        return self


class MappingOverride(Model):
    exchange: Exchange
    symbol: str = Field(min_length=1)
    coin_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class TokenOverride(Model):
    coin_id: str = Field(min_length=1)
    chain_index: str = Field(pattern=r"^[1-9][0-9]*$")
    address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    native: bool = False
    reason: str = Field(min_length=1)


class Exclusion(Model):
    coin_id: str = Field(min_length=1)
    exchange: Exchange | None = None
    chain_index: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")
    reason: str = Field(min_length=1)


class Overrides(Model):
    mappings: list[MappingOverride] = Field(default_factory=list)
    tokens: list[TokenOverride] = Field(default_factory=list)
    exclusions: list[Exclusion] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self):
        for keys in (
            [(m.exchange, m.symbol) for m in self.mappings],
            [(t.coin_id, t.chain_index) for t in self.tokens],
        ):
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate override")
        for token in self.tokens:
            if token.native != (token.address.lower() == native_address(token.chain_index)):
                raise ValueError("native placeholder mismatch")
        return self


class Token(Model):
    chain_index: str = Field(pattern=r"^[1-9][0-9]*$")
    address: str = Field(pattern=r"^0x[0-9a-f]{40}$")
    decimals: int = Field(ge=0, le=255)
    name: str
    symbol: str
    community_recognized: bool | None
    native: bool
    source: Literal["coingecko", "override"]
    status: dict[Exchange, Status]
    # M1 identifies routes only. Execution readiness is implemented in M4/M5.
    execution_enabled: Literal[False] = False
    exclusion_reasons: dict[Exchange, str] = Field(default_factory=dict)


class ExchangeListing(Model):
    symbol: str
    markets: list[str]
    net_types: list[str]
    networks: list[dict[str, str]]


class RegistryCoin(Model):
    coin_id: str
    symbol: str
    names: dict[str, str]
    exchanges: dict[Exchange, ExchangeListing]
    tokens: list[Token]
    source: Literal["coingecko", "override"]
    excluded_reason: str | None = None


class Registry(Model):
    schema_version: Literal[1] = 1
    source_digest: str
    # Build time in ISO 8601 UTC; absent in registries built before this field existed.
    generated_at: str | None = None
    source_markets: dict[Exchange, list[str]] = Field(default_factory=dict)
    chains: list[dict]
    coins: list[RegistryCoin]
    issues: list[dict]

    @field_validator("generated_at")
    @classmethod
    def utc_iso(cls, value):
        if value is not None and datetime.fromisoformat(value).utcoffset() != timedelta(0):
            raise ValueError("generated_at must be an ISO 8601 UTC timestamp")
        return value
