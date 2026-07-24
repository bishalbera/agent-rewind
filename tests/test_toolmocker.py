
from __future__ import annotations

from contextlib import contextmanager

from rewind import conventions as c
from rewind.recorder import ToolSpan
from rewind.replay import MockToolRunner
from rewind.trace_store import RecordedSession, RecordedTool


class FakeSession:
    """Stands in for recorder.Session: yields a ToolSpan, records what it saw."""

    def __init__(self) -> None:
        self._step = 0
        self.spans: list[tuple[str, ToolSpan]] = []

    @property
    def current_step(self) -> int:
        return self._step

    @contextmanager
    def tool_call(self, *, name, args, call_id=None):
        span = ToolSpan()
        try:
            yield span
        finally:
            self.spans.append((name, span))
            self._step += 1


def _recorded():
    return RecordedSession(
        trace_id="t", session_id="s", user_query="q", system="sys",
        model="claude-opus-4-8", temperature=None, max_tokens=1024,
        tools=[
            RecordedTool(1, "lookup_order", {"order_id": "ORD-1002"},
                         {"found": True, "item": "USB-C hub"}),
            RecordedTool(2, "get_shipping_status", {"order_id": "ORD-1002"},
                         {"error": "malformed"}, error="JSONDecodeError: malformed"),
        ],
    )


def test_exact_match_serves_recorded_response():
    runner = MockToolRunner(_recorded())
    sess = FakeSession()
    resp, is_err = runner.run(sess, "lookup_order", {"order_id": "ORD-1002"}, "tu1")
    assert resp == {"found": True, "item": "USB-C hub"}
    assert is_err is False
    assert runner.matches[-1].status == "matched"
    assert sess.spans[-1][1].divergence is None


def test_args_mismatch_still_serves_recorded_but_flags_divergence():
    runner = MockToolRunner(_recorded())
    sess = FakeSession()
    resp, _ = runner.run(sess, "lookup_order", {"order_id": "ORD-9999"}, "tu1")
    # served from the recording regardless of the different args
    assert resp == {"found": True, "item": "USB-C hub"}
    assert runner.matches[-1].status == "args_mismatch"
    assert sess.spans[-1][1].divergence == c.DIVERGENCE_ARGS
    assert len(runner.divergences) == 1


def test_recorded_error_propagates_as_is_error():
    runner = MockToolRunner(_recorded())
    sess = FakeSession()
    resp, is_err = runner.run(sess, "get_shipping_status", {"order_id": "ORD-1002"}, "tu2")
    assert is_err is True
    assert resp == {"error": "malformed"}
    assert runner.matches[-1].status == "matched"


def test_unrecorded_tool_serves_synthetic_no_data():
    runner = MockToolRunner(_recorded())
    sess = FakeSession()
    resp, is_err = runner.run(sess, "issue_refund", {"order_id": "ORD-1002", "amount": 45.5}, "tu")
    assert resp.get("__rewind_no_data__") is True
    assert is_err is False
    assert runner.matches[-1].status == "unmatched"
    assert sess.spans[-1][1].divergence == c.DIVERGENCE_UNMATCHED


def test_second_call_of_tool_beyond_recording_is_unmatched():
    runner = MockToolRunner(_recorded())
    sess = FakeSession()
    runner.run(sess, "lookup_order", {"order_id": "ORD-1002"}, "a")  # 1st -> matched
    resp, _ = runner.run(sess, "lookup_order", {"order_id": "ORD-1002"}, "b")  # 2nd -> unrecorded
    assert resp.get("__rewind_no_data__") is True
    assert [m.status for m in runner.matches] == ["matched", "unmatched"]


def test_matching_is_order_independent():
    # Replay calls the tools in the opposite order from the recording; each still
    # gets its own recorded response (turn/order-robust).
    runner = MockToolRunner(_recorded())
    sess = FakeSession()
    r1, _ = runner.run(sess, "get_shipping_status", {"order_id": "ORD-1002"}, "a")
    r2, _ = runner.run(sess, "lookup_order", {"order_id": "ORD-1002"}, "b")
    assert r1 == {"error": "malformed"}
    assert r2 == {"found": True, "item": "USB-C hub"}
    assert [m.status for m in runner.matches] == ["matched", "matched"]


def test_full_clean_replay_all_matched():
    runner = MockToolRunner(_recorded())
    sess = FakeSession()
    runner.run(sess, "lookup_order", {"order_id": "ORD-1002"}, "a")
    runner.run(sess, "get_shipping_status", {"order_id": "ORD-1002"}, "b")
    assert [m.status for m in runner.matches] == ["matched", "matched"]
    assert runner.divergences == []
