from __future__ import annotations

import math

from option_intel.model import OptionType

VOL_LOWER = 1e-4
VOL_UPPER = 10.0


_VOL_TOL = 1e-10


_INTRINSIC_REL_TOL = 1e-9
_MAX_ITER = 200


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(forward: float, strike: float, vol: float, t: float) -> tuple[float, float]:
    v = vol * math.sqrt(t)
    d1 = (math.log(forward / strike) + 0.5 * v * v) / v
    return d1, d1 - v


def black76_price(
    forward: float,
    strike: float,
    vol: float,
    t: float,
    option_type: OptionType,
    discount_factor: float = 1.0,
) -> float:

    if forward <= 0 or strike <= 0:
        raise ValueError("forward and strike must be positive")
    if t <= 0 or vol <= 0:
        return discount_factor * intrinsic(forward, strike, option_type)
    d1, d2 = _d1_d2(forward, strike, vol, t)
    if option_type is OptionType.CALL:
        return discount_factor * (forward * norm_cdf(d1) - strike * norm_cdf(d2))
    return discount_factor * (strike * norm_cdf(-d2) - forward * norm_cdf(-d1))


def intrinsic(forward: float, strike: float, option_type: OptionType) -> float:
    if option_type is OptionType.CALL:
        return max(forward - strike, 0.0)
    return max(strike - forward, 0.0)


def black76_vega(
    forward: float,
    strike: float,
    vol: float,
    t: float,
    discount_factor: float = 1.0,
) -> float:

    if t <= 0 or vol <= 0:
        return 0.0
    d1, _ = _d1_d2(forward, strike, vol, t)
    return discount_factor * forward * norm_pdf(d1) * math.sqrt(t)


def black76_gamma(
    forward: float,
    strike: float,
    vol: float,
    t: float,
    discount_factor: float = 1.0,
) -> float:

    if t <= 0 or vol <= 0 or forward <= 0:
        return 0.0
    d1, _ = _d1_d2(forward, strike, vol, t)
    return discount_factor * norm_pdf(d1) / (forward * vol * math.sqrt(t))


def dollar_gamma(
    forward: float,
    strike: float,
    vol: float,
    t: float,
    quantity: float,
    *,
    move: float = 0.01,
    discount_factor: float = 1.0,
) -> float:

    gamma = black76_gamma(forward, strike, vol, t, discount_factor)
    return gamma * forward * forward * move * quantity


def black76_delta(
    forward: float,
    strike: float,
    vol: float,
    t: float,
    option_type: OptionType,
    discount_factor: float = 1.0,
) -> float:
    if t <= 0 or vol <= 0:
        itm = forward > strike if option_type is OptionType.CALL else forward < strike
        base = 1.0 if itm else 0.0
        return discount_factor * (base if option_type is OptionType.CALL else -base)
    d1, _ = _d1_d2(forward, strike, vol, t)
    if option_type is OptionType.CALL:
        return discount_factor * norm_cdf(d1)
    return discount_factor * (norm_cdf(d1) - 1.0)


class SolveFailure(str):
    pass


NO_TIME = SolveFailure("no_time_to_expiry")
NON_POSITIVE_PRICE = SolveFailure("non_positive_price")
BELOW_INTRINSIC = SolveFailure("below_intrinsic")
NO_TIME_VALUE = SolveFailure("no_recoverable_time_value")
ABOVE_UPPER_BOUND = SolveFailure("above_vol_upper_bound")
NOT_CONVERGED = SolveFailure("not_converged")


def solve_iv(
    price: float,
    forward: float,
    strike: float,
    t: float,
    option_type: OptionType,
    discount_factor: float = 1.0,
) -> tuple[float | None, SolveFailure | None]:

    if t <= 0:
        return None, NO_TIME
    if price <= 0:
        return None, NON_POSITIVE_PRICE
    if forward <= 0 or strike <= 0:
        return None, NON_POSITIVE_PRICE

    floor = discount_factor * intrinsic(forward, strike, option_type)
    if price < floor - _INTRINSIC_REL_TOL * forward:
        return None, BELOW_INTRINSIC

    if floor > 0.0:
        price = price - discount_factor * (forward - strike) * (1 if option_type is OptionType.CALL else -1)
        option_type = OptionType.PUT if option_type is OptionType.CALL else OptionType.CALL
        if price <= _INTRINSIC_REL_TOL * forward:
            return None, NO_TIME_VALUE

    hi_price = black76_price(forward, strike, VOL_UPPER, t, option_type, discount_factor)
    if price > hi_price:
        return None, ABOVE_UPPER_BOUND

    lo, hi = VOL_LOWER, VOL_UPPER
    lo_price = black76_price(forward, strike, lo, t, option_type, discount_factor)
    if price <= lo_price:
        return lo, None

    for _ in range(_MAX_ITER):
        mid = 0.5 * (lo + hi)
        if (hi - lo) < _VOL_TOL:
            return mid, None
        mid_price = black76_price(forward, strike, mid, t, option_type, discount_factor)
        if mid_price < price:
            lo = mid
        else:
            hi = mid
    return None, NOT_CONVERGED


def forward_from_spot(spot: float, rate: float, t: float) -> float:

    return spot * math.exp(rate * t)
