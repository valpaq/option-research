from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter

from option_intel import writers
from option_intel.charts import ACCENT, CALL, GONE, GRID, INK, KEPT, PUT, _finish, _pct
from option_intel.model import OptionType


def money(x: float) -> str:

    sign = "\u2212" if x < 0 else ""
    if abs(x) >= 1e6:
        return f"{sign}${abs(x) / 1e6:,.2f}M"
    return f"{sign}${abs(x):,.0f}"


def _axis_millions(value: float, _pos: object = None) -> str:

    if not value:
        return _t("$0")
    sign = "\u2212" if value < 0 else ""
    return _t(f"{sign}${abs(value) / 1e6:,.0f}M")


def _t(text: str) -> str:

    return text.replace("$", r"\$")


def _decade(value: float) -> str:

    if not value:
        return "$0"
    sign = "\u2212" if value < 0 else ""
    size = abs(value)
    for scale, suffix in ((1e6, "M"), (1e3, "K")):
        if size >= scale:
            return f"{sign}${size / scale:,.0f}{suffix}"
    return f"{sign}${size:,.0f}"


def _calls(rows: list[Any]) -> list[Any]:

    return [
        r
        for r in rows
        if r.option_type is OptionType.CALL and r.collateral_amount > 0 and not writers.is_cash_secured(r)
    ]


def the_trade(figures: dict[str, Any], path: Path) -> Path:

    t = figures["call_totals"]
    held, kept = t["asset"], t["net"]

    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    ax.bar(0, held, width=0.5, color=GRID, alpha=0.55)
    ax.bar(1, kept, width=0.5, color=KEPT, alpha=0.95)

    for x, value in ((0, held), (1, kept)):
        ax.text(
            x, value + held * 0.035, _t(money(value)), ha="center", fontsize=19, fontweight="bold", color=INK
        )

    ax.annotate(
        "", xy=(0.5, held), xytext=(0.5, kept), arrowprops={"arrowstyle": "<->", "color": GONE, "lw": 1.4}
    )
    ax.text(
        0.58,
        (held + kept) / 2,
        f"{t['forwent']:.0%}\ngone",
        fontsize=13,
        color=GONE,
        va="center",
        fontweight="bold",
    )

    ax.set_xticks([0, 1])
    ax.set_xticklabels(["Asset move over\noption lifetimes", "Holding and\nselling calls"], fontsize=13)
    ax.set_xlim(-0.6, 1.9)
    ax.set_ylim(0, held * 1.22)
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.axhline(0, color=GRID, lw=0.9)

    return _finish(
        fig,
        ax,
        path,
    )


def waterfall(figures: dict[str, Any], path: Path) -> Path:

    t = figures["call_totals"]
    steps = [
        (
            "Collateral proxy\nwhile written"
            if figures.get("wallets", {}).get("basis") == "combined_proxy"
            else "Assets held\nwhile written",
            t["asset"],
            KEPT,
        ),
        ("Premium\ncollected", t["premium"], KEPT),
        ("Oracle-derived\npayoff", t["paid"], GONE),
    ]

    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    running, tops = 0.0, []
    for i, (_, value, colour) in enumerate(steps):
        ax.bar(i, value, bottom=running, width=0.62, color=colour, alpha=0.9)
        tops.append(running + max(value, 0))

        ax.text(
            i,
            running + value * 0.62,
            _t(money(value)),
            ha="center",
            va="center",
            fontsize=10.5,
            color="white",
            fontweight="bold",
        )
        running += value
        if i < len(steps) - 1:
            ax.plot([i + 0.31, i + 1 - 0.31], [running, running], color=GRID, lw=0.9, ls=(0, (3, 3)))

    ax.bar(len(steps), running, width=0.62, color=INK, alpha=0.55)
    ceiling = max(tops) * 1.20

    ax.text(
        len(steps),
        running + ceiling * 0.02,
        _t(money(t["net"])),
        ha="center",
        va="bottom",
        fontsize=10.5,
        color=INK,
        fontweight="bold",
    )

    ax.axhline(t["asset"], color=ACCENT, lw=1.3, ls=(0, (5, 3)))
    ax.text(
        len(steps) + 0.42,
        t["asset"] + ceiling * 0.015,
        _t(f"asset move during\noption lifetimes: {money(t['asset'])}"),
        va="bottom",
        ha="center",
        fontsize=9,
        color=ACCENT,
    )

    ax.axhline(0, color=GRID, lw=0.9)
    ax.set_ylim(0, ceiling)
    ax.set_xticks(range(len(steps) + 1))

    exercised = figures["buckets"]["through"]["calls"]
    ax.set_xticklabels(
        [
            f"{steps[0][0]}\nall {figures['calls']:,}",
            f"{steps[1][0]}\nall {figures['calls']:,}",
            f"{steps[2][0]}\nthe {exercised:,} ITM",
            "Estimated combined\nresult"
            if figures.get("wallets", {}).get("basis") == "combined_proxy"
            else "What writers\nwere left with",
        ],
        fontsize=9.5,
    )

    ax.yaxis.set_major_locator(plt.MultipleLocator(5e6))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_axis_millions))
    ax.grid(axis="y")
    ax.set_xlim(-0.6, len(steps) + 1.5)

    return _finish(
        fig,
        ax,
        path,
    )


