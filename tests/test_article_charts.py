from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from option_intel import article_charts
from option_intel.model import OptionType
from option_intel.settlement import Settled

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def settled(
    *,
    writer: str = "0xw",
    option_type: OptionType = OptionType.CALL,
    strike: float = 110.0,
    spot_at_write: float = 100.0,
    spot_at_expiry: float = 100.0,
    contracts: float = 1.0,
    premium: float = 50.0,
    collateral: float = 1.0,
    symbol: str = "HYPE",
    tag: str = "",
) -> Settled:
    return Settled(
        protocol="rysk",
        chain="hyperevm",
        tx_hash=f"0x{writer}{strike}{premium}{tag}",
        writer=writer,
        underlying="HYPE",
        option_type=option_type,
        strike=strike,
        written_at=NOW - timedelta(days=20),
        expired_at=NOW - timedelta(days=13),
        contracts=contracts,
        premium=premium,
        fee=0.0,
        spot_at_write=spot_at_write,
        spot_at_expiry=spot_at_expiry,
        collateral_symbol=symbol,
        collateral_amount=collateral,
    )


def book() -> list[Settled]:

    rows = [
        settled(writer="0xa", strike=105.0, spot_at_expiry=120.0, tag="x"),
        settled(writer="0xb", strike=108.0, spot_at_expiry=115.0, tag="y"),
        settled(writer="0xc", strike=130.0, spot_at_expiry=112.0, tag="u"),
        settled(writer="0xd", strike=125.0, spot_at_expiry=88.0, tag="d"),
        settled(writer="0xe", strike=140.0, spot_at_expiry=70.0, tag="e"),
        settled(
            writer="0xf",
            option_type=OptionType.PUT,
            strike=90.0,
            spot_at_expiry=80.0,
            collateral=90.0,
            symbol="USDC",
            tag="p",
        ),
    ]
    return rows


