from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from option_intel.insight import bucket_moneyness, bucket_tenor
from option_intel.model import Instrument, OptionType, VolPrint
from option_intel.rates import forward_and_discount
from option_intel.spot import SpotSource
from option_intel.vol import black76_price, intrinsic


@dataclass(frozen=True, slots=True)
class Position:
    writer: str
    protocol: str
    instrument: Instrument
    contracts: float
    premium_received: float
    opened_at: datetime
    asof: datetime
    spot: float
    sold_iv: float | None
    mark_iv: float | None

    @property
    def side(self) -> str:

        return "short_call" if self.instrument.option_type is OptionType.CALL else "short_put"

    @property
    def days_left(self) -> float:
        return max(self.instrument.time_to_expiry(self.asof) * 365.0, 0.0)

    @property
    def distance_to_strike(self) -> float:

        gap = (
            self.instrument.strike - self.spot
            if self.instrument.option_type is OptionType.CALL
            else self.spot - self.instrument.strike
        )
        return gap / self.spot

    @property
    def in_the_money(self) -> bool:
        return self.distance_to_strike < 0

    @property
    def intrinsic_owed(self) -> float:

        return intrinsic(self.spot, self.instrument.strike, self.instrument.option_type) * self.contracts

    @property
    def mark_value(self) -> float | None:

        if self.mark_iv is None:
            return None
        t = self.instrument.time_to_expiry(self.asof)
        if t <= 0:
            return self.intrinsic_owed
        forward, discount = forward_and_discount(self.spot, 0.0, t)
        price = black76_price(
            forward, self.instrument.strike, self.mark_iv, t, self.instrument.option_type, discount
        )
        return price * self.contracts

    @property
    def unrealized(self) -> float | None:

        value = self.mark_value
        return None if value is None else self.premium_received - value

    @property
    def unrealized_intrinsic(self) -> float:

        return self.premium_received - self.intrinsic_owed


def mark_ivs(reference: Iterable[VolPrint], asof: datetime, *, lookback_days: int = 7) -> dict[tuple, float]:

    cutoff = asof.timestamp() - lookback_days * 86400
    groups: dict[tuple, list[float]] = defaultdict(list)
    for p in reference:
        if p.iv is None or p.ts.timestamp() < cutoff:
            continue
        groups[
            (
                p.instrument.underlying,
                p.instrument.option_type.value,
                bucket_tenor(p),
                bucket_moneyness(p),
            )
        ].append(p.iv)
    return {k: statistics.median(v) for k, v in groups.items() if len(v) >= 3}


def open_positions(
    writes: Iterable[VolPrint],
    spot: SpotSource,
    *,
    reference: Iterable[VolPrint] | None = None,
    asof: datetime | None = None,
) -> tuple[list[Position], dict[str, int]]:

    asof = asof or datetime.now(UTC)
    marks = mark_ivs(reference or [], asof)

    skipped: dict[str, int] = defaultdict(int)
    out: list[Position] = []
    for w in writes:
        if w.instrument.expiry <= asof.date():
            skipped["expired"] += 1
            continue
        if not w.taker:
            skipped["no_writer"] += 1
            continue
        px = spot.price_at(w.instrument.underlying, asof)
        if px is None or px <= 0:
            skipped[f"no_spot:{w.instrument.underlying}"] += 1
            continue

        position = Position(
            writer=w.taker,
            protocol=str(w.extra.get("protocol", "")),
            instrument=w.instrument,
            contracts=w.quantity,
            premium_received=w.premium_total,
            opened_at=w.ts,
            asof=asof,
            spot=px,
            sold_iv=w.iv,
            mark_iv=marks.get(
                (
                    w.instrument.underlying,
                    w.instrument.option_type.value,
                    bucket_tenor(w),
                    bucket_moneyness(w),
                )
            ),
        )
        out.append(position)
    out.sort(key=lambda p: p.distance_to_strike)
    return out, dict(skipped)


def underwater(positions: Iterable[Position]) -> list[Position]:

    return sorted((p for p in positions if p.in_the_money), key=lambda p: p.distance_to_strike)


def by_writer(positions: Iterable[Position]) -> list[dict[str, Any]]:

    groups: dict[str, list[Position]] = defaultdict(list)
    for p in positions:
        groups[p.writer].append(p)

    out = []
    for writer, items in groups.items():
        marked = [p for p in items if p.unrealized is not None]
        itm = [p for p in items if p.in_the_money]
        out.append(
            {
                "writer": writer,
                "positions": len(items),
                "protocols": sorted({p.protocol for p in items if p.protocol}),
                "assets": sorted({p.instrument.underlying for p in items}),
                "short_calls": sum(1 for p in items if p.side == "short_call"),
                "short_puts": sum(1 for p in items if p.side == "short_put"),
                "contracts": sum(p.contracts for p in items),
                "premium_received": sum(p.premium_received for p in items),
                "in_the_money": len(itm),
                "intrinsic_owed": sum(p.intrinsic_owed for p in items),
                "unrealized_intrinsic": sum(p.unrealized_intrinsic for p in items),
                "unrealized_marked": sum(p.unrealized for p in marked) if marked else None,
                "marked": len(marked),
                "closest_to_strike": min(p.distance_to_strike for p in items),
                "days_left_median": statistics.median([p.days_left for p in items]),
            }
        )
    out.sort(key=lambda r: r["unrealized_intrinsic"])
    return out


def by_side(positions: Iterable[Position]) -> list[dict[str, Any]]:

    groups: dict[tuple[str, str], list[Position]] = defaultdict(list)
    for p in positions:
        groups[(p.instrument.underlying, p.side)].append(p)

    out = []
    for (underlying, side), items in groups.items():
        itm = [p for p in items if p.in_the_money]
        marked = [p for p in items if p.unrealized is not None]
        out.append(
            {
                "underlying": underlying,
                "side": side,
                "positions": len(items),
                "writers": len({p.writer for p in items}),
                "contracts": sum(p.contracts for p in items),
                "premium_received": sum(p.premium_received for p in items),
                "in_the_money": len(itm),
                "share_itm": len(itm) / len(items),
                "intrinsic_owed": sum(p.intrinsic_owed for p in items),
                "unrealized_intrinsic": sum(p.unrealized_intrinsic for p in items),
                "unrealized_marked": sum(p.unrealized for p in marked) if marked else None,
                "median_distance_to_strike": statistics.median([p.distance_to_strike for p in items]),
                "median_days_left": statistics.median([p.days_left for p in items]),
            }
        )
    out.sort(key=lambda r: -r["premium_received"])
    return out
