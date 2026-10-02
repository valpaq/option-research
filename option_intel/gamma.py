from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eth_utils import keccak

from option_intel.chains import Chain
from option_intel.model import Instrument, OptionType, parse_otoken_symbol
from option_intel.rpc import Rpc

TRANSFER_TOPIC = "0x" + keccak(text="Transfer(address,address,uint256)").hex()

SELECTORS = {
    name: "0x" + keccak(text=name).hex()[:8]
    for name in (
        "strikePrice()",
        "expiryTimestamp()",
        "isPut()",
        "symbol()",
        "underlyingAsset()",
        "collateralAsset()",
    )
}


GAMMA_STRIKE_DECIMALS = 8
OTOKEN_DECIMALS = 8

ZERO_ADDRESS = "0x" + "0" * 40


@dataclass(frozen=True, slots=True)
class SeriesInfo:
    otoken: str
    symbol: str | None
    strike: float
    expiry_ts: int
    is_put: bool
    underlying_address: str | None
    collateral_address: str | None

    @property
    def expiry(self) -> datetime:
        return datetime.fromtimestamp(self.expiry_ts, tz=UTC)

    @property
    def underlying(self) -> str:

        parsed = parse_otoken_symbol(self.symbol or "")
        return parsed.underlying if parsed else "UNKNOWN"

    def instrument(self) -> Instrument | None:

        underlying = self.underlying
        if underlying == "UNKNOWN":
            return None
        return Instrument(
            underlying=underlying,
            expiry=self.expiry.date(),
            strike=self.strike,
            option_type=OptionType.PUT if self.is_put else OptionType.CALL,
        )


@dataclass(frozen=True, slots=True)
class OptionLeg:
    chain: str
    block: int
    tx_hash: str
    log_index: int
    otoken: str
    series_symbol: str | None
    underlying: str
    strike: float
    expiry: str
    option_type: str
    contracts: float
    sender: str
    receiver: str
    is_mint: bool
    is_burn: bool


def _to_int(hexstr: str | None) -> int | None:
    if not hexstr or hexstr == "0x":
        return None
    try:
        return int(hexstr, 16)
    except ValueError:
        return None


def _topic_address(topic: str | None) -> str:
    if not topic or len(topic) < 42:
        return ZERO_ADDRESS
    return "0x" + topic[-40:].lower()


def _decode_string(hexstr: str | None) -> str | None:
    if not hexstr or len(hexstr) < 130:
        return None
    try:
        raw = bytes.fromhex(hexstr[2:])
        offset = int.from_bytes(raw[:32], "big")
        length = int.from_bytes(raw[offset : offset + 32], "big")
        return raw[offset + 32 : offset + 32 + length].decode("utf-8").strip("\x00") or None
    except ValueError, IndexError, UnicodeDecodeError:
        return None


class SeriesResolver:
    def __init__(self, rpc: Rpc, cache_path: Path | None = None) -> None:
        self.rpc = rpc
        self.cache_path = cache_path
        self._known: dict[str, SeriesInfo | None] = {}
        if cache_path and cache_path.exists():
            for addr, payload in json.loads(cache_path.read_text()).items():
                self._known[addr] = SeriesInfo(**payload) if payload else None

    def resolve_many(self, addresses: Iterable[str]) -> dict[str, SeriesInfo]:

        pending = sorted({a.lower() for a in addresses} - set(self._known))
        for i in range(0, len(pending), 50):
            chunk = pending[i : i + 50]
            calls: list[tuple[str, list[Any]]] = []
            for addr in chunk:
                for name in (
                    "strikePrice()",
                    "expiryTimestamp()",
                    "isPut()",
                    "symbol()",
                    "underlyingAsset()",
                    "collateralAsset()",
                ):
                    calls.append(("eth_call", [{"to": addr, "data": SELECTORS[name]}, "latest"]))
            results = self.rpc.batch(calls)
            for j, addr in enumerate(chunk):
                base = j * 6
                strike = _to_int(results[base])
                expiry = _to_int(results[base + 1])
                if strike is None or not expiry:
                    self._known[addr] = None
                    continue
                self._known[addr] = SeriesInfo(
                    otoken=addr,
                    symbol=_decode_string(results[base + 3]),
                    strike=strike / (10**GAMMA_STRIKE_DECIMALS),
                    expiry_ts=expiry,
                    is_put=bool(_to_int(results[base + 2])),
                    underlying_address=_addr_or_none(results[base + 4]),
                    collateral_address=_addr_or_none(results[base + 5]),
                )
        self.save()
        return {a: s for a, s in self._known.items() if s is not None}

    def save(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(
                {a: (asdict(s) if s else None) for a, s in sorted(self._known.items())},
                indent=1,
            )
        )

    @property
    def probed(self) -> int:
        return len(self._known)


def _addr_or_none(hexstr: str | None) -> str | None:
    value = _to_int(hexstr)
    return None if value is None else "0x" + f"{value:040x}"


