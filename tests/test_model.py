from datetime import UTC, date, datetime

import pytest

from option_intel.model import (
    Instrument,
    OptionType,
    Venue,
    VolPrint,
    parse_deribit_instrument,
    parse_derive_instrument,
    parse_otoken_symbol,
    parse_rysk_series,
    resolve_underlying,
    split_rysk_pair,
)


def test_parse_derive_option():
    inst = parse_derive_instrument("ETH-20260828-4000-C")
    assert inst == Instrument("ETH", date(2026, 8, 28), 4000.0, OptionType.CALL)
    assert inst.key == "ETH-20260828-4000-C"


def test_parse_derive_rejects_perp():
    assert parse_derive_instrument("ETH-PERP") is None
    assert parse_derive_instrument("USDC") is None


def test_parse_deribit_option():
    inst = parse_deribit_instrument("BTC-26DEC26-120000-P")
    assert inst == Instrument("BTC", date(2026, 12, 26), 120000.0, OptionType.PUT)


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("oWHYPEUSD₮0/kHYPE-20FEB26-35C", Instrument("HYPE", date(2026, 2, 20), 35.0, OptionType.CALL)),
        ("oWHYPEUSDH/USDH-27MAR26-25P", Instrument("HYPE", date(2026, 3, 27), 25.0, OptionType.PUT)),
        ("oWHYPEUSDC/USDC-17JUL26-64P", Instrument("HYPE", date(2026, 7, 17), 64.0, OptionType.PUT)),
    ],
)
def test_parse_rysk_series_from_real_symbols(symbol, expected):
    assert parse_rysk_series(symbol) == expected


def test_underlying_comes_from_the_pair_not_the_collateral():

    inst = parse_rysk_series("oWHYPEUSDH/USDH-27MAR26-25P")
    assert inst is not None and inst.underlying == "HYPE"


def test_resolve_underlying_leaves_stablecoins_intact():

    assert resolve_underlying("USDH") == "USDH"
    assert resolve_underlying("USDC") == "USDC"
    assert resolve_underlying("WHYPE") == "HYPE"
    assert resolve_underlying("kHYPE") == "HYPE"


@pytest.mark.parametrize(
    ("pair", "expected"),
    [
        ("WHYPEUSDC", ("WHYPE", "USDC")),
        ("WHYPEUSDH", ("WHYPE", "USDH")),
        ("WHYPEUSD₮0", ("WHYPE", "USD₮0")),
    ],
)
def test_split_rysk_pair(pair, expected):
    assert split_rysk_pair(pair) == expected


def test_derive_and_rysk_symbols_reconcile_to_one_key():
    a = parse_derive_instrument("HYPE-20260220-35-C")
    b = parse_rysk_series("oWHYPEUSD₮0/kHYPE-20FEB26-35C")
    assert a is not None and a == b, "the same series must reconcile across venues"


def test_time_to_expiry_is_positive_before_settlement():
    inst = Instrument("ETH", date(2026, 8, 28), 4000.0, OptionType.CALL)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    assert inst.time_to_expiry(at) == pytest.approx(7 / 365, rel=1e-9)


def test_time_to_expiry_requires_tz():
    inst = Instrument("ETH", date(2026, 8, 28), 4000.0, OptionType.CALL)
    with pytest.raises(ValueError):
        inst.time_to_expiry(datetime(2026, 8, 21, 8))


def _print(**kw):
    base = dict(
        venue=Venue.DERIVE,
        chain="derive-957",
        ts=datetime(2026, 8, 21, 12, tzinfo=UTC),
        instrument=Instrument("ETH", date(2026, 8, 28), 4000.0, OptionType.CALL),
        quantity=2.0,
        premium_per_unit=50.0,
        spot=4000.0,
        trade_id="t1",
    )
    base.update(kw)
    return VolPrint(**base)


def test_volprint_requires_provenance():
    with pytest.raises(ValueError, match="source reference"):
        _print(trade_id=None)


def test_volprint_rejects_naive_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        _print(ts=datetime(2026, 8, 21, 12))


def test_volprint_rejects_nonpositive_quantity():
    with pytest.raises(ValueError, match="quantity"):
        _print(quantity=0.0)


def test_volprint_derived_amounts():
    p = _print()
    assert p.notional == 8000.0
    assert p.premium_total == 100.0
    row = p.to_row()
    assert row["instrument_key"] == "ETH-20260828-4000-C"
    assert row["venue"] == "derive"
    assert row["underlying"] == "ETH"


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("oUBTC/USDC/USDC-18SEP26-71500P", Instrument("BTC", date(2026, 9, 18), 71500.0, OptionType.PUT)),
        ("oWHYPE/USDC/WHYPE-23AUG26-83C", Instrument("HYPE", date(2026, 8, 23), 83.0, OptionType.CALL)),
        ("oUETHUSDC/UETH-24JUL26-1875C", Instrument("ETH", date(2026, 7, 24), 1875.0, OptionType.CALL)),
    ],
)
def test_parse_otoken_symbol_across_protocols(symbol, expected):
    assert parse_otoken_symbol(symbol) == expected


def test_collateral_never_becomes_the_underlying():

    call = parse_otoken_symbol("oWHYPE/USDC/WHYPE-23AUG26-83C")
    put = parse_otoken_symbol("oWHYPE/USDC/USDC-25SEP26-82P")
    assert call is not None and put is not None
    assert call.underlying == put.underlying == "HYPE"


def test_parse_otoken_rejects_malformed():
    assert parse_otoken_symbol("not-an-option") is None
    assert parse_otoken_symbol("oWHYPE-23AUG26-83C") is None
