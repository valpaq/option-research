from __future__ import annotations

import math
import statistics as st
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

from option_intel import oracle, positions, settlement, weekly, writers
from option_intel.model import OptionType
from option_intel.reconstruct import load_writes
from option_intel.spot import HyperliquidSpot


def load(data_dir: Path = Path("data/onchain"), protocol: str = "rysk") -> list[Any]:
    spot = HyperliquidSpot()
    paths = [p for p in sorted(data_dir.glob("*/sales.csv")) if protocol in p.parent.name]
    return load_writes(paths, spot)


NEAR_THE_MONEY = 0.05


ON_THE_STRIKE = 0.02


BY_ASSET_MIN_SHARE = 0.01


class Settled(NamedTuple):
    rows: list[Any]
    book: list[Any]
    writes: list[Any]
    spot: HyperliquidSpot
    asof: datetime
    why: dict[str, Any]


def refined_spot(path: Path = Path("data/cache/spot-refined.csv")) -> dict[tuple[str, str], float]:

    import csv

    if not path.exists():
        return {}
    with path.open() as fh:
        return {(row["underlying"], row["written_at"]): float(row["spot"]) for row in csv.DictReader(fh)}


def refine(book: list[Any], fine: dict[tuple[str, str], float]) -> list[Any]:

    if not fine:
        return book
    from dataclasses import replace as _replace

    return [
        _replace(r, spot_at_write=fine[(r.underlying, r.written_at.isoformat())])
        if (r.underlying, r.written_at.isoformat()) in fine
        else r
        for r in book
    ]


def settled(
    start: datetime,
    end: datetime,
    *,
    chain: str = "hyperevm",
    data_dir: Path = Path("data/onchain"),
    refined: Path = Path("data/cache/spot-refined.csv"),
) -> Settled:

    spot = HyperliquidSpot()
    asof = datetime.now(UTC)
    writes = load(data_dir)
    book, why = settlement.settle(
        writes,
        spot,
        asof=asof,
        oracle=oracle.OraclePrices(),
        asset_addresses={
            k: v
            for c in Path("data/cache").glob("otokens-*.json")
            for k, v in oracle.asset_addresses(c).items()
        },
    )
    fine = refined_spot(refined)
    if fine:

        def in_the_money(rows: list[Any]) -> tuple[int, int]:
            return (
                sum(1 for r in rows if r.option_type is OptionType.CALL and r.strike < r.spot_at_write),
                sum(1 for r in rows if r.option_type is OptionType.PUT and r.strike > r.spot_at_write),
            )

        def windowed(rows: list[Any]) -> list[Any]:
            return [r for r in rows if (not chain or r.chain == chain) and start <= r.expired_at <= end]

        before = in_the_money(windowed(book))
        book = refine(book, fine)
        after = in_the_money(windowed(book))
        why["spot_refined"] = sum(1 for r in book if (r.underlying, r.written_at.isoformat()) in fine)
        why["itm_hourly_calls"], why["itm_hourly_puts"] = before
        why["itm_minute_calls"], why["itm_minute_puts"] = after

        why["itm_at_expiry"] = sum(
            1
            for r in windowed(book)
            if r.option_type is OptionType.CALL and r.strike < r.spot_at_write and r.held_days <= 1
        )

    rows = [r for r in book if (not chain or r.chain == chain) and start <= r.expired_at <= end]
    return Settled(rows, book, writes, spot, asof, why)


def position_result(row):

    move = (
        row.collateral_amount * (row.spot_at_expiry - row.spot_at_write)
        if row.option_type is OptionType.CALL and not writers.is_cash_secured(row)
        else 0.0
    )
    return row.realised + move


