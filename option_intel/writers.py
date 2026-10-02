from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any

from option_intel.model import OptionType
from option_intel.settlement import Settled


ACTIVITY_BANDS = (
    ("once", 1, 1),
    ("2-5", 2, 5),
    ("6-20", 6, 20),
    ("21-100", 21, 100),
    ("100+", 101, None),
)


def distance(row: Settled) -> float:

    d = math.log(row.strike / row.spot_at_write)
    return d if row.option_type is OptionType.CALL else -d


def by_wallet(rows: list[Settled]) -> dict[str, list[Settled]]:

    groups: dict[str, list[Settled]] = defaultdict(list)
    for r in rows:
        if r.writer:
            groups[r.writer].append(r)
    for items in groups.values():
        items.sort(key=lambda i: i.written_at)
    return dict(groups)


def _summarise(items: list[Settled]) -> dict[str, Any]:
    premium = sum(i.premium for i in items)
    paid = sum(i.intrinsic_paid for i in items)
    assigned = sum(1 for i in items if i.assigned)
    return {
        "settled": len(items),
        "premium": premium,
        "intrinsic_paid": paid,
        "realised": premium - paid,
        "keep_rate": (premium - paid) / premium if premium else None,
        "assigned": assigned,
        "assigned_share": assigned / len(items) if items else None,
        "median_distance": statistics.median(distance(i) for i in items),
        "median_held_days": statistics.median(i.held_days for i in items),
        "call_share": sum(1 for i in items if i.option_type is OptionType.CALL) / len(items),
    }


def population(rows: list[Settled]) -> list[dict[str, Any]]:

    wallets = by_wallet(rows)
    total_premium = sum(i.premium for items in wallets.values() for i in items) or 1.0
    total_loss = sum(min(sum(i.realised for i in items), 0.0) for items in wallets.values()) or -1.0

    out = []
    for label, lo, hi in ACTIVITY_BANDS:
        members = [
            items for items in wallets.values() if lo <= len(items) and (hi is None or len(items) <= hi)
        ]
        if not members:
            continue
        flat = [i for items in members for i in items]
        summary = _summarise(flat)
        losses = sum(min(sum(i.realised for i in items), 0.0) for items in members)
        ahead = sum(1 for items in members if sum(i.realised for i in items) > 0)
        out.append(
            {
                "band": label,
                "writers": len(members),
                "writer_share": len(members) / len(wallets),
                "ahead": ahead,
                "ahead_rate": ahead / len(members),
                "premium_share": summary["premium"] / total_premium,
                "loss_share": losses / total_loss,
                **summary,
            }
        )
    return out


