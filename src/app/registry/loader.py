import json
import os
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from app.config import ConfigError, Model

from .models import Registry


def load_config(path: Path, model: type[Model]):
    try:
        with path.open("rb") as stream:
            return model.model_validate(tomllib.load(stream))
    except (OSError, ValueError, ValidationError):
        raise ConfigError("레지스트리 TOML 설정 형식·중복·필수 항목을 확인하세요.") from None


def canonical_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_atomic(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix="." + path.name + ".", delete=False
        ) as stream:
            name = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def load_registry(path: Path) -> Registry:
    try:
        return Registry.model_validate_json(path.read_text())
    except (OSError, ValueError, ValidationError):
        raise ConfigError("레지스트리 파일 형식을 확인하세요.") from None


def registry_time(registry: Registry, path: Path | None = None) -> tuple[str | None, str | None]:
    """Build time as (ISO 8601 UTC, source). The file field wins; mtime is only a fallback."""
    generated_at = getattr(registry, "generated_at", None)  # fixture registries may lack it
    if generated_at is not None:
        return generated_at, "field"
    if path is None:
        return None, None
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return None, None
    return modified.replace(microsecond=0).isoformat(), "mtime"
