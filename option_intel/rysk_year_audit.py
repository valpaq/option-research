from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from option_intel.gamma import ZERO_ADDRESS, transfer_legs
from option_intel.model import OptionType

WSTHYPE = "0x94e8396e0869c9f2200760af0621afd240e1cf38"
RYSK_MARGIN_POOL = "0x24a44f1dc25540c62c1196ffc297dfc951c91ab4"
EXCESS_DEPOSIT_TX = "0xf88a8ca30a002e4d59ceaf0f6bab9a90db035edf240cada894f3361d5e882403"
RYSK_MMARKET = "0x691a5fc3a81a144e36c6c4fbca1fc82843c80d0d"
RYSK_HYPE = "0x8c8bcb6d2c0e31c5789253ecc8431ca6209b4e35"
RYSK_FEE_RECIPIENT = "0xfb69f38eae27705720eb4aabb04be9edbec5b555"
RYSK_GAS_FEE_RECIPIENT = "0xafe32eb89391dfd5900f98857f009477e4423db4"


def fee_gas_split(work: Path, sales: list[dict], nonwriter_total: float) -> dict:

    manifest = json.loads((work / "data__cache__rysk-year-fee-gas.json").read_text())
    by_tx = {r["tx_hash"].lower(): r for r in sales}
    assert len(by_tx) == len(sales) == manifest["cohort_transactions"]
    tx_digest = hashlib.sha256("\n".join(r["tx_hash"] for r in sales).encode()).hexdigest()
    assert tx_digest == manifest["tx_hashes_sha256"]
    assert manifest["chain"] == "hyperevm"
    assert manifest["maker_contract"] == RYSK_MMARKET
    recipient_raw = manifest["recipient_totals_micro_usd"]
    fee_recipient = manifest["fee_recipient"]
    gas_recipient = manifest["gas_recipient"]
    assert fee_recipient == RYSK_FEE_RECIPIENT
    assert gas_recipient == RYSK_GAS_FEE_RECIPIENT
    roles = manifest["recipient_role_evidence"]
    assert roles["contract"] == RYSK_HYPE
    assert roles["url"] == ("https://hyperevmscan.io/address/" + RYSK_HYPE + "#readProxyContract")
    assert roles["feeRecipient"] == {"selector": "0x46904840", "value": fee_recipient}
    assert roles["gasFeeRecipient"] == {"selector": "0x7a1f6755", "value": gas_recipient}
    assert roles["mmarket"] == {"selector": "0x2c39aef2", "value": RYSK_MMARKET}
    assert fee_recipient != gas_recipient
    assert set(recipient_raw) == {fee_recipient, gas_recipient}
    assert sum(recipient_raw.values()) == manifest["nonwriter_total_micro_usd"]
    ledger_total = sum(int(Decimal(r["fee"]) * 1_000_000) for r in sales)
    assert ledger_total == manifest["nonwriter_total_micro_usd"]
    assert math.isclose(ledger_total / 1_000_000, nonwriter_total, abs_tol=1e-6)
    assert manifest["recipient_transaction_counts"][fee_recipient] == sum(
        Decimal(r["fee"]) > 0 for r in sales
    )

    receipts = manifest["gas_receipts"]
    gas_rows = manifest["gas_transactions"]
    assert len(receipts) == len(gas_rows) == manifest["recipient_transaction_counts"][gas_recipient]
    assert {r["tx_hash"] for r in gas_rows} == set(receipts)
    gas_sum = 0
    for item in gas_rows:
        tx = item["tx_hash"]
        sale = by_tx[tx.lower()]
        receipt = receipts[tx]
        assert receipt["transactionHash"].lower() == tx.lower()
        assert int(receipt["status"], 16) == 1
        assert int(receipt["blockNumber"], 16) == item["block"] == int(sale["block"])
        transfers = [leg for leg in transfer_legs([receipt]) if leg["from"] == RYSK_MMARKET]
        premium_tokens = {leg["address"] for leg in transfers if leg["to"] == sale["writer"].lower()}
        stable = [leg for leg in transfers if leg["address"] in premium_tokens]
        writer_raw = sum(leg["value"] for leg in stable if leg["to"] == sale["writer"].lower())
        fee_raw = sum(leg["value"] for leg in stable if leg["to"] == fee_recipient)
        gas_raw = sum(leg["value"] for leg in stable if leg["to"] == gas_recipient)
        assert writer_raw == int((Decimal(sale["premium"]) - Decimal(sale["fee"])) * 1_000_000)
        assert fee_raw + gas_raw == int(Decimal(sale["fee"]) * 1_000_000)
        assert gas_raw == item["amount_micro_usd"] > 0
        assert sum(leg["value"] for leg in stable) == writer_raw + fee_raw + gas_raw
        gas_sum += gas_raw
    assert gas_sum == recipient_raw[gas_recipient]
    return {
        "fee_recipient": fee_recipient,
        "rysk_fee_recipient_total": recipient_raw[fee_recipient] / 1_000_000,
        "gas_recipient": gas_recipient,
        "gas_fee_recipient_total": recipient_raw[gas_recipient] / 1_000_000,
        "gas_transactions": len(gas_rows),
        "receipt_scan_coverage": len(sales),
        "basis": "MMarket stablecoin transfers in sale receipts; RyskHype feeRecipient and gasFeeRecipient address labels observed on 2026-09-27, not proven for every historical sale",
    }


