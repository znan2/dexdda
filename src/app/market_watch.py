"""Distinguish first-seen exchange markets from unresolved registry identities."""

import json
import re
from pathlib import Path

from app.registry.loader import write_atomic

EXCHANGES = ("upbit", "bithumb")


def market_set(value):
    if (
        not isinstance(value, list)
        or len(value) > 10000
        or any(not isinstance(v, str) or not re.fullmatch(r"KRW-[A-Z0-9]+", v) for v in value)
    ):
        raise ValueError("invalid market list")
    return set(value)


class MarketWatch:
    def __init__(self, registry, path: Path | None = None):
        self.registry, self.path = registry, path
        self.baseline = {
            ex: set(getattr(registry, "source_markets", {}).get(ex, [])) for ex in EXCHANGES
        }
        self.seen = {ex: set(items) for ex, items in self.baseline.items()}
        self.pending = {ex: set() for ex in EXCHANGES}
        self.initialized = {ex: bool(self.baseline[ex]) for ex in EXCHANGES}
        if path:
            try:
                saved = json.loads(path.read_text())
                if saved.get("version") != 1:
                    raise ValueError("unsupported version")
                # Validate all exchanges before applying any persisted state.
                seen = {ex: market_set(saved["seen"][ex]) for ex in EXCHANGES}
                pending = {ex: market_set(saved["pending"][ex]) for ex in EXCHANGES}
                initialized = saved["initialized"]
                if any(type(initialized[ex]) is not bool for ex in EXCHANGES):
                    raise ValueError("invalid initialization state")
                for ex in EXCHANGES:
                    self.seen[ex] |= seen[ex]
                    self.pending[ex] = pending[ex] & seen[ex]
                    self.initialized[ex] |= initialized[ex]
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass

    def observe(self, exchange, incoming):
        known = {
            market
            for coin in self.registry.coins
            if exchange in coin.exchanges
            for market in coin.exchanges[exchange].markets
        }
        incoming = market_set(sorted(incoming))
        fresh = incoming - self.seen[exchange] if self.initialized[exchange] else set()
        pending = ((self.pending[exchange] | fresh) & incoming) - known - self.baseline[exchange]
        seen = {**self.seen, exchange: self.seen[exchange] | incoming}
        all_pending = {**self.pending, exchange: pending}
        initialized = {**self.initialized, exchange: True}
        if self.path:
            write_atomic(
                self.path,
                json.dumps(
                    {
                        "version": 1,
                        "seen": {ex: sorted(v) for ex, v in seen.items()},
                        "pending": {ex: sorted(v) for ex, v in all_pending.items()},
                        "initialized": initialized,
                    }
                ),
            )
        self.seen, self.pending, self.initialized = seen, all_pending, initialized
        reasons = {
            "missing_coin_id": "코인 ID 연결 정보 없음",
            "missing_coin_record": "연결된 코인 ID가 코인 목록에 없음",
            "ambiguous_coin_id": "여러 코인 ID가 연결되어 확인 필요",
        }
        issues = getattr(self.registry, "issues", [])
        held = []
        for market in sorted(incoming - known - pending):
            codes = sorted(
                {
                    i["code"]
                    for i in issues
                    if i.get("exchange") == exchange
                    and i.get("symbol") == market[4:]
                    and i.get("code") in reasons
                }
            )
            held.append(
                {
                    "market": market,
                    "reason": " / ".join(reasons[c] for c in codes) or "코인 연결 정보 확인 필요",
                }
            )
        return {"new": sorted(pending), "mapping_pending": held}
