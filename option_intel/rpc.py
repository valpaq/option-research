from __future__ import annotations

import itertools
import time
from collections.abc import Iterable, Iterator
from typing import Any

import requests

from option_intel.chains import Chain, Endpoint


class RpcError(RuntimeError):
    pass


class Rpc:
    def __init__(
        self,
        chain: Chain,
        timeout: float = 60.0,
        retries: int = 8,
        batch_size: int | None = None,
        throttle: float = 0.0,
        base_backoff: float = 1.0,
        max_backoff: float = 30.0,
        rate_limit_retries: int = 24,
        rate_limit_wait: float = 0.2,
        max_rate_limit_wait: float = 2.0,
    ) -> None:
        self.chain = chain
        self.timeout = timeout
        self.retries = retries
        self.batch_override = batch_size
        self.throttle = throttle
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.rate_limit_retries = rate_limit_retries
        self.rate_limit_wait = rate_limit_wait
        self.max_rate_limit_wait = max_rate_limit_wait
        self.session = requests.Session()
        self._endpoints = list(chain.rpcs)
        self._cursor = 0
        self._counter = itertools.count(1)
        self._last_call_at = 0.0

    @property
    def endpoint(self) -> Endpoint:
        return self._endpoints[self._cursor % len(self._endpoints)]

    @property
    def batch_size(self) -> int:

        limit = self.endpoint.max_batch
        return min(self.batch_override, limit) if self.batch_override else limit

    def _advance(self) -> None:
        self._cursor = (self._cursor + 1) % len(self._endpoints)

    def _respect_rps(self, endpoint: Endpoint) -> None:
        if endpoint.max_rps:
            gap = 1.0 / endpoint.max_rps
            wait = gap - (time.monotonic() - self._last_call_at)
            if wait > 0:
                time.sleep(wait)

    def _post(self, payload: Any) -> Any:
        last: Exception | str | None = None
        errors = 0
        limits = 0
        while errors < self.retries and limits < self.rate_limit_retries:
            endpoint = self.endpoint
            if self.throttle:
                time.sleep(self.throttle)
            self._respect_rps(endpoint)
            try:
                self._last_call_at = time.monotonic()
                resp = self.session.post(endpoint.url, json=payload, timeout=self.timeout)
                if resp.status_code in (429, 503):
                    limits += 1
                    self._advance()
                    time.sleep(self._rate_limit_wait(limits))
                    continue
                resp.raise_for_status()
                body = resp.json()

                if _is_rate_limited(body):
                    last = f"rate limited by {endpoint.url}"
                    limits += 1
                    self._advance()
                    time.sleep(self._rate_limit_wait(limits))
                    continue
                return body
            except (requests.RequestException, ValueError) as exc:
                last = exc
                errors += 1
                self._advance()
                time.sleep(self._backoff(errors))
        raise RpcError(f"{self.chain.key}: gave up after {errors} errors and {limits} rate limits: {last}")

    def _backoff(self, attempt: int) -> float:
        return min(self.max_backoff, self.base_backoff * (2**attempt))

    def _rate_limit_wait(self, hits: int) -> float:

        return min(self.max_rate_limit_wait, self.rate_limit_wait * hits)

    def call(self, method: str, params: list[Any]) -> Any:
        body = self._post({"jsonrpc": "2.0", "id": next(self._counter), "method": method, "params": params})
        if isinstance(body, dict) and "error" in body:
            raise RpcError(f"{method}: {body['error']}")
        return body.get("result") if isinstance(body, dict) else None

    def batch(
        self,
        requests_: list[tuple[str, list[Any]]],
        *,
        strict: bool = False,
    ) -> list[Any]:

        if not requests_:
            return []
        out: list[Any] = []
        index = 0
        while index < len(requests_):
            size = max(1, self.batch_size)
            chunk = requests_[index : index + size]
            payload = [
                {"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(chunk)
            ]
            body = self._post(payload)
            if isinstance(body, dict) and "error" in body:
                if len(chunk) > 1:
                    self._advance()
                    self.batch_override = max(1, len(chunk) // 2)
                    continue
                raise RpcError(f"single request rejected: {body['error']}")
            if not isinstance(body, list):
                raise RpcError(f"expected a batch response, got {type(body).__name__}")
            by_id = {item.get("id"): item for item in body}
            if len(by_id) != len(chunk):
                raise RpcError(f"batch truncated: sent {len(chunk)}, got {len(by_id)} ids")
            for i in range(len(chunk)):
                item = by_id.get(i) or {}
                if "error" in item:
                    if strict:
                        raise RpcError(f"{chunk[i][0]}: {item['error']}")
                    out.append(None)
                else:
                    out.append(item.get("result"))
            index += len(chunk)
        return out

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def receipts(self, tx_hashes: Iterable[str]) -> Iterator[dict]:
        hashes = list(tx_hashes)
        index = 0
        while index < len(hashes):
            chunk = hashes[index : index + max(1, self.batch_size)]
            for result in self.batch([("eth_getTransactionReceipt", [h]) for h in chunk]):
                if result:
                    yield result
            index += len(chunk)

    def get_logs(
        self,
        address: str | list[str] | None,
        from_block: int,
        to_block: int,
        topics: list[Any] | None = None,
    ) -> Iterator[dict]:

        span = self.chain.max_log_range
        start = from_block
        while start <= to_block:
            end = min(start + span - 1, to_block)
            flt: dict[str, Any] = {"fromBlock": hex(start), "toBlock": hex(end)}
            if address:
                flt["address"] = address
            if topics:
                flt["topics"] = topics
            yield from (self.call("eth_getLogs", [flt]) or [])
            start = end + 1


def _is_rate_limited(body: Any) -> bool:

    if isinstance(body, dict):
        errors = [body.get("error")]
    elif isinstance(body, list):
        errors = [item.get("error") for item in body if isinstance(item, dict)]
    else:
        return False
    for err in errors:
        if not isinstance(err, dict):
            continue
        if err.get("code") == -32005:
            return True
        if "rate limit" in str(err.get("message", "")).lower():
            return True
    return False
