from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from typing import Any

import pandas as pd

from option_intel.model import OptionType, Venue, VolPrint
from option_intel.vol import black76_vega


def to_frame(prints: Iterable[VolPrint]) -> pd.DataFrame:
    rows = [p.to_row() for p in prints]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
    return df


def moneyness(p: VolPrint) -> float:

    import math

    ref = p.forward or p.spot
    return math.log(p.instrument.strike / ref)


def bucket_moneyness(p: VolPrint) -> str:
    m = moneyness(p)
    if m < -0.15:
        return "deep_below"
    if m < -0.03:
        return "below"
    if m <= 0.03:
        return "atm"
    if m <= 0.15:
        return "above"
    return "deep_above"


def bucket_tenor(p: VolPrint) -> str:
    days = p.instrument.time_to_expiry(p.ts) * 365.0
    if days <= 2:
        return "0-2d"
    if days <= 7:
        return "3-7d"
    if days <= 30:
        return "8-30d"
    if days <= 90:
        return "31-90d"
    return "90d+"


def average_vol_surface(prints: Iterable[VolPrint]) -> list[dict[str, Any]]:

    groups: dict[tuple, list[VolPrint]] = defaultdict(list)
    for p in prints:
        if p.iv is None:
            continue
        key = (p.venue.value, p.instrument.underlying, bucket_tenor(p), bucket_moneyness(p))
        groups[key].append(p)

    out: list[dict[str, Any]] = []
    for (venue, underlying, tenor, money), items in sorted(groups.items()):
        weights = [i.notional for i in items]
        total = sum(weights)
        vw = sum(i.iv * w for i, w in zip(items, weights, strict=True)) / total if total else None
        ivs = [i.iv for i in items]
        out.append(
            {
                "venue": venue,
                "underlying": underlying,
                "tenor": tenor,
                "moneyness": money,
                "prints": len(items),
                "notional": total,
                "iv_vw": vw,
                "iv_median": statistics.median(ivs),
                "iv_min": min(ivs),
                "iv_max": max(ivs),
            }
        )
    return out


def maker_league(prints: Iterable[VolPrint], venue: Venue | None = None) -> list[dict[str, Any]]:

    groups: dict[str, list[VolPrint]] = defaultdict(list)
    for p in prints:
        if venue is not None and p.venue is not venue:
            continue
        if p.maker and p.iv is not None:
            groups[p.maker].append(p)

    venue_prints = [p for p in prints if p.iv is not None and (venue is None or p.venue is venue)]
    venue_notional = sum(p.notional for p in venue_prints)
    venue_iv = sum(p.iv * p.notional for p in venue_prints) / venue_notional if venue_notional else None

    out: list[dict[str, Any]] = []
    for maker, items in groups.items():
        notional = sum(i.notional for i in items)
        iv_vw = sum(i.iv * i.notional for i in items) / notional if notional else None
        out.append(
            {
                "maker": maker,
                "prints": len(items),
                "notional": notional,
                "share_of_notional": notional / venue_notional if venue_notional else None,
                "iv_vw": iv_vw,
                "vs_venue_vol_points": (iv_vw - venue_iv) * 100.0
                if iv_vw is not None and venue_iv is not None
                else None,
                "underlyings": sorted({i.instrument.underlying for i in items}),
                "calls": sum(1 for i in items if i.instrument.option_type is OptionType.CALL),
                "puts": sum(1 for i in items if i.instrument.option_type is OptionType.PUT),
            }
        )
    out.sort(key=lambda r: -r["notional"])
    return out


