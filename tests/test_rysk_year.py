import csv
import gzip
import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from option_intel.model import OptionType
from option_intel.rysk_year import FrozenSpot, calculate
from option_intel.rysk_year_audit import reconstruct_collateral


@pytest.fixture
def snapshot(tmp_path):
    source = Path("articles/rysk/rysk-year/data")
    if not source.exists():
        source = Path("research/rysk/rysk-year/data")
    for packed in source.glob("*.gz"):
        (tmp_path / packed.name[:-3]).write_bytes(gzip.decompress(packed.read_bytes()))
    config = json.loads((tmp_path / "data__cache__rysk-year-provenance.json").read_text())
    return tmp_path, config


def test_frozen_spot_refuses_stale_forward_fill(tmp_path):
    path = tmp_path / "spot.csv"
    ts = datetime(2026, 9, 18, 8, tzinfo=UTC)
    path.write_text(f"HYPE,{int(ts.timestamp()) // 3600 - 1},42\n")
    with pytest.raises(ValueError, match="Missing pinned spot hour"):
        FrozenSpot(cache_path=path).price_at("HYPE", ts)


def test_snapshot_rejects_collateral_transfer_to_another_recipient(snapshot):
    work, _ = snapshot
    tx = "0xaf0f406e5a482b8b21f76acefd623ebd36d280a6a051b54f1967482d5ee13dc4"
    evidence_path = work / "data__cache__rysk-year-collateral-receipts.json"
    evidence = json.loads(evidence_path.read_text())
    receipt = evidence[tx]
    sale = next(
        row
        for row in csv.DictReader((work / "data__onchain__rysk__sales.csv").open())
        if row["tx_hash"] == tx
    )
    target = next(
        log
        for log in receipt["logs"]
        if log["address"].lower() == "0x94e8396e0869c9f2200760af0621afd240e1cf38"
        and log["topics"][1][-40:].lower() == sale["writer"].lower()[2:]
    )
    target["topics"][2] = "0x" + "0" * 24 + "1" * 40
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(AssertionError):
        reconstruct_collateral(work)


def test_snapshot_replays_offline_and_includes_final_expiry(snapshot):
    work, config = snapshot
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network")):
        rows, figures = calculate(work, config)
    assert figures["coverage"]["eligible_sales"] == len(rows)
    assert figures["coverage"]["spot_fallback"] == 0
    fee = figures["fee"]
    assert fee["rysk_fee_recipient_total"] == 1_872_560.358196
    assert fee["gas_fee_recipient_total"] == 21.441656
    assert fee["gas_transactions"] == 12
    assert fee["receipt_scan_coverage"] == 40_067
    assert fee["fee_recipient"] == "0xfb69f38eae27705720eb4aabb04be9edbec5b555"
    assert fee["gas_recipient"] == "0xafe32eb89391dfd5900f98857f009477e4423db4"
    end = datetime.fromisoformat(config["window"]["end"])
    start = datetime.fromisoformat(config["window"]["start"])
    assert max(r.expired_at for r in rows) == end
    assert all(start <= r.expired_at <= end for r in rows)
    assert any(r.written_at < start for r in rows)
    assert figures["window"]["days"] == 364
    assert figures["settled"] == 40_067
    assert figures["writers"] == 2_856
    assert figures["accounting_audit"]["option_only"]["total"] == pytest.approx(-9_522_204.07180738)
    assert figures["accounting_audit"]["combined_proxy"]["total"] == pytest.approx(6_541_154.913997)
    assert figures["wallets"]["basis"] == "combined_proxy"
    assert "Underlying-price proxy" in figures["collateral_valuation"]
    assert figures["call_distance"]["basis"] == "strike_over_spot_minus_one"
    calls = [r for r in rows if r.option_type is OptionType.CALL]
    assert figures["call_distance"]["beyond_10pct"] == pytest.approx(
        sum(r.strike > 1.10 * r.spot_at_write for r in calls) / len(calls)
    )
    assert sum(v["calls"] for v in figures["by_asset"]["calls"].values()) == len(calls)
    assert figures["accounting_audit"]["receipt_backed_collateral_rows"] == 220
    assert figures["accounting_audit"]["oversized_transfer_rows"] == 1
    anomaly = figures["collateral_exposure_anomalies"][0]
    assert anomaly["writer_to_pool_transfer"] == 5000
    assert anomaly["call_contracts"] == 50
    assert anomaly["source_proxy_units"] == 5000
    assert anomaly["proxy_difference"] == pytest.approx(1873.2677535)
    assert all(r.collateral_symbol != "stHYPE" for r in rows)
    assert sum(r.collateral_symbol == "wstHYPE" for r in rows) == 220
    for r in rows:
        if r.collateral_symbol == "wstHYPE":
            assert r.collateral_amount == pytest.approx(r.contracts)
        if r.tx_hash == anomaly["tx_hash"]:
            assert r.collateral_amount == 5000
        elif r.option_type is OptionType.CALL:
            assert r.collateral_amount == pytest.approx(r.contracts)
        else:
            assert r.collateral_amount == pytest.approx(r.contracts * r.strike)
    audit = figures["accounting_audit"]
    assert audit["combined_proxy"]["total"] == pytest.approx(
        audit["option_only"]["total"] + audit["collateral_proxy_change"]
    )
    assert sum(x["options"] for x in figures["expiry_results"]) == len(rows)
    assert sum(x["calls"] + x["puts"] for x in figures["expiry_results"]) == pytest.approx(
        sum(
            r.realised
            + (
                r.collateral_amount * (r.spot_at_expiry - r.spot_at_write)
                if r.option_type is OptionType.CALL
                else 0
            )
            for r in rows
        )
    )
    assert figures["wallets"]["mean"] * figures["wallets"]["n"] == pytest.approx(
        figures["call_totals"]["net"] + figures["puts_totals"]["realised"]
    )

    (work / "stale__sales.csv").write_bytes((work / "data__onchain__rysk__sales.csv").read_bytes())
    (work / "stale__otokens-other.json").write_text("invalid stale JSON")
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network")):
        replay_rows, replay = calculate(work, config)
    assert replay == figures
    assert len(replay_rows) == len(rows)