def figures() -> dict:

    return {
        "calls": 5,
        "window": {"days": 367},
        "weekly": {
            "assets": ["HYPE", "ETH", "BTC", "SOL"],
            "min_cell": 10,
            "mostly_assigned": 0.5,
            "cells": 16,
            "thin_cells": 1,
            "expiries": 2,
            "by_week": {
                asset: {
                    side: [
                        {
                            "expiry": "2026-01-23",
                            "options": 40,
                            "implied": vol,
                            "notional": 400_000.0,
                            "contracts": 4_000.0,
                            "price_per_contract": 2.0,
                            "strike": level * (1.1 if side == "call" else 0.9),
                            "spot_at_write": level,
                            "distance": 0.10,
                            "assigned": 0.8,
                            "tenor_days": 14.0,
                        },
                        {
                            "expiry": "2026-01-30",
                            "options": 30,
                            "implied": vol + 0.05,
                            "notional": 150_000.0,
                            "contracts": 1_500.0,
                            "price_per_contract": 2.4,
                            "strike": level * (1.05 if side == "call" else 0.95),
                            "spot_at_write": level,
                            "distance": 0.05,
                            "assigned": 0.1,
                            "tenor_days": 12.0,
                        },
                    ]
                    for side, vol in (("put", base + 0.06), ("call", base))
                }
                for asset, base, level in (
                    ("HYPE", 0.60, 40.0),
                    ("ETH", 0.55, 2_500.0),
                    ("BTC", 0.35, 90_000.0),
                    ("SOL", 0.50, 120.0),
                )
            },
            "oracle": {
                asset: [
                    {"expiry": "2026-01-23", "price": level},
                    {"expiry": "2026-01-30", "price": level * 1.1},
                ]
                for asset, level in (("HYPE", 40.0), ("ETH", 2_500.0), ("BTC", 90_000.0), ("SOL", 120.0))
            },
            "oracle_prints": {a: 2 for a in ("HYPE", "ETH", "BTC", "SOL")},
            "expiry_cadence": {
                "expiries": 2,
                "gaps": 1,
                "weekly_gaps": 1,
                "median_gap_days": 7,
                "longest_gap_days": 7,
            },
            "against_price": {
                asset: {"cells": 40, "rho": -0.12, "p": 0.31, "detectable": 0.20}
                for asset in ("HYPE", "ETH", "BTC", "SOL")
            },
            "spread": {
                asset: {
                    "expiries": 2,
                    "implied_low": 0.35,
                    "implied_high": 0.46,
                    "price_low": 2.0,
                    "price_high": 2.4,
                    "put_over_call_median": 0.06,
                    "call_distance_median": 0.10,
                    "put_distance_median": 0.07,
                    "weeks_calls_further": 2,
                    "ringed_expiries": 1,
                    "calls_breached": 1,
                    "puts_breached": 1,
                    "busiest_assigned": {
                        "side": "call",
                        "expiry": "2026-01-23",
                        "strike": 44.0,
                        "spot_at_write": 40.0,
                        "contracts": 4_000.0,
                        "assigned": 0.8,
                    },
                    "weeks_puts_dearer": 2,
                    "weeks_compared": 2,
                }
                for asset in ("HYPE", "ETH", "BTC", "SOL")
            },
        },
        "versus_holding": {
            "unexercised": 4_914_218.8,
            "exercised": -14_526_633.9,
            "gap": -9_612_415.1,
            "unexercised_calls": 3,
            "exercised_calls": 2,
        },
        "call_totals": {
            "asset": 10_880_510.6,
            "premium": 6_638_144.7,
            "paid": -16_250_559.9,
            "net": 1_268_095.4,
            "retained": 0.1165,
            "paid_multiple": 2.448,
            "assigned": 0.22737,
            "forwent": 0.88345,
        },
        "premium_notional": {"p50": 0.0104, "mean": 0.0119},
        "decline": {"p50": 0.0711, "mean": 0.0938, "covered": 774, "of": 9230},
        "covered_share": 0.0839,
        "call_distance": {
            "p50": 0.094,
            "beyond_10pct": 0.470,
            "beyond_10pct_exercised": 0.135,
            "written_through": 1,
            "exercised": 2,
            "untouched": 2,
        },
        "wallets": {
            "n": 2991,
            "positive": 1979,
            "negative": 1012,
            "positive_share": 0.6617,
            "gains": 2_862_760.3,
            "losses": -11_468_147.7,
            "median": 47.4,
            "mean": -2877.1,
            "cut": 2,
            "cut_remaining": 2938,
            "cut_rest": 14_505.65,
            "share_worst": {"1": 0.2995, "10": 0.5056, "100": 0.8395},
            "buckets": [
                {"label": "lost over $1,000", "writers": 478, "share": 0.160, "dollars": -11_276_523.0},
                {"label": "lost $100-1,000", "writers": 419, "share": 0.140, "dollars": -186_474.0},
                {"label": "lost under $100", "writers": 115, "share": 0.038, "dollars": -5_151.0},
                {"label": "made under $100", "writers": 774, "share": 0.259, "dollars": 30_717.0},
                {"label": "made $100-1,000", "writers": 796, "share": 0.266, "dollars": 278_469.0},
                {"label": "made over $1,000", "writers": 409, "share": 0.137, "dollars": 2_553_574.0},
            ],
        },
        "buckets": {
            "fell": {
                "calls": 9230,
                "asset": -24_052_030.4,
                "premium": 3_528_918.4,
                "paid": -0.0,
                "net": -20_523_112.0,
            },
            "under": {
                "calls": 4515,
                "asset": 8_188_744.3,
                "premium": 1_385_300.4,
                "paid": -0.0,
                "net": 9_574_044.6,
            },
            "through": {
                "calls": 4045,
                "asset": 26_743_796.7,
                "premium": 1_723_926.0,
                "paid": -16_250_559.9,
                "net": 12_217_162.8,
            },
            "all": {
                "calls": 17790,
                "asset": 10_880_510.6,
                "premium": 6_638_144.7,
                "paid": -16_250_559.9,
                "net": 1_268_095.4,
            },
        },
        "by_asset": {
            "calls": {
                "HYPE": {"calls": 10154, "asset": 15_371_444.9, "net": 5_496_441.9},
                "ETH": {"calls": 4267, "asset": -1_936_556.5, "net": -1_761_434.9},
                "BTC": {"calls": 2335, "asset": -1_750_081.7, "net": -1_727_212.5},
                "SOL": {"calls": 747, "asset": -580_816.6, "net": -500_071.5},
            }
        },
        "payouts": {
            "puts": {
                "n": 4009,
                "p50": 0.05498,
                "mean": 0.08213,
                "p75": 0.10572,
                "p90": 0.1609,
                "p99": 0.29728,
                "worst": 0.42937,
            },
            "calls": {
                "n": 4045,
                "p50": 0.07876,
                "mean": 0.13044,
                "p75": 0.18006,
                "p90": 0.28037,
                "p99": 0.42682,
                "worst": 0.86425,
            },
        },
        "puts_totals": {"assigned_share": 0.20101},
        "persistence": {
            "10": {"wallets": 897, "lift": 0.0805, "lift_stderr": 0.0369, "lift_over_stderr": 2.184},
            "20": {"wallets": 463, "lift": 0.0449, "lift_stderr": 0.0475, "lift_over_stderr": 0.946},
            "40": {"wallets": 199, "lift": 0.0430, "lift_stderr": 0.0713, "lift_over_stderr": 0.605},
        },
        "learning": {
            "wallets": 479,
            "before": {"distance": 0.11288, "held_days": 11.697, "premium": 56.67},
            "after": {"distance": 0.07613, "held_days": 8.223, "premium": 62.71},
            "moved_out": {"distance": 0.21086},
        },
    }


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        (1_268_095.4176, "$1.27M"),
        (-16_250_559.9, "−$16.25M"),
        (14_505.65, "$14,506"),
        (-2877.09, "−$2,877"),
        (47.39, "$47"),
        (0.0, "$0"),
    ],
)
def test_money_keeps_the_precision_the_claim_is_made_at(value, shown):
    assert article_charts.money(value) == shown


