from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

matplotlib.use("Agg")

CALL = "#C07A2B"
PUT = "#0E8C77"
INK = "#6B7A7C"
GRID = "#9AA7A9"
ACCENT = "#D9534F"


KEPT = "#2E7D63"
GONE = "#B4483F"

SIDE_COLOUR = {"short_call": CALL, "short_put": PUT, "C": CALL, "P": PUT}

plt.rcParams.update(
    {
        "figure.facecolor": "none",
        "axes.facecolor": "none",
        "savefig.facecolor": "none",
        "savefig.transparent": True,
        "font.family": ["DejaVu Sans"],
        "font.size": 10,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK,
        "axes.titlecolor": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "text.color": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "grid.color": GRID,
        "grid.alpha": 0.25,
        "grid.linewidth": 0.6,
        "legend.frameon": False,
    }
)


def _finish(fig, ax, path: Path, title: str = "", subtitle: str = "") -> Path:

    if title or subtitle:
        height = fig.get_figheight()
        header = 0.62 if subtitle else 0.42
        fig.subplots_adjust(top=1 - header / height)
        fig.text(
            0.0, 1 - 0.16 / height, title, fontsize=13, fontweight="bold", color=INK, va="top", ha="left"
        )
        if subtitle:
            fig.text(
                0.0, 1 - 0.40 / height, subtitle, fontsize=9.5, color=INK, alpha=0.85, va="top", ha="left"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, bbox_inches="tight", pad_inches=0.35)
    plt.close(fig)
    return path


def _pct(x, _pos):
    return f"{x:.0%}"


def _usd(x, _pos):
    for unit, scale in (("M", 1e6), ("K", 1e3)):
        if abs(x) >= scale:
            return f"${x / scale:,.0f}{unit}"
    return f"${x:,.0f}"


def underwater_map(positions: list[Any], path: Path) -> Path:

    fig, ax = plt.subplots(figsize=(10, 5.5))
    for side, colour, label in (
        ("short_put", PUT, "short put"),
        ("short_call", CALL, "short call"),
    ):
        items = [p for p in positions if p.side == side and p.premium_received > 0]
        if not items:
            continue
        ax.scatter(
            [p.distance_to_strike for p in items],
            [p.premium_received for p in items],
            s=[max(12, min(900, (p.contracts**0.45) * 12)) for p in items],
            c=colour,
            alpha=0.45,
            edgecolors="none",
            label=f"{label} ({len(items)})",
        )
    ax.axvline(0, color=ACCENT, lw=1.2, ls="--", alpha=0.8)
    ax.text(0, ax.get_ylim()[1], "  strike", color=ACCENT, fontsize=9, va="top")
    ax.set_yscale("log")
    ax.set_xlabel("distance from spot to strike  (negative = already through it)")
    ax.set_ylabel("premium collected")
    ax.xaxis.set_major_formatter(FuncFormatter(_pct))
    ax.yaxis.set_major_formatter(FuncFormatter(_usd))
    ax.grid(True, axis="both")
    ax.legend(loc="upper left")
    return _finish(
        fig,
        ax,
        path,
        "Open short options, by how far the market has moved past them",
        "bubble area is contracts written",
    )


def side_split(rows: list[dict], path: Path) -> Path:

    rows = [r for r in rows if r["positions"] >= 5]
    rows.sort(key=lambda r: (r["underlying"], r["side"]))
    labels = [f"{r['underlying']}  {r['side'].replace('short_', '')}" for r in rows]
    values = [r["share_itm"] for r in rows]
    colours = [SIDE_COLOUR[r["side"]] for r in rows]

    fig, ax = plt.subplots(figsize=(9, max(3.2, 0.42 * len(rows) + 1.4)))
    bars = ax.barh(labels, values, color=colours, height=0.62)
    for bar, r in zip(bars, rows, strict=True):
        ax.text(
            bar.get_width() + 0.015,
            bar.get_y() + bar.get_height() / 2,
            f"{r['share_itm']:.0%}  ({r['in_the_money']}/{r['positions']})",
            va="center",
            fontsize=9,
            color=INK,
        )
    ax.set_xlim(0, 1.18)
    ax.xaxis.set_major_formatter(FuncFormatter(_pct))
    ax.invert_yaxis()
    ax.grid(True, axis="x")
    ax.set_xlabel("share of open positions now in the money")
    return _finish(
        fig,
        ax,
        path,
        "Who got caught depends entirely on the side they picked",
        "calls in ochre, puts in teal",
    )


