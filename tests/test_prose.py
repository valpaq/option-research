from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from option_intel import prose

FIGURES = {"premium": 6_638_145.0, "kept": 0.123, "calls": 17790, "gap": {"median": 12.297777}}


def test_a_figure_that_traces_is_accepted():
    text = "17,790 calls, $6,638,145 of premium, 12.3% of the move."
    assert prose.untraced(text, FIGURES, set()) == []


def test_a_figure_that_does_not_trace_is_named():
    assert prose.untraced("2.4x the premium", FIGURES, set()) == ["2.4"]


def test_one_decimal_is_read():

    assert prose.untraced("half within 9.7 hours", FIGURES, set()) == ["9.7"]


def test_a_written_date_is_not_a_measurement():

    assert prose.untraced("none since 24 June 2026", FIGURES, set()) == []


def test_a_decimal_is_not_read_as_the_digits_inside_it():

    assert prose.untraced("came back 26.36 short", {"loss": -26.3613}, set()) == []


def test_an_allowed_threshold_passes():
    assert prose.untraced("inside 2% of the strike", FIGURES, {"2%"}) == []


def test_blocks_split_on_a_rule():
    cut = prose.posts("first post\n---\nsecond post\n")
    assert [p["text"] for p in cut] == ["first post", "second post"]


def test_an_image_is_lifted_out_of_the_body():
    cut = prose.posts("look at this\n\n![](charts/the-gap.png)\n")
    assert cut[0]["image"] == "charts/the-gap.png"
    assert cut[0]["text"] == "look at this"


def test_an_empty_block_is_not_a_post():
    assert len(prose.posts("one\n---\n\n---\ntwo")) == 2


@pytest.mark.parametrize("link", ["https://example.com/a/very/long/path", "{link}"])
def test_a_link_costs_the_same_however_long_it_is(link):

    assert prose.length(f"read it: {link}") == len("read it: ") + prose.LINK_COST


def test_a_post_at_the_limit_is_measured_exactly():
    assert prose.length("x" * prose.LIMIT) == prose.LIMIT


def test_an_exact_match_is_reported_as_exact():
    got = prose.trace("50 wallets carry it", {"carry": {"wallets": 50}}, set())
    assert [m["status"] for m in got] == ["exact"]
    assert got[0]["key"] == "carry.wallets"
    assert got[0]["error"] == 0.0


def test_a_match_reached_by_rounding_says_so():

    got = prose.trace("53 wallets carry it", {"bands": {"call_share": 0.5259928963513077}}, set())
    assert got[0]["status"] == "rounded"
    assert got[0]["key"] == "bands.call_share"
    assert got[0]["error"] > prose.ROUNDING_TOLERANCE


def test_a_number_nothing_produced_is_untraced():
    got = prose.trace("9,999 of them", {"a": 1.0}, set())
    assert got[0]["status"] == "untraced"
    assert got[0]["key"] is None
    assert prose.untraced("9,999 of them", {"a": 1.0}, set()) == ["9,999"]


def test_a_declared_figure_is_marked_rather_than_matched():
    got = prose.trace("0.340 on the old window", {"a": 1.0}, {"0.340"})
    assert got[0]["status"] == "declared"
    assert got[0]["key"] is None


def test_candidates_keep_the_true_source_when_it_is_not_the_closest():

    figures = {"withdrawal": 3.228687305, "case": {"loss_share": -0.0315941}}
    got = prose.trace("a loss of 3.2%", figures, set())[0]
    assert got["key"] == "withdrawal"
    assert "case.loss_share" in {k for k, _ in got["candidates"]}


def test_candidates_are_closest_first():
    figures = {"far": 0.53, "near": 0.5259928963513077}
    got = prose.trace("53%", figures, set())[0]
    errors = [abs(float("53") - abs(v) * 100) / (abs(v) * 100) for _, v in got["candidates"]]
    assert errors == sorted(errors)


def test_two_names_for_one_value_are_not_counted_as_ambiguity():
    figures = {"call_totals": {"net": 2011489.0}, "buckets": {"all": {"net": 2011489.0}}}
    assert prose.trace("$2,011,489", figures, set())[0]["others"] == 0


def test_two_different_values_are_counted_as_ambiguity():
    figures = {"a": 0.82722, "b": 0.82663}
    assert prose.trace("82.7%", figures, set())[0]["others"] == 1


def test_every_number_appears_once_in_reading_order():
    got = prose.trace("10 and 20 and 10 again", {"a": 10.0, "b": 20.0}, set())
    assert [m["token"] for m in got] == ["10", "20"]


def test_provenance_prints_a_line_per_number_and_names_the_key():
    out = prose.provenance("50 wallets", {"carry": {"wallets": 50}}, set())
    assert "carry.wallets" in out
    assert "1 figure(s)" in out


def test_provenance_shouts_about_a_number_nothing_produced():
    assert "NOTHING PRODUCED THIS" in prose.provenance("9,999", {"a": 1.0}, set())


def test_provenance_survives_prose_with_no_numbers_in_it():
    assert prose.provenance("no figures here", {"a": 1.0}, set()) == "no figures in the prose"
