from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any

from option_intel.model import OptionType
from option_intel.spot import SpotSource


SETTLEMENT_HOUR = 8


@dataclass(frozen=True, slots=True)
class Settled:
    protocol: str
    chain: str
    tx_hash: str
    writer: str
    underlying: str
    option_type: OptionType
    strike: float
    written_at: datetime
    expired_at: datetime
    contracts: float
    premium: float
    fee: float
    spot_at_write: float
    spot_at_expiry: float

    price_source: str = "spot"

    collateral_symbol: str = ""
    collateral_amount: float = 0.0

    @property
    def assigned(self) -> bool:

        if self.option_type is OptionType.CALL:
            return self.spot_at_expiry > self.strike
        return self.spot_at_expiry < self.strike

    @property
    def intrinsic_paid(self) -> float:

        if self.option_type is OptionType.CALL:
            per = max(self.spot_at_expiry - self.strike, 0.0)
        else:
            per = max(self.strike - self.spot_at_expiry, 0.0)
        return per * self.contracts

    @property
    def realised(self) -> float:

        return self.premium - self.intrinsic_paid

    @property
    def held_days(self) -> float:
        return (self.expired_at - self.written_at).total_seconds() / 86400.0

    @property
    def move(self) -> float:

        return self.spot_at_expiry / self.spot_at_write - 1.0

    def to_row(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "chain": self.chain,
            "tx_hash": self.tx_hash,
            "writer": self.writer,
            "underlying": self.underlying,
            "option_type": self.option_type.value,
            "strike": self.strike,
            "written_at": self.written_at.isoformat(),
            "expired_at": self.expired_at.isoformat(),
            "held_days": round(self.held_days, 3),
            "contracts": self.contracts,
            "premium": self.premium,
            "fee": self.fee,
            "spot_at_write": self.spot_at_write,
            "spot_at_expiry": self.spot_at_expiry,
            "price_source": self.price_source,
            "collateral_symbol": self.collateral_symbol,
            "collateral_amount": self.collateral_amount,
            "move": round(self.move, 6),
            "assigned": self.assigned,
            "intrinsic_paid": round(self.intrinsic_paid, 6),
            "realised": round(self.realised, 6),
        }


def settle(
    writes: Iterable[Any],
    spot: SpotSource,
    *,
    asof: datetime | None = None,
    oracle: Any = None,
    asset_addresses: dict[str, str] | None = None,
) -> tuple[list[Settled], dict[str, int]]:

    asof = asof or datetime.now(UTC)
    out: list[Settled] = []
    skipped: dict[str, int] = defaultdict(int)

    by_ticker: dict[str, list[str]] = defaultdict(list)
    for address, ticker in (asset_addresses or {}).items():
        by_ticker[ticker].append(address)

    for w in writes:
        expiry = datetime.combine(w.instrument.expiry, time(SETTLEMENT_HOUR, tzinfo=UTC))
        if expiry >= asof:
            skipped["still_open"] += 1
            continue
        settle_spot, source = None, "spot"
        if oracle is not None:
            for address in by_ticker.get(w.instrument.underlying, ()):
                published = oracle.price_at(w.chain, address, expiry)
                if published and published > 0:
                    settle_spot, source = published, "oracle"
                    break
        if settle_spot is None:
            settle_spot = spot.price_at(w.instrument.underlying, expiry)
        if settle_spot is None or settle_spot <= 0:
            skipped[f"no_price_at_expiry:{w.instrument.underlying}"] += 1
            continue
        skipped[f"priced_by_{source}"] += 1
        out.append(
            Settled(
                protocol=w.extra.get("protocol", "unknown"),
                chain=w.chain,
                tx_hash=w.tx_hash or "",
                writer=w.taker or "",
                underlying=w.instrument.underlying,
                option_type=w.instrument.option_type,
                strike=w.instrument.strike,
                written_at=w.ts,
                expired_at=expiry,
                contracts=w.quantity,
                premium=float(w.extra.get("premium_total") or w.premium_total),
                fee=float(w.extra.get("fee") or 0.0),
                spot_at_write=w.spot,
                spot_at_expiry=settle_spot,
                price_source=source,
                collateral_symbol=str(w.extra.get("collateral_symbol") or ""),
                collateral_amount=float(w.extra.get("collateral_amount") or 0.0),
            )
        )
    return out, dict(skipped)


