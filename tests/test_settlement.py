from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from option_intel import settlement
from option_intel.model import Instrument, LiquidityRole, OptionType, Venue, VolPrint
from option_intel.spot import StaticSpot

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def write(
    *,
    option_type: OptionType = OptionType.CALL,
    strike: float = 100.0,
    spot: float = 100.0,
    contracts: float = 10.0,
    premium: float = 50.0,
    expires_in: int = -7,
    writer: str = "0xwriter",
    underlying: str = "HYPE",
    written_days_ago: int = 14,
) -> VolPrint:
    ts = NOW - timedelta(days=written_days_ago)
    return VolPrint(
        venue=Venue.RYSK,
        chain="hyperevm",
        ts=ts,
        instrument=Instrument(underlying, (NOW + timedelta(days=expires_in)).date(), strike, option_type),
        quantity=contracts,
        premium_per_unit=premium / contracts,
        spot=spot,
        forward=spot,
        maker=None,
        taker=writer,
        role_of_record=LiquidityRole.TAKER,
        tx_hash=f"0x{option_type.value}{strike}{expires_in}{writer}",
        iv=0.6,
        extra={"protocol": "rysk", "premium_total": premium, "fee": 5.0},
    )


def settle_one(w: VolPrint, spot_at_expiry: float):
    rows, skipped = settlement.settle([w], StaticSpot({w.instrument.underlying: spot_at_expiry}), asof=NOW)
    return (rows[0] if rows else None), skipped


def test_a_call_that_finished_below_the_strike_keeps_the_whole_premium():
    row, _ = settle_one(write(option_type=OptionType.CALL, strike=100.0), 90.0)
    assert not row.assigned
    assert row.intrinsic_paid == 0.0
    assert row.realised == pytest.approx(50.0)


def test_a_call_through_the_strike_owes_the_move_times_size():
    row, _ = settle_one(write(option_type=OptionType.CALL, strike=100.0, contracts=10.0), 112.0)
    assert row.assigned
    assert row.intrinsic_paid == pytest.approx(120.0)
    assert row.realised == pytest.approx(50.0 - 120.0)


def test_a_put_that_finished_above_the_strike_keeps_the_premium():
    row, _ = settle_one(write(option_type=OptionType.PUT, strike=100.0), 110.0)
    assert not row.assigned
    assert row.realised == pytest.approx(50.0)


def test_a_put_below_the_strike_owes_the_move():
    row, _ = settle_one(write(option_type=OptionType.PUT, strike=100.0, contracts=10.0), 93.0)
    assert row.assigned
    assert row.intrinsic_paid == pytest.approx(70.0)
    assert row.realised == pytest.approx(-20.0)


@pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT])
def test_finishing_exactly_at_the_strike_is_not_an_assignment(option_type):

    row, _ = settle_one(write(option_type=option_type, strike=100.0), 100.0)
    assert not row.assigned
    assert row.intrinsic_paid == 0.0


def test_the_two_sides_cannot_both_be_assigned_at_one_price():
    spot = StaticSpot({"HYPE": 120.0})
    call, _ = settlement.settle([write(option_type=OptionType.CALL, strike=110.0)], spot, asof=NOW)
    put, _ = settlement.settle([write(option_type=OptionType.PUT, strike=110.0)], spot, asof=NOW)
    assert call[0].assigned is not put[0].assigned


def test_an_option_that_has_not_expired_is_not_settled():
    row, skipped = settle_one(write(expires_in=+5), 120.0)
    assert row is None
    assert skipped["still_open"] == 1


def test_a_missing_expiry_price_is_refused_rather_than_guessed():
    rows, skipped = settlement.settle([write()], StaticSpot({}), asof=NOW)
    assert not rows
    assert skipped["no_price_at_expiry:HYPE"] == 1


class FakeOracle:
    def __init__(self, price: float | None):
        self.price = price

    def price_at(self, chain, asset, expiry):
        return self.price


def test_the_protocol_price_wins_over_spot():

    rows, skipped = settlement.settle(
        [write(option_type=OptionType.CALL, strike=100.0, contracts=10.0)],
        StaticSpot({"HYPE": 105.0}),
        asof=NOW,
        oracle=FakeOracle(120.0),
        asset_addresses={"0xhype": "HYPE"},
    )
    assert rows[0].spot_at_expiry == 120.0
    assert rows[0].price_source == "oracle"
    assert rows[0].intrinsic_paid == pytest.approx(200.0)
    assert skipped["priced_by_oracle"] == 1


