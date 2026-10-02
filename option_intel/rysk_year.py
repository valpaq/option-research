from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path

from option_intel import figures, oracle, settlement
from option_intel.model import OptionType
from option_intel.reconstruct import load_writes
from option_intel.rysk_year_audit import audit_call_exposure, fee_gas_split, reconcile, reconstruct_collateral
from option_intel.spot import HyperliquidSpot, spot_symbol


class FrozenSpot(HyperliquidSpot):
    def price_at(self, coin, ts):
        key = (spot_symbol(coin), int(ts.timestamp()) // 3600)
        if key not in self._cache:
            raise ValueError(f"Missing pinned spot hour: {coin} {ts.isoformat()}")
        return self._cache[key]


def calculate(work: Path, config: dict):
    start = datetime.fromisoformat(config["window"]["start"])
    end = datetime.fromisoformat(config["window"]["end"])
    asof = datetime.fromisoformat(config["asof"])
    chain = config["chain"]
    if not start < end < asof:
        raise ValueError("Expected start < end < collection asof")
    if datetime.fromisoformat(config["sales_scanned_through"]) < end:
        raise ValueError("Sales scan does not reach the final expiry")
    collateral_records = reconstruct_collateral(work)
    exposure_anomalies = audit_call_exposure(work)
    spot = FrozenSpot(cache_path=work / "data__cache__spot-hourly.csv")

    inputs = [work / name for name in config["input_sha256"]]
    paths = sorted(p for p in inputs if p.name.endswith("sales.csv"))
    expected = []
    for path in paths:
        with path.open() as stream:
            for row in csv.DictReader(stream):
                expiry = datetime.combine(
                    datetime.fromisoformat(row["expiry"]).date(), time(8, tzinfo=end.tzinfo)
                )
                if row["chain"] == chain and start <= expiry <= end:
                    expected.append(row)
    keys = {(r["tx_hash"], r["otoken"]) for r in expected}
    if len(keys) != len(expected):
        raise ValueError("Duplicate transaction/option-token rows in expiry cohort")

    import math

    anomaly_txs = {item["tx_hash"] for item in exposure_anomalies}
    for row in expected:
        required = float(row["contracts"]) * (float(row["strike"]) if row["option_type"] == "P" else 1.0)
        if (
            not math.isclose(float(row["collateral_amount"]), required, rel_tol=1e-7, abs_tol=1e-7)
            and row["tx_hash"] not in anomaly_txs
        ):
            raise ValueError(f"Collateral exposure differs from option size: {row['tx_hash']}")
    writes = load_writes(paths, spot, chain=chain, expiry_start=start.date(), expiry_end=end.date())
    if len(writes) != len(expected):
        raise ValueError(f"Reconstruction dropped eligible sales: {len(expected)} -> {len(writes)}")
    prices = oracle.OraclePrices(work / "data__cache__oracle-prices.csv")
    addresses = {
        k: v
        for p in sorted(inputs)
        if "otokens-" in p.name and p.suffix == ".json"
        for k, v in oracle.asset_addresses(p).items()
    }

    missing = set()
    for w in writes:
        expiry = datetime.combine(w.instrument.expiry, time(8, tzinfo=end.tzinfo))
        candidates = [
            prices.price_at(chain, a, expiry)
            for a, ticker in addresses.items()
            if ticker == w.instrument.underlying
        ]
        if not any(p is not None and p > 0 for p in candidates):
            missing.add((w.instrument.underlying, expiry.isoformat()))
    if missing:
        raise ValueError(f"Missing protocol oracle prices: {sorted(missing)}")
    book, why = settlement.settle(writes, spot, asof=asof, oracle=prices, asset_addresses=addresses)
    if len(book) != len(expected) or any(r.price_source != "oracle" for r in book):
        raise ValueError("Settlement coverage mismatch")
    fine = figures.refined_spot(work / "data__cache__spot-refined.csv")

    def itm(rows, kind):
        return sum(
            (r.strike < r.spot_at_write if kind == OptionType.CALL else r.strike > r.spot_at_write)
            for r in rows
            if r.option_type == kind
        )

    before = {kind: itm(book, kind) for kind in OptionType}
    near = [r for r in book if abs(r.strike / r.spot_at_write - 1) < figures.ON_THE_STRIKE]
    rows = figures.refine(book, fine)
    why.update(
        spot_refined=sum((r.underlying, r.written_at.isoformat()) in fine for r in rows),
        itm_hourly_calls=before[OptionType.CALL],
        itm_hourly_puts=before[OptionType.PUT],
        itm_minute_calls=itm(rows, OptionType.CALL),
        itm_minute_puts=itm(rows, OptionType.PUT),
        itm_at_expiry=sum(
            r.option_type == OptionType.CALL and r.strike < r.spot_at_write and r.held_days <= 1 for r in rows
        ),
    )
    context = figures.Settled(rows, rows, writes, spot, asof, why)
    result = figures.compute(
        start,
        end,
        chain=chain,
        context=context,
        fine_prices=fine,
        wallet_include_collateral=True,
        simple_call_distance=True,
        all_assets=True,
    )

    for key in ("open", "excluded_chain", "in_the_money"):
        result.pop(key, None)
    result["fee"].update(fee_gas_split(work, expected, result["fee"]["total"]))
    result["coverage"] = {
        "eligible_sales": len(expected),
        "reconstructed": len(writes),
        "oracle_priced": len(rows),
        "spot_fallback": 0,
        "near_strike_hourly": len(near),
        "near_strike_unrefined": sum((r.underlying, r.written_at.isoformat()) not in fine for r in near),
    }
    series = defaultdict(lambda: {"calls": 0.0, "puts": 0.0, "options": 0})
    for r in rows:
        cell = series[r.expired_at.isoformat()]
        cell["calls" if r.option_type == OptionType.CALL else "puts"] += figures.position_result(r)
        cell["options"] += 1
    result["expiry_results"] = [{"expiry": k, **v} for k, v in sorted(series.items())]
    result["provenance"] = config
    result["collateral_valuation"] = "Underlying-price proxy; token exchange rates and basis excluded"
    result["combined_total"] = sum(figures.position_result(r) for r in rows)
    assert (
        abs(result["combined_total"] - result["call_totals"]["net"] - result["puts_totals"]["realised"])
        < 1e-6
    )

    import math

    assert len(rows) == result["calls"] + result["puts"]
    assert sum(result["buckets"][k]["calls"] for k in ("fell", "under", "through")) == result["calls"]
    assert math.isclose(
        sum(figures.position_result(r) for r in rows),
        result["wallets"]["mean"] * result["wallets"]["n"],
        abs_tol=1e-6,
    )
    assert result["wallets"]["cut"] + result["wallets"]["cut_remaining"] == result["wallets"]["n"]
    result["accounting_audit"] = reconcile(rows, result)
    result["accounting_audit"]["receipt_backed_collateral_rows"] = len(collateral_records)
    result["accounting_audit"]["oversized_transfer_rows"] = len(exposure_anomalies)
    result["collateral_reconstruction"] = collateral_records
    by_tx = {row.tx_hash: row for row in rows}
    for item in exposure_anomalies:
        row = by_tx[item["tx_hash"]]
        move = row.spot_at_expiry - row.spot_at_write
        item["source_proxy_change"] = item["source_proxy_units"] * move
        item["comparison_proxy_change"] = item["comparison_units"] * move
        item["proxy_difference"] = item["source_proxy_change"] - item["comparison_proxy_change"]
    result["collateral_exposure_anomalies"] = exposure_anomalies

    return rows, json.loads(json.dumps(result))


def export_tables(rows, result, out: Path):
    out.mkdir(exist_ok=True)
    records = [
        {
            **r.to_row(),
            "call_collateral_change_proxy": figures.position_result(r) - r.realised,
            "combined_result_proxy": figures.position_result(r),
        }
        for r in rows
    ]
    with (out / "settled.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    wallets = defaultdict(
        lambda: {
            "option_result": 0.0,
            "call_collateral_change_proxy": 0.0,
            "combined_result_proxy": 0.0,
            "options": 0,
        }
    )
    for r in rows:
        cell = wallets[r.writer]
        cell["option_result"] += r.realised
        cell["call_collateral_change_proxy"] += figures.position_result(r) - r.realised
        cell["combined_result_proxy"] += figures.position_result(r)
        cell["options"] += 1
    with (out / "wallets.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["wallet", *next(iter(wallets.values()))])
        writer.writeheader()
        writer.writerows({"wallet": wallet, **values} for wallet, values in sorted(wallets.items()))
    (out / "accounting-audit.json").write_text(json.dumps(result["accounting_audit"], indent=2) + "\n")
    (out / "collateral-reconstruction.json").write_text(
        json.dumps(result["collateral_reconstruction"], indent=2) + "\n"
    )
    (out / "collateral-exposure-anomalies.json").write_text(
        json.dumps(result["collateral_exposure_anomalies"], indent=2) + "\n"
    )
    (out / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
