from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path

import requests

DEFAULT_CACHE = Path("data/cache/minute-prices.csv")


MARKETS: dict[str, tuple[str, str, str]] = {
    "HYPE": ("fapi.binance.com", "/fapi/v1/klines", "HYPEUSDT"),
    "ETH": ("api.binance.com", "/api/v3/klines", "ETHUSDT"),
    "BTC": ("api.binance.com", "/api/v3/klines", "BTCUSDT"),
    "SOL": ("api.binance.com", "/api/v3/klines", "SOLUSDT"),
    "XRP": ("api.binance.com", "/api/v3/klines", "XRPUSDT"),
    "ZEC": ("api.binance.com", "/api/v3/klines", "ZECUSDT"),
    "PUMP": ("api.binance.com", "/api/v3/klines", "PUMPUSDT"),
}

UNCOVERED = frozenset({"PURR"})


class MinutePrices:
    def __init__(self, cache_path: Path | None = DEFAULT_CACHE, timeout: float = 25.0) -> None:
        self.cache_path = cache_path
        self.timeout = timeout
        self.session = requests.Session()
        self._cache: dict[tuple[str, int], float] = {}
        self._dirty = False
        self._read()

    def _read(self) -> None:
        if not self.cache_path or not self.cache_path.exists():
            return
        with self.cache_path.open() as fh:
            for row in csv.reader(fh):
                if len(row) == 3:
                    try:
                        self._cache[(row[0], int(row[1]))] = float(row[2])
                    except ValueError:
                        continue

    def save(self) -> None:
        if not self.cache_path or not self._dirty:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            for (coin, minute), price in sorted(self._cache.items()):
                writer.writerow([coin, minute, f"{price:.10g}"])
        self._dirty = False

    def _fetch(self, coin: str, start_minute: int, count: int) -> None:

        market = MARKETS.get(coin.upper())
        if not market:
            return
        host, path, symbol = market
        start_ms = start_minute * 60_000
        params = {
            "symbol": symbol,
            "interval": "1m",
            "startTime": start_ms,
            "endTime": start_ms + count * 60_000,
            "limit": min(count + 1, 1000),
        }
        for attempt in range(3):
            try:
                resp = self.session.get(f"https://{host}{path}", params=params, timeout=self.timeout)
                if resp.status_code == 429:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                resp.raise_for_status()
                for candle in resp.json() or []:
                    self._cache[(coin.upper(), int(candle[0]) // 60_000)] = float(candle[4])
                self._dirty = True
                return
            except requests.RequestException, ValueError, KeyError, IndexError:
                time.sleep(1.0 * (attempt + 1))

    def warm(self, wanted: list[tuple[str, datetime]], *, pause: float = 0.2, progress=None) -> int:

        span = 1000 * 60
        buckets: dict[tuple[str, int], None] = {}
        for coin, ts in wanted:
            coin = coin.upper()
            if coin in MARKETS:
                buckets[(coin, int(ts.timestamp()) // span)] = None

        done = 0
        for coin, bucket in sorted(buckets):
            start_minute = (bucket * span) // 60
            if all((coin, start_minute + m) in self._cache for m in (0, 500, 999)):
                continue
            self._fetch(coin, start_minute, 999)
            done += 1
            if progress and done % 50 == 0:
                progress(done, len(buckets))
            time.sleep(pause)
        self.save()
        return done

    def price_at(self, coin: str, ts: datetime, *, window: int = 30) -> float | None:

        coin = coin.upper()
        if coin in UNCOVERED or coin not in MARKETS:
            return None
        minute = int(ts.timestamp()) // 60
        if (coin, minute) not in self._cache:
            self._fetch(coin, minute - window, window * 2)
        for offset in range(window + 1):
            for candidate in ((minute - offset), (minute + offset)) if offset else (minute,):
                hit = self._cache.get((coin, candidate))
                if hit and hit > 0:
                    return hit
        return None
