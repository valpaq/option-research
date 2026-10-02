from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from option_intel import flow
from option_intel.model import OptionType
from option_intel.settlement import Settled

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def settled(
    *,
    option_type: OptionType = OptionType.CALL,
    strike: float = 110.0,
    contracts: float = 1.0,
    premium: float = 50.0,
    spot_at_write: float = 100.0,
    spot_at_expiry: float = 100.0,
    held_days: int = 7,
    written_days_ago: int = 20,
    underlying: str = "HYPE",
    tag: str = "",
) -> Settled:
    written = NOW - timedelta(days=written_days_ago)
    return Settled(
        protocol="rysk",
        chain="hyperevm",
        tx_hash=f"0x{underlying}{written_days_ago}{premium}{tag}",
        writer="0xw",
        underlying=underlying,
        option_type=option_type,
        strike=strike,
        written_at=written,
        expired_at=written + timedelta(days=held_days),
        contracts=contracts,
        premium=premium,
        fee=0.0,
        spot_at_write=spot_at_write,
        spot_at_expiry=spot_at_expiry,
    )


def _cell(
    *, days_ago: int, put_share_by_count: float, spot_at_expiry: float, n: int = 10, tag: str = ""
) -> list[Settled]:
    puts = round(n * put_share_by_count)
    return [
        settled(
            option_type=OptionType.PUT if i < puts else OptionType.CALL,
            written_days_ago=days_ago,
            spot_at_expiry=spot_at_expiry,
            tag=f"{tag}{i}",
        )
        for i in range(n)
    ]


def test_cells_report_the_weighted_put_share_and_the_move():
    rows = _cell(days_ago=40, put_share_by_count=0.3, spot_at_expiry=110.0)
    points = flow.cells(rows, weighting="contracts")
    assert len(points) == 1
    assert points[0]["put_share"] == pytest.approx(0.3)
    assert points[0]["move"] == pytest.approx(0.10)


def test_cells_drop_what_is_too_thin_to_have_a_level():
    rows = _cell(days_ago=40, put_share_by_count=0.5, spot_at_expiry=100.0, n=9)
    assert flow.cells(rows, weighting="contracts", min_cell=10) == []


def test_weightings_can_disagree_and_the_disagreement_survives():

    rows = [
        settled(option_type=OptionType.CALL, contracts=1.0, premium=100.0, written_days_ago=40, tag=f"c{i}")
        for i in range(9)
    ]
    rows.append(
        settled(option_type=OptionType.PUT, contracts=91.0, premium=1.0, written_days_ago=40, tag="p")
    )
    by_contracts = flow.cells(rows, weighting="contracts")[0]["put_share"]
    by_premium = flow.cells(rows, weighting="premium")[0]["put_share"]
    assert by_contracts == pytest.approx(0.91)
    assert by_premium == pytest.approx(1 / 901)


def test_predicts_reads_more_puts_before_rises_as_positive():
    rows = []

    for days_ago, share, out in ((60, 0.9, 120.0), (50, 0.5, 100.0), (40, 0.1, 80.0)):
        rows += _cell(days_ago=days_ago, put_share_by_count=share, spot_at_expiry=out, tag=str(days_ago))
    out = flow.predicts(rows)
    assert out["by_weighting"]["contracts"]["pooled_rho"] == pytest.approx(1.0)
    assert out["by_weighting"]["contracts"]["cells"] == 3
    assert out["by_weighting"]["contracts"]["per_asset"]["HYPE"]["rho"] == pytest.approx(1.0)


def test_predicts_counts_dropped_cells_instead_of_hiding_them():
    rows = _cell(days_ago=60, put_share_by_count=0.5, spot_at_expiry=100.0)
    rows += _cell(days_ago=50, put_share_by_count=0.5, spot_at_expiry=100.0, n=3, tag="thin")
    out = flow.predicts(rows)
    assert out["by_weighting"]["contracts"]["dropped"] == 1


def test_spearman_needs_three_points_and_some_variation():
    assert flow.spearman([1.0, 2.0], [1.0, 2.0]) is None
    assert flow.spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
    assert flow.spearman([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)


def test_permutation_p_is_small_for_a_perfect_relationship():
    rows = []
    for days_ago, share, out in (
        (90, 0.9, 130.0),
        (80, 0.75, 120.0),
        (70, 0.6, 110.0),
        (60, 0.45, 100.0),
        (50, 0.3, 90.0),
        (40, 0.15, 80.0),
    ):
        rows += _cell(days_ago=days_ago, put_share_by_count=share, spot_at_expiry=out, tag=str(days_ago))
    out = flow.predicts(rows, permutation=500)
    assert out["by_weighting"]["contracts"]["pooled_p"] < 0.05


def test_permutation_p_is_deterministic_for_a_fixed_seed():
    xs, ys = [0.1, 0.5, 0.9, 0.3, 0.7], [1.0, 3.0, 2.0, 5.0, 4.0]
    assert flow.permutation_p(xs, ys, draws=300) == flow.permutation_p(xs, ys, draws=300)


def test_null_rhos_share_the_seed_with_the_p_value():
    xs, ys = [0.1, 0.5, 0.9, 0.3, 0.7], [1.0, 3.0, 2.0, 5.0, 4.0]
    observed = flow.spearman(xs, ys)
    null = flow.null_rhos(xs, ys, draws=300)
    share = sum(1 for r in null if abs(r) >= abs(observed)) / 300
    assert share == pytest.approx(flow.permutation_p(xs, ys, draws=300))


def test_delta_weighting_scores_the_same_cells_and_counts_failures():

    rows = [
        settled(option_type=OptionType.CALL, strike=110.0, premium=2.0, written_days_ago=40, tag=f"c{i}")
        for i in range(8)
    ]
    rows += [
        settled(option_type=OptionType.PUT, strike=90.0, premium=2.0, written_days_ago=40, tag=f"p{i}")
        for i in range(4)
    ]

    rows.append(
        settled(
            option_type=OptionType.CALL,
            strike=110.0,
            premium=0.01,
            spot_at_write=200.0,
            written_days_ago=40,
            tag="broken",
        )
    )
    points = flow.cells(rows, weighting="delta")
    assert len(points) == 1
    assert points[0]["solve_failed"] == 1
    assert 0.0 < points[0]["put_share"] < 1.0


def test_delta_units_refuses_rather_than_defaulting():
    row = settled(option_type=OptionType.CALL, premium=0.01, spot_at_write=200.0, tag="deep")
    assert flow.delta_units(row) is None
    fine = settled(
        option_type=OptionType.CALL,
        strike=110.0,
        premium=2.0,
        contracts=10.0,
        spot_at_write=100.0,
        tag="fine",
    )
    units = flow.delta_units(fine)
    assert units is not None and 0.0 < units < 10.0
