import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import ConfigError
from app.registry.builder import counts, finalize, prepare, report
from app.registry.loader import canonical_json, load_registry, registry_time
from app.registry.models import ChainMap, Overrides, Registry

GENSYN = "0x4d7078ddd6ccfed2f85db5b7d3ff16828d378d48"


@pytest.fixture
def sources():
    return json.loads((Path(__file__).parent / "fixtures/registry_sources.json").read_text())


@pytest.fixture
def chain_map():
    return ChainMap.model_validate(
        {
            "networks": [
                {
                    "exchange": "upbit",
                    "net_type": "ETH",
                    "chain_index": "1",
                    "platform_id": "ethereum",
                    "evidence": "fixture",
                },
                {
                    "exchange": "bithumb",
                    "net_type": "BASE_ETH",
                    "chain_index": "8453",
                    "platform_id": "base",
                    "evidence": "fixture",
                },
                {
                    "exchange": "upbit",
                    "net_type": "ARC",
                    "chain_index": "5042",
                    "platform_id": "arc",
                    "evidence": "fixture",
                },
            ]
        }
    )


@pytest.fixture
def overrides():
    return Overrides.model_validate(
        {
            "tokens": [
                {
                    "coin_id": "ethereum",
                    "chain_index": "1",
                    "address": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                    "native": True,
                    "reason": "fixture",
                },
                {
                    "coin_id": "usd-coin",
                    "chain_index": "5042",
                    "address": "0x3600000000000000000000000000000000000000",
                    "native": True,
                    "reason": "OKX Arc exception",
                },
            ]
        }
    )


def metadata(draft):
    return [
        {
            "chainIndex": chain,
            "tokenContractAddress": address,
            "decimal": "18",
            "tokenName": "Gensyn" if address == GENSYN else "Fixture token",
            "tokenSymbol": "AI",
            "tagList": {"communityRecognized": True},
        }
        for chain, address in draft.token_keys()
    ]


BUILT_AT = "2026-09-22T00:00:00+00:00"


def build(sources, chain_map, overrides):
    draft = prepare(sources, chain_map, overrides)
    # Pin the only wall-clock field so repeat/reorder comparisons stay byte-identical.
    return finalize(draft, metadata(draft), generated_at=BUILT_AT)


def test_generated_at_is_recorded_and_must_be_utc(sources, chain_map, overrides):
    r = build(sources, chain_map, overrides)
    assert r.generated_at == BUILT_AT
    draft = prepare(sources, chain_map, overrides)
    stamped = finalize(draft, metadata(draft)).generated_at
    assert datetime.fromisoformat(stamped).utcoffset() == timedelta(0)
    for bad in ("2026-09-22T00:00:00", "2026-09-22T09:00:00+09:00", "yesterday"):
        with pytest.raises(ValueError):
            Registry.model_validate({**r.model_dump(), "generated_at": bad})


def test_registry_without_generated_at_loads_and_falls_back_to_mtime(
    sources, chain_map, overrides, tmp_path
):
    data = build(sources, chain_map, overrides).model_dump()
    del data["generated_at"]  # the pre-field layout still committed as data/registry.json
    path = tmp_path / "registry.json"
    path.write_text(canonical_json(data))
    modified = 1_790_000_000
    os.utime(path, (modified, modified))
    legacy = load_registry(path)
    assert legacy.generated_at is None
    assert registry_time(legacy, path) == (
        datetime.fromtimestamp(modified, UTC).isoformat(),
        "mtime",
    )
    assert registry_time(legacy) == (None, None)
    path.write_text(canonical_json({**data, "generated_at": BUILT_AT}))
    os.utime(path, (modified, modified))
    assert registry_time(load_registry(path), path) == (BUILT_AT, "field")


def coin(registry, coin_id):
    return next(c for c in registry.coins if c.coin_id == coin_id)


def test_ai_resolves_exchange_coin_id_not_same_symbol(sources, chain_map, overrides):
    registry = build(sources, chain_map, overrides)
    gensyn = coin(registry, "gensyn")
    token = next(t for t in gensyn.tokens if t.chain_index == "1")
    assert token.address == GENSYN
    assert not any(c.coin_id == "artificial-inu" for c in registry.coins)
    assert gensyn.names["coingecko"] == "Gensyn"


def test_per_exchange_direct_and_bridge_with_paused_wallet(sources, chain_map, overrides):
    registry = build(sources, chain_map, overrides)
    gensyn = coin(registry, "gensyn")
    routes = {t.chain_index: t for t in gensyn.tokens}
    assert routes["1"].status == {"upbit": "tradable", "bithumb": "bridge_candidate"}
    assert routes["8453"].status == {"upbit": "bridge_candidate", "bithumb": "tradable"}
    assert set(routes["56"].status.values()) == {"bridge_candidate"}
    assert all(t.execution_enabled is False for t in gensyn.tokens)
    assert gensyn.exchanges["upbit"].networks[0]["wallet_state"] == "paused"


