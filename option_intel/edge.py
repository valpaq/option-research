from __future__ import annotations

import csv
import math
import statistics as st
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from option_intel.model import OptionType
from option_intel.settlement import Settled
from option_intel.spot import spot_symbol
from option_intel.vol import black76_price, black76_vega, solve_iv


MIN_OBSERVATIONS = 12


STEPS: tuple[int, ...] = (1, 4, 24)


HOURS_A_YEAR = 24 * 365


TENORS: tuple[tuple[str, float, float], ...] = (
    ("0-3d", 0.0, 3.0),
    ("3-7d", 3.0, 7.0),
    ("7-14d", 7.0, 14.0),
    ("14-30d", 14.0, 30.0),
    ("30d+", 30.0, math.inf),
)


def hour_index(path: Path) -> dict[str, dict[int, float]]:

    out: dict[str, dict[int, float]] = {}
    with Path(path).open() as fh:
        for row in csv.reader(fh):
            if len(row) != 3:
                continue
            try:
                out.setdefault(row[0], {})[int(row[1])] = float(row[2])
            except ValueError:
                continue
    return out


def covers(index: dict[int, float], start: datetime, end: datetime) -> bool:

    if not index:
        return False
    return min(index) <= int(start.timestamp()) // 3600 and int(end.timestamp()) // 3600 <= max(index)


def closes(index: dict[int, float], start: datetime, end: datetime, step: int = 1) -> list[float]:

    first = int(start.timestamp()) // 3600 + 1
    last = int(end.timestamp()) // 3600
    return [index[h] for h in range(first, last + 1, step) if h in index]


def realised(series: Sequence[float], step: int = 1) -> float | None:

    need = max(MIN_OBSERVATIONS, 2)
    if len(series) < need:
        return None
    steps = [math.log(b / a) for a, b in zip(series, series[1:], strict=False) if a > 0 and b > 0]
    if len(steps) < need - 1:
        return None
    return st.pstdev(steps) * math.sqrt(HOURS_A_YEAR / step)


def implied(row: Settled, gross: bool = False) -> tuple[float | None, str | None]:

    if not row.contracts:
        return None, "no contracts"
    t = row.held_days / 365
    price = (row.premium + row.fee if gross else row.premium) / row.contracts
    iv, why = solve_iv(price, row.spot_at_write, row.strike, t, row.option_type)
    return iv, (None if iv is not None else str(why))


def vega(row: Settled, iv: float) -> float:

    t = row.held_days / 365
    return black76_vega(row.spot_at_write, row.strike, iv, t) * row.contracts / 100


def repriced(row: Settled, vol: float) -> float:

    t = row.held_days / 365
    return black76_price(row.spot_at_write, row.strike, vol, t, row.option_type) * row.contracts


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:

    if len(xs) < 3:
        return None
    mx, my = st.fmean(xs), st.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else None


def tenor_of(days: float) -> str:
    for label, lo, hi in TENORS:
        if lo <= days < hi:
            return label
    return TENORS[-1][0]


class Print:
    __slots__ = ("row", "iv", "rv", "prior", "vega", "hours")

    def __init__(self, row: Settled, iv: float, rv: float, prior: float | None, v: float, hours: int) -> None:
        self.row, self.iv, self.rv, self.prior = row, iv, rv, prior
        self.vega, self.hours = v, hours

    @property
    def gap(self) -> float:

        return self.iv - self.rv

    @property
    def gap_prior(self) -> float | None:

        return None if self.prior is None else self.iv - self.prior


def prints(
    rows: Iterable[Settled], index: dict[str, dict[int, float]], step: int = 1, gross: bool = False
) -> tuple[list[Print], dict[str, int]]:

    out: list[Print] = []
    why: dict[str, int] = {}

    def drop(reason: str) -> None:
        why[reason] = why.get(reason, 0) + 1

    for row in rows:
        iv, failure = implied(row, gross)
        if iv is None:
            drop(f"implied: {failure}")
            continue
        coin = index.get(spot_symbol(row.underlying), {})
        if not covers(coin, row.written_at, row.expired_at):
            drop("realised: hourly closes do not span the option's life")
            continue
        series = closes(coin, row.written_at, row.expired_at, step)
        rv = realised(series, step)
        if rv is None:
            drop("realised: too few closes over the option's life")
            continue

        life = row.expired_at - row.written_at
        prior = (
            realised(closes(coin, row.written_at - life, row.written_at, step), step)
            if covers(coin, row.written_at - life, row.written_at)
            else None
        )
        out.append(Print(row, iv, rv, prior, vega(row, iv), len(series)))
    return out, why