def reconstruct_collateral(work: Path):

    path = work / "data__onchain__rysk__sales.csv"
    with path.open() as stream:
        reader = csv.DictReader(stream)
        fields, records = reader.fieldnames, list(reader)
    evidence = json.loads((work / "data__cache__rysk-year-collateral-receipts.json").read_text())
    tokens = json.loads((work / "data__cache__otokens-rysk.json").read_text())
    changes = []
    for row in records:
        tx = row["tx_hash"]
        if tx not in evidence:
            continue
        assert row["collateral_symbol"] in ("stHYPE", "wstHYPE")
        assert tokens[row["otoken"]]["collateral_address"].lower() == WSTHYPE
        receipt = evidence[tx]
        assert receipt["transactionHash"].lower() == tx.lower()
        legs = [x for x in transfer_legs([receipt]) if x["from"] == row["writer"].lower()]
        matching = [x for x in legs if x["address"] == WSTHYPE]
        assert matching and all(x["to"] == RYSK_MARGIN_POOL for x in matching)
        amount = sum(x["value"] for x in matching) / 10**18
        changes.append(
            {
                "tx_hash": tx,
                "collateral_address": WSTHYPE,
                "margin_pool": RYSK_MARGIN_POOL,
                "collateral_symbol": "wstHYPE",
                "collateral_amount": amount,
                "transfer_log_indices": [x["log_index"] for x in matching],
            }
        )
        row["collateral_symbol"], row["collateral_amount"] = "wstHYPE", str(amount)
    assert len(changes) == len(evidence)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    return changes


def audit_call_exposure(work: Path):

    path = work / "data__onchain__rysk__sales.csv"
    with path.open() as stream:
        reader = csv.DictReader(stream)
        records = list(reader)
    matches = [row for row in records if row["tx_hash"] == EXCESS_DEPOSIT_TX]
    assert len(matches) == 1
    row = matches[0]
    assert (row["chain"], row["option_type"], row["collateral_symbol"]) == ("hyperevm", "C", "kHYPE")
    assert float(row["contracts"]) == 50.0
    assert float(row["collateral_amount"]) == 5000.0
    evidence = json.loads((work / "data__cache__rysk-year-overcollateralized-tx.json").read_text())
    tx, receipt = evidence["transaction"], evidence["receipt"]
    assert tx["hash"].lower() == receipt["transactionHash"].lower() == EXCESS_DEPOSIT_TX
    assert int(tx["blockNumber"], 16) == int(receipt["blockNumber"], 16) == 22529167
    assert int(receipt["status"], 16) == 1
    assert evidence["implementation_at_transaction_block"].endswith(
        "fad2adf5aca6043cc00def6e74ef901725d14632"
    )
    call = bytes.fromhex(tx["input"][2:])
    assert call[:4].hex() == "778ddcb3"
    assert int.from_bytes(call[4:36]) == 32
    assert int.from_bytes(call[36:68]) == 344
    payload = call[68:]
    assert len(payload) == 352 and payload[344:] == b"\x00" * 8
    assert int.from_bytes(payload[73:89]) == 50 * 10**18
    assert int.from_bytes(payload[312:328]) == 5000 * 10**18
    collateral_token = "0x" + payload[292:312].hex()
    assert collateral_token == "0xfd739d4e423301ce9385c1fb8850539d657c296d"
    assert "0x" + payload[243:263].hex() == row["writer"].lower()
    assert int.from_bytes(payload[227:243]) == int(float(row["strike"]) * 10**8)
    transfers = list(transfer_legs([receipt]))
    deposits = [
        leg
        for leg in transfers
        if leg["address"] == collateral_token
        and leg["from"] == row["writer"].lower()
        and leg["to"] == RYSK_MARGIN_POOL
    ]
    assert len(deposits) == 1 and deposits[0]["value"] == 5000 * 10**18
    mints = [
        leg for leg in transfers if leg["address"] == row["otoken"].lower() and leg["from"] == ZERO_ADDRESS
    ]
    assert len(mints) == 1 and mints[0]["value"] == 50 * 10**8
    return [
        {
            "tx_hash": EXCESS_DEPOSIT_TX,
            "collateral_symbol": "kHYPE",
            "writer_to_pool_transfer": 5000.0,
            "call_contracts": 50.0,
            "source_proxy_units": 5000.0,
            "comparison_units": 50.0,
            "source": "Pinned transaction payload and receipt",
            "historical_implementation": "0xfad2adf5aca6043cc00def6e74ef901725d14632",
            "interpretation": "Signed position collateral is 100x minted call quantity; 50 units is counterfactual only",
        }
    ]


