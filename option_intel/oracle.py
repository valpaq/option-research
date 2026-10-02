from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eth_utils import keccak

from option_intel.chains import Chain
from option_intel.rpc import Rpc

EXPIRY_PRICE_TOPIC = "0x" + keccak(text="ExpiryPriceUpdated(address,uint256,uint256,uint256)").hex()


PRICE_DECIMALS = 8

DEFAULT_CACHE = Path("data/cache/oracle-prices.csv")


@dataclass(frozen=True, slots=True)
class ExpiryPrice:
    asset: str
    expiry: datetime
    price: float
    posted_at: datetime
    block: int


def _addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def _words(data: str) -> list[int]:
    body = data[2:]
    return [int(body[i : i + 64], 16) for i in range(0, len(body), 64)]


def decode(log: dict[str, Any]) -> ExpiryPrice | None:
    topics = log.get("topics") or []
    if len(topics) < 3:
        return None
    values = _words(log.get("data") or "0x")
    if len(values) < 1:
        return None
    expiry = int(topics[2], 16)
    if not 1_600_000_000 < expiry < 3_000_000_000:
        return None
    return ExpiryPrice(
        asset=_addr(topics[1]),
        expiry=datetime.fromtimestamp(expiry, tz=UTC),
        price=values[0] / (10**PRICE_DECIMALS),
        posted_at=datetime.fromtimestamp(values[1], tz=UTC) if len(values) > 1 else None,
        block=int(log.get("blockNumber", "0x0"), 16),
    )


class OraclePrices:
    def __init__(self, cache_path: Path | None = DEFAULT_CACHE) -> None:
        self.cache_path = cache_path
        self._by_asset: dict[tuple[str, str, int], float] = {}
        self._dirty = False
        self._read()

    def _read(self) -> None:
        if not self.cache_path or not self.cache_path.exists():
            return
        with self.cache_path.open() as fh:
            for row in csv.reader(fh):
                if len(row) != 4:
                    continue
                try:
                    self._by_asset[(row[0], row[1].lower(), int(row[2]))] = float(row[3])
                except ValueError:
                    continue

    def save(self) -> None:
        if not self.cache_path or not self._dirty:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        with tmp.open("w", newline="") as fh:
            writer = csv.writer(fh)
            for (chain, asset, expiry), price in sorted(self._by_asset.items()):
                writer.writerow([chain, asset, expiry, f"{price:.10g}"])
        tmp.replace(self.cache_path)
        self._dirty = False

    def add(self, chain: str, price: ExpiryPrice) -> None:
        key = (chain, price.asset, int(price.expiry.timestamp()))
        if key not in self._by_asset:
            self._by_asset[key] = price.price
            self._dirty = True

    def price_at(self, chain: str, asset: str, expiry: datetime) -> float | None:
        return self._by_asset.get((chain, asset.lower(), int(expiry.timestamp())))

    def collect(
        self,
        rpc: Rpc,
        chain: Chain,
        *,
        from_block: int,
        to_block: int | None = None,
    ) -> int:

        to_block = to_block or rpc.block_number()
        span = chain.max_log_range
        starts = list(range(from_block, to_block + 1, span))
        calls = [
            (
                "eth_getLogs",
                [
                    {
                        "fromBlock": hex(s),
                        "toBlock": hex(min(s + span - 1, to_block)),
                        "topics": [EXPIRY_PRICE_TOPIC],
                    }
                ],
            )
            for s in starts
        ]
        found = 0

        for result in rpc.batch(calls, strict=True):
            for log in result or []:
                price = decode(log)
                if price is not None:
                    before = len(self._by_asset)
                    self.add(chain.key, price)
                    found += len(self._by_asset) - before
        self.save()
        return found

    @property
    def size(self) -> int:
        return len(self._by_asset)

    def coverage(self) -> dict[str, Any]:

        by_chain: dict[str, list[int]] = defaultdict(list)
        for chain, _asset, expiry in self._by_asset:
            by_chain[chain].append(expiry)
        out = {}
        for chain, expiries in by_chain.items():
            out[chain] = {
                "prices": len(expiries),
                "expiries": len(set(expiries)),
                "first": datetime.fromtimestamp(min(expiries), tz=UTC),
                "last": datetime.fromtimestamp(max(expiries), tz=UTC),
            }
        return out


def asset_addresses(series_cache: Path) -> dict[str, str]:

    import json

    from option_intel.model import parse_otoken_symbol

    if not series_cache.exists():
        return {}
    out: dict[str, str] = {}
    for payload in json.loads(series_cache.read_text()).values():
        if not payload:
            continue
        address = (payload.get("underlying_address") or "").lower()
        parsed = parse_otoken_symbol(payload.get("symbol") or "")
        if address and parsed:
            out[address] = parsed.underlying
    return out