def _summary(group: Sequence[Print]) -> dict:

    if not group:
        return {}
    gaps = sorted(p.gap for p in group)
    priors = sorted(p.gap_prior for p in group if p.gap_prior is not None)
    total_vega = sum(p.vega for p in group)
    return {
        "options": len(group),
        "implied": st.fmean(p.iv for p in group),
        "realised": st.fmean(p.rv for p in group),
        "gap_mean": st.fmean(gaps),
        "gap_median": st.median(gaps),
        "gap_p25": gaps[len(gaps) // 4],
        "gap_p75": gaps[3 * len(gaps) // 4],
        "underpaid_share": sum(1 for g in gaps if g < 0) / len(gaps),
        "gap_vega_weighted": (sum(p.gap * p.vega for p in group) / total_vega if total_vega > 0 else None),
        "vega": total_vega,
        "prior_median": st.median(priors) if priors else None,
        "prior_underpaid_share": (sum(1 for g in priors if g < 0) / len(priors) if priors else None),
        "prior_options": len(priors),
    }


def measure(
    rows: Iterable[Settled], hourly: Path, *, assets: Sequence[str] = ("HYPE", "ETH", "BTC", "SOL")
) -> dict:

    rows = list(rows)
    index = hour_index(hourly)
    priced, dropped = prints(rows, index, STEPS[0])
    total = sum(dropped.values()) + len(priced)
    if not priced:
        raise ValueError("no option admitted both volatilities")

    by_asset = {a: _summary([p for p in priced if p.row.underlying == a]) for a in assets}
    by_tenor = {
        label: _summary([p for p in priced if tenor_of(p.row.held_days) == label]) for label, _, _ in TENORS
    }
    by_type = {
        "call": _summary([p for p in priced if p.row.option_type is OptionType.CALL]),
        "put": _summary([p for p in priced if p.row.option_type is OptionType.PUT]),
    }

    months: dict[str, list[Print]] = {}
    for p in priced:
        months.setdefault(f"{p.row.written_at:%Y-%m}", []).append(p)
    by_month = {k: _summary(v) for k, v in sorted(months.items())}

    taken = sum(p.row.premium for p in priced)
    fair = sum(repriced(p.row, p.rv) for p in priced)

    ivs = [v["implied"] for v in by_month.values() if v]
    rvs = [v["realised"] for v in by_month.values() if v]
    surface = {
        "months": len(ivs),
        "implied_range": max(ivs) - min(ivs),
        "realised_range": max(rvs) - min(rvs),
        "implied_low": min(ivs),
        "implied_high": max(ivs),
        "realised_low": min(rvs),
        "realised_high": max(rvs),
        "correlation": _pearson(ivs, rvs),
        "months_underpaid": sum(
            1
            for v in by_month.values()
            if v and v["gap_vega_weighted"] is not None and v["gap_vega_weighted"] < 0
        ),
    }

    return {
        "all": _summary(priced),
        "by_asset": {k: v for k, v in by_asset.items() if v},
        "by_tenor": {k: v for k, v in by_tenor.items() if v},
        "by_type": {k: v for k, v in by_type.items() if v},
        "by_month": by_month,
        "coverage": {
            "priced": len(priced),
            "of": total,
            "share": len(priced) / total if total else None,
            "dropped": dict(sorted(dropped.items(), key=lambda kv: -kv[1])),
        },
        "surface": surface,
        "money": {
            "premium_taken": taken,
            "premium_at_realised": fair,
            "difference": taken - fair,
            "share_of_premium": (taken - fair) / taken if taken else None,
        },
        "by_sampling": {f"{s}h": _summary(prints(rows, index, s)[0]) for s in STEPS},
        "at_gross_premium": _summary(prints(rows, index, STEPS[0], True)[0]),
        "convention": {
            "min_observations": MIN_OBSERVATIONS,
            "hours_a_year": HOURS_A_YEAR,
            "steps": list(STEPS),
            "sign": "implied minus realised, from the writer's side",
        },
        "asof": datetime.now(UTC).isoformat(),
    }