def premium_against_move(rows: list[Any], figures: dict[str, Any], path: Path) -> Path:

    calls = _calls(rows)
    prem = np.array(
        [
            r.premium / (r.collateral_amount * r.spot_at_write)
            for r in calls
            if r.collateral_amount and r.spot_at_write
        ]
    )
    fell = np.array(
        [
            (r.spot_at_write - r.spot_at_expiry) / r.spot_at_write
            for r in calls
            if not r.assigned and r.spot_at_expiry < r.spot_at_write
        ]
    )

    top = 0.30
    bins = np.linspace(0, top, 61)
    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    for data, colour, label in (
        (prem, PUT, f"premium collected ({len(prem):,} calls)"),
        (fell, CALL, f"the fall it had to cover ({len(fell):,})"),
    ):
        ax.hist(
            np.clip(data, 0, top),
            bins=bins,
            color=colour,
            alpha=0.55,
            weights=np.full(len(data), 1 / len(data)),
            label=label,
        )

    ax.set_ylim(0, ax.get_ylim()[1] * 1.30)
    ceiling = ax.get_ylim()[1]

    for key, colour, height in ((("premium_notional"), PUT, 0.94), (("decline"), CALL, 0.76)):
        stats = figures[key]
        ax.axvline(stats["p50"], color=colour, lw=1.6)
        ax.axvline(stats["mean"], color=colour, lw=1.3, ls=(0, (4, 3)))

        anchor = max(stats["p50"], stats["mean"])
        ax.text(
            anchor,
            ceiling * height,
            f"  median {stats['p50']:.1%}",
            color=colour,
            fontsize=12,
            ha="left",
            va="top",
            fontweight="bold",
        )
        ax.text(
            anchor,
            ceiling * (height - 0.10),
            f"  mean {stats['mean']:.1%}",
            color=colour,
            fontsize=12,
            ha="left",
            va="top",
        )

    ax.xaxis.set_major_formatter(plt.FuncFormatter(_pct))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_pct))
    ax.set_xlabel(
        "as a share of estimated collateral value"
        if figures.get("wallets", {}).get("basis") == "combined_proxy"
        else "as a share of the collateral",
        fontsize=12,
    )
    ax.set_ylabel("share of group", fontsize=12)
    ax.tick_params(labelsize=11)
    ax.grid(axis="y")
    ax.legend(loc="upper right", fontsize=11.5)

    return _finish(
        fig,
        ax,
        path,
    )


