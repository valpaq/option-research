from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from option_intel import flow
from option_intel.edge import implied, vega
from option_intel.model import OptionType
from option_intel.settlement import Settled


MAJORS = ("HYPE", "ETH", "BTC", "SOL")


MIN_CELL = 10


MIN_PAIRS = 20


MOSTLY_ASSIGNED = 0.5


DRAWS = 2000


def notional(row: Settled) -> float:

    return row.contracts * row.spot_at_write


def cell(rows: list[Settled]) -> dict[str, Any]:

    paid = sum(r.premium for r in rows)
    lots = sum(r.contracts for r in rows)
    struck = sum(r.strike * r.contracts for r in rows)
    at = sum(r.spot_at_write * r.contracts for r in rows)
    solved, weights, refused = [], [], 0
    for r in rows:
        iv, _ = implied(r)
        if iv is None:
            refused += 1
            continue
        solved.append(iv)
        weights.append(vega(r, iv))
    total = sum(weights)
    return {
        "options": len(rows),
        "contracts": lots,
        "notional": sum(notional(r) for r in rows),
        "premium": paid,
        "strike": struck / lots if lots else 0.0,
        "spot_at_write": at / lots if lots else 0.0,
        "distance": abs(struck / at - 1) if at else 0.0,
        "price_per_contract": paid / lots if lots else 0.0,
        "implied": (sum(v * x for v, x in zip(solved, weights, strict=True)) / total if total else None),
        "solved": len(solved),
        "refused": refused,
        "assigned": (sum(r.contracts for r in rows if r.assigned) / lots if lots else 0.0),
        "tenor_days": (sum(r.held_days * r.contracts for r in rows) / lots if lots else 0.0),
    }


def settlement_price(rows: list[Settled]) -> float | None:

    if not rows:
        return None
    return max(rows, key=lambda r: r.contracts).spot_at_expiry


def series(
    rows: list[Settled],
    *,
    assets: tuple[str, ...] = MAJORS,
    min_cell: int = MIN_CELL,
) -> dict[str, Any]:

    buckets: dict[tuple[str, str, date], list[Settled]] = defaultdict(list)
    prices: dict[tuple[str, date], list[Settled]] = defaultdict(list)
    excluded = 0
    for r in rows:
        if r.underlying not in assets:
            excluded += 1
            continue
        side = "call" if r.option_type is OptionType.CALL else "put"
        day = r.expired_at.date()
        buckets[(r.underlying, side, day)].append(r)
        prices[(r.underlying, day)].append(r)

    out: dict[str, Any] = {a: {"call": [], "put": []} for a in assets}
    thin = thin_options = 0
    for (asset, side, day), group in sorted(buckets.items(), key=lambda kv: kv[0][2]):
        if len(group) < min_cell:
            thin += 1
            thin_options += len(group)
            continue
        out[asset][side].append({"expiry": day.isoformat(), **cell(group)})

    settled = {
        a: [
            {"expiry": day.isoformat(), "price": settlement_price(group)}
            for (asset, day), group in sorted(prices.items(), key=lambda kv: kv[0][1])
            if asset == a
        ]
        for a in assets
    }

    quoted = [c for a in assets for s in ("call", "put") for c in out[a][s]]
    dates = sorted({c["expiry"] for c in quoted})
    return {
        "min_cell": min_cell,
        "mostly_assigned": MOSTLY_ASSIGNED,
        "assets": list(assets),
        "cells": len(quoted),
        "thin_cells": thin,
        "thin_options": thin_options,
        "excluded_options": excluded,
        "expiries": len(dates),
        "by_week": out,
        "oracle": settled,
        "oracle_prints": {a: len(settled[a]) for a in assets},
        "expiry_cadence": cadence(rows, assets),
    }


