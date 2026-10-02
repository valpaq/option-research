from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from option_intel import settlement
from option_intel.model import OptionType
from option_intel.reconstruct import load_writes
from option_intel.spot import StaticSpot

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

COLUMNS = [
    "chain",
    "protocol",
    "tx_hash",
    "block",
    "ts",
    "otoken",
    "series_symbol",
    "underlying",
    "strike",
    "expiry",
    "option_type",
    "contracts",
    "writer",
    "premium",
    "premium_symbol",
    "fee",
    "collateral_symbol",
    "collateral_amount",
]


def write_csv(tmp: Path, **over) -> Path:

    row = {
        "chain": "hyperevm",
        "protocol": "rysk",
        "tx_hash": "0xabc",
        "block": "1",
        "ts": (NOW - timedelta(days=10)).isoformat(),
        "otoken": "0xtok",
        "series_symbol": "oHYPE-27JUN26-110C",
        "underlying": "HYPE",
        "strike": "110",
        "expiry": (NOW - timedelta(days=3)).date().isoformat(),
        "option_type": "C",
        "contracts": "10",
        "writer": "0xw",
        "premium": "60.712267",
        "premium_symbol": "USDC",
        "fee": "6.745807",
        "collateral_symbol": "WHYPE",
        "collateral_amount": "10",
    }
    row.update({k: str(v) for k, v in over.items()})
    path = tmp / "sales.csv"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerow(row)
    return path


def rebuild(tmp: Path, spot_now: float = 100.0, **over):
    spot = StaticSpot({"HYPE": spot_now})
    return load_writes([write_csv(tmp, **over)], spot)


def test_the_premium_that_survives_is_what_the_writer_received(tmp_path):

    write = rebuild(tmp_path)[0]
    assert write.extra["premium_total"] == pytest.approx(53.966460)
    assert write.extra["premium_gross"] == pytest.approx(60.712267)
    assert write.extra["fee"] == pytest.approx(6.745807)


def test_the_fee_is_one_eighth_of_what_the_writer_signed_for(tmp_path):
    write = rebuild(tmp_path)[0]
    net, fee = write.extra["premium_total"], write.extra["fee"]
    assert fee / net == pytest.approx(0.125, abs=1e-6)
    assert net + fee == pytest.approx(write.extra["premium_gross"])


def test_the_fee_reaches_the_settled_row(tmp_path):

    spot = StaticSpot({"HYPE": 100.0})
    rows, _ = settlement.settle(rebuild(tmp_path), spot, asof=NOW)
    assert len(rows) == 1
    assert rows[0].fee == pytest.approx(6.745807)
    assert rows[0].premium == pytest.approx(53.966460)


def test_realised_is_computed_on_the_writers_premium_not_the_makers_cost(tmp_path):

    spot = StaticSpot({"HYPE": 100.0})
    rows, _ = settlement.settle(rebuild(tmp_path), spot, asof=NOW)
    row = rows[0]
    assert row.realised == pytest.approx(53.966460)
    assert row.realised != pytest.approx(60.712267)


def test_a_sale_charged_no_fee_keeps_its_whole_premium(tmp_path):
    write = rebuild(tmp_path, fee="0")[0]
    assert write.extra["premium_total"] == pytest.approx(60.712267)
    assert write.extra["fee"] == 0.0


def test_a_sale_whose_fee_would_swallow_it_is_refused(tmp_path):

    assert rebuild(tmp_path, fee="60.712267") == []
    assert rebuild(tmp_path, fee="99") == []


def test_collateral_travels_far_enough_to_identify_the_structure(tmp_path):

    spot = StaticSpot({"HYPE": 100.0})
    rows, _ = settlement.settle(rebuild(tmp_path), spot, asof=NOW)
    assert rows[0].collateral_symbol == "WHYPE"
    assert rows[0].collateral_amount == pytest.approx(10.0)
    assert rows[0].option_type is OptionType.CALL


def test_an_assigned_call_pays_from_the_settlement_price(tmp_path):
    spot = StaticSpot({"HYPE": 130.0})
    rows, _ = settlement.settle(rebuild(tmp_path, contracts="10"), spot, asof=NOW)
    row = rows[0]
    assert row.assigned
    assert row.intrinsic_paid == pytest.approx((130 - 110) * 10)
    assert row.realised == pytest.approx(53.966460 - 200)


def test_a_premium_netted_into_collateral_is_not_a_fee(tmp_path):

    rows = rebuild(
        tmp_path,
        protocol="hypersurface",
        option_type="P",
        strike="61",
        contracts="5",
        premium="3.55164",
        fee="3.55164",
        collateral_symbol="USDC",
        collateral_amount="301.44836",
    )
    assert len(rows) == 1
    assert rows[0].extra["fee"] == 0.0
    assert rows[0].extra["premium_total"] == pytest.approx(3.55164)


def test_a_real_fee_is_still_subtracted(tmp_path):

    rows = rebuild(tmp_path)
    assert rows[0].extra["fee"] == pytest.approx(6.745807)
    assert rows[0].extra["premium_total"] == pytest.approx(60.712267 - 6.745807)


def test_a_full_fee_without_the_netting_identity_is_left_alone(tmp_path):

    rows = rebuild(
        tmp_path,
        option_type="P",
        strike="61",
        contracts="5",
        premium="3.55164",
        fee="3.55164",
        collateral_symbol="USDC",
        collateral_amount="999",
    )
    assert rows == []
