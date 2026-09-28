"""Load secrets only from a local dotenv file; never from ambient environment."""

import logging
import re
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    """Contains a static, non-sensitive message only."""


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServerSettings(Model):
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8000, ge=1024, le=65535)


class HttpSettings(Model):
    timeout_seconds: float = Field(default=15, gt=0, le=60)


class PricingSettings(Model):
    default_amount_usdt: str = Field(default="1000", pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    poll_seconds: float = Field(default=5, gt=0)
    ui_poll_seconds: float = Field(default=2, gt=0)
    stale_seconds: float = Field(default=15, gt=0)
    usdt_stale_seconds: float = Field(default=300, ge=15, le=86400)
    okx_batch_size: int = Field(default=100, ge=1, le=100)
    okx_interval_seconds: float = Field(default=1.1, ge=0.1)
    wallet_poll_seconds: float = Field(default=30, ge=10, le=300)
    wallet_stale_seconds: float = Field(default=90, ge=30, le=600)
    liquidity_poll_seconds: float = Field(default=300, ge=10)
    liquidity_stale_seconds: float = Field(default=900, ge=10)
    rest_batch_size: int = Field(default=20, ge=1, le=100)
    rest_interval_seconds: float = Field(default=0.21, ge=0.1)
    ws_batch_size: int = Field(default=100, ge=1, le=100)


class FilterSettings(Model):
    hide_low_liquidity: bool = True
    max_abs_gap_percent: str = Field(default="30", pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    min_liquidity_usd: str = Field(default="50000", pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    unrecognized_action: Literal["warn", "hide"] = "warn"


class CoinGeckoSettings(Model):
    plan: Literal["demo", "pro"] = "demo"


class RegistrySettings(Model):
    coingecko_interval_seconds: float = Field(default=2.2, ge=0)
    okx_interval_seconds: float = Field(default=1.1, ge=0)
    basic_info_batch_size: int = Field(default=20, ge=1, le=100)
    max_age_hours: float = Field(default=72, gt=0)


class DetailSettings(Model):
    token_transfer_gas: int = Field(default=100000, ge=21000, le=1000000)
    native_transfer_gas: int = Field(default=21000, ge=21000, le=1000000)


class SwapSettings(Model):
    max_amount_usdt: str = Field(default="1000", pattern=r"^[0-9]+(?:\.[0-9]{1,6})?$")
    min_gap_percent: str = Field(default="0", pattern=r"^-?[0-9]+(?:\.[0-9]+)?$")
    slippage_percent: str = Field(default="0.5", pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    approval_policy: Literal["exact", "capped"] = "capped"
    approval_cap_usdt: str = Field(default="5000", pattern=r"^[0-9]+(?:\.[0-9]{1,6})?$")
    receipt_timeout_seconds: float = Field(default=120, ge=1, le=600)
    receipt_poll_seconds: float = Field(default=3, ge=0.05, le=30)
    receipt_confirmations: int = Field(default=2, ge=1, le=100)
    confirmation_seconds: int = Field(default=60, ge=5, le=300)


class OperationsSettings(Model):
    market_check_seconds: float = Field(default=1800, ge=60)
    fx_check_seconds: float = Field(default=3600, ge=60)
    reference_fx_enabled: bool = True


class Settings(Model):
    swap: SwapSettings = Field(default_factory=SwapSettings)
    operations: OperationsSettings = Field(default_factory=OperationsSettings)
    detail: DetailSettings = Field(default_factory=DetailSettings)
    registry: RegistrySettings = Field(default_factory=RegistrySettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)
    pricing: PricingSettings = Field(default_factory=PricingSettings)
    filters: FilterSettings = Field(default_factory=FilterSettings)
    coingecko: CoinGeckoSettings = Field(default_factory=CoinGeckoSettings)


class Credentials(Model):
    okx_api_key: SecretStr = SecretStr("")
    okx_secret_key: SecretStr = SecretStr("")
    okx_passphrase: SecretStr = SecretStr("")
    upbit_access_key: SecretStr = SecretStr("")
    upbit_secret_key: SecretStr = SecretStr("")
    bithumb_access_key: SecretStr = SecretStr("")
    bithumb_secret_key: SecretStr = SecretStr("")
    coingecko_api_key: SecretStr = SecretStr("")
    wallet_address: SecretStr = SecretStr("")
    wallet_private_key: SecretStr = SecretStr("")
    rpc_urls: dict[str, SecretStr] = Field(default_factory=dict)
    dry_run: bool = True

    def present(self, *names: str) -> bool:
        return all(getattr(self, name).get_secret_value() for name in names)


def load_settings(path: Path | None = None) -> Settings:
    try:
        with (path or PROJECT_ROOT / "config/settings.toml").open("rb") as f:
            return Settings.model_validate(tomllib.load(f))
    except (OSError, ValueError, ValidationError):
        raise ConfigError("settings.toml 파일 또는 설정 형식을 확인하세요.") from None


def load_credentials(path: Path | None = None) -> Credentials:
    path = path or PROJECT_ROOT / ".env"
    if not path.is_file():
        raise ConfigError(".env 파일이 없습니다. .env.example을 복사해 작성하세요.")
    # dotenv can warn about malformed lines. Suppress parser messages; validate below.
    logger = logging.getLogger("dotenv.main")
    previous = logger.disabled
    logger.disabled = True
    try:
        from dotenv.parser import parse_stream

        with path.open(encoding="utf-8") as stream:
            bindings = list(parse_stream(stream))
        if any(item.error for item in bindings):
            raise ConfigError(".env 문법을 확인하세요. 원문은 출력하지 않습니다.")
        keys = [item.key for item in bindings if item.key is not None]
        if len(keys) != len(set(keys)):
            raise ConfigError(".env에 중복된 변수명이 있습니다.")
        values = dotenv_values(path, interpolate=False, encoding="utf-8")
        raw_dry_run = values.get("DRY_RUN", "true")
        if raw_dry_run not in ("true", "false"):
            raise ConfigError("DRY_RUN은 true 또는 false로 명시하세요.")
        rpc_urls = {}
        for key, value in values.items():
            if key.startswith("RPC_URL_"):
                if not re.fullmatch(r"RPC_URL_[1-9][0-9]*", key):
                    raise ConfigError("RPC 변수명은 RPC_URL_<숫자 체인 ID> 형식이어야 합니다.")
                if value:
                    rpc_urls[key.removeprefix("RPC_URL_")] = SecretStr(value)
        fields = {
            name: SecretStr(values.get(name.upper()) or "")
            for name in Credentials.model_fields
            if name not in ("rpc_urls", "dry_run")
        }
        return Credentials(**fields, rpc_urls=rpc_urls, dry_run=raw_dry_run == "true")
    except ConfigError:
        raise
    except (OSError, ValueError):
        raise ConfigError(".env 파일을 읽을 수 없거나 형식이 잘못되었습니다.") from None
    finally:
        logger.disabled = previous