def test_money_uses_a_real_minus_not_a_hyphen():

    assert article_charts.money(-1e6).startswith("−")
    assert "-" not in article_charts.money(-1e6)


def test_two_amounts_in_one_caption_do_not_open_maths():

    caption = f"Median {article_charts.money(47.39)}, mean {article_charts.money(-2877.09)}."
    assert caption.count("$") == 2
    assert article_charts._t(caption).count(r"\$") == 2


def test_escaping_leaves_everything_else_alone():
    assert article_charts._t("47% of calls, 10% away") == "47% of calls, 10% away"


def test_the_registry_covers_every_public_figure():

    drawn = set(article_charts.FIGURES)
    defined = {
        n
        for n in dir(article_charts)
        if not n.startswith("_")
        and callable(getattr(article_charts, n))
        and getattr(article_charts, n).__module__ == article_charts.__name__
        and n not in {"money", "waterfall"}
    }

    assert (
        defined
        - {
            "the_trade",
            "outcomes",
            "premium_against_move",
            "strike_distance",
            "payout_severity",
            "by_asset",
            "wallet_spread",
            "loss_concentration",
            "persistence",
            "after_assignment",
            "by_price_move",
            "the_bridge",
            "weekly_strikes",
        }
        == set()
    )

    assert len(drawn) == 16

    assert "persistence" not in drawn


def test_every_named_figure_draws(tmp_path):
    rows, figs = book(), figures()
    for name, draw in article_charts.FIGURES.items():
        path = draw(rows, figs, tmp_path / f"{name}.png")
        assert path.exists(), name
        assert path.stat().st_size > 5_000, f"{name} drew almost nothing"
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{name} is not a PNG"


def test_the_bridge_closes():

    from option_intel.articles import home

    canonical = home("rysk-year") / "figures.json"
    if not canonical.exists():
        pytest.skip("rysk-year has no figures.json yet")
    figs = json.loads(canonical.read_text())
    t, v = figs["call_totals"], figs["versus_holding"]
    assert t["asset"] + v["unexercised"] + v["exercised"] == pytest.approx(t["net"])
    assert v["unexercised"] + v["exercised"] == pytest.approx(v["gap"])


def test_calls_excludes_cash_secured_puts():

    assert len(article_charts._calls(book())) == 5


def test_a_figure_reads_its_annotations_from_the_canonical_values(tmp_path):

    rows = book()

    plain = article_charts.the_trade(figures(), tmp_path / "a.png").read_bytes()
    moved = figures()
    moved["call_totals"]["forwent"] = 0.11
    other = article_charts.the_trade(moved, tmp_path / "b.png").read_bytes()
    assert plain != other

    same = article_charts.the_trade(figures(), tmp_path / "c.png").read_bytes()
    assert plain == same, "the same figures must draw the same figure"

    flat = article_charts.waterfall(figures(), tmp_path / "d.png").read_bytes()
    lifted = figures()
    lifted["call_totals"]["premium"] = 999.0
    assert flat != article_charts.waterfall(lifted, tmp_path / "e.png").read_bytes()

    assert rows


def test_published_figures_json_carries_every_key_the_charts_read(tmp_path):

    from option_intel.articles import home

    canonical = home("rysk-year") / "figures.json"
    if not canonical.exists():
        pytest.skip("rysk-year has no figures.json yet; run check_article.py")
    figs = json.loads(canonical.read_text())
    for name, draw in article_charts.FIGURES.items():
        draw(book(), figs, tmp_path / f"{name}.png")


def _canonical() -> dict:
    from option_intel.articles import home

    path = home("rysk-year") / "figures.json"
    if not path.exists():
        pytest.skip("rysk-year has no figures.json yet")
    return json.loads(path.read_text())


def test_the_waterfall_closes():

    t = _canonical()["call_totals"]
    assert t["asset"] + t["premium"] + t["paid"] == pytest.approx(t["net"])


def test_the_strike_distance_counts_partition_the_calls():

    figs = _canonical()
    d = figs["call_distance"]
    assert d["written_through"] + d["exercised"] + d["untouched"] == figs["calls"]