def cross_venue_gap(
    prints: Iterable[VolPrint],
    *,
    base: Venue = Venue.RYSK,
    reference: Venue = Venue.DERIVE,
    strike_tol: float = 0.03,
    expiry_tol_days: int = 3,
    time_tol_hours: float = 24.0,
) -> list[dict[str, Any]]:

    items = [p for p in prints if p.iv is not None]
    base_prints = [p for p in items if p.venue is base]
    ref_prints = [p for p in items if p.venue is reference]

    by_key: dict[tuple[str, OptionType], list[VolPrint]] = defaultdict(list)
    for r in ref_prints:
        by_key[(r.instrument.underlying, r.instrument.option_type)].append(r)

    out: list[dict[str, Any]] = []
    for p in base_prints:
        candidates = by_key.get((p.instrument.underlying, p.instrument.option_type), [])
        best: VolPrint | None = None
        best_cost = float("inf")
        for c in candidates:
            if abs(c.instrument.strike - p.instrument.strike) > strike_tol * p.instrument.strike:
                continue
            if abs((c.instrument.expiry - p.instrument.expiry).days) > expiry_tol_days:
                continue
            dt_hours = abs((c.ts - p.ts).total_seconds()) / 3600.0
            if dt_hours > time_tol_hours:
                continue
            cost = dt_hours + abs(c.instrument.strike - p.instrument.strike) / p.instrument.strike * 100
            if cost < best_cost:
                best, best_cost = c, cost
        if best is None:
            continue
        out.append(
            {
                "underlying": p.instrument.underlying,
                "option_type": p.instrument.option_type.value,
                "base_venue": base.value,
                "reference_venue": reference.value,
                "base_instrument": p.instrument.key,
                "reference_instrument": best.instrument.key,
                "ts": p.ts.isoformat(),
                "hours_apart": abs((best.ts - p.ts).total_seconds()) / 3600.0,
                "base_iv": p.iv,
                "reference_iv": best.iv,
                "gap_vol_points": (p.iv - best.iv) * 100.0,
                "base_notional": p.notional,
                "base_maker": p.maker,
                "reference_maker": best.maker,
                "tenor": bucket_tenor(p),
                "moneyness": bucket_moneyness(p),
            }
        )
    out.sort(key=lambda r: r["gap_vol_points"])
    return out


def daily_average_iv(prints: Iterable[VolPrint]) -> list[dict[str, Any]]:

    groups: dict[tuple[str, str, date], list[VolPrint]] = defaultdict(list)
    for p in prints:
        if p.iv is not None:
            groups[(p.venue.value, p.instrument.underlying, p.ts.date())].append(p)
    out = []
    for (venue, underlying, day), items in sorted(groups.items()):
        notional = sum(i.notional for i in items)
        out.append(
            {
                "date": day.isoformat(),
                "venue": venue,
                "underlying": underlying,
                "prints": len(items),
                "notional": notional,
                "iv_vw": sum(i.iv * i.notional for i in items) / notional if notional else None,
            }
        )
    return out


def effective_half_spread(
    prints: Iterable[VolPrint],
    *,
    venue: Venue = Venue.DERIVE,
    min_per_side: int = 20,
) -> dict[tuple[str, str, str], float]:

    sides: dict[tuple[str, str, str], dict[str, list[float]]] = defaultdict(lambda: {"buy": [], "sell": []})
    for p in prints:
        if p.venue is not venue or p.iv is None:
            continue
        direction = p.extra.get("direction")
        if direction not in ("buy", "sell"):
            continue
        key = (p.instrument.underlying, bucket_tenor(p), bucket_moneyness(p))
        sides[key][direction].append(p.iv)

    out: dict[tuple[str, str, str], float] = {}
    for key, side in sides.items():
        if len(side["buy"]) < min_per_side or len(side["sell"]) < min_per_side:
            continue
        round_trip = (statistics.median(side["buy"]) - statistics.median(side["sell"])) * 100.0
        if round_trip > 0:
            out[key] = round_trip / 2.0
    return out


def excess_over_spread(
    matches: list[dict[str, Any]],
    half_spreads: dict[tuple[str, str, str], float],
    fallback: dict[str, float] | None = None,
) -> list[dict[str, Any]]:

    fallback = fallback or {}
    out = []
    for m in matches:
        key = (m["underlying"], m.get("tenor", ""), m.get("moneyness", ""))
        half = half_spreads.get(key, fallback.get(m["underlying"]))
        enriched = dict(m)
        enriched["half_spread_vol_points"] = half
        enriched["excess_vol_points"] = m["gap_vol_points"] + half if half is not None else None
        out.append(enriched)
    return out


