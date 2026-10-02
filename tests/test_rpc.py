from __future__ import annotations

import pytest

from option_intel.chains import ETHEREUM, HYPEREVM
from option_intel.rpc import Rpc, RpcError, _is_rate_limited


class FakeRpc(Rpc):
    def __init__(self, chain, reply):
        super().__init__(chain)
        self._reply = reply
        self.calls = 0

    def _post(self, payload):
        self.calls += 1
        return self._reply(payload)


def ok(results):
    def reply(payload):
        return [{"jsonrpc": "2.0", "id": i, "result": r} for i, r in enumerate(results)]

    return reply


def with_error(index, message="Archive requests require a personal token"):
    def reply(payload):
        out = []
        for i, _ in enumerate(payload):
            if i == index:
                out.append({"jsonrpc": "2.0", "id": i, "error": {"code": -32602, "message": message}})
            else:
                out.append({"jsonrpc": "2.0", "id": i, "result": []})
        return out

    return reply


def test_a_batch_returns_results_in_order():
    rpc = FakeRpc(HYPEREVM, ok(["a", "b", "c"]))
    assert rpc.batch([("eth_call", [])] * 3) == ["a", "b", "c"]


def test_by_default_an_item_error_is_a_null_because_a_revert_is_an_answer():

    rpc = FakeRpc(HYPEREVM, with_error(1, "execution reverted"))
    assert rpc.batch([("eth_call", [])] * 3) == [[], None, []]


def test_strict_raises_so_an_unreadable_range_is_not_an_empty_one():
    rpc = FakeRpc(ETHEREUM, with_error(1))
    with pytest.raises(RpcError, match="Archive"):
        rpc.batch([("eth_getLogs", [])] * 3, strict=True)


def test_the_error_names_the_method_that_failed():
    rpc = FakeRpc(ETHEREUM, with_error(0))
    with pytest.raises(RpcError, match="eth_getLogs"):
        rpc.batch([("eth_getLogs", [])], strict=True)


def test_an_empty_batch_costs_no_request():
    rpc = FakeRpc(HYPEREVM, ok([]))
    assert rpc.batch([]) == []
    assert rpc.calls == 0


def test_a_truncated_batch_is_refused_rather_than_silently_short():
    def short(payload):
        return [{"jsonrpc": "2.0", "id": 0, "result": "only one"}]

    rpc = FakeRpc(HYPEREVM, short)
    with pytest.raises(RpcError, match="truncated"):
        rpc.batch([("eth_call", [])] * 3)


def test_batch_size_follows_the_endpoint_in_use():

    rpc = Rpc(HYPEREVM)
    assert rpc.batch_size == rpc.endpoint.max_batch
    assert {e.max_batch for e in HYPEREVM.rpcs} != {e.max_batch for e in ETHEREUM.rpcs}
    assert min(e.max_batch for e in HYPEREVM.rpcs) == 20


def test_throttling_is_recognised_inside_a_two_hundred_response():
    assert _is_rate_limited({"error": {"code": -32005, "message": "rate limited"}})
    assert _is_rate_limited([{"error": {"code": -32005}}, {"result": 1}])
    assert _is_rate_limited({"error": {"message": "Your app has exceeded its rate limit"}})
    assert not _is_rate_limited({"result": "0x1"})
    assert not _is_rate_limited([{"result": "0x1"}])


def test_a_revert_is_not_mistaken_for_throttling():
    assert not _is_rate_limited({"error": {"code": 3, "message": "execution reverted"}})
