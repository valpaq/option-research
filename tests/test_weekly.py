from datetime import UTC, datetime, timedelta

from option_intel import weekly
from option_intel.model import OptionType
from option_intel.settlement import Settled


THURSDAY = datetime(2026, 1, 8, 12, tzinfo=UTC)


def a_row(**kw) -> Settled:
    base = dict(
        protocol="rysk",
        chain="hyperevm",
        tx_hash="0x0",
        writer="0xw",
        underlying="HYPE",
        option_type=OptionType.CALL,
        strike=110.0,
        written_at=THURSDAY,
        expired_at=THURSDAY + timedelta(days=7),
        contracts=10.0,
        premium=16.0,
        fee=2.0,
        spot_at_write=100.0,
        spot_at_expiry=105.0,
    )
    base.update(kw)
    return Settled(**base)


def test_cells_are_grouped_by_the_date_they_settled():

    same = [a_row(written_at=THURSDAY + timedelta(days=n)) for n in range(4)]
    got = weekly.series(same * 3, assets=("HYPE",), min_cell=10)

    assert len(got["by_week"]["HYPE"]["call"]) == 1
    assert got["by_week"]["HYPE"]["call"][0]["expiry"] == (THURSDAY + timedelta(days=7)).date().isoformat()


def test_two_expiries_are_two_cells():
    early = [a_row() for _ in range(12)]
    late = [a_row(expired_at=THURSDAY + timedelta(days=14)) for _ in range(12)]
    got = weekly.series(early + late, assets=("HYPE",), min_cell=10)
    assert len(got["by_week"]["HYPE"]["call"]) == 2
    assert got["expiries"] == 2


def test_the_settlement_price_is_one_print_per_expiry():

    rows = [a_row(contracts=1.0, spot_at_expiry=105.0) for _ in range(3)]
    rows.append(a_row(contracts=900.0, spot_at_expiry=107.0))
    assert weekly.settlement_price(rows) == 107.0
    assert weekly.settlement_price([]) is None


def test_price_is_volume_weighted_not_averaged():

    big = a_row(contracts=90.0, premium=90.0)
    small = a_row(contracts=10.0, premium=50.0)
    got = weekly.cell([big, small])
    assert got["contracts"] == 100.0
    assert got["price_per_contract"] == 1.40
    assert got["notional"] == 10_000.0


def test_a_refused_solve_is_counted_not_filled():

    got = weekly.cell([a_row(), a_row(premium=5_000.0), a_row(premium=0.0)])
    assert got["options"] == 3
    assert got["solved"] == 1
    assert got["refused"] == 2
    assert got["premium"] == 16.0 + 5_000.0
    assert got["notional"] == 3_000.0


def test_thin_cells_are_dropped_and_counted():

    thin = [a_row() for _ in range(3)]
    fat = [a_row(underlying="BTC", strike=110.0) for _ in range(12)]
    got = weekly.series(thin + fat, assets=("HYPE", "BTC"), min_cell=10)
    assert got["thin_cells"] == 1
    assert got["thin_options"] == 3
    assert got["by_week"]["HYPE"]["call"] == []
    assert len(got["by_week"]["BTC"]["call"]) == 1


def test_assets_outside_the_majors_are_counted_not_silently_dropped():
    got = weekly.series([a_row(underlying="PUMP") for _ in range(20)], assets=("HYPE",), min_cell=1)
    assert got["excluded_options"] == 20
    assert got["cells"] == 0


def test_skew_sign_is_put_minus_call():

    calls = [a_row(premium=16.0) for _ in range(10)]
    puts = [a_row(option_type=OptionType.PUT, strike=90.0, premium=40.0) for _ in range(10)]
    got = weekly.measure(calls + puts, assets=("HYPE",), min_cell=10)
    spread = got["spread"]["HYPE"]
    assert spread["weeks_compared"] == 1
    assert spread["put_over_call_median"] > 0
    assert spread["weeks_puts_dearer"] == 1


def test_the_series_is_drawn_not_quoted():

    from option_intel import prose

    got = weekly.series([a_row() for _ in range(12)], assets=("HYPE",), min_cell=10)
    assert "by_week" in got
    assert not any(k.startswith("by_week") for k in prose.flatten(got))

    assert any("cells" in k for k in prose.flatten(got))


def test_the_expiry_calendar_is_measured_here():

    weekly_rows = [a_row(expired_at=THURSDAY + timedelta(days=7 * n)) for n in range(1, 5)]
    got = weekly.cadence(weekly_rows, ("HYPE",))
    assert got["expiries"] == 4
    assert got["gaps"] == 3
    assert got["weekly_gaps"] == 3
    assert got["median_gap_days"] == 7


def test_a_skipped_week_shows_in_the_calendar():
    rows = [a_row(expired_at=THURSDAY + timedelta(days=d)) for d in (7, 14, 35)]
    got = weekly.cadence(rows, ("HYPE",))
    assert got["weekly_gaps"] == 1
    assert got["longest_gap_days"] == 21


def test_the_assignment_threshold_is_a_named_parameter():

    assert 0.0 < weekly.MOSTLY_ASSIGNED < 1.0
    got = weekly.series([a_row() for _ in range(12)], assets=("HYPE",), min_cell=10)
    assert got["mostly_assigned"] == weekly.MOSTLY_ASSIGNED


def test_the_ringed_example_is_computed_not_lifted():

    def puts(contracts: float, offset: int) -> list:

        return [
            a_row(
                contracts=contracts,
                strike=90.0,
                spot_at_expiry=80.0,
                option_type=OptionType.PUT,
                written_at=THURSDAY + timedelta(days=offset),
                expired_at=THURSDAY + timedelta(days=offset + 7),
            )
            for _ in range(12)
        ]

    def calls(offset: int) -> list:

        return [
            a_row(
                contracts=10.0,
                strike=200.0,
                spot_at_expiry=80.0,
                written_at=THURSDAY + timedelta(days=offset),
                expired_at=THURSDAY + timedelta(days=offset + 7),
            )
            for _ in range(12)
        ]

    rows = puts(1_000.0, 0) + calls(0) + puts(1.0, 14) + calls(14)
    got = weekly.measure(rows, assets=("HYPE",), min_cell=10)
    best = got["spread"]["HYPE"]["busiest_assigned"]
    assert best is not None
    assert best["side"] == "put"
    assert best["contracts"] == 12_000.0
    assert best["assigned"] == 1.0
    assert got["spread"]["HYPE"]["ringed_expiries"] >= 1
    assert got["spread"]["HYPE"]["puts_breached"] == 2