def loss_concentration(rows: list[Any], figures: dict[str, Any], path: Path) -> Path:

    from option_intel.figures import position_result

    score = position_result if figures["wallets"].get("basis") == "combined_proxy" else lambda r: r.realised
    scored = sorted(sum(score(i) for i in v) for v in writers.by_wallet(rows).values())
    losses = [-x for x in scored if x < 0]
    share = np.cumsum(losses) / sum(losses)
    rank = np.arange(1, len(losses) + 1)

    w = figures["wallets"]
    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    ax.plot(rank, share, color=ACCENT, lw=2.0)
    ax.fill_between(rank, share, color=ACCENT, alpha=0.10)

    for n, label in ((1, "the single worst"), (10, "worst 10"), (100, "worst 100")):
        value = w["share_worst"][str(n)]
        ax.plot([n], [value], "o", color=ACCENT, ms=5)
        ax.annotate(
            f"{label}: {value:.1%}",
            (n, value),
            textcoords="offset points",
            xytext=(9, -3),
            fontsize=11.5,
            color=INK,
            va="top",
        )

    cut = w["cut"]
    if cut > 0:
        ax.axvline(cut, color=INK, lw=1.0, ls=(0, (4, 3)))

    if cut > 0:
        ax.text(
            cut * 0.92,
            0.30,
            f"remove these {cut}\nand the other {w['cut_remaining']:,}\nare positive together",
            ha="right",
            va="center",
            fontsize=12,
            color=INK,
        )
    else:
        ax.text(
            0.97,
            0.12,
            "Estimated aggregate is positive",
            transform=ax.transAxes,
            ha="right",
            fontsize=12,
            color=INK,
        )

    ax.set_xscale("log")

    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.xaxis.set_minor_formatter(plt.NullFormatter())
    ax.set_xlim(1, max(len(losses), 2))
    ax.set_ylim(0, 1.02)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_pct))
    ax.set_xlabel("wallets, worst first", fontsize=12)
    ax.set_ylabel(
        "share of estimated losses" if w.get("basis") == "combined_proxy" else "share of all losses",
        fontsize=12,
    )
    ax.tick_params(labelsize=11)
    ax.grid(axis="y")

    return _finish(
        fig,
        ax,
        path,
    )


def strike_distance(rows: list[Any], figures: dict[str, Any], path: Path) -> Path:

    calls = _calls(rows)
    distance = (
        (lambda r: r.strike / r.spot_at_write - 1)
        if figures["call_distance"].get("basis") == "strike_over_spot_minus_one"
        else writers.distance
    )
    top = 0.40
    bins = np.linspace(0, top, 41)

    out = [r for r in calls if distance(r) >= 0]
    kept = np.clip([distance(r) for r in out if not r.assigned], 0, top)
    took = np.clip([distance(r) for r in out if r.assigned], 0, top)

    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    ax.hist([kept, took], bins=bins, stacked=True, color=[PUT, GONE], alpha=0.8)

    d = figures["call_distance"]
    ceiling = ax.get_ylim()[1] * 1.26
    ax.set_ylim(0, ceiling)
    ax.axvline(d["p50"], color=INK, lw=1.4)
    ax.text(
        d["p50"],
        ceiling * 0.78,
        f"  median {d['p50']:.1%} out of the money",
        fontsize=12,
        color=INK,
        ha="left",
        va="top",
        fontweight="bold",
    )

    ax.text(
        0.175,
        ceiling * 0.36,
        f"ITM at expiry\n{d['exercised']:,}",
        fontsize=12,
        color=GONE,
        ha="left",
        va="center",
        fontweight="bold",
    )
    ax.text(
        0.175,
        ceiling * 0.55,
        f"expired untouched\n{d['untouched']:,}",
        fontsize=12,
        color=PUT,
        ha="left",
        va="center",
        fontweight="bold",
    )

    ax.axvline(0, color=GRID, lw=1.0, ls=(0, (3, 3)))
    ax.text(
        0.002,
        ceiling * 0.955,
        f" not drawn: {d['written_through']} calls written through the strike",
        fontsize=11.5,
        color=INK,
        ha="left",
        va="top",
        zorder=5,
        bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5},
    )
    ax.set_xlim(-0.012, top)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(_pct))
    ax.set_xlabel("how far out of the money it was written", fontsize=12)
    ax.set_ylabel("calls", fontsize=12)
    ax.tick_params(labelsize=11)
    ax.grid(axis="y")

    return _finish(
        fig,
        ax,
        path,
    )