def writer_damage(rows: list[dict], path: Path, limit: int = 15) -> Path:

    rows = sorted(rows, key=lambda r: r["unrealized_intrinsic"])[:limit]
    labels = [f"{r['writer'][:8]}  {r['short_calls']}C/{r['short_puts']}P" for r in rows]
    premium = [r["premium_received"] for r in rows]
    owed = [-r["intrinsic_owed"] for r in rows]

    fig, ax = plt.subplots(figsize=(10, max(3.5, 0.44 * len(rows) + 1.5)))
    ax.barh(labels, premium, color=PUT, height=0.6, label="premium collected")
    ax.barh(labels, owed, color=CALL, height=0.6, label="owed at today's spot")
    ax.axvline(0, color=GRID, lw=1)
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(FuncFormatter(_usd))
    ax.grid(True, axis="x")
    ax.legend(loc="lower right")
    return _finish(
        fig,
        ax,
        path,
        "What writers took, against what their open shorts now owe",
        "worst fifteen by net; none of it is realised until expiry",
    )


def iv_over_time(
    writes: Iterable[Any],
    reference: Iterable[Any],
    path: Path,
    *,
    assets: tuple[str, ...] = ("HYPE", "ETH", "BTC"),
    days: int = 90,
) -> Path:

    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(days=days)

    def daily(prints, asset):
        buckets: dict[Any, list[float]] = defaultdict(list)
        for p in prints:
            if p.iv is None or p.instrument.underlying != asset or p.ts < cutoff:
                continue
            buckets[p.ts.date()].append(p.iv * 100)
        return sorted((d, statistics.median(v)) for d, v in buckets.items() if len(v) >= 3)

    fig, axes = plt.subplots(len(assets), 1, figsize=(10, 2.6 * len(assets)), sharex=True)
    axes = axes if len(assets) > 1 else [axes]
    ref_list, write_list = list(reference), list(writes)
    for ax, asset in zip(axes, assets, strict=True):
        book = daily(ref_list, asset)
        sold = daily(write_list, asset)
        if book:
            ax.plot([d for d, _ in book], [v for _, v in book], color=INK, lw=1.4, label="order book")
        if sold:
            ax.plot(
                [d for d, _ in sold],
                [v for _, v in sold],
                color=CALL,
                lw=1.8,
                marker="o",
                ms=3,
                label="written on chain",
            )
        ax.set_ylabel(f"{asset}\nIV %")
        ax.grid(True)
        if ax is axes[0]:
            ax.legend(loc="upper left", ncol=2)
    axes[-1].set_xlabel("")
    fig.autofmt_xdate()
    return _finish(
        fig,
        axes[0],
        path,
        "Where on-chain writing priced volatility, against the order book",
        f"daily medians, last {days} days, days with at least three prints",
    )


def premium_by_day(writes: Iterable[Any], path: Path, days: int = 60) -> Path:

    from datetime import UTC, datetime, timedelta

    from option_intel.model import OptionType

    cutoff = datetime.now(UTC) - timedelta(days=days)
    calls: dict[Any, float] = defaultdict(float)
    puts: dict[Any, float] = defaultdict(float)
    for p in writes:
        if p.ts < cutoff:
            continue
        target = calls if p.instrument.option_type is OptionType.CALL else puts
        target[p.ts.date()] += p.premium_total

    dates = sorted(set(calls) | set(puts))
    if not dates:
        raise ValueError("no writes in window")

    fig, ax = plt.subplots(figsize=(11, 4.2))
    c = [calls.get(d, 0.0) for d in dates]
    p_ = [puts.get(d, 0.0) for d in dates]
    ax.bar(dates, c, color=CALL, label="calls written", width=0.9)
    ax.bar(dates, p_, bottom=c, color=PUT, label="puts written", width=0.9)
    ax.yaxis.set_major_formatter(FuncFormatter(_usd))
    ax.grid(True, axis="y")

    ax.set_ylim(0, max(a + b for a, b in zip(c, p_, strict=True)) * 1.22)
    ax.legend(loc="upper left", ncol=2)
    fig.autofmt_xdate()
    return _finish(
        fig,
        ax,
        path,
        "Premium collected by on-chain writers",
        f"last {days} days",
    )