def cadence(rows: list[Settled], assets: tuple[str, ...]) -> dict[str, Any]:

    dates = sorted({r.expired_at.date() for r in rows if r.underlying in assets})
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    if not gaps:
        return {}
    ordered = sorted(gaps)
    return {
        "expiries": len(dates),
        "gaps": len(gaps),
        "weekly_gaps": sum(g == 7 for g in gaps),
        "median_gap_days": ordered[len(ordered) // 2],
        "longest_gap_days": ordered[-1],
    }


def spread(weekly: dict[str, Any]) -> dict[str, Any]:

    out = {}
    for asset in weekly["assets"]:
        both = weekly["by_week"][asset]
        calls = {c["expiry"]: c for c in both["call"]}
        puts = {p["expiry"]: p for p in both["put"]}
        shared = sorted(set(calls) & set(puts))
        if not shared:
            continue
        skews = [
            puts[d]["implied"] - calls[d]["implied"]
            for d in shared
            if puts[d]["implied"] is not None and calls[d]["implied"] is not None
        ]
        ringed = [
            (side, c) for side in ("call", "put") for c in both[side] if c["assigned"] > MOSTLY_ASSIGNED
        ]

        worst = max(ringed, key=lambda kv: kv[1]["contracts"], default=None)
        gaps = sorted(calls[d]["distance"] - puts[d]["distance"] for d in shared)
        call_far = sorted(c["distance"] for c in both["call"])
        put_far = sorted(p["distance"] for p in both["put"])
        skews.sort()
        out[asset] = {
            "expiries": len(shared),
            "call_distance_median": call_far[len(call_far) // 2],
            "put_distance_median": put_far[len(put_far) // 2],
            "weeks_calls_further": sum(g > 0 for g in gaps),
            "put_over_call_median": skews[len(skews) // 2] if skews else None,
            "weeks_puts_dearer": sum(s > 0 for s in skews),
            "weeks_compared": len(skews),
            "ringed_expiries": len(ringed),
            "calls_breached": sum(1 for c in both["call"] if c["assigned"] > MOSTLY_ASSIGNED),
            "puts_breached": sum(1 for p in both["put"] if p["assigned"] > MOSTLY_ASSIGNED),
            "busiest_assigned": (
                {
                    "side": worst[0],
                    "expiry": worst[1]["expiry"],
                    "strike": worst[1]["strike"],
                    "spot_at_write": worst[1]["spot_at_write"],
                    "contracts": worst[1]["contracts"],
                    "assigned": worst[1]["assigned"],
                }
                if worst
                else None
            ),
            "implied_low": min(
                c["implied"] for s in ("call", "put") for c in both[s] if c["implied"] is not None
            ),
            "implied_high": max(
                c["implied"] for s in ("call", "put") for c in both[s] if c["implied"] is not None
            ),
            "price_low": min(c["price_per_contract"] for s in ("call", "put") for c in both[s]),
            "price_high": max(c["price_per_contract"] for s in ("call", "put") for c in both[s]),
        }
    return out


def against_price(weekly: dict[str, Any]) -> dict[str, Any]:

    out = {}
    for asset in weekly["assets"]:
        prices = {d["expiry"]: d["price"] for d in weekly["oracle"].get(asset) or []}
        pairs = []
        for side in ("put", "call"):
            for c in weekly["by_week"][asset][side]:
                settled = prices.get(c["expiry"])
                if c["implied"] is None or not settled or not c["spot_at_write"]:
                    continue
                pairs.append((c["implied"], settled / c["spot_at_write"] - 1))
        if len(pairs) < MIN_PAIRS:
            continue
        xs = [a for a, _ in pairs]
        ys = [b for _, b in pairs]
        out[asset] = {
            "cells": len(pairs),
            "rho": flow.spearman(xs, ys),
            "p": flow.permutation_p(xs, ys, draws=DRAWS),
            "detectable": flow.detectable(xs, ys, draws=DRAWS),
        }
    return out


def measure(rows: list[Settled], **kwargs: Any) -> dict[str, Any]:
    weekly = series(rows, **kwargs)
    return {**weekly, "spread": spread(weekly), "against_price": against_price(weekly)}
