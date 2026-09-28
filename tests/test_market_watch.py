from types import SimpleNamespace

import pytest
from test_pricing import coin, token

from app.market_watch import MarketWatch


def registry():
    return SimpleNamespace(
        coins=[coin([token()])],
        source_markets={ex: ["KRW-COIN", "KRW-HELD"] for ex in ("upbit", "bithumb")},
        issues=[{"exchange": "upbit", "symbol": "HELD", "code": "missing_coin_id"}],
    )


def test_existing_unmapped_is_not_new_and_restart_preserves_new(tmp_path):
    r = registry()
    path = tmp_path / "listings.json"
    watch = MarketWatch(r, path)
    rows = {"KRW-COIN", "KRW-HELD"}
    first = watch.observe("upbit", rows)
    assert first["new"] == []
    assert first["mapping_pending"] == [{"market": "KRW-HELD", "reason": "코인 ID 연결 정보 없음"}]
    assert watch.observe("upbit", rows)["new"] == []
    assert watch.observe("upbit", rows | {"KRW-NEW"})["new"] == ["KRW-NEW"]
    watch = MarketWatch(r, path)
    assert watch.observe("upbit", rows | {"KRW-NEW"})["new"] == ["KRW-NEW"]
    assert watch.observe("bithumb", rows)["new"] == []
    # Rebuilding with an unresolved new listing reclassifies it as mapping pending.
    r.source_markets["upbit"].append("KRW-NEW")
    rebuilt = MarketWatch(r, path)
    result = rebuilt.observe("upbit", rows | {"KRW-NEW"})
    assert result["new"] == []
    assert {item["market"] for item in result["mapping_pending"]} == {"KRW-HELD", "KRW-NEW"}
    # Confirmed registry entries clear pending without changing contracts or execution.
    r.coins += [coin([token()], "held"), coin([token()], "new")]
    assert MarketWatch(r, path).observe("upbit", rows | {"KRW-NEW"}) == {
        "new": [],
        "mapping_pending": [],
    }


def test_legacy_or_corrupt_baseline_does_not_claim_first_snapshot_new(tmp_path):
    r = registry()
    r.source_markets = {}
    path = tmp_path / "listings.json"
    path.write_text('{"version":1,"seen":null}')
    watch = MarketWatch(r, path)
    rows = {"KRW-COIN", "KRW-HELD"}
    assert watch.observe("upbit", rows)["new"] == []
    assert watch.observe("upbit", rows | {"KRW-LATER"})["new"] == ["KRW-LATER"]
    # Existing classified markets stay separate even without any source snapshot.
    assert watch.observe("upbit", rows)["mapping_pending"][0]["market"] == "KRW-HELD"


def test_failed_persistence_does_not_consume_new_listing(tmp_path, monkeypatch):
    watch = MarketWatch(registry(), tmp_path / "state.json")

    def fail(*args):
        raise OSError("cannot save")

    monkeypatch.setattr("app.market_watch.write_atomic", fail)
    with pytest.raises(OSError):
        watch.observe("upbit", {"KRW-NEW"})
    assert "KRW-NEW" not in watch.seen["upbit"]
