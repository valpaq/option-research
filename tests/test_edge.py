import math
from datetime import UTC, datetime, timedelta

import pytest

from option_intel import edge
from option_intel.model import OptionType
from option_intel.settlement import Settled
from option_intel.vol import black76_price

HOUR = 3600
START = datetime(2026, 1, 1, tzinfo=UTC)


def hours(start: datetime) -> int:
    return int(start.timestamp()) // HOUR


def flat_index(n: int, price: float = 100.0, start: datetime = START) -> dict[int, float]:

    return {hours(start) + i: price for i in range(n)}


def walk_index(steps: int, step: float, start: datetime = START) -> dict[int, float]:

    assert steps % 2 == 0, "an odd number of steps has a non-zero mean"
    out, price = {}, 100.0
    for i in range(steps + 1):
        out[hours(start) + i] = price
        price *= math.exp(step if i % 2 == 0 else -step)
    return out


def a_row(**kw) -> Settled:
    base = dict(
        protocol="rysk",
        chain="hyperevm",
        tx_hash="0x0",
        writer="0xw",
        underlying="HYPE",
        option_type=OptionType.CALL,
        strike=110.0,
        written_at=START,
        expired_at=START + timedelta(days=7),
        contracts=10.0,
        premium=16.0,
        fee=2.0,
        spot_at_write=100.0,
        spot_at_expiry=105.0,
    )
    base.update(kw)
    return Settled(**base)


def test_a_span_running_past_the_data_is_refused_not_truncated():

    index = flat_index(24 * 3)
    inside = (START, START + timedelta(days=2))
    beyond = (START, START + timedelta(days=10))
    assert edge.covers(index, *inside)
    assert not edge.covers(index, *beyond)


def test_a_span_starting_before_the_data_is_refused_too():
    index = flat_index(24 * 3, start=START)
    assert not edge.covers(index, START - timedelta(days=1), START + timedelta(days=1))


def test_an_empty_series_covers_nothing():
    assert not edge.covers({}, START, START + timedelta(hours=1))


def test_prints_counts_the_uncovered_rather_than_dropping_them_silently():
    rows = [a_row(), a_row(expired_at=START + timedelta(days=60))]
    priced, why = edge.prints(rows, {"HYPE": flat_index(24 * 10)})
    assert len(priced) + sum(why.values()) == len(rows)
    assert any("do not span" in k for k in why)


def test_a_flat_series_has_no_volatility():
    assert edge.realised(list(flat_index(48).values())) == 0.0


def test_realised_recovers_a_known_step_size():

    step = 0.01
    got = edge.realised(list(walk_index(200, step).values()))
    assert got == pytest.approx(step * math.sqrt(edge.HOURS_A_YEAR), rel=1e-9)


def test_too_few_observations_returns_none_rather_than_a_number():
    assert edge.realised(list(flat_index(edge.MIN_OBSERVATIONS - 1).values())) is None
    assert edge.realised(list(flat_index(edge.MIN_OBSERVATIONS).values())) is not None


def test_annualisation_follows_the_sampling_interval():

    series = list(walk_index(200, 0.01).values())
    fine = edge.realised(series)
    coarse = edge.realised(series, step=4)
    assert fine / coarse == pytest.approx(2.0, rel=1e-9)


def test_closes_skips_a_gap_rather_than_filling_it():
    index = flat_index(24)
    missing = hours(START) + 5
    del index[missing]
    got = edge.closes(index, START, START + timedelta(hours=23))
    assert len(got) == 22


def test_closes_steps_by_the_interval():
    index = flat_index(48)
    every_hour = edge.closes(index, START, START + timedelta(hours=47))
    every_fourth = edge.closes(index, START, START + timedelta(hours=47), step=4)
    assert len(every_fourth) == math.ceil(len(every_hour) / 4)


def test_implied_round_trips_a_priced_option():
    vol, t = 0.62, 7 / 365
    per_contract = black76_price(100.0, 110.0, vol, t, OptionType.CALL)
    row = a_row(premium=per_contract * 10, contracts=10.0, fee=0.0)
    got, why = edge.implied(row)
    assert why is None
    assert got == pytest.approx(vol, rel=1e-4)


def test_the_gross_price_implies_more_volatility_than_the_net_one():

    row = a_row()
    net, _ = edge.implied(row)
    gross, _ = edge.implied(row, gross=True)
    assert gross > net


def test_a_refused_solve_reports_a_reason_and_no_number():

    row = a_row(strike=50.0, spot_at_write=100.0, premium=1.0, fee=0.0)
    got, why = edge.implied(row)
    assert got is None
    assert why


def test_no_contracts_is_refused_rather_than_dividing_by_zero():
    got, why = edge.implied(a_row(contracts=0.0))
    assert got is None and why


def test_the_gap_is_implied_minus_realised_from_the_writers_side():
    p = edge.Print(a_row(), iv=0.60, rv=0.75, prior=0.70, v=100.0, hours=168)
    assert p.gap == pytest.approx(-0.15)
    assert p.gap_prior == pytest.approx(-0.10)


def test_a_missing_prior_window_stays_missing():
    p = edge.Print(a_row(), iv=0.60, rv=0.75, prior=None, v=100.0, hours=168)
    assert p.gap_prior is None


def test_vega_weighting_follows_the_bigger_position():

    small = edge.Print(a_row(), iv=0.70, rv=0.60, prior=None, v=1.0, hours=168)
    large = edge.Print(a_row(), iv=0.60, rv=0.70, prior=None, v=100.0, hours=168)
    out = edge._summary([small, large])
    assert out["gap_mean"] == pytest.approx(0.0, abs=1e-12)
    assert out["gap_vega_weighted"] < -0.09


def test_underpaid_share_counts_only_negative_gaps():
    group = [
        edge.Print(a_row(), iv=iv, rv=0.70, prior=None, v=1.0, hours=168) for iv in (0.60, 0.65, 0.80, 0.90)
    ]
    assert edge._summary(group)["underpaid_share"] == pytest.approx(0.5)


def test_tenor_buckets_cover_the_line_without_a_gap():
    for days in (0.0, 2.9, 3.0, 6.9, 7.0, 13.9, 14.0, 29.9, 30.0, 400.0):
        assert edge.tenor_of(days) in {label for label, _, _ in edge.TENORS}
    assert edge.tenor_of(0.5) == "0-3d"
    assert edge.tenor_of(3.0) == "3-7d"
    assert edge.tenor_of(400.0) == "30d+"


def test_repricing_at_a_higher_volatility_costs_more():
    row = a_row()
    assert edge.repriced(row, 0.9) > edge.repriced(row, 0.5) > 0