def test_snapshot_rejects_oracle_gap(snapshot):
    work, config = snapshot
    path = work / "data__cache__oracle-prices.csv"
    expiry = str(int(datetime.fromisoformat(config["window"]["end"]).timestamp()))
    kept = [row for row in csv.reader(path.open()) if row[2] != expiry]
    with path.open("w", newline="") as out:
        csv.writer(out).writerows(kept)
    with pytest.raises(ValueError, match="Missing protocol oracle prices"):
        calculate(work, config)


def test_snapshot_rejects_incomplete_scan(snapshot):
    work, config = snapshot
    config["sales_scanned_through"] = "2026-09-11T08:00:00+00:00"
    with pytest.raises(ValueError, match="Sales scan does not reach"):
        calculate(work, config)


def test_fee_split_rejects_tampered_gas_transfer(snapshot):
    work, config = snapshot
    path = work / "data__cache__rysk-year-fee-gas.json"
    scan = json.loads(path.read_text())
    tx = scan["gas_transactions"][0]["tx_hash"]
    gas_address = scan["gas_recipient"][2:]
    receipt = scan["gas_receipts"][tx]
    matching = [
        log
        for log in receipt["logs"]
        if len(log["topics"]) == 3 and log["topics"][2].lower().endswith(gas_address)
    ]
    assert len(matching) == 1
    matching[0]["data"] = "0x" + "0" * 64
    path.write_text(json.dumps(scan))
    with pytest.raises(AssertionError):
        calculate(work, config)


@pytest.mark.parametrize(
    ("kind", "expiry_spot", "payoff", "expected"),
    [
        (OptionType.CALL, 140, 40, 50),
        (OptionType.CALL, 80, 0, -30),
        (OptionType.PUT, 80, 40, -30),
    ],
)
def test_combined_result_adds_call_collateral_only(kind, expiry_spot, payoff, expected):
    from types import SimpleNamespace

    from option_intel.figures import position_result

    row = SimpleNamespace(
        option_type=kind,
        collateral_amount=2,
        spot_at_write=100,
        spot_at_expiry=expiry_spot,
        realised=10 - payoff,
        collateral_symbol="ETH" if kind is OptionType.CALL else "USDC",
    )
    assert position_result(row) == expected


def test_staking_collateral_is_explicitly_only_an_underlying_proxy():
    from types import SimpleNamespace

    from option_intel.figures import position_result

    row = SimpleNamespace(
        option_type=OptionType.CALL,
        collateral_amount=2,
        spot_at_write=100,
        spot_at_expiry=120,
        realised=5,
        collateral_symbol="kHYPE",
    )

    assert position_result(row) == 45