def outcomes(figures: dict[str, Any], path: Path) -> Path:

    b = figures["buckets"]
    order = [
        ("Worthless,\nprice fell", b["fell"]),
        ("Worthless,\nprice rose", b["under"]),
        ("In the money\nat expiry", b["through"]),
    ]

    fig, (top, low) = plt.subplots(
        2, 1, figsize=(8.4, 5.6), sharex=True, gridspec_kw={"height_ratios": [1, 1.5]}
    )
    x = range(len(order))
    top.bar(x, [g["calls"] for _, g in order], width=0.55, color=INK, alpha=0.45)
    for i, (_, g) in enumerate(order):
        top.text(
            i,
            g["calls"],
            f"{g['calls']:,}",
            ha="center",
            va="bottom",
            fontsize=10,
            color=INK,
            fontweight="bold",
        )
    top.set_ylim(0, max(g["calls"] for _, g in order) * 1.28)
    top.set_ylabel("calls")
    top.set_yticks([])
    top.grid(False)
    top.spines["left"].set_visible(False)

    values = [g["net"] for _, g in order]
    low.bar(x, values, width=0.55, color=[KEPT if v > 0 else GONE for v in values], alpha=0.9)
    for i, v in enumerate(values):
        low.text(
            i,
            v + (6e5 if v > 0 else -6e5),
            _t(money(v)),
            ha="center",
            va="bottom" if v > 0 else "top",
            fontsize=10.5,
            color=INK,
            fontweight="bold",
        )
    low.axhline(0, color=GRID, lw=0.9)
    low.set_ylim(min(values) * 1.32, max(values) * 1.32)
    low.set_ylabel(
        "estimated net result"
        if figures.get("wallets", {}).get("basis") == "combined_proxy"
        else "net result"
    )
    low.yaxis.set_major_formatter(plt.FuncFormatter(_axis_millions))
    low.set_xticks(list(x))
    low.set_xticklabels([label for label, _ in order], fontsize=10)
    low.grid(axis="y")

    return _finish(
        fig,
        low,
        path,
    )


def by_asset(figures: dict[str, Any], path: Path) -> Path:

    assets = dict(figures["by_asset"]["calls"])
    small = figures.get("by_asset_small_calls")
    names = sorted(
        (k for k in assets if not small or k not in small["assets"]), key=lambda k: -assets[k]["calls"]
    )
    if small:
        assets["other"] = small
        names.append("other")
    moved = [assets[k]["asset"] for k in names]
    net = [assets[k]["net"] for k in names]
    combined = figures.get("wallets", {}).get("basis") == "combined_proxy"

    fig, ax = plt.subplots(figsize=(max(8.4, len(names) * 2.0), 5.2))
    x = np.arange(len(names))
    ax.bar(
        x - 0.19,
        moved,
        0.36,
        color=INK,
        alpha=0.45,
        label="estimated collateral move" if combined else "what the asset did",
    )
    ax.bar(
        x + 0.19,
        net,
        0.36,
        color=[KEPT if v > 0 else GONE for v in net],
        alpha=0.9,
        label="estimated combined result" if combined else "what the writers kept",
    )

    for i, (m, n) in enumerate(zip(moved, net, strict=True)):
        for offset, value in ((-0.19, m), (0.19, n)):
            sign = "\u2212" if value < 0 else ""
            ax.annotate(
                _t(f"{sign}${abs(value) / 1e6:,.2f}M"),
                (i + offset, value),
                xytext=(0, 4 if value >= 0 else (-4 if offset < 0 else -24)),
                textcoords="offset points",
                ha="center",
                va="bottom" if value >= 0 else "top",
                fontsize=12.5,
                color=INK,
            )

    ax.axhline(0, color=GRID, lw=0.9)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{k}\n{assets[k]['calls']:,} calls" for k in names], fontsize=13)
    ax.tick_params(axis="x", length=0, pad=8)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_axis_millions))
    ax.tick_params(axis="y", labelsize=12)
    low, high = min(min(moved), min(net), 0), max(max(moved), max(net), 0)
    span = high - low
    ax.set_ylim(low - span * 0.2, high + span * 0.12)
    ax.grid(axis="y")
    from matplotlib.patches import Patch

    result = "estimated result" if combined else "what the writers kept"
    ax.legend(
        handles=[
            Patch(
                color=INK, alpha=0.45, label="estimated collateral move" if combined else "what the asset did"
            ),
            Patch(color=KEPT, alpha=0.9, label=f"{result}, gain"),
            Patch(color=GONE, alpha=0.9, label=f"{result}, loss"),
        ],
        loc="upper right",
        fontsize=12,
        frameon=False,
    )
    if small:
        ax.text(
            0.99,
            0.55,
            "other: " + ", ".join(small["assets"]),
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=12,
            color=INK,
        )

    return _finish(
        fig,
        ax,
        path,
    )