MAX_BENCHMARK_DISPERSION = 1.5


def bucket_benchmarks(
    prints: Iterable[VolPrint],
    *,
    min_prints: int = 5,
    max_dispersion: float = MAX_BENCHMARK_DISPERSION,
) -> dict[tuple, float]:

    groups: dict[tuple, list[float]] = defaultdict(list)
    for p in prints:
        if p.iv is None:
            continue
        key = (
            p.venue.value,
            p.instrument.underlying,
            bucket_tenor(p),
            bucket_moneyness(p),
            p.ts.date(),
        )
        groups[key].append(p.iv)

    out: dict[tuple, float] = {}
    for key, ivs in groups.items():
        if len(ivs) < min_prints:
            continue
        ivs.sort()
        spread = ivs[(3 * len(ivs)) // 4] - ivs[len(ivs) // 4]
        if spread > max_dispersion:
            continue
        out[key] = statistics.median(ivs)
    return out


def _vol_sign(p: VolPrint, side: str) -> int | None:

    if p.venue is Venue.RYSK:
        return -1 if side == "taker" else 1
    direction = p.extra.get("direction")
    if direction not in ("buy", "sell"):
        return None
    taker_long = 1 if direction == "buy" else -1
    return taker_long if side == "taker" else -taker_long


def edge_by_wallet(
    prints: Iterable[VolPrint],
    benchmarks: dict[tuple, float] | None = None,
    *,
    venue: Venue | None = None,
    min_fills: int = 5,
    clip_vol_points: float = 50.0,
) -> list[dict[str, Any]]:

    prints = [p for p in prints if p.iv is not None and (venue is None or p.venue is venue)]
    benchmarks = benchmarks if benchmarks is not None else bucket_benchmarks(prints)

    wallets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "fills": 0,
            "notional": 0.0,
            "vega": 0.0,
            "weighted": 0.0,
            "dollars": 0.0,
            "assets": set(),
            "as_maker": 0,
            "as_taker": 0,
        }
    )
    scored = 0
    for p in prints:
        key = (
            p.venue.value,
            p.instrument.underlying,
            bucket_tenor(p),
            bucket_moneyness(p),
            p.ts.date(),
        )
        benchmark = benchmarks.get(key)
        if benchmark is None:
            continue
        deviation = (p.iv - benchmark) * 100.0
        if deviation == 0.0:
            continue

        deviation = max(-clip_vol_points, min(clip_vol_points, deviation))
        t = p.instrument.time_to_expiry(p.ts)

        vega = black76_vega(p.forward or p.spot, p.instrument.strike, p.iv, max(t, 0.0)) * p.quantity / 100.0
        if vega <= 0:
            continue
        scored += 1
        for side, wallet in (("maker", p.maker), ("taker", p.taker)):
            if not wallet:
                continue
            sign = _vol_sign(p, side)
            if sign is None:
                continue
            paid = sign * deviation
            acc = wallets[wallet]
            acc["fills"] += 1
            acc["notional"] += p.notional
            acc["vega"] += vega
            acc["weighted"] += paid * vega
            acc["dollars"] += paid * vega
            acc["assets"].add(p.instrument.underlying)
            acc[f"as_{side}"] += 1

    out: list[dict[str, Any]] = []
    for wallet, acc in wallets.items():
        if acc["fills"] < min_fills or acc["vega"] <= 0:
            continue
        out.append(
            {
                "wallet": wallet,
                "fills": acc["fills"],
                "notional": acc["notional"],
                "dollar_vega": acc["vega"],
                "vol_points_paid": acc["weighted"] / acc["vega"],
                "dollars_paid": acc["dollars"],
                "assets": sorted(acc["assets"]),
                "as_maker": acc["as_maker"],
                "as_taker": acc["as_taker"],
            }
        )
    out.sort(key=lambda r: -r["vol_points_paid"])
    return out