def concentration_curve(rows: list[Settled], *, points: int = 20) -> list[dict[str, Any]]:

    totals = sorted(sum(i.realised for i in items) for items in by_wallet(rows).values())
    losses = [t for t in totals if t < 0]
    if not losses:
        return []
    grand = sum(losses)
    out, running = [], 0.0
    step = max(len(losses) // points, 1)
    for i, value in enumerate(losses, start=1):
        running += value
        if i % step == 0 or i == len(losses):
            out.append(
                {
                    "wallets": i,
                    "wallet_share": i / len(losses),
                    "loss_share": running / grand,
                }
            )
    return out


def learning(rows: list[Settled], *, min_each_side: int = 3) -> dict[str, Any]:

    deltas: list[dict[str, float]] = []
    sides: dict[str, list[dict[str, float]]] = {"before": [], "after": []}
    eligible = 0
    for items in by_wallet(rows).values():
        first = next((n for n, s in enumerate(items) if s.assigned), None)
        if first is None:
            continue
        before, after = items[:first], items[first + 1 :]
        if len(before) < min_each_side or len(after) < min_each_side:
            continue
        eligible += 1
        for name, half in (("before", before), ("after", after)):
            sides[name].append(
                {
                    "distance": statistics.median(distance(i) for i in half),
                    "held_days": statistics.median(i.held_days for i in half),
                    "premium": statistics.median(i.premium for i in half),
                    "assigned_share": sum(1 for i in half if i.assigned) / len(half),
                }
            )
        deltas.append(
            {
                "distance": (
                    statistics.median(distance(i) for i in after)
                    - statistics.median(distance(i) for i in before)
                ),
                "held_days": (
                    statistics.median(i.held_days for i in after)
                    - statistics.median(i.held_days for i in before)
                ),
                "premium": (
                    statistics.median(i.premium for i in after) - statistics.median(i.premium for i in before)
                ),
                "assigned_share": (
                    sum(1 for i in after if i.assigned) / len(after)
                    - sum(1 for i in before if i.assigned) / len(before)
                ),
            }
        )

    if not deltas:
        return {"wallets": 0}

    out: dict[str, Any] = {
        "wallets": eligible,
        "before": {},
        "after": {},
        "delta": {},
        "moved_out": {},
    }
    for field in ("distance", "held_days", "premium", "assigned_share"):
        values = [d[field] for d in deltas]
        out["delta"][field] = statistics.median(values)
        out["moved_out"][field] = sum(1 for v in values if v > 0) / len(values)
        for name in ("before", "after"):
            out[name][field] = statistics.median(d[field] for d in sides[name])
    return out


def survival_curve(rows: list[Settled], *, limit: int = 12) -> list[dict[str, Any]]:

    hits: list[int] = []
    for items in by_wallet(rows).values():
        hits.append(sum(1 for i in items if i.assigned))
    if not hits:
        return []
    total = len(hits)
    return [
        {
            "assignments": n,
            "writers": sum(1 for h in hits if h >= n),
            "share": sum(1 for h in hits if h >= n) / total,
        }
        for n in range(0, limit + 1)
    ]


def outcome_split(rows: list[Settled], *, min_settled: int = 5) -> list[dict[str, Any]]:

    groups: dict[str, list[Settled]] = {"ahead": [], "behind": []}
    counts: dict[str, int] = {"ahead": 0, "behind": 0}
    for items in by_wallet(rows).values():
        if len(items) < min_settled:
            continue
        side = "ahead" if sum(i.realised for i in items) > 0 else "behind"
        counts[side] += 1
        groups[side].extend(items)

    out = []
    for side in ("ahead", "behind"):
        if not groups[side]:
            continue
        out.append({"side": side, "writers": counts[side], **_summarise(groups[side])})
    return out


def distance_response(rows: list[Settled], *, min_settled: int = 5) -> list[dict[str, Any]]:

    bands = [(0.0, 0.05), (0.05, 0.10), (0.10, 0.18), (0.18, 0.30), (0.30, 10.0)]
    labels = ["under 5%", "5-10%", "10-18%", "18-30%", "over 30%"]
    buckets: dict[str, list[Settled]] = defaultdict(list)
    members: dict[str, int] = defaultdict(int)

    for items in by_wallet(rows).values():
        if len(items) < min_settled:
            continue
        median = statistics.median(distance(i) for i in items)
        for (lo, hi), label in zip(bands, labels, strict=True):
            if lo <= median < hi:
                buckets[label].extend(items)
                members[label] += 1
                break

    return [
        {"band": label, "writers": members[label], **_summarise(buckets[label])}
        for label in labels
        if buckets[label]
    ]


def cohorts(rows: list[Settled]) -> list[dict[str, Any]]:

    buckets: dict[str, list[list[Settled]]] = defaultdict(list)
    for items in by_wallet(rows).values():
        buckets[f"{items[0].written_at:%Y-%m}"].append(items)

    out = []
    for month in sorted(buckets):
        members = buckets[month]
        flat = [i for items in members for i in items]
        returned = sum(1 for items in members if len({i.written_at.date() for i in items}) > 1)
        ahead = sum(1 for items in members if sum(i.realised for i in items) > 0)
        out.append(
            {
                "month": month,
                "writers": len(members),
                "returned": returned,
                "return_rate": returned / len(members),
                "ahead": ahead,
                "ahead_rate": ahead / len(members),
                **_summarise(flat),
            }
        )
    return out


def leaderboard(
    rows: list[Settled], *, min_settled: int = 10, limit: int = 10
) -> dict[str, list[dict[str, Any]]]:

    ranked = []
    for writer, items in by_wallet(rows).items():
        if len(items) < min_settled:
            continue
        ranked.append(
            {
                "writer": writer,
                "assets": sorted({i.underlying for i in items}),
                "first": items[0].written_at,
                "last": max(i.expired_at for i in items),
                **_summarise(items),
            }
        )
    ranked.sort(key=lambda r: r["realised"])
    return {"worst": ranked[:limit], "best": list(reversed(ranked[-limit:]))}


def persistence(rows: list[Settled], *, min_settled: int = 10) -> dict[str, Any]:

    from option_intel import timeline

    out = timeline.persistence(timeline.from_settled(rows), min_events=min_settled)
    if "min_events" in out:
        out["min_settled"] = out.pop("min_events")
    return out


WINDOW_BANDS = (
    ("1", 1, 1),
    ("2-3", 2, 3),
    ("4-7", 4, 7),
    ("8-15", 8, 15),
    ("16-40", 16, 40),
    ("41+", 41, None),
)


def window_control(rows: list[Settled], *, min_observations: int = 30) -> list[dict[str, Any]]:

    months: dict[tuple[str, str], list[Settled]] = defaultdict(list)
    for r in rows:
        if r.writer:
            months[(r.writer, f"{r.written_at:%Y-%m}")].append(r)

    out = []
    for label, lo, hi in WINDOW_BANDS:
        group = [items for items in months.values() if lo <= len(items) and (hi is None or len(items) <= hi)]
        if len(group) < min_observations:
            continue
        totals = sorted(sum(i.realised for i in items) for items in group)
        ahead = sum(1 for t in totals if t > 0)
        out.append(
            {
                "band": label,
                "observations": len(group),
                "ahead": ahead,
                "ahead_rate": ahead / len(group),
                "median": statistics.median(totals),
                "total": sum(totals),
                "options": sum(len(items) for items in group),
            }
        )
    return out


def window_control_by_month(
    rows: list[Settled], *, min_per_cell: int = 8, min_per_month: int = 80
) -> dict[str, Any]:

    months: dict[tuple[str, str], list[Settled]] = defaultdict(list)
    for r in rows:
        if r.writer:
            months[(r.writer, f"{r.written_at:%Y-%m}")].append(r)
    by_month: dict[str, list[list[Settled]]] = defaultdict(list)
    for (_, month), items in months.items():
        by_month[month].append(items)

    checked, monotone = 0, 0
    for cohort in by_month.values():
        if len(cohort) < min_per_month:
            continue
        rates = []
        for _, lo, hi in WINDOW_BANDS:
            cell = [i for i in cohort if lo <= len(i) and (hi is None or len(i) <= hi)]
            if len(cell) < min_per_cell:
                continue
            rates.append(sum(1 for i in cell if sum(x.realised for x in i) > 0) / len(cell))
        if len(rates) < 3:
            continue
        checked += 1
        if all(a >= b - 1e-9 for a, b in zip(rates, rates[1:], strict=False)):
            monotone += 1
    return {"months": checked, "monotone": monotone}


STAKED = frozenset({"KHYPE", "LHYPE", "STHYPE", "WSTHYPE"})
STABLES = frozenset({"USDC", "USDT", "USD₮0", "USDT0", "USDH", "DAI", "USDE"})


def is_cash_secured(row: Settled) -> bool:
    return row.collateral_symbol.upper() in {s.upper() for s in STABLES}


def covered_calls(rows: list[Settled]) -> dict[str, Any]:

    calls = [
        r
        for r in rows
        if r.option_type is OptionType.CALL and r.collateral_amount > 0 and not is_cash_secured(r)
    ]
    if not calls:
        return {}

    def summarise(group: list[Settled]) -> dict[str, Any]:
        at_write = sum(r.collateral_amount * r.spot_at_write for r in group)
        at_expiry = sum(r.collateral_amount * r.spot_at_expiry for r in group)
        premium = sum(r.premium for r in group)
        paid = sum(r.intrinsic_paid for r in group)
        holding = at_expiry - at_write
        option_leg = premium - paid
        return {
            "calls": len(group),
            "writers": len({r.writer for r in group if r.writer}),
            "collateral_at_write": at_write,
            "collateral_at_expiry": at_expiry,
            "holding_gain": holding,
            "premium": premium,
            "intrinsic_paid": paid,
            "option_leg": option_leg,
            "total": holding + option_leg,
            "captured": (holding + option_leg) / holding if holding else None,
        }

    staked = [r for r in calls if r.collateral_symbol.upper() in STAKED]
    plain = [r for r in calls if r.collateral_symbol.upper() not in STAKED]
    out = {"all": summarise(calls)}
    if plain:
        out["plain"] = summarise(plain)
    if staked:
        out["staked"] = summarise(staked)
    return out


def cash_secured_puts(rows: list[Settled]) -> dict[str, Any]:

    puts = [r for r in rows if r.option_type is OptionType.PUT and is_cash_secured(r)]
    if not puts:
        return {}
    premium = sum(r.premium for r in puts)
    paid = sum(r.intrinsic_paid for r in puts)
    return {
        "puts": len(puts),
        "writers": len({r.writer for r in puts if r.writer}),
        "collateral": sum(r.collateral_amount for r in puts),
        "premium": premium,
        "intrinsic_paid": paid,
        "realised": premium - paid,
        "return_on_premium": (premium - paid) / premium if premium else None,
        "assigned_share": sum(1 for r in puts if r.assigned) / len(puts),
    }


def by_dimension(rows: list[Settled], key, *, min_premium: float = 0.0) -> list[dict[str, Any]]:

    groups: dict[Any, list[Settled]] = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)

    out = []
    for name, items in groups.items():
        premium = sum(i.premium for i in items)
        if premium < min_premium:
            continue
        paid = sum(i.intrinsic_paid for i in items)
        out.append(
            {
                "group": name,
                "settled": len(items),
                "contracts": sum(i.contracts for i in items),
                "notional": sum(i.contracts * i.spot_at_write for i in items),
                "writers": len({i.writer for i in items if i.writer}),
                "premium": premium,
                "intrinsic_paid": paid,
                "realised": premium - paid,
                "return_on_premium": (premium - paid) / premium if premium else None,
                "assigned_share": sum(1 for i in items if i.assigned) / len(items),
                "call_share": sum(1 for i in items if i.option_type is OptionType.CALL) / len(items),
                "median_distance": statistics.median(distance(i) for i in items),
            }
        )
    out.sort(key=lambda r: -r["premium"])
    return out