def overall(rows: list[Settled]) -> dict[str, Any]:

    if not rows:
        return {}
    premium = sum(r.premium for r in rows)
    paid = sum(r.intrinsic_paid for r in rows)
    assigned = [r for r in rows if r.assigned]
    return {
        "settled": len(rows),
        "writers": len({r.writer for r in rows if r.writer}),
        "premium": premium,
        "intrinsic_paid": paid,
        "realised": premium - paid,
        "keep_rate": (premium - paid) / premium if premium else None,
        "assigned": len(assigned),
        "assigned_share": len(assigned) / len(rows),
        "median_held_days": statistics.median([r.held_days for r in rows]),
        "first": min(r.written_at for r in rows),
        "last": max(r.expired_at for r in rows),
    }


def by_group(rows: list[Settled], key) -> list[dict[str, Any]]:

    groups: dict[Any, list[Settled]] = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)

    out = []
    for name, items in groups.items():
        premium = sum(i.premium for i in items)
        paid = sum(i.intrinsic_paid for i in items)
        assigned = sum(1 for i in items if i.assigned)
        out.append(
            {
                "group": name,
                "settled": len(items),
                "premium": premium,
                "intrinsic_paid": paid,
                "realised": premium - paid,
                "keep_rate": (premium - paid) / premium if premium else None,
                "assigned": assigned,
                "assigned_share": assigned / len(items),
                "median_held_days": statistics.median([i.held_days for i in items]),
            }
        )
    out.sort(key=lambda r: -r["premium"])
    return out


def by_writer(rows: list[Settled], *, min_settled: int = 1) -> list[dict[str, Any]]:

    groups: dict[str, list[Settled]] = defaultdict(list)
    for r in rows:
        if r.writer:
            groups[r.writer].append(r)

    out = []
    for writer, items in groups.items():
        if len(items) < min_settled:
            continue
        premium = sum(i.premium for i in items)
        paid = sum(i.intrinsic_paid for i in items)
        assigned = sum(1 for i in items if i.assigned)
        out.append(
            {
                "writer": writer,
                "settled": len(items),
                "premium": premium,
                "intrinsic_paid": paid,
                "realised": premium - paid,
                "keep_rate": (premium - paid) / premium if premium else None,
                "assigned": assigned,
                "assigned_share": assigned / len(items),
                "calls": sum(1 for i in items if i.option_type is OptionType.CALL),
                "puts": sum(1 for i in items if i.option_type is OptionType.PUT),
                "assets": sorted({i.underlying for i in items}),
                "first": min(i.written_at for i in items),
                "last": max(i.expired_at for i in items),
                "worst": min(i.realised for i in items),
                "best": max(i.realised for i in items),
            }
        )
    out.sort(key=lambda r: -r["realised"])
    return out


def survival_after_first_loss(rows: list[Settled]) -> dict[str, Any]:

    by_wallet: dict[str, list[Settled]] = defaultdict(list)
    for r in rows:
        if r.writer:
            by_wallet[r.writer].append(r)

    hit, hit_continued, clean, clean_continued = 0, 0, 0, 0
    after_counts: list[int] = []
    for items in by_wallet.values():
        items.sort(key=lambda i: i.written_at)
        first_loss = next((i for i, s in enumerate(items) if s.assigned), None)
        if first_loss is None:
            clean += 1
            if len(items) > 1:
                clean_continued += 1
            continue
        hit += 1
        after = len(items) - first_loss - 1
        after_counts.append(after)
        if after > 0:
            hit_continued += 1

    return {
        "writers": len(by_wallet),
        "ever_assigned": hit,
        "never_assigned": clean,
        "continued_after_assignment": hit_continued,
        "continued_rate_after_assignment": hit_continued / hit if hit else None,
        "continued_rate_never_assigned": clean_continued / clean if clean else None,
        "median_writes_after_first_assignment": (statistics.median(after_counts) if after_counts else None),
    }


def monthly(rows: list[Settled]) -> list[dict[str, Any]]:

    out = by_group(rows, lambda r: (r.expired_at.year, r.expired_at.month))
    for row in out:
        year, month = row["group"]
        row["month"] = f"{year}-{month:02d}"
    out.sort(key=lambda r: r["month"])
    return out


def moneyness_ladder(rows: list[Settled], *, bins: int = 9) -> list[dict[str, Any]]:

    import math

    edges = [-0.30 + 0.075 * i for i in range(bins + 1)]

    def bucket(r: Settled) -> str:
        distance = math.log(r.strike / r.spot_at_write)
        if r.option_type is OptionType.PUT:
            distance = -distance
        for lo, hi in zip(edges[:-1], edges[1:], strict=True):
            if lo <= distance < hi:
                return f"{lo:+.0%}..{hi:+.0%}"
        return "beyond"

    rows_out = by_group(rows, bucket)
    order = {f"{lo:+.0%}..{hi:+.0%}": i for i, (lo, hi) in enumerate(zip(edges[:-1], edges[1:], strict=True))}
    rows_out.sort(key=lambda r: order.get(r["group"], 999))
    return rows_out