def test_native_non_evm_and_pegged_tokens(sources, chain_map, overrides):
    r = build(sources, chain_map, overrides)
    assert coin(r, "ethereum").tokens[0].native
    assert coin(r, "ethereum").tokens[0].status["upbit"] == "tradable"
    assert coin(r, "usd-coin").tokens[0].address.startswith("0x3600")
    assert coin(r, "usd-coin").tokens[0].native
    assert coin(r, "bitcoin").tokens == []
    assert coin(r, "bitcoin").excluded_reason == "no_supported_evm_contract"
    assert coin(r, "ripple").tokens[0].status["upbit"] == "bridge_candidate"
    assert not coin(r, "ripple").tokens[0].native
    assert "501" not in {c["chain_index"] for c in r.chains}


def test_missing_mapping_is_top_warning_and_build_still_succeeds(sources, chain_map, overrides):
    r = build(sources, chain_map, overrides)
    assert "경고" in report(r).splitlines()[2]
    assert "NEW_NETWORK" in report(r)
    assert any(i["code"] == "missing_coin_id" and i["symbol"] == "MISSING" for i in r.issues)
    assert any(i["code"] == "ambiguous_coin_id" and i["symbol"] == "CLASH" for i in r.issues)


def test_override_address_and_exclusion_take_priority(sources, chain_map, overrides):
    value = overrides.model_dump()
    value["tokens"].append(
        {
            "coin_id": "gensyn",
            "chain_index": "1",
            "address": "0x" + "f" * 40,
            "native": False,
            "reason": "confirmed replacement",
        }
    )
    value["exclusions"].append(
        {"coin_id": "gensyn", "exchange": "bithumb", "reason": "manual exclusion"}
    )
    r = build(sources, chain_map, Overrides.model_validate(value))
    g = coin(r, "gensyn")
    assert next(t.address for t in g.tokens if t.chain_index == "1") == "0x" + "f" * 40
    assert all(t.status["bithumb"] == "excluded" for t in g.tokens)
    assert all(t.execution_enabled is False for t in g.tokens)


def test_mapping_override_resolves_conflict(sources, chain_map, overrides):
    # Remove AI listing to avoid two markets of one exchange resolving to the same ID.
    sources["upbit_markets"] = [r for r in sources["upbit_markets"] if r["market"] != "KRW-AI"]
    value = overrides.model_dump()
    value["mappings"] = [
        {"exchange": "upbit", "symbol": "CLASH", "coin_id": "gensyn", "reason": "verified"}
    ]
    r = build(sources, chain_map, Overrides.model_validate(value))
    assert coin(r, "gensyn").exchanges["upbit"].symbol == "CLASH"
    assert any(i.get("resolved_by_override") for i in r.issues)


@pytest.mark.parametrize("extra", [{"allow_swap": True}, {"status": "tradable"}])
def test_unsafe_override_fields_rejected(extra):
    with pytest.raises(ValidationError):
        Overrides.model_validate(
            {
                "tokens": [
                    {
                        "coin_id": "gensyn",
                        "chain_index": "1",
                        "address": GENSYN,
                        "reason": "fixture",
                        **extra,
                    }
                ]
            }
        )


def test_native_identity_mismatch_fails(sources, chain_map):
    o = Overrides.model_validate(
        {
            "tokens": [
                {
                    "coin_id": "gensyn",
                    "chain_index": "1",
                    "address": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                    "native": True,
                    "reason": "wrong",
                }
            ]
        }
    )
    with pytest.raises(ConfigError):
        prepare(sources, chain_map, o)


def test_unknown_okx_contract_excluded_and_reported(sources, chain_map, overrides):
    d = prepare(sources, chain_map, overrides)
    metas = [m for m in metadata(d) if m["tokenContractAddress"] != GENSYN]
    r = finalize(d, metas)
    assert all(t.address != GENSYN for t in coin(r, "gensyn").tokens)
    assert GENSYN in report(r)
    assert any(i["code"] == "okx_unrecognized_contract" for i in r.issues)


def test_unknown_decimal_not_guessed(sources, chain_map, overrides):
    d = prepare(sources, chain_map, overrides)
    metas = metadata(d)
    for m in metas:
        m["decimal"] = "garbage"
    r = finalize(d, metas)
    assert all(not c.tokens for c in r.coins)


def test_deterministic_under_input_reordering_and_repeat(sources, chain_map, overrides):
    first = build(sources, chain_map, overrides)
    shuffled = copy.deepcopy(sources)
    for value in shuffled.values():
        value.reverse()
    second = build(shuffled, chain_map, overrides)
    assert canonical_json(first.model_dump()) == canonical_json(second.model_dump())
    assert report(first) == report(second)
    assert counts(first) == counts(second)