def per_expiry(rows: list[Settled]) -> list[dict[str, Any]]:

    out = by_dimension(rows, lambda r: r.expired_at.date())
    out.sort(key=lambda r: r["group"])
    return out


def _percentiles(values: list[float], qs: tuple[int, ...] = (10, 25, 50, 75, 90, 99)) -> dict[str, float]:
    ordered = sorted(values)
    return {f"p{q}": ordered[min(int(q / 100 * len(ordered)), len(ordered) - 1)] for q in qs}


def clips(rows: list[Settled], *, min_settled: int = 2, tolerance: float = 0.10) -> dict[str, Any]:

    pairs: dict[tuple[str, str], list[Settled]] = defaultdict(list)
    for r in rows:
        if r.writer:
            pairs[(r.writer, r.underlying)].append(r)
    pairs = {k: v for k, v in pairs.items() if len(v) >= min_settled}
    if not pairs:
        return {"wallets": 0, "pairs": 0}

    notionals, repeats, weighted = [], [], []
    for items in pairs.values():
        clip = statistics.median(i.contracts for i in items)
        notional = statistics.median(i.contracts * i.spot_at_write for i in items)
        notionals.append(notional)
        repeats.append(
            sum(1 for i in items if clip and abs(i.contracts - clip) <= tolerance * clip) / len(items)
        )
        weighted.append((notional, sum(i.contracts * i.spot_at_write for i in items)))

    weighted.sort()
    total = sum(w for _, w in weighted)
    running, by_notional = 0.0, weighted[-1][0]
    for notional, w in weighted:
        running += w
        if running >= total / 2:
            by_notional = notional
            break

    bins = ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0))
    return {
        "wallets": len({w for w, _ in pairs}),
        "pairs": len(pairs),
        "tolerance": tolerance,
        "clip_notional": _percentiles(notionals) | {"mean": statistics.fmean(notionals)},
        "clip_by_dollar": by_notional,
        "repeat_share": _percentiles(repeats),
        "repeat_exact": sum(1 for r in repeats if r == 1.0) / len(repeats),
        "repeat_hist": [
            {"lo": lo, "hi": hi, "share": sum(1 for r in repeats if lo <= r < hi) / len(repeats)}
            for lo, hi in bins
        ],
    }


