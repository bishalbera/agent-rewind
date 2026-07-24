
from __future__ import annotations

import json
from pathlib import Path

from rewind.trace_store import parse_api_payload, reconstruct

_FIXTURE = Path(__file__).parent / "fixtures" / "trace_api.json"


def _recorded():
    payload = json.loads(_FIXTURE.read_text())
    spans = parse_api_payload(payload)
    return spans, reconstruct(spans, "traceFIX")


def test_parses_all_spans():
    spans, _ = _recorded()
    assert len(spans) == 5
    # parallel TagsKeys/TagsValues merged into an attribute dict
    tool = next(s for s in spans if s.name == "execute_tool lookup_order")
    assert tool.attributes["gen_ai.tool.name"] == "lookup_order"


def test_reconstructs_session_metadata():
    _, rec = _recorded()
    assert rec.trace_id == "traceFIX"
    assert rec.session_id == "sess-fix"
    assert rec.user_query == "Where is ORD-1002?"
    assert rec.system == "You are support."
    assert rec.model == "claude-opus-4-8"
    assert rec.max_tokens == 1024
    assert rec.temperature is None


def test_tools_ordered_by_step_index():
    _, rec = _recorded()
    assert [t.step_index for t in rec.tools] == [1, 2]
    assert [t.name for t in rec.tools] == ["lookup_order", "get_shipping_status"]


def test_tool_args_and_response_decoded_from_json():
    _, rec = _recorded()
    lookup = rec.tools_by_step()[1]
    assert lookup.args == {"order_id": "ORD-1002"}
    assert lookup.response["item"] == "USB-C hub"


def test_recorded_tool_error_preserved():
    _, rec = _recorded()
    shipping = rec.tools_by_step()[2]
    assert shipping.error == "JSONDecodeError: malformed"
    assert shipping.response["error"].startswith("JSONDecodeError")