def transfer_legs(receipts: Iterable[dict]) -> Iterator[dict]:

    for receipt in receipts:
        block = _to_int(receipt.get("blockNumber")) or 0
        tx_hash = receipt.get("transactionHash", "")
        for log in receipt.get("logs", []):
            topics = log.get("topics") or []
            if not topics or topics[0].lower() != TRANSFER_TOPIC:
                continue
            yield {
                "block": block,
                "tx_hash": tx_hash,
                "log_index": _to_int(log.get("logIndex")) or 0,
                "address": (log.get("address") or "").lower(),
                "from": _topic_address(topics[1] if len(topics) > 1 else None),
                "to": _topic_address(topics[2] if len(topics) > 2 else None),
                "value": _to_int(log.get("data")) or 0,
            }


def scan(
    chain: Chain,
    tx_hashes: list[str],
    *,
    rpc: Rpc | None = None,
    cache_path: Path | None = None,
    progress_every: int = 2000,
) -> tuple[list[OptionLeg], dict[str, SeriesInfo]]:

    rpc = rpc or Rpc(chain)
    resolver = SeriesResolver(rpc, cache_path)

    legs_raw: list[dict] = []
    for i, receipt in enumerate(rpc.receipts(tx_hashes), start=1):
        legs_raw.extend(transfer_legs([receipt]))
        if progress_every and i % progress_every == 0:
            print(
                f"  {chain.key}: {i:,}/{len(tx_hashes):,} receipts, {len(legs_raw):,} legs",
                flush=True,
            )

    candidates = {leg["address"] for leg in legs_raw}
    print(f"  {chain.key}: probing {len(candidates):,} contracts for Gamma oToken interface", flush=True)
    series = resolver.resolve_many(candidates)
    print(f"  {chain.key}: {len(series):,} are option series", flush=True)

    legs: list[OptionLeg] = []
    for leg in legs_raw:
        info = series.get(leg["address"])
        if info is None:
            continue
        instrument = info.instrument()
        if instrument is None:
            continue
        legs.append(
            OptionLeg(
                chain=chain.key,
                block=leg["block"],
                tx_hash=leg["tx_hash"],
                log_index=leg["log_index"],
                otoken=leg["address"],
                series_symbol=info.symbol,
                underlying=instrument.underlying,
                strike=instrument.strike,
                expiry=instrument.expiry.isoformat(),
                option_type=instrument.option_type.value,
                contracts=leg["value"] / (10**OTOKEN_DECIMALS),
                sender=leg["from"],
                receiver=leg["to"],
                is_mint=leg["from"] == ZERO_ADDRESS,
                is_burn=leg["to"] == ZERO_ADDRESS,
            )
        )
    return legs, series


def write_legs(legs: list[OptionLeg], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(OptionLeg.__slots__))
        writer.writeheader()
        for leg in legs:
            writer.writerow(asdict(leg))


OTOKEN_CREATED_TOPIC = (
    "0x" + keccak(text="OtokenCreated(address,address,address,address,address,uint256,uint256,bool)").hex()
)


def factory_series(rpc: Rpc, factory: str, from_block: int, to_block: int) -> list[dict]:

    span = rpc.chain.max_log_range
    starts = list(range(from_block, to_block + 1, span))
    calls = [
        (
            "eth_getLogs",
            [
                {
                    "address": factory,
                    "fromBlock": hex(s),
                    "toBlock": hex(min(s + span - 1, to_block)),
                    "topics": [OTOKEN_CREATED_TOPIC],
                }
            ],
        )
        for s in starts
    ]
    seen: dict[str, dict] = {}
    for result in rpc.batch(calls):
        for log in result or []:
            data = (log.get("data") or "")[2:]

            if len(data) < 64 * 4:
                continue
            otoken = "0x" + data[:64][-40:].lower()
            seen.setdefault(
                otoken,
                {
                    "otoken": otoken,
                    "block": int(log["blockNumber"], 16),
                    "expiry": int(data[64 * 3 : 64 * 4], 16),
                },
            )
    return list(seen.values())


def factory_otokens(rpc: Rpc, factory: str, from_block: int, to_block: int) -> list[str]:

    return [s["otoken"] for s in factory_series(rpc, factory, from_block, to_block)]


def otoken_transactions(
    rpc: Rpc,
    otokens: list[str],
    from_block: int,
    to_block: int,
    *,
    tokens_per_filter: int = 400,
) -> list[str]:

    span = rpc.chain.max_log_range
    starts = list(range(from_block, to_block + 1, span))
    seen: dict[str, None] = {}
    for i in range(0, len(otokens), tokens_per_filter):
        chunk = otokens[i : i + tokens_per_filter]
        calls = [
            (
                "eth_getLogs",
                [
                    {
                        "address": chunk,
                        "fromBlock": hex(s),
                        "toBlock": hex(min(s + span - 1, to_block)),
                        "topics": [TRANSFER_TOPIC],
                    }
                ],
            )
            for s in starts
        ]
        for result in rpc.batch(calls):
            for log in result or []:
                tx = log.get("transactionHash")
                if tx:
                    seen.setdefault(tx, None)
    return list(seen)