def reconcile(rows, figures):

    D = lambda value: Decimal(str(value))
    wallets = defaultdict(lambda: [Decimal(0), Decimal(0)])
    tokens = defaultdict(
        lambda: {"options": 0, "option_result": Decimal(0), "collateral_proxy_change": Decimal(0)}
    )
    for row in rows:
        expiry, strike = D(row.spot_at_expiry), D(row.strike)
        intrinsic = max(
            Decimal(0), expiry - strike if row.option_type is OptionType.CALL else strike - expiry
        )
        option = D(row.premium) - D(row.contracts) * intrinsic
        assert math.isclose(float(option), row.realised, abs_tol=1e-7)
        move = (
            D(row.collateral_amount) * (expiry - D(row.spot_at_write))
            if row.option_type is OptionType.CALL
            else Decimal(0)
        )
        wallets[row.writer][0] += option
        wallets[row.writer][1] += move
        t = tokens[row.collateral_symbol]
        t["options"] += 1
        t["option_result"] += option
        t["collateral_proxy_change"] += move
    option = [float(a) for a, b in wallets.values()]
    combined = [float(a + b) for a, b in wallets.values()]

    def summary(values):
        return {
            "total": math.fsum(values),
            "mean": statistics.mean(values),
            "median": statistics.median(values),
            "positive": sum(v > 0 for v in values),
            "positive_share": sum(v > 0 for v in values) / len(values),
        }

    a, b = summary(option), summary(combined)
    if figures is not None:
        assert math.isclose(b["total"], figures["combined_total"], abs_tol=1e-6)
        for key in ("mean", "median", "positive", "positive_share"):
            assert math.isclose(b[key], figures["wallets"][key], abs_tol=1e-7)
    move = float(sum(x[1] for x in wallets.values()))
    assert math.isclose(a["total"] + move, b["total"], abs_tol=1e-6)
    transitions = {
        f"{old}_to_{new}": sum((a > 0) == old and (a + b > 0) == new for a, b in wallets.values())
        for old in (False, True)
        for new in (False, True)
    }
    by_token = [
        {
            **v,
            "collateral": token,
            "option_result": float(v["option_result"]),
            "collateral_proxy_change": float(v["collateral_proxy_change"]),
            "combined_proxy": float(v["option_result"] + v["collateral_proxy_change"]),
        }
        for token, v in sorted(tokens.items())
    ]
    return {
        "option_only": a,
        "combined_proxy": b,
        "collateral_proxy_change": move,
        "mean_change": move / len(wallets),
        "transitions": transitions,
        "by_collateral": by_token,
        "top_changes": [
            {
                "wallet": wallet,
                "option_result": float(v[0]),
                "collateral_proxy_change": float(v[1]),
                "combined_proxy": float(sum(v)),
            }
            for wallet, v in sorted(wallets.items(), key=lambda x: x[1][1], reverse=True)[:10]
        ],
    }
