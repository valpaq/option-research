from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from option_intel import writers
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
    written_days_ago: int = 20,
    held_days: int = 7,
    underlying: str = "HYPE",
    tag: str = "",
) -> Settled:
    written = NOW - timedelta(days=written_days_ago)
    return Settled(
        protocol="rysk",
        chain="hyperevm",
        tx_hash=f"0x{writer}{strike}{written_days_ago}{premium}{tag}",
        writer=writer,
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


def test_distance_is_positive_out_of_the_money_for_both_sides():
    call = settled(option_type=OptionType.CALL, strike=110.0, spot_at_write=100.0)
    put = settled(option_type=OptionType.PUT, strike=90.0, spot_at_write=100.0)
    assert writers.distance(call) > 0
    assert writers.distance(put) > 0
    assert writers.distance(call) == pytest.approx(math.log(1.1))


def test_distance_is_negative_when_written_already_in_the_money():
    assert writers.distance(settled(strike=90.0, spot_at_write=100.0)) < 0


def test_by_wallet_returns_each_history_in_the_order_written():
    rows = [
        settled(writer="0xa", written_days_ago=5, tag="c"),
        settled(writer="0xa", written_days_ago=30, tag="a"),
        settled(writer="0xa", written_days_ago=20, tag="b"),
        settled(writer="0xb", tag="d"),
    ]
    grouped = writers.by_wallet(rows)
    assert set(grouped) == {"0xa", "0xb"}
    written = [r.written_at for r in grouped["0xa"]]
    assert written == sorted(written)


def test_population_bands_by_how_much_each_wallet_wrote():
    rows = [settled(writer="0xonce")]
    rows += [settled(writer="0xfew", tag=str(i)) for i in range(3)]
    rows += [settled(writer="0xmany", tag=str(i)) for i in range(9)]
    bands = {b["band"]: b for b in writers.population(rows)}
    assert bands["once"]["writers"] == 1
    assert bands["2-5"]["writers"] == 1
    assert bands["6-20"]["writers"] == 1
    assert "21-100" not in bands


def test_population_shares_are_computed_against_the_whole_venue():
    rows = [settled(writer="0xa", premium=100.0)]
    rows += [settled(writer="0xb", premium=100.0, tag=str(i)) for i in range(3)]
    bands = {b["band"]: b for b in writers.population(rows)}
    assert bands["once"]["premium_share"] == pytest.approx(0.25)
    assert bands["2-5"]["premium_share"] == pytest.approx(0.75)
    assert sum(b["writer_share"] for b in writers.population(rows)) == pytest.approx(1.0)


def test_concentration_curve_only_counts_wallets_that_finished_behind():
    winner = settled(writer="0xwin", premium=500.0, spot_at_expiry=100.0)
    loser = settled(writer="0xlose", premium=1.0, contracts=10.0, spot_at_expiry=200.0)
    curve = writers.concentration_curve([winner, loser])
    assert curve[-1]["wallets"] == 1
    assert curve[-1]["loss_share"] == pytest.approx(1.0)


def test_concentration_curve_accumulates_to_the_whole_loss():
    rows = [
        settled(writer=f"0x{i}", premium=1.0, contracts=float(i + 1), spot_at_expiry=200.0) for i in range(5)
    ]
    curve = writers.concentration_curve(rows)
    shares = [p["loss_share"] for p in curve]
    assert shares == sorted(shares)
    assert shares[-1] == pytest.approx(1.0)


def test_concentration_curve_is_empty_when_nobody_lost():
    assert writers.concentration_curve([settled(spot_at_expiry=100.0)]) == []


def _wallet_that_moved_closer(name: str) -> list[Settled]:

    rows = [
        settled(
            writer=name,
            strike=120.0,
            spot_at_expiry=100.0,
            held_days=14,
            premium=40.0,
            written_days_ago=60 - i,
            tag=f"b{i}",
        )
        for i in range(3)
    ]
    rows.append(
        settled(
            writer=name,
            strike=105.0,
            spot_at_expiry=115.0,
            contracts=1.0,
            premium=40.0,
            written_days_ago=40,
            tag="hit",
        )
    )
    rows += [
        settled(
            writer=name,
            strike=104.0,
            spot_at_expiry=100.0,
            held_days=5,
            premium=60.0,
            written_days_ago=30 - i,
            tag=f"a{i}",
        )
        for i in range(3)
    ]
    return rows


def test_learning_reports_the_move_toward_the_money():
    lg = writers.learning(_wallet_that_moved_closer("0xa"))
    assert lg["wallets"] == 1
    assert lg["before"]["distance"] == pytest.approx(math.log(1.2))
    assert lg["after"]["distance"] == pytest.approx(math.log(1.04))
    assert lg["delta"]["distance"] < 0
    assert lg["moved_out"]["distance"] == 0.0


def test_learning_reports_shorter_tenor_and_larger_size():
    lg = writers.learning(_wallet_that_moved_closer("0xa"))
    assert lg["before"]["held_days"] == pytest.approx(14.0)
    assert lg["after"]["held_days"] == pytest.approx(5.0)
    assert lg["delta"]["premium"] == pytest.approx(20.0)
    assert lg["moved_out"]["premium"] == 1.0


def test_learning_needs_enough_writes_on_both_sides():
    rows = _wallet_that_moved_closer("0xa")
    assert writers.learning(rows, min_each_side=4)["wallets"] == 0
    assert writers.learning(rows, min_each_side=3)["wallets"] == 1


def test_learning_ignores_wallets_that_were_never_assigned():
    rows = [
        settled(writer="0xclean", spot_at_expiry=100.0, written_days_ago=30 - i, tag=str(i))
        for i in range(10)
    ]
    assert writers.learning(rows) == {"wallets": 0}


def test_learning_pairs_within_wallets_rather_than_pooling():

    rows = _wallet_that_moved_closer("0xa") + _wallet_that_moved_closer("0xb")

    loud = [
        settled(writer="0xloud", strike=104.0, spot_at_expiry=100.0, written_days_ago=90 - i, tag=f"b{i}")
        for i in range(30)
    ]
    loud.append(settled(writer="0xloud", strike=105.0, spot_at_expiry=115.0, written_days_ago=55, tag="hit"))
    loud += [
        settled(writer="0xloud", strike=140.0, spot_at_expiry=100.0, written_days_ago=50 - i, tag=f"a{i}")
        for i in range(30)
    ]
    lg = writers.learning(rows + loud)
    assert lg["wallets"] == 3

    assert lg["delta"]["distance"] < 0
    assert lg["moved_out"]["distance"] == pytest.approx(1 / 3)


def test_survival_curve_never_rises():
    rows = [settled(writer="0xa", spot_at_expiry=200.0, tag=str(i)) for i in range(4)]
    rows += [settled(writer="0xb", spot_at_expiry=100.0, tag=str(i)) for i in range(2)]
    curve = writers.survival_curve(rows, limit=5)
    counts = [c["writers"] for c in curve]
    assert counts == sorted(counts, reverse=True)
    assert curve[0]["writers"] == 2
    assert curve[0]["share"] == pytest.approx(1.0)
    assert curve[4]["writers"] == 1


def test_outcome_split_separates_by_the_wallet_total():
    rows = [settled(writer="0xwin", premium=100.0, spot_at_expiry=100.0, tag=str(i)) for i in range(5)]
    rows += [
        settled(writer="0xlose", premium=1.0, contracts=10.0, spot_at_expiry=200.0, tag=str(i))
        for i in range(5)
    ]
    split = {s["side"]: s for s in writers.outcome_split(rows)}
    assert split["ahead"]["writers"] == 1
    assert split["behind"]["writers"] == 1
    assert split["ahead"]["realised"] > 0 > split["behind"]["realised"]


def test_outcome_split_excludes_wallets_without_a_record():
    rows = [settled(writer="0xa", tag=str(i)) for i in range(4)]
    assert writers.outcome_split(rows, min_settled=5) == []
    assert writers.outcome_split(rows, min_settled=4) != []


def test_distance_response_bands_people_by_their_own_median():
    near = [settled(writer="0xnear", strike=102.0, tag=str(i), spot_at_expiry=100.0) for i in range(5)]
    far = [settled(writer="0xfar", strike=125.0, tag=str(i), spot_at_expiry=100.0) for i in range(5)]
    bands = {b["band"]: b for b in writers.distance_response(near + far)}
    assert bands["under 5%"]["writers"] == 1
    assert bands["18-30%"]["writers"] == 1
    assert bands["under 5%"]["settled"] == 5


def test_cohorts_group_by_the_month_a_wallet_first_wrote():
    rows = [
        settled(writer="0xa", written_days_ago=90, tag="a"),
        settled(writer="0xa", written_days_ago=5, tag="b"),
        settled(writer="0xb", written_days_ago=5, tag="c"),
    ]
    months = {c["month"]: c for c in writers.cohorts(rows)}
    assert len(months) == 2
    early = months[f"{NOW - timedelta(days=90):%Y-%m}"]
    assert early["writers"] == 1
    assert early["settled"] == 2
    assert early["return_rate"] == pytest.approx(1.0)


def test_cohort_return_rate_counts_wallets_that_came_back_another_day():
    same_day = [settled(writer="0xa", written_days_ago=5, tag=str(i)) for i in range(3)]
    assert writers.cohorts(same_day)[0]["return_rate"] == 0.0


def test_leaderboard_puts_the_worst_first_and_the_best_first():
    rows = []
    for name, contracts in (("0xworst", 20.0), ("0xmid", 5.0)):
        rows += [
            settled(writer=name, premium=1.0, contracts=contracts, spot_at_expiry=200.0, tag=str(i))
            for i in range(10)
        ]
    rows += [settled(writer="0xbest", premium=100.0, spot_at_expiry=100.0, tag=str(i)) for i in range(10)]
    lb = writers.leaderboard(rows, min_settled=10, limit=3)
    assert lb["worst"][0]["writer"] == "0xworst"
    assert lb["best"][0]["writer"] == "0xbest"
    assert [r["realised"] for r in lb["worst"]] == sorted(r["realised"] for r in lb["worst"])


def test_leaderboard_requires_a_track_record():
    rows = [settled(writer="0xa", tag=str(i)) for i in range(3)]
    lb = writers.leaderboard(rows, min_settled=10)
    assert lb["worst"] == [] and lb["best"] == []


def _wallet(name: str, first_half_wins: bool, second_half_wins: bool) -> list[Settled]:

    out = []
    for n, wins in enumerate((first_half_wins, second_half_wins)):
        for i in range(3):
            out.append(
                settled(
                    writer=name,
                    premium=100.0,
                    contracts=1.0,
                    strike=110.0,
                    spot_at_expiry=100.0 if wins else 250.0,
                    written_days_ago=60 - n * 20 - i,
                    tag=f"{n}{i}",
                )
            )
    return out


def test_persistence_counts_the_four_outcomes():
    rows = (
        _wallet("0xa", True, True)
        + _wallet("0xb", True, False)
        + _wallet("0xc", False, True)
        + _wallet("0xd", False, False)
    )
    r = writers.persistence(rows, min_settled=6)
    assert r["wallets"] == 4
    assert r["win_win"] == r["win_lose"] == r["lose_win"] == r["lose_lose"] == 1


def test_persistence_reports_no_lift_when_halves_are_independent():
    rows = []
    for i in range(20):
        rows += _wallet(f"0x{i}", i % 2 == 0, i % 4 < 2)
    r = writers.persistence(rows, min_settled=6)
    assert r["ahead_after_ahead"] == pytest.approx(r["ahead_after_behind"])
    assert r["lift"] == pytest.approx(0.0)


def test_persistence_reports_a_lift_when_the_first_half_predicts_the_second():
    rows = []
    for i in range(10):
        rows += _wallet(f"0xw{i}", True, True)
    for i in range(10):
        rows += _wallet(f"0xl{i}", False, False)
    r = writers.persistence(rows, min_settled=6)
    assert r["ahead_after_ahead"] == 1.0
    assert r["ahead_after_behind"] == 0.0
    assert r["lift"] == pytest.approx(1.0)
    assert r["correlation"] is not None and r["correlation"] > 0.9


def test_persistence_carries_the_noise_around_its_own_lift():
    rows = []
    for i in range(20):
        rows += _wallet(f"0x{i}", i % 2 == 0, i % 4 < 2)
    r = writers.persistence(rows, min_settled=6)
    assert r["lift_stderr"] > 0

    assert abs(r["lift"]) < r["lift_stderr"]


def test_persistence_needs_a_track_record():
    rows = _wallet("0xa", True, True)
    assert writers.persistence(rows, min_settled=20)["wallets"] == 0


def test_population_reports_the_share_of_each_band_that_finished_ahead():
    rows = [settled(writer="0xwin", premium=500.0, spot_at_expiry=100.0)]
    rows += [
        settled(writer="0xlose", premium=1.0, contracts=10.0, spot_at_expiry=200.0, tag=str(i))
        for i in range(3)
    ]
    bands = {b["band"]: b for b in writers.population(rows)}
    assert bands["once"]["ahead_rate"] == 1.0
    assert bands["2-5"]["ahead_rate"] == 0.0


def test_window_control_counts_a_wallet_once_per_month():

    rows = [
        settled(writer="0xa", written_days_ago=90, tag="jan"),
        settled(writer="0xa", written_days_ago=5, tag="aug"),
    ]
    bands = {b["band"]: b for b in writers.window_control(rows, min_observations=1)}
    assert bands["1"]["observations"] == 2


def test_window_control_bands_by_what_was_written_inside_the_month():
    rows = [settled(writer="0xa", written_days_ago=5, tag=str(i)) for i in range(6)]
    rows += [settled(writer="0xb", written_days_ago=5, tag="only")]
    bands = {b["band"]: b for b in writers.window_control(rows, min_observations=1)}
    assert bands["1"]["observations"] == 1
    assert bands["4-7"]["observations"] == 1
    assert bands["4-7"]["options"] == 6


def test_window_control_scores_each_month_on_its_own_options():
    winning = [
        settled(writer="0xa", written_days_ago=90, premium=100.0, spot_at_expiry=100.0, tag=f"w{i}")
        for i in range(3)
    ]
    losing = [
        settled(
            writer="0xa", written_days_ago=5, premium=1.0, contracts=10.0, spot_at_expiry=200.0, tag=f"l{i}"
        )
        for i in range(3)
    ]
    band = writers.window_control(winning + losing, min_observations=1)[0]
    assert band["band"] == "2-3"
    assert band["observations"] == 2
    assert band["ahead"] == 1
    assert band["total"] < 0


def test_window_control_drops_bands_without_enough_observations():
    rows = [settled(writer="0xa", tag=str(i)) for i in range(3)]
    assert writers.window_control(rows, min_observations=5) == []


def test_window_control_by_month_counts_months_rather_than_asserting_a_trend():

    rows = [settled(writer=f"0x{i}", written_days_ago=5, tag="a") for i in range(3)]
    assert writers.window_control_by_month(rows)["months"] == 0


def covered(
    *,
    strike=110.0,
    spot_at_expiry=100.0,
    contracts=1.0,
    premium=50.0,
    collateral="WHYPE",
    writer="0xw",
    tag="",
) -> Settled:
    row = settled(
        writer=writer,
        strike=strike,
        spot_at_expiry=spot_at_expiry,
        contracts=contracts,
        premium=premium,
        tag=tag,
    )
    return replace(row, collateral_symbol=collateral, collateral_amount=contracts)


def test_a_stablecoin_collateral_marks_a_cash_secured_position():
    assert writers.is_cash_secured(covered(collateral="USDC"))
    assert writers.is_cash_secured(covered(collateral="USD₮0"))
    assert not writers.is_cash_secured(covered(collateral="WHYPE"))


def test_covered_call_counts_the_collateral_alongside_the_option():

    row = covered(strike=110.0, spot_at_expiry=150.0, contracts=10.0, premium=100.0)
    r = writers.covered_calls([row])["all"]

    assert r["holding_gain"] == pytest.approx(500.0)

    assert r["option_leg"] == pytest.approx(-300.0)
    assert r["total"] == pytest.approx(200.0)
    assert r["captured"] == pytest.approx(0.4)


def test_covered_call_can_end_worse_than_holding_nothing_at_all():
    row = covered(strike=101.0, spot_at_expiry=105.0, contracts=10.0, premium=1.0)
    r = writers.covered_calls([row])["all"]
    assert r["holding_gain"] == pytest.approx(50.0)
    assert r["total"] == pytest.approx(11.0)
    assert 0 < r["captured"] < 1


def test_covered_calls_separate_staked_collateral_from_plain():
    rows = [
        covered(collateral="kHYPE", tag="a"),
        covered(collateral="WHYPE", tag="b"),
        covered(collateral="stHYPE", tag="c"),
    ]
    r = writers.covered_calls(rows)
    assert r["all"]["calls"] == 3
    assert r["staked"]["calls"] == 2
    assert r["plain"]["calls"] == 1


def test_covered_calls_exclude_cash_secured_rows():
    rows = [covered(collateral="USDC", tag="a"), covered(collateral="WHYPE", tag="b")]
    assert writers.covered_calls(rows)["all"]["calls"] == 1


def test_cash_secured_puts_report_the_writers_actual_result():

    row = replace(
        settled(option_type=OptionType.PUT, strike=90.0, spot_at_expiry=85.0, contracts=10.0, premium=100.0),
        collateral_symbol="USDC",
        collateral_amount=900.0,
    )
    r = writers.cash_secured_puts([row])
    assert r["puts"] == 1
    assert r["intrinsic_paid"] == pytest.approx(50.0)
    assert r["realised"] == pytest.approx(50.0)
    assert r["return_on_premium"] == pytest.approx(0.5)
    assert r["collateral"] == pytest.approx(900.0)


def test_cash_secured_puts_ignore_puts_backed_by_something_that_moves():
    row = replace(settled(option_type=OptionType.PUT), collateral_symbol="WHYPE", collateral_amount=10.0)
    assert writers.cash_secured_puts([row]) == {}


def test_by_dimension_splits_on_any_key_and_sorts_by_premium():
    rows = [
        settled(writer="0xa", underlying="HYPE", premium=100.0, tag="a"),
        settled(writer="0xb", underlying="BTC", premium=500.0, tag="b"),
        settled(writer="0xc", underlying="BTC", premium=500.0, tag="c"),
    ]
    out = writers.by_dimension(rows, lambda r: r.underlying)
    assert [r["group"] for r in out] == ["BTC", "HYPE"]
    assert out[0]["settled"] == 2 and out[0]["writers"] == 2


def test_by_dimension_can_drop_groups_too_small_to_report():
    rows = [
        settled(underlying="HYPE", premium=1000.0, tag="a"),
        settled(underlying="PURR", premium=5.0, tag="b"),
    ]
    out = writers.by_dimension(rows, lambda r: r.underlying, min_premium=100.0)
    assert [r["group"] for r in out] == ["HYPE"]


def test_per_expiry_groups_by_date_in_calendar_order():
    rows = [
        settled(writer="0xa", written_days_ago=30, held_days=10, contracts=2.0, tag="a"),
        settled(writer="0xb", written_days_ago=30, held_days=10, contracts=3.0, tag="b"),
        settled(writer="0xc", written_days_ago=10, held_days=5, premium=9999.0, tag="c"),
    ]
    out = writers.per_expiry(rows)

    assert [r["settled"] for r in out] == [2, 1]
    assert out[0]["contracts"] == 5.0
    assert out[0]["group"] < out[1]["group"]


def test_clips_report_the_wallets_habitual_size():
    rows = [
        *[settled(writer="0xa", contracts=10.0, tag=str(n)) for n in range(3)],
        settled(writer="0xa", contracts=100.0, tag="big"),
        *[settled(writer="0xb", contracts=1000.0, tag=str(n)) for n in range(3)],
    ]
    out = writers.clips(rows)
    assert out["wallets"] == 2 and out["pairs"] == 2

    assert out["clip_notional"]["p50"] in (1_000.0, 100_000.0)

    assert out["clip_by_dollar"] == 100_000.0
    assert out["repeat_share"]["p10"] == 0.75
    assert out["repeat_share"]["p99"] == 1.0


def test_clips_take_the_habit_per_asset_so_units_never_mix():
    rows = [
        *[settled(writer="0xa", underlying="HYPE", contracts=10.0, tag=str(n)) for n in range(2)],
        *[settled(writer="0xa", underlying="BTC", contracts=1.0, tag=str(n)) for n in range(2)],
    ]
    out = writers.clips(rows)
    assert out["wallets"] == 1 and out["pairs"] == 2
    assert out["repeat_share"]["p10"] == 1.0


def test_clips_need_enough_writes_to_call_anything_habitual():
    rows = [settled(writer="0xa", tag="only")]
    assert writers.clips(rows) == {"wallets": 0, "pairs": 0}


def test_tenors_report_each_wallets_own_level_and_spread():
    rows = [
        *[settled(writer="0xa", written_days_ago=40 - n, held_days=7, tag=str(n)) for n in range(4)],
        settled(writer="0xb", written_days_ago=40, held_days=7, tag="w1"),
        settled(writer="0xb", written_days_ago=30, held_days=30, tag="m1"),
        settled(writer="0xb", written_days_ago=20, held_days=7, tag="w2"),
        settled(writer="0xb", written_days_ago=10, held_days=30, tag="m2"),
    ]
    out = writers.tenors(rows)
    assert out["wallets"] == 2
    assert out["spread"]["p10"] == 0.0
    assert out["spread"]["p99"] > 10.0
    assert out["tenor"]["p10"] == pytest.approx(7.0)


ANCHOR = settled(writer="0xz", written_days_ago=1, held_days=30, tag="anchor")


def test_rolls_count_a_rewrite_soon_after_expiry_at_matching_size():
    rows = [
        settled(writer="0xa", written_days_ago=60, held_days=7, contracts=10.0, tag="one"),
        settled(writer="0xa", written_days_ago=50, held_days=7, contracts=10.5, tag="two"),
        ANCHOR,
    ]
    out = writers.rolls(rows)

    week = out["windows"]["7d"]
    assert week["observable"] == 2
    assert week["followed"] == 0.5
    assert week["matched"]["10%"] == 0.5
    assert out["gap"]["p50"] == pytest.approx(3.0)


def test_rolls_report_a_size_miss_as_followed_but_not_matched():
    rows = [
        settled(writer="0xa", written_days_ago=60, held_days=7, contracts=10.0, tag="one"),
        settled(writer="0xa", written_days_ago=50, held_days=7, contracts=100.0, tag="two"),
        ANCHOR,
    ]
    week = writers.rolls(rows)["windows"]["7d"]
    assert week["followed"] == 0.5
    assert week["matched"]["50%"] == 0.0


def test_rolls_exclude_expiries_the_data_cannot_follow():
    rows = [
        settled(writer="0xa", written_days_ago=10, held_days=7, tag="one"),
    ]
    out = writers.rolls(rows)
    assert out["windows"]["7d"]["observable"] == 0
    assert out["windows"]["7d"]["followed"] is None


def test_rolls_use_the_full_write_history_when_given():
    class Instrument:
        def __init__(self, underlying):
            self.underlying = underlying

    class Write:
        def __init__(self, taker, underlying, ts, quantity):
            self.taker, self.ts, self.quantity = taker, ts, quantity
            self.instrument = Instrument(underlying)

    rows = [settled(writer="0xa", written_days_ago=30, held_days=7, contracts=10.0, tag="one")]

    writes = [
        Write("0xa", "HYPE", NOW - timedelta(days=30), 10.0),
        Write("0xa", "HYPE", NOW - timedelta(days=21), 10.0),
        Write("0xa", "HYPE", NOW, 1.0),
    ]
    out = writers.rolls(rows, writes=writes)
    assert out["windows"]["7d"]["followed"] == 1.0

    assert writers.rolls(rows)["windows"]["7d"]["followed"] is None


def test_rolls_split_the_follow_up_by_assignment():
    rows = [
        settled(
            writer="0xa", written_days_ago=60, held_days=7, spot_at_expiry=200.0, contracts=10.0, tag="hit"
        ),
        settled(writer="0xa", written_days_ago=50, held_days=7, contracts=10.0, tag="calm"),
        settled(writer="0xb", written_days_ago=60, held_days=7, contracts=5.0, tag="b1"),
        ANCHOR,
    ]
    out = writers.rolls(rows)

    assert out["by_outcome"]["7d"]["assigned"] == 1.0
    assert out["by_outcome"]["7d"]["unassigned"] == 0.0


def test_rolls_base_rate_reads_the_wallets_own_rhythm():
    rows = [settled(writer="0xa", written_days_ago=30 - 7 * n, held_days=5, tag=str(n)) for n in range(4)]
    out = writers.rolls(rows)
    assert out["base_gap"]["p50"] == pytest.approx(7.0)


def test_rolls_pair_each_wallet_against_its_own_rhythm():
    rows = [settled(writer="0xa", written_days_ago=30 - 7 * n, held_days=5, tag=str(n)) for n in range(4)]
    out = writers.rolls(rows)
    assert out["paired"]["pairs"] == 1
    assert out["paired"]["delta_median"] == pytest.approx(-5.0)
    assert out["paired"]["faster_share"] == 1.0


def test_clips_keep_the_exact_repeat_spike_apart_from_the_bins():
    rows = [
        *[settled(writer="0xa", contracts=10.0, tag=str(n)) for n in range(2)],
        settled(writer="0xb", contracts=10.0, tag="b1"),
        settled(writer="0xb", contracts=100.0, tag="b2"),
    ]
    out = writers.clips(rows)
    assert out["repeat_exact"] == 0.5
    assert sum(b["share"] for b in out["repeat_hist"]) + out["repeat_exact"] == pytest.approx(1.0)


def test_tenors_histograms_cover_the_population():
    rows = [
        *[settled(writer="0xa", written_days_ago=40 - n, held_days=7, tag=str(n)) for n in range(2)],
        *[settled(writer="0xb", written_days_ago=40 - n, held_days=30, tag=str(n)) for n in range(2)],
    ]
    out = writers.tenors(rows)
    assert sum(b["share"] for b in out["tenor_hist"]) == pytest.approx(1.0)
    assert sum(b["share"] for b in out["spread_hist"]) == pytest.approx(1.0)
    weekly = next(b for b in out["tenor_hist"] if b["lo"] == 6)
    monthly = next(b for b in out["tenor_hist"] if b["lo"] == 25)
    assert weekly["share"] == 0.5 and monthly["share"] == 0.5
