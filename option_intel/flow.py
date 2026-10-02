from __future__ import annotations

import random
import statistics
from collections import defaultdict
from typing import Any

from option_intel.model import OptionType
from option_intel.settlement import Settled
from option_intel.vol import black76_delta, solve_iv

WEIGHTS = {
    "contracts": lambda r: r.contracts,
    "premium": lambda r: r.premium,
}


def delta_units(row: Settled) -> float | None:

    if not row.contracts:
        return None
    t = row.held_days / 365
    iv, _why = solve_iv(row.premium / row.contracts, row.spot_at_write, row.strike, t, row.option_type)
    if iv is None:
        return None
    return abs(black76_delta(row.spot_at_write, row.strike, iv, t, row.option_type)) * row.contracts


def _weight(row: Settled, weighting: str) -> float | None:
    if weighting == "delta":
        return delta_units(row)
    return WEIGHTS[weighting](row)


def _ranks(values: list[float]) -> list[float]:

    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:

    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    try:
        return statistics.correlation(rx, ry)
    except statistics.StatisticsError:
        return None


def cells(rows: list[Settled], *, weighting: str, min_cell: int = 10) -> list[dict[str, Any]]:

    groups: dict[tuple[str, Any], list[Settled]] = defaultdict(list)
    for r in rows:
        groups[(r.underlying, r.expired_at.date())].append(r)

    out = []
    for (asset, expiry), group in sorted(groups.items()):
        if len(group) < min_cell:
            continue
        weighted = [(r, _weight(r, weighting)) for r in group]
        failed = sum(1 for _, w in weighted if w is None)
        weighted = [(r, w) for r, w in weighted if w is not None]
        total = sum(w for _, w in weighted)
        if total <= 0:
            continue
        puts = sum(w for r, w in weighted if r.option_type is OptionType.PUT)
        ref = sum(w * r.spot_at_write for r, w in weighted) / total
        settle = statistics.median(r.spot_at_expiry for r in group)
        out.append(
            {
                "asset": asset,
                "expiry": expiry,
                "writes": len(group),
                "solve_failed": failed,
                "put_share": puts / total,
                "move": settle / ref - 1,
            }
        )
    return out


def permutation_p(
    xs: list[float],
    ys: list[float],
    *,
    draws: int = 10000,
    seed: int = 20260831,
) -> float | None:

    observed = spearman(xs, ys)
    if observed is None:
        return None
    rng = random.Random(seed)
    shuffled = list(ys)
    hits = 0
    for _ in range(draws):
        rng.shuffle(shuffled)
        drawn = spearman(xs, shuffled)
        if drawn is not None and abs(drawn) >= abs(observed):
            hits += 1
    return hits / draws


def null_rhos(
    xs: list[float],
    ys: list[float],
    *,
    draws: int = 10000,
    seed: int = 20260831,
) -> list[float]:

    rng = random.Random(seed)
    shuffled = list(ys)
    out = []
    for _ in range(draws):
        rng.shuffle(shuffled)
        drawn = spearman(xs, shuffled)
        if drawn is not None:
            out.append(drawn)
    return out


def detectable(
    xs: list[float],
    ys: list[float],
    *,
    draws: int = 10000,
    seed: int = 20260831,
    alpha: float = 0.05,
) -> float | None:

    drawn = sorted(abs(r) for r in null_rhos(xs, ys, draws=draws, seed=seed))
    if not drawn:
        return None
    return drawn[min(len(drawn) - 1, int(round((1 - alpha) * len(drawn))))]


def predicts(
    rows: list[Settled],
    *,
    weightings: tuple[str, ...] = ("contracts", "premium", "delta"),
    min_cell: int = 10,
    permutation: int = 0,
) -> dict[str, Any]:

    total_cells = len({(r.underlying, r.expired_at.date()) for r in rows})
    out: dict[str, Any] = {"min_cell": min_cell, "by_weighting": {}}
    for weighting in weightings:
        points = cells(rows, weighting=weighting, min_cell=min_cell)
        per_asset = {}
        for asset in sorted({p["asset"] for p in points}):
            mine = [p for p in points if p["asset"] == asset]
            axs = [p["put_share"] for p in mine]
            ays = [p["move"] for p in mine]
            entry: dict[str, Any] = {"cells": len(mine), "rho": spearman(axs, ays)}
            if permutation and entry["rho"] is not None:
                entry["p"] = permutation_p(axs, ays, draws=permutation)
            per_asset[asset] = entry
        xs = [p["put_share"] for p in points]
        ys = [p["move"] for p in points]
        pooled: dict[str, Any] = {
            "cells": len(points),
            "dropped": total_cells - len(points),
            "solve_failed": sum(p["solve_failed"] for p in points),
            "pooled_rho": spearman(xs, ys),
            "per_asset": per_asset,
        }
        if permutation and pooled["pooled_rho"] is not None:
            pooled["pooled_p"] = permutation_p(xs, ys, draws=permutation)
            pooled["detectable"] = detectable(xs, ys, draws=permutation)
        out["by_weighting"][weighting] = pooled
    return out
