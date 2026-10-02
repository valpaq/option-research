import math
from datetime import UTC, datetime

import pytest

from option_intel.model import OptionType
from option_intel.rates import FlatRate, forward_and_discount
from option_intel.vol import black76_price, intrinsic, solve_iv

CALL = OptionType.CALL
PUT = OptionType.PUT


def test_flat_rate_is_constant():
    r = FlatRate(0.045)
    assert r.rate_at(datetime(2026, 1, 1, tzinfo=UTC)) == 0.045
    assert r.rate_at(datetime(2026, 8, 1, tzinfo=UTC)) == 0.045


def test_forward_and_discount_are_consistent():
    spot, rate, t = 4000.0, 0.046, 0.25
    forward, discount = forward_and_discount(spot, rate, t)
    assert forward == pytest.approx(spot * math.exp(rate * t))
    assert discount == pytest.approx(math.exp(-rate * t))

    assert forward * discount == pytest.approx(spot)


def test_zero_rate_is_the_identity():
    forward, discount = forward_and_discount(500.0, 0.0, 1.0)
    assert forward == pytest.approx(500.0)
    assert discount == pytest.approx(1.0)


def test_expired_option_has_no_carry():
    forward, discount = forward_and_discount(100.0, 0.05, -1.0)
    assert forward == pytest.approx(100.0)
    assert discount == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("option_type", "strike", "direction"),
    [
        (CALL, 3000.0, "up"),
        (PUT, 5000.0, "down"),
    ],
)
def test_rate_moves_call_and_put_floors_in_opposite_directions(option_type, strike, direction):
    spot, t = 4000.0, 0.5
    zero_f, zero_df = forward_and_discount(spot, 0.0, t)
    carry_f, carry_df = forward_and_discount(spot, 0.046, t)

    zero_floor = zero_df * intrinsic(zero_f, strike, option_type)
    carry_floor = carry_df * intrinsic(carry_f, strike, option_type)

    if direction == "up":
        assert carry_floor > zero_floor
    else:
        assert carry_floor < zero_floor


def test_discounted_round_trip_recovers_volatility():

    spot, rate, t, strike, vol = 4000.0, 0.046, 0.25, 4200.0, 0.55
    forward, discount = forward_and_discount(spot, rate, t)
    price = black76_price(forward, strike, vol, t, CALL, discount)
    solved, reason = solve_iv(price, forward, strike, t, CALL, discount)
    assert reason is None
    assert solved == pytest.approx(vol, rel=1e-5)


def test_ignoring_the_discount_factor_biases_the_result():

    spot, rate, t, strike, vol = 4000.0, 0.046, 1.0, 4000.0, 0.55
    forward, discount = forward_and_discount(spot, rate, t)
    price = black76_price(forward, strike, vol, t, CALL, discount)
    wrong, reason = solve_iv(price, forward, strike, t, CALL, discount_factor=1.0)
    assert reason is None
    assert wrong is not None
    assert abs(wrong - vol) > 1e-3, "dropping the discount factor should shift the answer"