def strike_ladder(
    positions: list[Any],
    path: Path,
    asset: str = "HYPE",
    *,
    band: float = 0.45,
    max_rows: int = 34,
) -> Path:

    items = [p for p in positions if p.instrument.underlying == asset]
    if not items:
        raise ValueError(f"no open positions in {asset}")
    spot = statistics.median([p.spot for p in items])

    by_strike: dict[tuple[float, str], float] = defaultdict(float)
    for p in items:
        if abs(p.instrument.strike - spot) / spot > band:
            continue
        by_strike[(p.instrument.strike, p.side)] += p.contracts
    if not by_strike:
        raise ValueError(f"no {asset} strikes within {band:.0%} of spot")

    size: dict[float, float] = defaultdict(float)
    for (strike, _side), contracts in by_strike.items():
        size[strike] += contracts
    strikes = sorted(sorted(size, key=lambda s: -size[s])[:max_rows])
    call_v = [by_strike.get((s, "short_call"), 0.0) for s in strikes]
    put_v = [-by_strike.get((s, "short_put"), 0.0) for s in strikes]

    fig, ax = plt.subplots(figsize=(10, max(4.5, 0.28 * len(strikes) + 1.8)))
    ax.barh([str(s) for s in strikes], call_v, color=CALL, height=0.7, label="short calls")
    ax.barh([str(s) for s in strikes], put_v, color=PUT, height=0.7, label="short puts")
    ax.axvline(0, color=GRID, lw=1)

    nearest = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    ax.axhline(nearest, color=ACCENT, lw=1.2, ls="--", alpha=0.85)
    ax.text(ax.get_xlim()[1], nearest, f" spot {spot:,.2f}", color=ACCENT, fontsize=9, va="center")
    ax.set_xlabel("contracts written  (calls right, puts left)")
    ax.set_ylabel("strike")
    ax.grid(True, axis="x")
    ax.legend(loc="lower right")
    return _finish(
        fig,
        ax,
        path,
        f"{asset}: open short interest by strike",
        f"strikes within {band:.0%} of spot; the dashed line is spot",
    )


def writer_concentration(rows: list[dict], path: Path) -> Path:

    premiums = sorted((r["premium"] for r in rows), reverse=True)
    if not premiums:
        raise ValueError("no writers")
    total = sum(premiums)
    cumulative, running = [], 0.0
    for value in premiums:
        running += value
        cumulative.append(running / total)
    x = [(i + 1) / len(premiums) for i in range(len(premiums))]

    fig, ax = plt.subplots(figsize=(7.5, 5))
    ax.plot(x, cumulative, color=PUT, lw=2)
    ax.plot([0, 1], [0, 1], color=GRID, lw=1, ls="--")
    ax.fill_between(x, cumulative, x, color=PUT, alpha=0.12)
    for share in (0.1, 0.25):
        idx = max(0, int(share * len(premiums)) - 1)
        ax.plot([share], [cumulative[idx]], "o", color=ACCENT, ms=6)
        ax.annotate(
            f"top {share:.0%} of writers\n{cumulative[idx]:.0%} of premium",
            (share, cumulative[idx]),
            textcoords="offset points",
            xytext=(10, -22),
            fontsize=9,
            color=INK,
        )
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.xaxis.set_major_formatter(FuncFormatter(_pct))
    ax.yaxis.set_major_formatter(FuncFormatter(_pct))
    ax.set_xlabel("writers, largest first")
    ax.set_ylabel("cumulative share of premium")
    ax.grid(True)
    return _finish(
        fig,
        ax,
        path,
        "A few writers collect most of the premium",
        "the dashed diagonal is what perfectly even writing would look like",
    )
