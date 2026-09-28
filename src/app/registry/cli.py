"""Build M1 artifacts using live APIs or explicit reproducible snapshots."""

import argparse
import asyncio
import json
from pathlib import Path

from app.adapters.base import AdapterError
from app.config import PROJECT_ROOT, ConfigError, load_credentials, load_settings

from .builder import counts, finalize, prepare, report
from .loader import canonical_json, load_config, write_atomic
from .models import ChainMap, Overrides
from .sources import collect, fetch_metadata, read_metadata, read_snapshot


def guard_secrets(texts: list[str], credentials):
    secrets = []
    for name, value in credentials:
        if name == "rpc_urls":
            secrets.extend(v.get_secret_value() for v in value.values())
        elif name != "dry_run":
            secrets.append(value.get_secret_value())
    if any(secret and any(secret in text for text in texts) for secret in secrets):
        raise ConfigError("산출물에서 설정 비밀값이 감지되어 저장을 중단했습니다.")


async def build(args):
    settings = load_settings(args.settings)
    chain_map = load_config(args.chain_map, ChainMap)
    overrides = load_config(args.overrides, Overrides)
    offline = bool(args.sources and args.metadata)
    credentials = None if offline else load_credentials(args.env_file)
    wanted = {m.coin_id for m in overrides.mappings} | {t.coin_id for t in overrides.tokens}
    sources = (
        read_snapshot(args.sources)
        if args.sources
        else await collect(settings, credentials, wanted)
    )
    draft = prepare(sources, chain_map, overrides)
    metadata = (
        read_metadata(args.metadata)
        if args.metadata
        else await fetch_metadata(settings, credentials, draft.token_keys())
    )
    registry = finalize(draft, metadata)
    json_text = canonical_json(registry.model_dump())
    report_text = report(registry)
    source_text = canonical_json(sources)
    meta_text = canonical_json(metadata)
    if credentials is not None:
        guard_secrets([json_text, report_text, source_text, meta_text], credentials)
    # Publish only after every mandatory fetch and validation succeeds.
    if args.save_sources:
        write_atomic(args.save_sources, source_text)
    if args.save_metadata:
        write_atomic(args.save_metadata, meta_text)
    write_atomic(args.output_dir / "registry_report.md", report_text)
    write_atomic(args.output_dir / "registry.json", json_text)
    print(
        json.dumps(
            {
                "ok": True,
                "coins": len(registry.coins),
                "chains": len(registry.chains),
                "counts": counts(registry),
                "issues": len(registry.issues),
                "source_digest": registry.source_digest,
            },
            ensure_ascii=False,
        )
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--chain-map", type=Path, default=PROJECT_ROOT / "config/chain_map.toml")
    parser.add_argument("--overrides", type=Path, default=PROJECT_ROOT / "config/overrides.toml")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--sources", type=Path, help="Explicit normalized public source snapshot")
    parser.add_argument("--metadata", type=Path, help="Explicit OKX metadata snapshot")
    parser.add_argument("--save-sources", type=Path)
    parser.add_argument("--save-metadata", type=Path)
    args = parser.parse_args(argv)
    try:
        asyncio.run(build(args))
        return 0
    except AdapterError as exc:
        print(
            json.dumps(
                {"ok": False, "category": exc.category.value, "detail": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1
    except ConfigError as exc:
        print(
            json.dumps(
                {"ok": False, "category": "configuration_error", "detail": str(exc)},
                ensure_ascii=False,
            )
        )
        return 2
    except KeyboardInterrupt:
        print("빌드를 중단했습니다.")
        return 130
    except Exception:  # noqa: BLE001 - CLI must not expose secret-bearing exceptions.
        print('{"ok":false,"category":"internal_error"}')
        return 2