def tenors(rows: list[Settled], *, min_settled: int = 2) -> dict[str, Any]:

    wallets = {w: items for w, items in by_wallet(rows).items() if len(items) >= min_settled}
    if not wallets:
        return {"wallets": 0}

    levels, spreads = [], []
    for items in wallets.values():
        days = [i.held_days for i in items]
        levels.append(statistics.median(days))
        quarters = statistics.quantiles(days, n=4, method="inclusive")
        spreads.append(quarters[2] - quarters[0])

    def hist(values: list[float], bins: tuple[tuple[float, float], ...]) -> list[dict[str, float]]:
        return [
            {"lo": lo, "hi": hi, "share": sum(1 for v in values if lo <= v < hi) / len(values)}
            for lo, hi in bins
        ]

    return {
        "wallets": len(wallets),
        "tenor": _percentiles(levels) | {"mean": statistics.fmean(levels)},
        "spread": _percentiles(spreads) | {"mean": statistics.fmean(spreads)},
        "tenor_hist": hist(
            levels, ((0, 4), (4, 6), (6, 9), (9, 13), (13, 18), (18, 25), (25, 32), (32, 400))
        ),
        "spread_hist": hist(spreads, ((0, 1), (1, 3), (3, 7), (7, 14), (14, 400))),
    }


