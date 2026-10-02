from __future__ import annotations

import csv
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

import requests

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
DEFAULT_CACHE = Path("data/cache/spot-hourly.csv")


HOURLY_HORIZON_DAYS = 200


SPOT_ALIASES = {
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


def spot_symbol(symbol: str) -> str:
    return SPOT_ALIASES.get(symbol.strip().upper(), symbol.strip().upper())


@runtime_checkable
class SpotSource(Protocol):
    def price_at(self, coin: str, ts: datetime) -> float | None: ...


class HyperliquidSpot:
    MAX_CANDLES_PER_REQUEST = 5000

    def __init__(
        self,
        timeout: float = 30.0,
        cache_path: Path | None = DEFAULT_CACHE,
        deep_days: int = 620,
    ) -> None:
        self.timeout = timeout
        self.cache_path = cache_path
        self.deep_days = deep_days
        self._cache: dict[tuple[str, int], float] = {}
        self._loaded: set[str] = set()
        self._dirty = False
        self.session = requests.Session()
        self._read_cache()

    def _read_cache(self) -> None:
        if not self.cache_path or not self.cache_path.exists():
            return
        with self.cache_path.open() as fh:
            for row in csv.reader(fh):
                if len(row) != 3:
                    continue
                try:
                    self._cache[(row[0], int(row[1]))] = float(row[2])
                except ValueError:
                    continue
        self._loaded = {coin for coin, _ in self._cache}

    def save(self) -> None:
        if not self.cache_path or not self._dirty:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        with tmp.open("w", newline="") as fh:
            writer = csv.writer(fh)
            for (coin, hour), price in sorted(self._cache.items()):
                writer.writerow([coin, hour, f"{price:.10g}"])
        tmp.replace(self.cache_path)
        self._dirty = False

    def _fetch(self, coin: str, interval: str, start_ms: int, end_ms: int) -> int:
        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms},
        }
        span = {"1h": 1, "4h": 4, "1d": 24}[interval]
        for attempt in range(3):
            try:
                resp = self.session.post(HL_INFO_URL, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                candles = resp.json() or []
                for candle in candles:
                    close = float(candle["c"])
                    first = int(candle["t"]) // 3_600_000

                    for offset in range(span):
                        self._cache.setdefault((coin, first + offset), close)
                if candles:
                    self._dirty = True
                return len(candles)
            except requests.RequestException, ValueError, KeyError, TypeError:
                time.sleep(1.0 * (attempt + 1))
        return 0

    def load_history(self, coin: str) -> None:

        now_ms = int(time.time() * 1000)
        deep_start = now_ms - self.deep_days * 86_400_000
        hourly_start = now_ms - HOURLY_HORIZON_DAYS * 86_400_000
        self._page(coin, "4h", deep_start, hourly_start, 4)
        self._page(coin, "1h", hourly_start, now_ms, 1)
        self._loaded.add(coin)
        self.save()

    def _page(self, coin: str, interval: str, since_ms: int, until_ms: int, span_h: int) -> None:
        window_ms = self.MAX_CANDLES_PER_REQUEST * span_h * 3_600_000
        cursor = until_ms
        while cursor > since_ms:
            start = max(since_ms, cursor - window_ms)
            if self._fetch(coin, interval, start, cursor) == 0:
                break
            cursor = start - 1

    def price_at(self, coin: str, ts: datetime) -> float | None:
        coin = spot_symbol(coin)
        hour = int(ts.timestamp()) // 3600
        if (coin, hour) in self._cache:
            return self._cache[(coin, hour)]
        if coin not in self._loaded:
            self.load_history(coin)
            if (coin, hour) in self._cache:
                return self._cache[(coin, hour)]

        earlier = [h for (c, h) in self._cache if c == coin and h <= hour]
        if not earlier:
            return None
        return self._cache[(coin, max(earlier))]

    def coverage(self, coin: str) -> tuple[datetime, datetime] | None:

        coin = spot_symbol(coin)
        hours = [h for (c, h) in self._cache if c == coin]
        if not hours:
            return None
        return (
            datetime.fromtimestamp(min(hours) * 3600, tz=UTC),
            datetime.fromtimestamp(max(hours) * 3600, tz=UTC),
        )


class StaticSpot:
    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = {spot_symbol(k): v for k, v in prices.items()}

    def price_at(self, coin: str, ts: datetime) -> float | None:
        return self._prices.get(spot_symbol(coin))


class W3ESpot:
    def __init__(self, *_args, **_kwargs) -> None:
        raise NotImplementedError(
            "W3ESpot is not wired up. It requires W3E host/port/database credentials "
            "and a verified candle schema. Use HyperliquidSpot until then."
        )

    def price_at(self, coin: str, ts: datetime) -> float | None:
        raise NotImplementedError