def test_spot_stands_in_where_the_oracle_published_nothing():
    rows, skipped = settlement.settle(
        [write()],
        StaticSpot({"HYPE": 90.0}),
        asof=NOW,
        oracle=FakeOracle(None),
        asset_addresses={"0xhype": "HYPE"},
    )
    assert rows[0].spot_at_expiry == 90.0
    assert rows[0].price_source == "spot"
    assert skipped["priced_by_spot"] == 1


def test_an_unmapped_asset_falls_back_rather_than_failing():

    rows, _ = settlement.settle(
        [write()],
        StaticSpot({"HYPE": 90.0}),
        asof=NOW,
        oracle=FakeOracle(120.0),
        asset_addresses={},
    )
    assert rows[0].price_source == "spot"


def test_settlement_uses_the_expiry_hour_not_the_write_price():

    row, _ = settle_one(write(spot=100.0, strike=100.0), 130.0)
    assert row.spot_at_write == 100.0
    assert row.spot_at_expiry == 130.0
    assert row.move == pytest.approx(0.30)
    assert row.assigned


def test_expiry_is_stamped_at_the_settlement_hour():
    row, _ = settle_one(write(expires_in=-7), 90.0)
    assert row.expired_at.time() == time(settlement.SETTLEMENT_HOUR)
    assert row.expired_at.tzinfo is UTC


def _book() -> list[settlement.Settled]:
    spot = StaticSpot({"HYPE": 120.0})
    writes = [
        write(option_type=OptionType.CALL, strike=110.0, writer="0xa"),
        write(option_type=OptionType.CALL, strike=130.0, writer="0xa"),
        write(option_type=OptionType.PUT, strike=100.0, writer="0xb"),
        write(option_type=OptionType.PUT, strike=140.0, writer="0xb"),
    ]
    rows, _ = settlement.settle(writes, spot, asof=NOW)
    return rows


def test_overall_reconciles_premium_less_payout():
    rows = _book()
    o = settlement.overall(rows)
    assert o["settled"] == 4
    assert o["realised"] == pytest.approx(o["premium"] - o["intrinsic_paid"])
    assert o["keep_rate"] == pytest.approx(o["realised"] / o["premium"])
    assert o["assigned"] == 2
    assert o["assigned_share"] == pytest.approx(0.5)


def test_groups_partition_the_whole_book():
    rows = _book()
    total = settlement.overall(rows)
    by_side = settlement.by_group(rows, lambda r: r.option_type.value)
    assert sum(g["settled"] for g in by_side) == total["settled"]
    assert sum(g["premium"] for g in by_side) == pytest.approx(total["premium"])
    assert sum(g["realised"] for g in by_side) == pytest.approx(total["realised"])


def test_writers_partition_the_whole_book():
    rows = _book()
    total = settlement.overall(rows)
    writers = settlement.by_writer(rows)
    assert {w["writer"] for w in writers} == {"0xa", "0xb"}
    assert sum(w["realised"] for w in writers) == pytest.approx(total["realised"])


def test_an_empty_book_reports_nothing_rather_than_dividing_by_zero():
    assert settlement.overall([]) == {}
    assert settlement.by_writer([]) == []


def test_survival_separates_those_ever_assigned_from_those_never():
    rows = _book()
    s = settlement.survival_after_first_loss(rows)
    assert s["writers"] == 2

    assert s["ever_assigned"] == 2
    assert s["never_assigned"] == 0


def test_the_moneyness_ladder_is_ordered_from_near_to_far():
    rows = _book()
    ladder = settlement.moneyness_ladder(rows)
    assert ladder
    assert sum(r["settled"] for r in ladder) == len(rows)


def test_a_row_round_trips_to_a_flat_record():
    row = _book()[0]
    record = row.to_row()
    assert record["realised"] == pytest.approx(row.realised)
    assert record["assigned"] is row.assigned
    assert record["option_type"] in ("C", "P")
    assert set(record) >= {"writer", "underlying", "strike", "premium", "intrinsic_paid"}
