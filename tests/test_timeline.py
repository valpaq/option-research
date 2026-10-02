from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from option_intel import timeline
from option_intel.timeline import Event

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def ev(wallet: str, pnl: float, days_ago: int = 0, scale: float = 100.0) -> Event:
    return Event(wallet=wallet, at=NOW - timedelta(days=days_ago), pnl=pnl, scale=scale)


def run(wallet: str, *pnls: float, start: int = 100) -> list[Event]:
    return [ev(wallet, p, start - i) for i, p in enumerate(pnls)]


def test_by_wallet_orders_each_run_oldest_first():
    events = [ev("0xa", 1.0, 5), ev("0xa", 2.0, 50), ev("0xb", 3.0, 1)]
    grouped = timeline.by_wallet(events)
    assert set(grouped) == {"0xa", "0xb"}
    assert [e.at for e in grouped["0xa"]] == sorted(e.at for e in grouped["0xa"])


def test_distribution_separates_the_median_from_the_mean():

    events = [ev(f"0x{i}", 10.0) for i in range(99)] + [ev("0xbad", -100_000.0)]
    d = timeline.distribution(events)
    assert d["wallets"] == 100
    assert d["median"] == pytest.approx(10.0)
    assert d["mean"] < 0
    assert d["positive"] == 99
    assert d["losses"] == pytest.approx(-100_000.0)


def test_concentration_reaches_the_whole_loss_and_ignores_winners():
    events = [ev("0xwin", 500.0)] + [ev(f"0x{i}", -float(i + 1)) for i in range(5)]
    curve = timeline.concentration(events)
    assert curve[-1]["wallets"] == 5
    assert curve[-1]["loss_share"] == pytest.approx(1.0)
    assert [p["loss_share"] for p in curve] == sorted(p["loss_share"] for p in curve)


def test_flip_point_finds_the_smallest_group_that_turns_the_book():

    events = [ev(f"0x{i}", 100.0) for i in range(10)] + [ev("0xbad", -1_100.0)]
    f = timeline.flip_point(events)
    assert f["removed"] == 1
    assert f["remaining"] == 10
    assert f["rest"] == pytest.approx(1000.0)


def test_flip_point_is_empty_when_the_book_is_already_positive():
    assert timeline.flip_point([ev("0xa", 5.0)]) == {}


def test_persistence_detects_a_real_signal():
    events = []
    for i in range(20):
        events += run(f"0xwin{i}", *[10.0] * 10)
        events += run(f"0xlose{i}", *[-10.0] * 10)
    r = timeline.persistence(events, min_events=10)
    assert r["wallets"] == 40
    assert r["lift"] == pytest.approx(1.0)
    assert r["correlation"] > 0.9


def test_persistence_reports_no_lift_when_the_halves_are_independent():
    events = []
    for i in range(40):
        first = 10.0 if i % 2 == 0 else -10.0
        second = 10.0 if i % 4 < 2 else -10.0
        events += run(f"0x{i}", *([first] * 5 + [second] * 5))
    r = timeline.persistence(events, min_events=10)
    assert r["lift"] == pytest.approx(0.0)
    assert abs(r["lift"]) < r["lift_stderr"]


def test_persistence_skips_a_half_that_booked_nothing():

    events = run("0xa", *([0.0] * 5 + [10.0] * 5))
    assert timeline.persistence(events, min_events=10)["wallets"] == 0


def test_by_activity_bands_wallets_by_how_many_events_they_have():
    events = run("0xone", 5.0) + run("0xfew", 1.0, 1.0, 1.0) + run("0xmany", *([1.0] * 9))
    bands = {
        b["band"]: b for b in timeline.by_activity(events, (("1", 1, 1), ("2-5", 2, 5), ("6+", 6, None)))
    }
    assert bands["1"]["wallets"] == 1
    assert bands["2-5"]["wallets"] == 1
    assert bands["6+"]["events"] == 9