def payout_severity(rows: list[Any], figures: dict[str, Any], path: Path) -> Path:

    p, c = figures["payouts"]["puts"], figures["payouts"]["calls"]
    sides = (
        ("ITM puts", OptionType.PUT, PUT, p),
        ("ITM calls", OptionType.CALL, CALL, c),
    )

    fig, ax = plt.subplots(figsize=(8.8, 4.6))
    for label, kind, colour, stats in sides:
        paid = sorted(
            r.intrinsic_paid / (r.contracts * r.strike)
            for r in rows
            if r.option_type is kind and r.assigned and r.contracts * r.strike > 0
        )
        if not paid:
            continue
        share = [(i + 1) / len(paid) for i in range(len(paid))]
        ax.plot(share, paid, color=colour, lw=2.1, label=f"{label} ({stats['n']:,})")

        ax.plot([0.5], [stats["p50"]], "o", color=colour, ms=6, zorder=4)
        below = "put" in label
        ax.annotate(
            f"{stats['p50']:.1%}",
            (0.5, stats["p50"]),
            textcoords="offset points",
            xytext=(8, -8) if below else (-6, 9),
            ha="left" if below else "right",
            va="top" if below else "bottom",
            fontsize=12,
            color=colour,
        )
        ax.annotate(
            f"worst {stats['worst']:.1%}",
            (1.0, stats["worst"]),
            textcoords="offset points",
            xytext=(-4, 6),
            ha="right",
            fontsize=12,
            color=colour,
        )

    ceiling = max(c["worst"], p["worst"]) * 1.1
    ax.axvline(0.5, color=GRID, lw=0.8, ls=(0, (4, 4)))
    ax.text(
        0.512,
        ceiling * 0.5,
        "half of them cost\nless than this",
        ha="left",
        va="center",
        fontsize=11.5,
        color=INK,
    )
    ax.xaxis.set_major_formatter(plt.FuncFormatter(_pct))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_pct))
    ax.set_xlabel("ITM options, smallest payoff first", fontsize=12, color=INK)
    ax.set_ylabel("intrinsic value / strike notional", fontsize=12, color=INK)
    ax.set_xlim(0, 1.0)
    ax.set_ylim(0, ceiling)
    ax.grid(True)
    ax.legend(loc="upper left", fontsize=12, frameon=False)

    return _finish(
        fig,
        ax,
        path,
    )


