from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class Event:
    wallet: str
    at: datetime
    pnl: float

    scale: float = 0.0
    venue: str = ""


def from_settled(rows: Iterable[Any], venue: str = "rysk") -> list[Event]:

    return [
        Event(wallet=r.writer, at=r.written_at, pnl=r.realised, scale=r.premium, venue=venue)
        for r in rows
        if r.writer
    ]


def by_wallet(events: Iterable[Event]) -> dict[str, list[Event]]:

    groups: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        if e.wallet:
            groups[e.wallet].append(e)
    for run in groups.values():
        run.sort(key=lambda e: e.at)
    return dict(groups)


def totals(events: Iterable[Event]) -> list[float]:
    return sorted(sum(e.pnl for e in run) for run in by_wallet(events).values())


def distribution(events: Iterable[Event]) -> dict[str, Any]:

    t = totals(events)
    if not t:
        return {}
    n = len(t)
    positive = [x for x in t if x > 0]
    negative = [x for x in t if x < 0]
    return {
        "wallets": n,
        "median": statistics.median(t),
        "mean": sum(t) / n,
        "total": sum(t),
        "positive": len(positive),
        "positive_share": len(positive) / n,
        "gains": sum(positive),
        "losses": sum(negative),
        "percentiles": {q: t[min(int(q / 100 * n), n - 1)] for q in (1, 5, 10, 25, 50, 75, 90, 95, 99)},
    }


def concentration(events: Iterable[Event], *, points: int = 20) -> list[dict[str, Any]]:

    losses = [x for x in totals(events) if x < 0]
    if not losses:
        return []
    grand = sum(losses)
    out, running = [], 0.0
    step = max(len(losses) // points, 1)
    for i, value in enumerate(losses, start=1):
        running += value
        if i % step == 0 or i == len(losses):
            out.append({"wallets": i, "wallet_share": i / len(losses), "loss_share": running / grand})
    return out


def flip_point(events: Iterable[Event]) -> dict[str, Any]:

    t = totals(events)
    if not t or sum(t) >= 0:
        return {}
    for k in range(len(t)):
        rest = sum(t[k:])
        if rest > 0:
            return {"removed": k, "remaining": len(t) - k, "rest": rest, "removed_total": sum(t[:k])}
    return {}


def _ranks(values: list[float]) -> list[float]:

    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2
        for k in range(i, j + 1):
            out[order[k]] = shared
        i = j + 1
    return out


def persistence(events: Iterable[Event], *, min_events: int = 10) -> dict[str, Any]:

    cells = {"win_win": 0, "win_lose": 0, "lose_win": 0, "lose_lose": 0}
    pairs: list[tuple[float, float]] = []
    for run in by_wallet(events).values():
        if len(run) < min_events:
            continue
        half = len(run) // 2
        first = sum(e.pnl for e in run[:half])
        second = sum(e.pnl for e in run[half:])
        if first == 0 or second == 0:
            continue
        pairs.append((first, second))
        cells[f"{'win' if first > 0 else 'lose'}_{'win' if second > 0 else 'lose'}"] += 1

    total = sum(cells.values())
    if total < 2:
        return {"wallets": total, **cells}

    ahead = cells["win_win"] + cells["win_lose"]
    behind = cells["lose_win"] + cells["lose_lose"]
    p_win = cells["win_win"] / ahead if ahead else None
    p_lose = cells["lose_win"] / behind if behind else None
    base = (cells["win_win"] + cells["lose_win"]) / total

    out: dict[str, Any] = {
        "wallets": total,
        "min_events": min_events,
        "ahead_first": ahead,
        "behind_first": behind,
        "ahead_after_ahead": p_win,
        "ahead_after_behind": p_lose,
        "lift": (p_win - p_lose) if (p_win is not None and p_lose is not None) else None,
        "base_rate": base,
        **cells,
    }
    if p_win is not None and p_lose is not None and ahead and behind:
        out["lift_stderr"] = math.sqrt(max(base * (1 - base), 0.0) * (1 / ahead + 1 / behind))

        if out["lift_stderr"]:
            out["lift_over_stderr"] = out["lift"] / out["lift_stderr"]
    if len(pairs) > 2:
        xs = [a for a, _ in pairs]
        ys = [b for _, b in pairs]
        try:
            out["correlation"] = statistics.correlation(_ranks(xs), _ranks(ys))
        except statistics.StatisticsError:
            out["correlation"] = None
    return out


def by_activity(
    events: Iterable[Event], bands: tuple[tuple[str, int, int | None], ...]
) -> list[dict[str, Any]]:

    wallets = by_wallet(events)
    if not wallets:
        return []
    out = []
    for label, lo, hi in bands:
        members = [run for run in wallets.values() if lo <= len(run) and (hi is None or len(run) <= hi)]
        if not members:
            continue
        sums = [sum(e.pnl for e in run) for run in members]
        out.append(
            {
                "band": label,
                "wallets": len(members),
                "wallet_share": len(members) / len(wallets),
                "events": sum(len(run) for run in members),
                "ahead": sum(1 for s in sums if s > 0),
                "ahead_rate": sum(1 for s in sums if s > 0) / len(members),
                "median": statistics.median(sums),
                "total": sum(sums),
            }
        )
    return out