def rolls(
    rows: list[Settled],
    *,
    writes: list[Any] | None = None,
    tolerances: tuple[float, ...] = (0.10, 0.25, 0.50),
    windows: tuple[int, ...] = (7, 30),
) -> dict[str, Any]:

    if writes is not None:
        events = [(w.taker, w.instrument.underlying, w.ts, w.quantity) for w in writes if w.taker]
    else:
        events = [(r.writer, r.underlying, r.written_at, r.contracts) for r in rows if r.writer]
    if not events:
        return {"expiries": 0}
    pool: dict[tuple[str, str], list[tuple[Any, float]]] = defaultdict(list)
    horizon = max(at for _, _, at, _ in events)
    for wallet, asset, at, qty in events:
        pool[(wallet, asset)].append((at, qty))
    for history in pool.values():
        history.sort()

    out_windows: dict[str, Any] = {}
    splits: dict[str, dict[str, Any]] = {}
    gaps: list[float] = []
    for days in windows:
        observable = [r for r in rows if (horizon - r.expired_at).total_seconds() >= days * 86400]
        followed, matched = 0, dict.fromkeys(tolerances, 0)
        split_counts = {"assigned": [0, 0], "unassigned": [0, 0]}
        for r in observable:
            later = [(at, qty) for at, qty in pool.get((r.writer, r.underlying), ()) if at > r.expired_at]
            inside = [(at, qty) for at, qty in later if (at - r.expired_at).total_seconds() <= days * 86400]
            side = "assigned" if r.assigned else "unassigned"
            split_counts[side][1] += 1
            if inside:
                followed += 1
                split_counts[side][0] += 1
                for tol in tolerances:
                    if any(abs(qty - r.contracts) <= tol * r.contracts for _, qty in inside):
                        matched[tol] += 1
            if days == max(windows) and later:
                gaps.append((later[0][0] - r.expired_at).total_seconds() / 86400)
        out_windows[f"{days}d"] = {
            "observable": len(observable),
            "followed": followed / len(observable) if observable else None,
            "matched": {
                f"{tol:.0%}": matched[tol] / len(observable) if observable else None for tol in tolerances
            },
        }
        splits[f"{days}d"] = {
            side: hit / total if total else None for side, (hit, total) in split_counts.items()
        }

    base_by_pair: dict[tuple[str, str], float] = {}
    for pair, history in pool.items():
        if len(history) < 2:
            continue
        steps = [(b[0] - a[0]).total_seconds() / 86400 for a, b in zip(history, history[1:], strict=False)]
        base_by_pair[pair] = statistics.median(steps)

    roll_by_pair: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        later = [at for at, _ in pool.get((r.writer, r.underlying), ()) if at > r.expired_at]
        if later:
            roll_by_pair[(r.writer, r.underlying)].append((min(later) - r.expired_at).total_seconds() / 86400)
    deltas = [
        statistics.median(roll) - base_by_pair[pair]
        for pair, roll in roll_by_pair.items()
        if pair in base_by_pair
    ]

    return {
        "expiries": len(rows),
        "windows": out_windows,
        "by_outcome": splits,
        "gap": _percentiles(gaps) if gaps else {},
        "base_gap": _percentiles(list(base_by_pair.values())) if base_by_pair else {},
        "paired": {
            "pairs": len(deltas),
            "delta_median": statistics.median(deltas) if deltas else None,
            "faster_share": (sum(1 for d in deltas if d < 0) / len(deltas) if deltas else None),
        },
    }