def test_collision_excludes_both_identities(sources, chain_map, overrides):
    sources["upbit_tickers"].append({"base": "OTHER", "target": "KRW", "coin_id": "artificial-inu"})
    sources["upbit_markets"].append(
        {"market": "KRW-OTHER", "korean_name": "Other", "english_name": "Other"}
    )
    for row in sources["coins"]:
        if row["id"] == "artificial-inu":
            row["platforms"] = {"ethereum": GENSYN}
    r = build(sources, chain_map, overrides)
    assert all(
        s == "excluded"
        for c in r.coins
        for t in c.tokens
        if t.address == GENSYN
        for s in t.status.values()
    )


def test_loader_rejects_execution_enabled(sources, chain_map, overrides, tmp_path):
    r = build(sources, chain_map, overrides)
    path = tmp_path / "registry.json"
    data = r.model_dump()
    next(c for c in data["coins"] if c["tokens"])["tokens"][0]["execution_enabled"] = True
    path.write_text(canonical_json(data))
    with pytest.raises(ConfigError):
        load_registry(path)


def test_new_supported_chain_not_limited_by_rpc_examples(sources, chain_map, overrides):
    sources["platforms"].append(
        {"id": "future", "chain_identifier": 999999, "name": "Future", "native_coin_id": "ethereum"}
    )
    for key in ("market_chains", "swap_chains"):
        sources[key].append({"chainIndex": "999999", "chainName": "Future"})
    next(c for c in sources["coins"] if c["id"] == "gensyn")["platforms"]["future"] = (
        "0x" + "d" * 40
    )
    r = build(sources, chain_map, overrides)
    assert "999999" in {t.chain_index for t in coin(r, "gensyn").tokens}


def test_unsupported_wallet_is_not_direct(sources, chain_map, overrides):
    sources["upbit_wallets"][0]["wallet_state"] = "unsupported"
    r = build(sources, chain_map, overrides)
    assert (
        next(t for t in coin(r, "gensyn").tokens if t.chain_index == "1").status["upbit"]
        == "bridge_candidate"
    )


def test_equal_symbols_on_different_exchanges_are_separate(sources, chain_map, overrides):
    sources["bithumb_tickers"][0]["coin_id"] = "artificial-inu"
    r = build(sources, chain_map, overrides)
    assert set(coin(r, "gensyn").exchanges) == {"upbit"}
    assert set(coin(r, "artificial-inu").exchanges) == {"bithumb"}


def test_unrecognized_route_count_is_included_even_with_other_valid_tokens(
    sources, chain_map, overrides
):
    d = prepare(sources, chain_map, overrides)
    full = finalize(d, metadata(d))
    reduced = finalize(d, [m for m in metadata(d) if m["tokenContractAddress"] != GENSYN])
    assert counts(reduced)["excluded"] == counts(full)["excluded"] + 2


def test_wrong_platform_chain_mapping_rejected(sources, chain_map, overrides):
    values = chain_map.model_dump()
    values["networks"][0]["chain_index"] = "8453"
    with pytest.raises(ConfigError):
        prepare(sources, ChainMap.model_validate(values), overrides)


def test_duplicate_network_mapping_rejected(chain_map):
    values = chain_map.model_dump()
    values["networks"].append(values["networks"][0])
    with pytest.raises(ValidationError):
        ChainMap.model_validate(values)


def test_report_escapes_remote_display_text(sources, chain_map, overrides):
    sources["upbit_wallets"][-2]["network_name"] = "<script>alert(1)</script>|unsafe"
    result = report(build(sources, chain_map, overrides))
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_unmapped_network_report_includes_unresolved_coin_ids(sources, chain_map, overrides):
    sources["upbit_wallets"].append(
        {
            "currency": "MISSING",
            "net_type": "NEW_UNRESOLVED",
            "network_name": "Unresolved coin network",
            "wallet_state": "working",
        }
    )
    r = build(sources, chain_map, overrides)
    assert any(
        i["code"] == "unmapped_network" and i["net_type"] == "NEW_UNRESOLVED" for i in r.issues
    )
    assert "NEW_UNRESOLVED" in report(r)


def test_source_markets_include_unresolved_identity(sources, chain_map, overrides):
    sources["upbit_markets"].append(
        {"market": "KRW-UNRESOLVED", "korean_name": "미확인", "english_name": "Unresolved"}
    )
    registry = build(sources, chain_map, overrides)
    assert "KRW-UNRESOLVED" in registry.source_markets["upbit"]
    assert not any(
        "KRW-UNRESOLVED" in listing.markets
        for coin in registry.coins
        for listing in coin.exchanges.values()
    )