def wallet_spread(rows: list[Any], figures: dict[str, Any], path: Path) -> Path:

    w = figures["wallets"]
    basis = "estimated" if w.get("basis") == "combined_proxy" else "option"
    buckets = w["buckets"]
    colours = [GONE if b["label"].startswith("lost") else KEPT for b in buckets]
    xs = range(len(buckets))

    fig, (top, low) = plt.subplots(2, 1, figsize=(9.4, 6.2), sharex=True, gridspec_kw={"hspace": 0.16})

    top.bar(xs, [b["writers"] for b in buckets], width=0.62, color=colours, alpha=0.9)
    for x, b in zip(xs, buckets, strict=True):
        top.text(
            x, b["writers"] + 16, f"{b['writers']:,}", ha="center", fontsize=11, fontweight="bold", color=INK
        )
    ceiling = max(b["writers"] for b in buckets) * 1.32
    top.set_ylim(0, ceiling)
    top.text(
        1,
        ceiling * 0.93,
        f"{w['negative'] / w['n']:.0%} negative {basis} P&L",
        ha="center",
        fontsize=12,
        color=GONE,
        fontweight="bold",
    )
    top.text(
        4,
        ceiling * 0.93,
        f"{w['positive_share']:.0%} positive {basis} P&L",
        ha="center",
        fontsize=12,
        color=KEPT,
        fontweight="bold",
    )
    top.set_ylabel("wallets", fontsize=10)

    low.bar(xs, [b["dollars"] for b in buckets], width=0.62, color=colours, alpha=0.9)
    span = max(abs(b["dollars"]) for b in buckets)
    for x, b in zip(xs, buckets, strict=True):
        above = b["dollars"] >= 0
        low.text(
            x,
            b["dollars"] + (span * 0.04 if above else -span * 0.04),
            _t(money(b["dollars"])),
            ha="center",
            va="bottom" if above else "top",
            fontsize=10.5,
            fontweight="bold",
            color=INK,
        )
    low.set_ylim(-span * 1.28, span * 1.28)
    low.set_ylabel(f"{basis} P&L, dollars", fontsize=10)
    low.axhline(0, color=GRID, lw=0.9)

    for ax in (top, low):
        ax.axvline(2.5, color=GRID, lw=1.0, ls=(0, (4, 3)))
        ax.set_yticks([])
        ax.spines["left"].set_visible(False)
    top.spines["bottom"].set_visible(False)

    low.set_xticks(list(xs))
    low.set_xticklabels([b["label"].replace(" $", "\n$") for b in buckets], fontsize=9.5)

    return _finish(
        fig,
        top,
        path,
    )


def persistence(figures: dict[str, Any], path: Path) -> Path:

    marks = sorted(figures["persistence"], key=int)
    lifts = [figures["persistence"][m]["lift"] * 100 for m in marks]
    errs = [figures["persistence"][m]["lift_stderr"] * 100 for m in marks]
    counts = [figures["persistence"][m]["wallets"] for m in marks]

    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    x = np.arange(len(marks))
    ax.errorbar(
        x,
        lifts,
        yerr=errs,
        fmt="o",
        ms=8,
        color=ACCENT,
        ecolor=ACCENT,
        elinewidth=1.6,
        capsize=9,
        capthick=1.6,
        alpha=0.9,
    )
    ax.axhline(0, color=INK, lw=1.2)
    for i, (lift, err) in enumerate(zip(lifts, errs, strict=True)):
        ax.text(i + 0.13, lift, f"  {lift:+.1f} ± {err:.1f}", va="center", fontsize=9.5, color=INK)

    ax.set_xticks(x)
    ax.set_xticklabels(
        [f"{m}+ options\n{n:,} wallets" for m, n in zip(marks, counts, strict=True)], fontsize=10
    )
    ax.set_xlim(-0.5, len(marks) - 0.15)
    ax.set_ylabel("extra chance of winning again\n(points)")
    ax.grid(axis="y")

    return _finish(
        fig,
        ax,
        path,
    )


def after_assignment(figures: dict[str, Any], path: Path) -> Path:

    learn = figures["learning"]
    items = [
        ("How far out of the money\nthey wrote", "distance", lambda v: f"{v:.1%}"),
        ("How long they held", "held_days", lambda v: f"{v:.1f} days"),
        ("Premium taken per option", "premium", lambda v: _t(money(v))),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(8.6, 4.3))
    for ax, (label, key, form) in zip(axes, items, strict=True):
        before, after = learn["before"][key], learn["after"][key]

        moved = GONE if key != "premium" else INK

        ax.bar([0], [before], width=0.55, color=INK, alpha=0.35)
        ax.bar([1], [after], width=0.55, color=moved, alpha=0.85)
        for i, value in enumerate((before, after)):
            ax.text(
                i, value, form(value), ha="center", va="bottom", fontsize=10, color=INK, fontweight="bold"
            )
        ax.set_ylim(0, max(before, after) * 1.32)
        ax.set_xticks([0, 1])

        ax.set_xticklabels(["before the first\nassignment", "after it"], fontsize=9.5)
        ax.set_xlabel(label, fontsize=9.5, labelpad=10)
        ax.set_yticks([])
        ax.grid(False)
        ax.spines["left"].set_visible(False)

    return _finish(
        fig,
        axes[0],
        path,
    )


