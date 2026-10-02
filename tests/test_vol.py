import math

import pytest

from option_intel.model import OptionType
from option_intel.vol import (
    ABOVE_UPPER_BOUND,
    BELOW_INTRINSIC,
    NO_TIME,
    NO_TIME_VALUE,
    black76_delta,
    black76_price,
    black76_vega,
    forward_from_spot,
    intrinsic,
    solve_iv,
)

CALL = OptionType.CALL
PUT = OptionType.PUT


@pytest.mark.parametrize("vol", [0.05, 0.35, 0.8, 2.5])
@pytest.mark.parametrize("strike", [3000.0, 4000.0, 5200.0])
@pytest.mark.parametrize("t", [1 / 365, 7 / 365, 0.5])
@pytest.mark.parametrize("otype", [CALL, PUT])
def test_iv_round_trips(vol, strike, t, otype):
    forward = 4000.0
    price = black76_price(forward, strike, vol, t, otype)
    if price <= 0:
        pytest.skip("price underflows at this parameterisation")

    time_value = price - intrinsic(forward, strike, otype)
    if time_value <= 1e-8 * forward:
        pytest.skip("time value is not representable at this price level")
    solved, reason = solve_iv(price, forward, strike, t, otype)
    assert reason is None
    assert solved == pytest.approx(vol, rel=1e-4, abs=1e-6)


@pytest.mark.parametrize(
    ("strike", "otype", "t", "vol"),
    [(3000.0, CALL, 1 / 365, 0.05), (5200.0, PUT, 7 / 365, 0.05)],
)
def test_intrinsic_only_premium_reports_failure_not_a_number(strike, otype, t, vol):
    forward = 4000.0
    price = black76_price(forward, strike, vol, t, otype)
    assert price == pytest.approx(intrinsic(forward, strike, otype), abs=1e-9)
    solved, reason = solve_iv(price, forward, strike, t, otype)
    assert solved is None
    assert reason is NO_TIME_VALUE


def test_itm_solves_via_parity_when_time_value_survives():

    forward, strike, vol, t = 4000.0, 3000.0, 0.9, 0.5
    call = black76_price(forward, strike, vol, t, CALL)
    solved, reason = solve_iv(call, forward, strike, t, CALL)
    assert reason is None
    assert solved == pytest.approx(vol, rel=1e-6)


def test_put_call_parity():
    forward, strike, vol, t = 4000.0, 4200.0, 0.6, 0.25
    call = black76_price(forward, strike, vol, t, CALL)
    put = black76_price(forward, strike, vol, t, PUT)
    assert call - put == pytest.approx(forward - strike, abs=1e-9)


def test_price_is_monotone_in_vol():
    forward, strike, t = 4000.0, 4500.0, 0.2
    prices = [black76_price(forward, strike, v, t, CALL) for v in (0.1, 0.3, 0.9, 1.8)]
    assert prices == sorted(prices)
    assert len(set(prices)) == len(prices)


def test_below_intrinsic_is_rejected_not_defaulted():
    forward, strike, t = 4000.0, 3000.0, 0.25
    iv, reason = solve_iv(500.0, forward, strike, t, CALL)
    assert iv is None
    assert reason is BELOW_INTRINSIC


def test_absurd_price_is_rejected():
    iv, reason = solve_iv(3999.0, 4000.0, 4000.0, 0.25, CALL)
    assert iv is None
    assert reason is ABOVE_UPPER_BOUND


def test_expired_option_has_no_iv():
    iv, reason = solve_iv(10.0, 4000.0, 4000.0, -0.01, CALL)
    assert iv is None
    assert reason is NO_TIME


def test_vega_positive_and_peaks_atm():
    t = 0.25
    atm = black76_vega(4000.0, 4000.0, 0.6, t)
    wing = black76_vega(4000.0, 7000.0, 0.6, t)
    assert atm > wing > 0


def test_delta_bounds():
    t, vol = 0.25, 0.6
    assert 0.0 < black76_delta(4000.0, 4000.0, vol, t, CALL) < 1.0
    assert -1.0 < black76_delta(4000.0, 4000.0, vol, t, PUT) < 0.0


def test_forward_from_spot():
    assert forward_from_spot(100.0, 0.0, 1.0) == pytest.approx(100.0)
    assert forward_from_spot(100.0, 0.1, 1.0) == pytest.approx(100.0 * math.exp(0.1))


def test_premium_exactly_at_parity_is_a_failure():
    forward, strike, t = 4000.0, 1000.0, 0.25
    iv, reason = solve_iv(forward - strike, forward, strike, t, CALL)
    assert iv is None
    assert reason is NO_TIME_VALUE