def compute(
    start: datetime,
    end: datetime,
    *,
    chain: str = "hyperevm",
    data_dir: Path = Path("data/onchain"),
    context: Settled | None = None,
    fine_prices: dict[tuple[str, str], float] | None = None,
    wallet_include_collateral: bool = False,
    simple_call_distance: bool = False,
    all_assets: bool = False,
) -> dict[str, Any]:

    rows, book, writes, spot, asof, why = (
        context if context is not None else settled(start, end, chain=chain, data_dir=data_dir)
    )
    calls = [
        r
        for r in rows
        if r.option_type is OptionType.CALL and r.collateral_amount > 0 and not writers.is_cash_secured(r)
    ]
    puts = writers.cash_secured_puts(rows)

    at_write = sum(r.collateral_amount * r.spot_at_write for r in calls)
    at_expiry = sum(r.collateral_amount * r.spot_at_expiry for r in calls)
    moved, premium = at_expiry - at_write, sum(r.premium for r in calls)
    paid = sum(r.intrinsic_paid for r in calls)

    def bucket(group: list[Any]) -> dict[str, Any]:
        a = sum(r.collateral_amount * (r.spot_at_expiry - r.spot_at_write) for r in group)
        p = sum(r.premium for r in group)
        d = sum(r.intrinsic_paid for r in group)
        return {
            "calls": len(group),
            "collateral": sum(r.collateral_amount * r.spot_at_write for r in group),
            "asset": a,
            "premium": p,
            "paid": -d,
            "net": a + p - d,
        }

    through = [r for r in calls if r.assigned]
    fell = [r for r in calls if not r.assigned and r.spot_at_expiry < r.spot_at_write]
    under = [r for r in calls if not r.assigned and r.spot_at_expiry >= r.spot_at_write]
    unexercised = [r for r in calls if not r.assigned]
    down = [r for r in unexercised if r.spot_at_expiry < r.spot_at_write]

    call_distance = (lambda r: r.strike / r.spot_at_write - 1) if simple_call_distance else writers.distance
    score = position_result if wallet_include_collateral else lambda r: r.realised
    wal = writers.by_wallet(rows)
    totals = sorted(sum(score(i) for i in v) for v in wal.values())
    n = len(totals)
    gains = [t for t in totals if t > 0]
    losses = [t for t in totals if t < 0]
    cut = next((k for k in range(n) if sum(totals[k:]) > 0), n)

    prem_notional = sorted(
        r.premium / (r.collateral_amount * r.spot_at_write)
        for r in calls
        if r.collateral_amount * r.spot_at_write > 0
    )
    declines = sorted((r.spot_at_write - r.spot_at_expiry) / r.spot_at_write for r in down)
    q = lambda v, x: v[min(int(x * len(v)), len(v) - 1)]

    prices = {}
    for coin in ("HYPE", "ETH", "BTC", "SOL"):
        a, z = spot.price_at(coin, start), spot.price_at(coin, end)
        if a and z:
            prices[coin] = {"start": a, "end": z, "change": z / a - 1}
    series = [spot.price_at("HYPE", start + timedelta(days=d)) for d in range((end - start).days + 1)]
    series = [x for x in series if x]
    vol = st.pstdev([math.log(b / a) for a, b in zip(series, series[1:], strict=False)]) * math.sqrt(365)

    by_asset_calls, by_asset_puts = {}, {}
    small_calls = None
    asset_names = sorted({r.underlying for r in rows}) if all_assets else ("HYPE", "ETH", "BTC", "SOL")
    for a in asset_names:
        g = [r for r in calls if r.underlying == a]
        if g:
            by_asset_calls[a] = bucket(g)
        pg = [
            r
            for r in rows
            if r.underlying == a and r.option_type is OptionType.PUT and writers.is_cash_secured(r)
        ]
        if pg:
            pp = sum(r.premium for r in pg)
            pd = sum(r.intrinsic_paid for r in pg)
            by_asset_puts[a] = {
                "puts": len(pg),
                "premium": pp,
                "paid": pd,
                "kept": pp - pd,
                "rate": (pp - pd) / pp if pp else None,
                "assigned": sum(1 for r in pg if r.assigned) / len(pg),
            }

    small = sorted(a for a, v in by_asset_calls.items() if v["calls"] < BY_ASSET_MIN_SHARE * len(calls))
    if small:
        small_calls = {
            "min_share": BY_ASSET_MIN_SHARE,
            "assets": small,
            **bucket([r for r in calls if r.underlying in small]),
        }

    def payouts(group: list[Any]) -> dict[str, float]:
        v = sorted(r.intrinsic_paid / (r.contracts * r.strike) for r in group if r.contracts * r.strike > 0)
        return {
            "n": len(v),
            **{f"p{int(x * 100)}": q(v, x) for x in (0.5, 0.75, 0.9, 0.99)},
            "mean": st.fmean(v) if v else 0.0,
            "worst": v[-1] if v else 0.0,
        }

    worst_wallet = min(wal.items(), key=lambda kv: sum(score(i) for i in kv[1]))
    worst30 = {k for _, k in sorted((sum(score(i) for i in v), k) for k, v in wal.items())[:30]}
    flat30 = [i for k, v in wal.items() if k in worst30 for i in v]
    rest = [i for k, v in wal.items() if k not in worst30 for i in v]
    near = lambda g: sum(1 for i in g if writers.distance(i) < NEAR_THE_MONEY) / len(g)
    open_pos, _ = positions.open_positions(writes, spot, asof=asof)
    before = [r for r in rows if r.written_at < start]
    matched = [r for r in calls if r.assigned and abs(r.collateral_amount - r.contracts) < 1e-9]
    near_rows = [r for r in rows if abs(writers.distance(r)) < ON_THE_STRIKE]
    shown = ("HYPE", "ETH", "BTC", "SOL")
    tail_calls = [r for r in calls if r.underlying not in shown]
    tail_puts = [
        r
        for r in rows
        if r.underlying not in shown and r.option_type is OptionType.PUT and writers.is_cash_secured(r)
    ]
    hype_calls = [r for r in calls if r.underlying == "HYPE"]
    hype_asset = sum(r.collateral_amount * (r.spot_at_expiry - r.spot_at_write) for r in hype_calls)
    hype_net = hype_asset + sum(r.premium for r in hype_calls) - sum(r.intrinsic_paid for r in hype_calls)
    others = [r for r in calls if r.underlying in ("ETH", "BTC", "SOL")]
    other_asset = sum(r.collateral_amount * (r.spot_at_expiry - r.spot_at_write) for r in others)
    other_prem = sum(r.premium for r in others)
    other_paid = sum(r.intrinsic_paid for r in others)

    from option_intel import minute

    mp = minute.MinutePrices() if fine_prices is None else None
    hourly_itm = [r for r in calls if r.strike < r.spot_at_write]
    deep = [r for r in hourly_itm if abs(writers.distance(r)) >= 0.02]
    left = entered = no_data = 0
    for r in calls:
        if abs(writers.distance(r)) >= 0.02:
            continue
        fine = (
            fine_prices.get((r.underlying, r.written_at.isoformat()))
            if fine_prices is not None
            else mp.price_at(r.underlying, r.written_at)
        )
        was = r.strike < r.spot_at_write
        if fine is None:
            no_data += 1 if was else 0
            continue
        if was and r.strike >= fine:
            left += 1
        elif not was and r.strike < fine:
            entered += 1
    itm = {
        "hourly": len(hourly_itm),
        "minute": len(hourly_itm) - left + entered,
        "left": left,
        "entered": entered,
        "no_data": no_data,
        "deep": len(deep),
        "deep_premium": sum(r.premium for r in deep),
        "deep_share": sum(r.premium for r in deep) / premium if premium else None,
        "deepest": sorted((1 - r.strike / r.spot_at_write) for r in deep)[-3:],
    }

    return {
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "chain": chain,
            "days": (end - start).days,
        },
        "asof": asof.isoformat(),
        "settled": len(rows),
        "writers": len({r.writer for r in rows if r.writer}),
        "calls": len(calls),
        "puts": puts.get("puts", 0),
        "collateral": {"at_write": at_write, "at_expiry": at_expiry},
        "call_totals": {
            "asset": moved,
            "premium": premium,
            "paid": -paid,
            "net": moved + premium - paid,
            "retained": (moved + premium - paid) / moved if moved else None,
            "paid_multiple": paid / premium if premium else None,
            "assigned": sum(1 for r in calls if r.assigned) / len(calls),
            "forwent": 1 - ((moved + premium - paid) / moved) if moved else None,
        },
        "buckets": {
            "fell": bucket(fell),
            "under": bucket(under),
            "through": bucket(through),
            "all": bucket(calls),
            "fell_share": len(fell) / len(calls),
            "unexercised_paid": sum(r.intrinsic_paid for r in fell + under),
            "fell_cushion": bucket(fell)["premium"] / abs(bucket(fell)["asset"]),
            "through_retained": bucket(through)["net"] / bucket(through)["asset"],
        },
        "unexercised": {
            "total": len(unexercised),
            "share": len(unexercised) / len(calls),
            "up": len(unexercised) - len(down),
            "down": len(down),
            "up_share": (len(unexercised) - len(down)) / len(unexercised),
            "down_share": len(down) / len(unexercised),
        },
        "premium_notional": {f"p{int(x * 100)}": q(prem_notional, x) for x in (0.1, 0.25, 0.5, 0.75, 0.9)}
        | {
            "mean": st.fmean(prem_notional) if prem_notional else None,
            "aggregate": premium / at_write if at_write else None,
        },
        "decline": {f"p{int(x * 100)}": q(declines, x) for x in (0.1, 0.25, 0.5, 0.75, 0.9)}
        | {
            "mean": st.fmean(declines) if declines else None,
            "covered": sum(
                1 for r in down if r.premium >= r.collateral_amount * (r.spot_at_write - r.spot_at_expiry)
            ),
            "of": len(down),
        },
        "call_distance": {
            "basis": "strike_over_spot_minus_one" if simple_call_distance else "log_strike_over_spot",
            **{f"p{int(x * 100)}": q(sorted(call_distance(r) for r in calls), x) for x in (0.25, 0.5, 0.75)},
            "beyond_10pct": sum(1 for r in calls if call_distance(r) > 0.10) / len(calls),
            "beyond_20pct": sum(1 for r in calls if call_distance(r) > 0.20) / len(calls),
            "beyond_10pct_exercised": (
                sum(1 for r in calls if call_distance(r) > 0.10 and r.assigned)
                / max(sum(1 for r in calls if call_distance(r) > 0.10), 1)
            ),
            "written_through": sum(1 for r in calls if call_distance(r) < 0),
            "exercised": sum(1 for r in calls if call_distance(r) >= 0 and r.assigned),
            "untouched": sum(1 for r in calls if call_distance(r) >= 0 and not r.assigned),
        },
        "versus_holding": {
            "unexercised": sum(r.premium for r in calls if not r.assigned),
            "unexercised_calls": sum(1 for r in calls if not r.assigned),
            "exercised": sum(r.premium - r.intrinsic_paid for r in calls if r.assigned),
            "exercised_calls": sum(1 for r in calls if r.assigned),
            "gap": sum(r.premium - r.intrinsic_paid for r in calls),
        },
        "weekly": weekly.measure(rows),
        "held_days_median": st.median(r.held_days for r in calls),
        "held_days_mean": st.fmean(r.held_days for r in calls),
        "annualised": (1 + q(prem_notional, 0.5)) ** (365 / st.median([r.held_days for r in calls])) - 1,
        "covered_share": (
            sum(1 for r in down if r.premium >= r.collateral_amount * (r.spot_at_write - r.spot_at_expiry))
            / len(down)
        )
        if down
        else None,
        "puts_totals": puts,
        "payouts": {
            "puts": payouts([r for r in rows if r.option_type is OptionType.PUT and r.assigned]),
            "calls": payouts([r for r in calls if r.assigned]),
        },
        "prices": prices,
        "hype_vol": vol,
        "hype_drawdown": min(x / max(series[: i + 1]) - 1 for i, x in enumerate(series)),
        "hype_calls_fell": sum(
            1 for r in calls if r.underlying == "HYPE" and r.spot_at_expiry < r.spot_at_write
        ),
        "by_asset": {"calls": by_asset_calls, "puts": by_asset_puts},
        "by_asset_small_calls": small_calls,
        "wallets": {
            "basis": "combined_proxy" if wallet_include_collateral else "option",
            "n": n,
            "positive": len(gains),
            "positive_share": len(gains) / n,
            "gains": sum(gains),
            "losses": sum(losses),
            "median": st.median(totals),
            "mean": sum(totals) / n,
            "percentiles": {
                f"p{x}": totals[min(int(x / 100 * n), n - 1)] for x in (1, 5, 10, 25, 50, 75, 90, 95, 99)
            },
            "median_winner": st.median(gains),
            "median_loser": st.median(losses),
            "share_worst": {k: sum(losses[:k]) / sum(losses) for k in (1, 10, 35, 100)},
            "cut": cut,
            "cut_remaining": n - cut,
            "cut_rest": sum(totals[cut:]),
            "negative": len(losses),
            "buckets": [
                {
                    "label": label,
                    "writers": sum(1 for v in totals if lo <= v < hi),
                    "share": sum(1 for v in totals if lo <= v < hi) / n,
                    "dollars": sum(v for v in totals if lo <= v < hi),
                }
                for lo, hi, label in (
                    (-1e18, -1000, "lost over $1,000"),
                    (-1000, -100, "lost $100-1,000"),
                    (-100, 0, "lost under $100"),
                    (0, 100, "made under $100"),
                    (100, 1000, "made $100-1,000"),
                    (1000, 1e18, "made over $1,000"),
                )
            ],
            "p99_gain": sorted(gains)[int(0.99 * len(gains))] if gains else None,
            "p1_loss": sorted(losses)[int(0.01 * len(losses))] if losses else None,
        },
        "thresholds": {"near_the_money": NEAR_THE_MONEY, "on_the_strike": ON_THE_STRIKE},
        "worst30": {
            "options": len(flat30),
            "hype": sum(1 for i in flat30 if i.underlying == "HYPE") / len(flat30),
            "calls": sum(1 for i in flat30 if i.option_type is OptionType.CALL) / len(flat30),
            "near": near(flat30),
            "near_rest": near(rest),
        },
        "worst_wallet": {
            "options": len(worst_wallet[1]),
            "assets": len({i.underlying for i in worst_wallet[1]}),
            "days": len({i.written_at.date() for i in worst_wallet[1]}),
            "median_contracts": st.median(i.contracts for i in worst_wallet[1]),
        },
        "persistence": {str(m): writers.persistence(rows, min_settled=m) for m in (10, 20, 40)},
        "learning": writers.learning(rows),
        "open": {
            "n": len(open_pos),
            "premium_share": sum(p.premium_received for p in open_pos)
            / (sum(p.premium_received for p in open_pos) + premium + puts.get("premium", 0)),
            "through_strike": sum(
                1 for p in open_pos if (p.spot > p.instrument.strike) == (p.side == "short_call")
            )
            / len(open_pos)
            if open_pos
            else None,
        },
        "assigned_rate": sum(1 for r in rows if r.assigned) / len(rows),
        "written_before": {
            "n": len(before),
            "share": len(before) / len(rows),
            "premium": sum(r.premium for r in before),
            "result": sum(r.premium - r.intrinsic_paid for r in before),
        },
        "identity": {
            "calls": len(matched),
            "value": sum(r.collateral_amount * r.strike + r.premium for r in matched),
        },
        "near_strike": {"n": len(near_rows), "share": len(near_rows) / len(rows), "threshold": ON_THE_STRIKE},
        "tail": {
            "calls": len(tail_calls),
            "net": sum(
                r.collateral_amount * (r.spot_at_expiry - r.spot_at_write) + r.premium - r.intrinsic_paid
                for r in tail_calls
            ),
            "premium": sum(r.premium for r in tail_puts),
        },
        "hype_calls": {"n": len(hype_calls), "retained": hype_net / hype_asset if hype_asset else None},
        "others": {
            "premium": other_prem,
            "decline": other_asset,
            "covers": other_prem / abs(other_asset) if other_asset else None,
            "net": other_asset + other_prem - other_paid,
        },
        "tail_assets": {
            a: {
                "n": len(g),
                "premium": sum(r.premium for r in g),
                "rate": (sum(r.premium for r in g) - sum(r.intrinsic_paid for r in g))
                / sum(r.premium for r in g)
                if sum(r.premium for r in g)
                else None,
            }
            for a in sorted({r.underlying for r in rows} - set(shown))
            if (g := [r for r in rows if r.underlying == a])
        },
        "in_the_money": itm,
        "fee": {
            "basis": "MMarket stablecoin outflow to non-writers; includes gas reimbursement where present",
            "total": sum(r.fee for r in rows),
            "calls": sum(r.fee for r in calls),
            "puts": sum(
                r.fee for r in rows if r.option_type is OptionType.PUT and writers.is_cash_secured(r)
            ),
            "as_share_of_gross": (sum(r.fee for r in rows) / sum(r.premium + r.fee for r in rows))
            if rows
            else None,
            "sales_charged": sum(1 for r in rows if r.fee > 0),
            "sales_free": sum(1 for r in rows if r.fee == 0),
            "wallets_never_charged": len(
                {r.writer for r in rows if r.fee == 0} - {r.writer for r in rows if r.fee > 0}
            ),
            "wallets_always_charged": len(
                {r.writer for r in rows if r.fee > 0} - {r.writer for r in rows if r.fee == 0}
            ),
        },
        "example": (
            lambda r: {
                "premium": r.premium,
                "fee": r.fee,
                "gross": r.premium + r.fee,
                "ratio": r.fee / r.premium if r.premium else None,
            }
        )(max((r for r in rows if r.fee > 0), key=lambda r: r.fee))
        if any(r.fee > 0 for r in rows)
        else {},
        "fees": sum(r.fee for r in rows),
        "priced": {
            "oracle": sum(r.price_source == "oracle" for r in rows),
            "spot": sum(r.price_source == "spot" for r in rows),
        },
        "refinement": {
            "rows": why.get("spot_refined", 0),
            "calls_hourly": why.get("itm_hourly_calls", 0),
            "puts_hourly": why.get("itm_hourly_puts", 0),
            "calls_minute": why.get("itm_minute_calls", 0),
            "puts_minute": why.get("itm_minute_puts", 0),
            "at_expiry": why.get("itm_at_expiry", 0),
        },
        "excluded_chain": {
            "options": sum(1 for r in book if r.chain == "ethereum"),
            "writers": len({r.writer for r in book if r.chain == "ethereum"}),
        },
    }
