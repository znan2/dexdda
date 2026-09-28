"""Coin-level route comparison. Registry identity remains the only source of rows."""

from decimal import Decimal

from app.pricing.filters import suspected


def coin_groups(collector):
    groups = []
    for coin in collector.registry.coins:
        if coin.excluded_reason or collector.preferences.blocked(coin.coin_id):
            continue
        routes = []
        for token in coin.tokens:
            if not set(token.status.values()) & {"tradable", "bridge_candidate"}:
                continue
            row, gaps = collector.row(coin, token, "comparison")
            row["suspected"] = suspected(gaps, collector.settings.filters)
            row["low_liquidity"] = "low_liquidity" in row["warnings"]
            if row["suspected"]:
                row["warnings"].append("gap_out_of_range")
            if collector.settings.filters.unrecognized_action == "hide" and any(
                w in row["warnings"]
                for w in ("community_unrecognized", "community_recognition_unknown")
            ):
                continue
            routes.append(row)
        if not routes:
            continue
        # Ranking by liquidity, never by an implausibly large price gap.
        routes.sort(
            key=lambda r: (
                not r["liquidity_usd"]["usable"],
                -Decimal(r["liquidity_usd"]["value"] or "0"),
                int(r["chain_index"]),
                r["address"],
            )
        )
        groups.append(
            {
                "coin_id": coin.coin_id,
                "symbol": coin.symbol,
                "routes": routes,
                "liquidity_reused": any(r["liquidity_usd"]["reused"] for r in routes),
                "liquidity_comparison_complete": all(r["liquidity_usd"]["usable"] for r in routes),
            }
        )
    return groups