MAKER_SHARE_FOR_MM = 0.6
FILLS_FOR_MM = 100
DAYS_FOR_MM = 20
DESK_CLIP_USD = 50_000
ACTIVE_FILLS = 50
RETAIL_CLIP_USD = 5_000
TOURIST_FILLS = 5


def classify_wallets(
    prints: Iterable[VolPrint],
    *,
    venue: Venue | None = None,
) -> dict[str, dict[str, Any]]:

    acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "maker_fills": 0,
            "taker_fills": 0,
            "days": set(),
            "clips": [],
            "notional": 0.0,
            "rfq": 0,
            "assets": set(),
            "calls": 0,
            "puts": 0,
        }
    )
    for p in prints:
        if venue is not None and p.venue is not venue:
            continue
        for side, wallet in (("maker", p.maker), ("taker", p.taker)):
            if not wallet:
                continue
            a = acc[wallet]
            a[f"{side}_fills"] += 1
            a["days"].add(p.ts.date())
            a["clips"].append(p.notional)
            a["notional"] += p.notional
            a["assets"].add(p.instrument.underlying)
            if p.extra.get("is_rfq"):
                a["rfq"] += 1
            if p.instrument.option_type is OptionType.CALL:
                a["calls"] += 1
            else:
                a["puts"] += 1

    out: dict[str, dict[str, Any]] = {}
    for wallet, a in acc.items():
        fills = a["maker_fills"] + a["taker_fills"]
        if not fills:
            continue
        maker_share = a["maker_fills"] / fills
        days = len(a["days"])
        clip = statistics.median(a["clips"])

        if maker_share >= MAKER_SHARE_FOR_MM and fills >= FILLS_FOR_MM and days >= DAYS_FOR_MM:
            cohort = "market_maker"
        elif clip >= DESK_CLIP_USD:
            cohort = "desk"
        elif fills >= ACTIVE_FILLS:
            cohort = "active"
        elif fills <= TOURIST_FILLS:
            cohort = "tourist"
        elif clip < RETAIL_CLIP_USD:
            cohort = "retail"
        else:
            cohort = "active"

        out[wallet] = {
            "wallet": wallet,
            "cohort": cohort,
            "fills": fills,
            "maker_share": maker_share,
            "active_days": days,
            "median_clip": clip,
            "notional": a["notional"],
            "rfq_share": a["rfq"] / fills,
            "assets": sorted(a["assets"]),
            "calls": a["calls"],
            "puts": a["puts"],
        }
    return out


def cohort_summary(
    wallets: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:

    edge_by = {e["wallet"]: e for e in (edges or [])}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for w in wallets.values():
        groups[w["cohort"]].append(w)

    total_notional = sum(w["notional"] for w in wallets.values()) or 1.0
    order = ["market_maker", "desk", "active", "retail", "tourist"]
    out = []
    for cohort in order:
        members = groups.get(cohort, [])
        if not members:
            continue
        scored = [edge_by[m["wallet"]] for m in members if m["wallet"] in edge_by]
        vega = sum(s["dollar_vega"] for s in scored)
        out.append(
            {
                "cohort": cohort,
                "wallets": len(members),
                "fills": sum(m["fills"] for m in members),
                "notional": sum(m["notional"] for m in members),
                "share_of_notional": sum(m["notional"] for m in members) / total_notional,
                "median_clip": statistics.median([m["median_clip"] for m in members]),
                "median_active_days": statistics.median([m["active_days"] for m in members]),
                "median_maker_share": statistics.median([m["maker_share"] for m in members]),
                "scored": len(scored),
                "vol_points_paid": (
                    sum(s["vol_points_paid"] * s["dollar_vega"] for s in scored) / vega if vega else None
                ),
                "dollars_paid": sum(s["dollars_paid"] for s in scored),
            }
        )
    return out
