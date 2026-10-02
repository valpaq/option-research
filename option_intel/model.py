from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum

SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0


class OptionType(StrEnum):
    CALL = "C"
    PUT = "P"


class Venue(StrEnum):
    DERIVE = "derive"
    RYSK = "rysk"
    DERIBIT = "deribit"


class LiquidityRole(StrEnum):
    MAKER = "maker"
    TAKER = "taker"


@dataclass(frozen=True, slots=True)
class Instrument:
    underlying: str
    expiry: date
    strike: float
    option_type: OptionType

    @property
    def key(self) -> str:
        return f"{self.underlying}-{self.expiry:%Y%m%d}-{self.strike:g}-{self.option_type.value}"

    def time_to_expiry(self, at: datetime, expiry_hour_utc: int = 8) -> float:

        if at.tzinfo is None:
            raise ValueError("time_to_expiry requires a timezone-aware datetime")
        settle = datetime(self.expiry.year, self.expiry.month, self.expiry.day, expiry_hour_utc, tzinfo=UTC)
        return (settle - at).total_seconds() / SECONDS_PER_YEAR


@dataclass(frozen=True, slots=True)
class VolPrint:
    venue: Venue
    chain: str
    ts: datetime
    instrument: Instrument
    quantity: float
    premium_per_unit: float
    spot: float

    maker: str | None = None
    taker: str | None = None
    role_of_record: LiquidityRole | None = None

    tx_hash: str | None = None
    trade_id: str | None = None

    iv: float | None = None
    forward: float | None = None

    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError("VolPrint.ts must be timezone-aware")
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")
        if self.spot <= 0:
            raise ValueError(f"spot must be positive, got {self.spot}")
        if self.tx_hash is None and self.trade_id is None:
            raise ValueError("VolPrint requires a source reference (tx_hash or trade_id)")

    @property
    def notional(self) -> float:
        return self.quantity * self.spot

    @property
    def premium_total(self) -> float:
        return self.quantity * self.premium_per_unit

    def to_row(self) -> dict:
        row = asdict(self)
        row.pop("instrument")
        row.pop("extra")
        row.update(
            venue=self.venue.value,
            ts=self.ts.isoformat(),
            underlying=self.instrument.underlying,
            expiry=self.instrument.expiry.isoformat(),
            strike=self.instrument.strike,
            option_type=self.instrument.option_type.value,
            instrument_key=self.instrument.key,
            role_of_record=self.role_of_record.value if self.role_of_record else None,
            notional=self.notional,
            premium_total=self.premium_total,
        )
        return row


_DERIVE_INSTRUMENT = re.compile(r"^(?P<u>[A-Z0-9]+)-(?P<e>\d{8})-(?P<k>[0-9.]+)-(?P<t>[CP])$")


def parse_derive_instrument(name: str) -> Instrument | None:

    m = _DERIVE_INSTRUMENT.match(name.strip())
    if not m:
        return None
    return Instrument(
        underlying=m.group("u"),
        expiry=datetime.strptime(m.group("e"), "%Y%m%d").date(),
        strike=float(m.group("k")),
        option_type=OptionType(m.group("t")),
    )


_DERIBIT_INSTRUMENT = re.compile(
    r"^(?P<u>[A-Z0-9_]+)-(?P<e>\d{1,2}[A-Z]{3}\d{2})-(?P<k>[0-9.]+)-(?P<t>[CP])$"
)


def parse_deribit_instrument(name: str) -> Instrument | None:

    m = _DERIBIT_INSTRUMENT.match(name.strip())
    if not m:
        return None
    return Instrument(
        underlying=m.group("u"),
        expiry=datetime.strptime(m.group("e"), "%d%b%y").date(),
        strike=float(m.group("k")),
        option_type=OptionType(m.group("t")),
    )


_OTOKEN_SERIES = re.compile(r"^o(?P<head>.+)-(?P<e>\d{1,2}[A-Z]{3}\d{2})-(?P<k>[0-9.]+)(?P<t>[CP])$")


RYSK_QUOTE_SUFFIXES = ("USD₮0", "USDT0", "USDH", "USDC", "USDE", "USDT", "USD")


UNDERLYING_ALIASES = {
    "WHYPE": "HYPE",
    "KHYPE": "HYPE",
    "HIHYPE": "HYPE",
    "STHYPE": "HYPE",
    "WSTHYPE": "HYPE",
    "LHYPE": "HYPE",
    "WETH": "ETH",
    "UETH": "ETH",
    "WSTETH": "ETH",
    "WEETH": "ETH",
    "RETH": "ETH",
    "WBTC": "BTC",
    "UBTC": "BTC",
    "CBBTC": "BTC",
    "WSOL": "SOL",
    "USOL": "SOL",
    "UPUMP": "PUMP",
    "FXRP": "XRP",
    "UXRP": "XRP",
    "XAUT": "XAUT",
    "BZEC": "ZEC",
    "UZEC": "ZEC",
    "UFART": "FARTCOIN",
}


def resolve_underlying(symbol: str) -> str:

    return UNDERLYING_ALIASES.get(symbol.strip().upper(), symbol.strip().upper())


def split_rysk_pair(pair: str) -> tuple[str, str] | None:

    p = pair.strip()
    for suffix in RYSK_QUOTE_SUFFIXES:
        if p.upper().endswith(suffix.upper()) and len(p) > len(suffix):
            return p[: -len(suffix)], p[-len(suffix) :]
    return None


def parse_otoken_symbol(symbol: str) -> Instrument | None:

    m = _OTOKEN_SERIES.match(symbol.strip())
    if not m:
        return None
    parts = m.group("head").split("/")
    if len(parts) == 3:
        base = parts[0]
    elif len(parts) == 2:
        split = split_rysk_pair(parts[0])
        if split is None:
            return None
        base = split[0]
    else:
        return None
    if not base:
        return None
    return Instrument(
        underlying=resolve_underlying(base),
        expiry=datetime.strptime(m.group("e"), "%d%b%y").date(),
        strike=float(m.group("k")),
        option_type=OptionType(m.group("t")),
    )


def parse_rysk_series(symbol: str) -> Instrument | None:

    return parse_otoken_symbol(symbol)
