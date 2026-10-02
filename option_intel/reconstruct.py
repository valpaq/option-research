from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from option_intel.model import Instrument, LiquidityRole, OptionType, Venue, VolPrint
from option_intel.rates import forward_and_discount
from option_intel.spot import HyperliquidSpot
from option_intel.vol import solve_iv


def load_writes(
    paths: list[Path], spot: HyperliquidSpot, *, chain: str = "", expiry_start=None, expiry_end=None
) -> list[VolPrint]:

    out: list[VolPrint] = []
    dropped = 0
    for path in paths:
        if not path.exists():
            continue
        with path.open() as fh:
            for row in csv.DictReader(fh):
                if chain and row.get("chain") != chain:
                    continue
                try:
                    expiry_day = datetime.fromisoformat(row["expiry"]).date()
                    if expiry_start and expiry_day < expiry_start:
                        continue
                    if expiry_end and expiry_day > expiry_end:
                        continue
                    ts = datetime.fromisoformat(row["ts"])
                    contracts = float(row["contracts"])
                    premium = float(row["premium"])
                    instrument = Instrument(
                        underlying=row["underlying"],
                        expiry=datetime.fromisoformat(row["expiry"]).date(),
                        strike=float(row["strike"]),
                        option_type=OptionType(row["option_type"]),
                    )
                except KeyError, ValueError:
                    dropped += 1
                    continue
                if contracts <= 0 or premium <= 0:
                    dropped += 1
                    continue
                px = spot.price_at(instrument.underlying, ts)
                if px is None or px <= 0:
                    dropped += 1
                    continue

                fee = float(row.get("fee") or 0.0)

                collateral = float(row.get("collateral_amount") or 0.0)
                notional = instrument.strike * contracts
                netted = (
                    fee == premium
                    and notional > 0
                    and abs((collateral + premium) - notional) <= 1e-6 * notional
                )
                if netted:
                    fee = 0.0

                gross, premium = premium, premium - fee
                if premium <= 0:
                    dropped += 1
                    continue
                per_contract = premium / contracts
                t = instrument.time_to_expiry(ts)
                forward, discount = forward_and_discount(px, 0.0, t)
                iv, _ = solve_iv(
                    per_contract, forward, instrument.strike, t, instrument.option_type, discount
                )
                out.append(
                    VolPrint(
                        venue=Venue.RYSK,
                        chain=row.get("chain", ""),
                        ts=ts,
                        instrument=instrument,
                        quantity=contracts,
                        premium_per_unit=per_contract,
                        spot=px,
                        taker=row.get("writer") or None,
                        role_of_record=LiquidityRole.TAKER,
                        tx_hash=row.get("tx_hash") or None,
                        iv=iv,
                        forward=forward,
                        extra={
                            "protocol": row.get("protocol", ""),
                            "premium_total": premium,
                            "premium_gross": gross,
                            "collateral_symbol": row.get("collateral_symbol", ""),
                            "collateral_amount": float(row.get("collateral_amount") or 0.0),
                            "fee": fee,
                        },
                    )
                )
    if dropped:
        print(f"load_writes: {dropped} row(s) refused, {len(out)} reconstructed")
    return out