def by_price_move(figures: dict[str, Any], path: Path) -> Path:

    b = figures["buckets"]
    order = [
        ("Worthless,\nprice fell", b["fell"]),
        ("Worthless,\nprice rose", b["under"]),
        ("In the money\nat expiry", b["through"]),
    ]

    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    x = np.arange(len(order))
    series = (
        ("what the asset did", [g["asset"] for _, g in order], INK, 0.45),
        ("premium collected", [g["premium"] for _, g in order], KEPT, 0.9),
        ("oracle-derived payoff", [g["paid"] for _, g in order], GONE, 0.9),
    )
    for offset, (label, values, colour, alpha) in zip((-0.27, 0.0, 0.27), series, strict=True):
        ax.bar(x + offset, values, 0.25, label=label, color=colour, alpha=alpha)

    every = [v for _, values, _, _ in series for v in values]
    span = max(every) - min(every)
    for offset, (_, values, _, _) in zip((-0.27, 0.0, 0.27), series, strict=True):
        for i, value in enumerate(values):
            if not value:
                continue
            ax.text(
                i + offset,
                value + (span * 0.018 if value >= 0 else -span * 0.018),
                _t(money(value)),
                ha="center",
                va="bottom" if value >= 0 else "top",
                fontsize=8.5,
                color=INK,
            )

    ax.axhline(0, color=GRID, lw=0.9)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{label}\n{g['calls']:,} calls" for label, g in order], fontsize=10)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_axis_millions))
    ax.set_ylim(min(every) - span * 0.16, max(every) + span * 0.16)
    ax.grid(axis="y")

    ax.legend(loc="upper left", fontsize=9)

    return _finish(
        fig,
        ax,
        path,
    )


def _ignoring_rows(draw):

    return lambda rows, figures, path: draw(figures, path)


