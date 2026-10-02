from __future__ import annotations

import bisect
import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

import requests

BASE_URL = "https://api.lyra.finance"
PAGE_SIZE = 1000


@runtime_checkable
class RateSource(Protocol):
    def rate_at(self, ts: datetime) -> float: ...


class FlatRate:
    def __init__(self, rate: float = 0.0) -> None:
        self.rate = rate

    def rate_at(self, ts: datetime) -> float:
        return self.rate


class DeriveRates:
    def __init__(
        self,
        field: str = "borrow_apy",
        cache_path: Path | None = None,
        timeout: float = 40.0,
    ) -> None:
        self.field = field
        self.cache_path = cache_path
        self.timeout = timeout
        self.session = requests.Session()
        self._ts: list[int] = []
        self._rates: list[float] = []
        if cache_path and cache_path.exists():
            self._load(json.loads(cache_path.read_text()))

    def _load(self, points: list[dict]) -> None:
        clean = sorted(
            (int(p["timestamp_sec"]), float(p[self.field]))
            for p in points
            if p.get("timestamp_sec") is not None and p.get(self.field) is not None
        )
        self._ts = [t for t, _ in clean]
        self._rates = [r for _, r in clean]

    def fetch(self, currency: str = "BTC", days: int = 800) -> int:

        now = int(time.time())
        collected: dict[int, dict] = {}
        page = 1
        while True:
            resp = self.session.post(
                f"{BASE_URL}/public/get_interest_rate_history",
                json={
                    "currency": currency,
                    "from_timestamp_sec": now - days * 86400,
                    "to_timestamp_sec": now,
                    "page": page,
                    "page_size": PAGE_SIZE,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            if "result" not in body:
                break
            result = body["result"]
            points = result.get("interest_rates", []) if isinstance(result, dict) else result
            if not points:
                break
            for p in points:
                collected[int(p["timestamp_sec"])] = p
            pagination = result.get("pagination") if isinstance(result, dict) else None
            if not pagination or page >= int(pagination.get("num_pages", page)):
                break
            page += 1

        self._load(list(collected.values()))
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(list(collected.values())))
        return len(self._ts)

    def rate_at(self, ts: datetime) -> float:

        if not self._ts:
            self.fetch()
        if not self._ts:
            raise RuntimeError("no interest-rate history available")
        target = int(ts.timestamp())
        i = bisect.bisect_right(self._ts, target)
        return self._rates[i - 1] if i else self._rates[0]


def forward_and_discount(spot: float, rate: float, t: float) -> tuple[float, float]:

    t = max(t, 0.0)
    return spot * math.exp(rate * t), math.exp(-rate * t)
