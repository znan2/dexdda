"""Decimal-only book consumption. Exchange trading fees are intentionally excluded."""

from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.adapters.prices import number
from app.pricing.gap import decimal_text


@dataclass(frozen=True)
class Fill:
    requested: Decimal
    filled: Decimal
    proceeds: Decimal

    @property
    def complete(self):
        return self.filled == self.requested

    def public(self):
        return {
            "requested": decimal_text(self.requested),
            "filled": decimal_text(self.filled),
            "proceeds_krw": decimal_text(self.proceeds),
            "average_price_krw": decimal_text(self.proceeds / self.filled) if self.filled else None,
            "complete": self.complete,
        }


def consume_bids(quantity, bids):
    quantity = number(quantity)
    levels = [(number(p), number(q, zero=True)) for p, q in bids]
    with localcontext() as ctx:
        ctx.prec = 100
        remaining, proceeds = quantity, Decimal(0)
        for price, size in sorted(levels, reverse=True):
            take = min(remaining, size)
            proceeds += take * price
            remaining -= take
            if remaining == 0:
                break
        return Fill(quantity, quantity - remaining, proceeds)


def effective_result(amount, fill, fx, gas):
    amount, fx, gas = number(amount), number(fx), number(gas, zero=True)
    if not fill.complete:
        return None
    with localcontext() as ctx:
        ctx.prec = 100
        profit = fill.proceeds / fx - gas - amount
        return {
            "gap_percent": decimal_text(profit / amount * 100),
            "profit_usdt": decimal_text(profit),
            "profit_krw": decimal_text(profit * fx),
        }