def the_bridge(figures: dict[str, Any], path: Path) -> Path:

    v = figures["versus_holding"]
    start = figures["call_totals"]["asset"]
    end = figures["call_totals"]["net"]
    after = start + v["unexercised"]

    steps = [
        ("asset move over\noption lifetimes", start, 0.0, GRID),
        (f"the {v['unexercised_calls']:,}\nworthless calls\nchange", v["unexercised"], start, KEPT),
        (f"the {v['exercised_calls']:,}\nITM calls\nchange", v["exercised"], after + v["exercised"], GONE),
        ("what the writers\nkept\nlevel", end, 0.0, INK),
    ]

    fig, ax = plt.subplots(figsize=(9.4, 4.8))
    ceiling = after * 1.18
    for x, (label, value, base, colour) in enumerate(steps):
        ax.bar(
            x,
            abs(value),
            bottom=base,
            width=0.58,
            color=colour,
            alpha=0.55 if colour in (GRID, INK) else 0.92,
        )
        ax.text(
            x,
            base + abs(value) + ceiling * 0.02,
            _t(money(value)),
            ha="center",
            va="bottom",
            fontsize=11,
            color=INK,
        )
        if 0 < x < len(steps) - 1:
            level = base + (value if value > 0 else 0)
            ax.plot([x + 0.29, x + 1 - 0.29], [level, level], color=GRID, lw=0.9, ls=(0, (3, 3)))
    ax.plot([0.29, 1 - 0.29], [start, start], color=GRID, lw=0.9, ls=(0, (3, 3)))

    ax.set_xticks(range(len(steps)))
    ax.set_xticklabels([s[0] for s in steps], fontsize=9.5, linespacing=1.45)
    ax.set_ylim(0, ceiling)

    ax.set_yticks([n * 5e6 for n in range(int(ceiling // 5e6) + 1)])
    ax.axhline(0, color=INK, lw=0.9)
    ax.grid(axis="y")
    ax.yaxis.set_major_formatter(_axis_millions)
    return _finish(fig, ax, path)


def _price_tick(value: float, _pos: int = 0) -> str:

    if value >= 10_000:
        return f"{value / 1000:,.0f}K"
    if value >= 10:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def weekly_strikes(asset: str):

    def draw(figures: dict[str, Any], path: Path) -> Path:
        w = figures["weekly"]
        s = w["spread"][asset]
        cells = {side: w["by_week"][asset][side] for side in ("put", "call")}
        busiest = max(c["contracts"] for side in cells for c in cells[side]) or 1.0

        fig, ax = plt.subplots(figsize=(12.4, 5.8))

        fig.subplots_adjust(top=0.84, bottom=0.24)

        settled = w["oracle"].get(asset) or []
        ax.plot(
            [date.fromisoformat(d["expiry"]) for d in settled],
            [d["price"] for d in settled],
            color=INK,
            lw=1.6,
            alpha=0.65,
            zorder=2,
            label="price it settled at",
        )

        for side, colour in (("put", PUT), ("call", CALL)):
            group = cells[side]
            sizes = [24 + 320 * c["contracts"] / busiest for c in group]
            xs = [date.fromisoformat(c["expiry"]) for c in group]
            ys = [c["strike"] for c in group]
            ax.scatter(
                xs, ys, s=sizes, color=colour, alpha=0.78, linewidths=0, label=f"{side} strike", zorder=3
            )
            hit = [i for i, c in enumerate(group) if c["assigned"] > w["mostly_assigned"]]
            if hit:
                ax.scatter(
                    [xs[i] for i in hit],
                    [ys[i] for i in hit],
                    s=[sizes[i] for i in hit],
                    facecolors="none",
                    edgecolors=GONE,
                    linewidths=1.4,
                    zorder=4,
                    label=("ring: the price finished through it" if side == "call" else None),
                )

        ax.yaxis.set_major_formatter(FuncFormatter(_price_tick))
        ax.set_ylabel(f"{asset} price", fontsize=9, color=INK)
        ax.grid(color=GRID, alpha=0.25, lw=0.7, zorder=0)
        ax.set_axisbelow(True)
        ax.tick_params(axis="x", labelrotation=30, labelsize=8.5)
        ax.tick_params(axis="y", labelsize=9)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        ax.legend(
            loc="upper left",
            frameon=False,
            fontsize=9,
            ncol=4,
            markerscale=0.8,
            handletextpad=0.3,
            bbox_to_anchor=(0.0, 1.07),
        )
        ax.text(
            0.0,
            1.13,
            f"the median expiry settled calls struck "
            f"{s['call_distance_median']:.1%} above where they were sold "
            f"and puts {s['put_distance_median']:.1%} below  ·  "
            f"{s['calls_breached']} call expiries and {s['puts_breached']} "
            f"put expiries finished through the strike",
            transform=ax.transAxes,
            fontsize=8.5,
            color=INK,
            ha="left",
            va="bottom",
        )
        fig.text(
            0.5,
            0.005,
            f"volume-weighted strike settling on each expiry  ·  dot area "
            f"is that expiry's contract volume\n"
            f"a ring means over {w['mostly_assigned']:.0%} of the "
            f"contracts finished ITM  ·  expiries under "
            f"{w['min_cell']} options dropped, which is every gap here",
            ha="center",
            va="bottom",
            fontsize=8,
            color=INK,
            linespacing=1.6,
        )
        return _finish(fig, ax, path)

    draw.__name__ = f"weekly_{asset.lower()}"
    return draw


FIGURES = {
    "the-trade": _ignoring_rows(the_trade),
    "waterfall": _ignoring_rows(waterfall),
    "the-bridge": _ignoring_rows(the_bridge),
    "outcomes": _ignoring_rows(outcomes),
    "premium-against-move": premium_against_move,
    "strike-distance": strike_distance,
    "payout-severity": payout_severity,
    "by-asset": _ignoring_rows(by_asset),
    "wallet-spread": wallet_spread,
    "loss-concentration": loss_concentration,
    "after-assignment": _ignoring_rows(after_assignment),
    "weekly-hype": _ignoring_rows(weekly_strikes("HYPE")),
    "weekly-eth": _ignoring_rows(weekly_strikes("ETH")),
    "weekly-btc": _ignoring_rows(weekly_strikes("BTC")),
    "weekly-sol": _ignoring_rows(weekly_strikes("SOL")),
    "by-price-move": _ignoring_rows(by_price_move),
}
