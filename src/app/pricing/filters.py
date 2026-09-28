from decimal import Decimal

from app.config import FilterSettings


def liquidity_warnings(
    liquidity: Decimal | None, recognized: bool | None, settings: FilterSettings
) -> list[str]:
    warnings = []
    if liquidity is None:
        warnings.append("liquidity_unknown_or_stale")
    elif liquidity < Decimal(settings.min_liquidity_usd):
        warnings.append("low_liquidity")
    if recognized is False:
        warnings.append("community_unrecognized")
    elif recognized is None:
        warnings.append("community_recognition_unknown")
    return warnings


def suspected(gaps: list[Decimal], settings: FilterSettings) -> bool:
    return any(abs(gap * 100) > Decimal(settings.max_abs_gap_percent) for gap in gaps)
