"""USDT-normalized surface gap. No CEX fee or fiat FX inputs."""

from decimal import Decimal, localcontext


def surface_gap(cex_krw: Decimal, dex_usd: Decimal, usdt_krw_ask: Decimal) -> Decimal:
    for value in (cex_krw, dex_usd, usdt_krw_ask):
        if not value.is_finite() or value <= 0:
            raise ValueError("prices must be finite and positive")
    with localcontext() as context:
        context.prec = 40
        return cex_krw / (dex_usd * usdt_krw_ask) - 1


def decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")
