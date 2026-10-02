from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from option_intel.spot import HyperliquidSpot, StaticSpot, spot_symbol

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def hour_of(ts: datetime) -> int:
    return int(ts.timestamp()) // 3600


@pytest.fixture
def offline(tmp_path):

    spot = HyperliquidSpot(cache_path=tmp_path / "spot.csv")

    def refuse(*_args, **_kwargs):
        raise AssertionError("the network was called when the cache should have answered")

    spot._fetch = refuse
    return spot


def test_a_cached_hour_answers_without_fetching(offline):
    offline._cache[("HYPE", hour_of(NOW))] = 42.0
    offline._loaded.add("HYPE")
    assert offline.price_at("HYPE", NOW) == 42.0


def test_the_cache_survives_a_restart(tmp_path):
    path = tmp_path / "spot.csv"
    first = HyperliquidSpot(cache_path=path)
    first._cache[("HYPE", hour_of(NOW))] = 81.5
    first._dirty = True
    first.save()

    second = HyperliquidSpot(cache_path=path)
    second._fetch = lambda *a, **k: 0
    assert second.price_at("HYPE", NOW) == 81.5


def test_history_already_collected_outlives_a_moving_horizon(tmp_path):

    path = tmp_path / "spot.csv"
    old = NOW - timedelta(days=400)
    seeded = HyperliquidSpot(cache_path=path)
    seeded._cache[("HYPE", hour_of(old))] = 12.25
    seeded._dirty = True
    seeded.save()

    later = HyperliquidSpot(cache_path=path)
    later._fetch = lambda *a, **k: 0
    assert later.price_at("HYPE", old) == 12.25


def test_a_four_hour_candle_fills_every_hour_it_spans(tmp_path):
    spot = HyperliquidSpot(cache_path=tmp_path / "spot.csv")
    base = hour_of(NOW)

    def one_candle(coin, interval, start_ms, end_ms):
        spot._cache.setdefault((coin, base), 100.0)
        for offset in range(1, 4):
            spot._cache.setdefault((coin, base + offset), 100.0)
        return 1

    spot._fetch = one_candle
    one_candle("HYPE", "4h", 0, 0)
    for offset in range(4):
        assert spot._cache[("HYPE", base + offset)] == 100.0


def test_an_hourly_value_is_never_overwritten_by_a_coarser_one(offline):

    base = hour_of(NOW)
    offline._cache[("HYPE", base)] = 99.0

    offline._cache.setdefault(("HYPE", base), 50.0)
    assert offline._cache[("HYPE", base)] == 99.0


def test_the_price_used_is_never_from_after_the_trade(offline):

    base = hour_of(NOW)
    offline._cache[("HYPE", base - 3)] = 70.0
    offline._cache[("HYPE", base + 3)] = 90.0
    offline._loaded.add("HYPE")
    assert offline.price_at("HYPE", NOW) == 70.0


def test_no_earlier_price_means_none_rather_than_a_guess(offline):
    offline._cache[("HYPE", hour_of(NOW) + 10)] = 90.0
    offline._loaded.add("HYPE")
    assert offline.price_at("HYPE", NOW) is None


def test_coverage_reports_what_is_actually_held(offline):
    base = hour_of(NOW)
    offline._cache[("HYPE", base - 240)] = 1.0
    offline._cache[("HYPE", base)] = 2.0
    offline._loaded.add("HYPE")
    span = offline.coverage("HYPE")
    assert span is not None
    assert (span[1] - span[0]) == timedelta(hours=240)


def test_bridged_and_wrapped_tickers_resolve_to_the_priced_asset():

    assert spot_symbol("UPUMP") == "PUMP"
    assert spot_symbol("FXRP") == "XRP"
    assert spot_symbol("BZEC") == "ZEC"
    assert spot_symbol("wstHYPE") == "HYPE"
    assert spot_symbol("UETH") == "ETH"

    assert spot_symbol("USDH") == "USDH"


def test_lookups_go_through_the_alias_table(offline):
    offline._cache[("HYPE", hour_of(NOW))] = 80.0
    offline._loaded.add("HYPE")
    assert offline.price_at("wstHYPE", NOW) == 80.0


def test_static_spot_is_alias_aware():
    static = StaticSpot({"HYPE": 75.0})
    assert static.price_at("kHYPE", NOW) == 75.0
    assert static.price_at("DOGE", NOW) is None
