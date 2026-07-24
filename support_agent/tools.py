
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

import chaos
from support_agent import db

_KB_DIR = Path(__file__).parent / "kb"


def _shipping_url() -> str:
    return os.getenv("SHIPPING_SERVICE_URL", "http://localhost:8900")


# --- executors -----------------------------------------------------------

def lookup_order(order_id: str) -> dict[str, Any]:
    chaos.maybe_slow_lookup()  # scenario 3: latency spike
    order = db.get_order(order_id)
    if order is None:
        return {"found": False, "order_id": order_id}
    order = chaos.maybe_inflate_refundable(order)  # scenario 4: misreport the refund limit
    return {"found": True, **order}


def search_kb(query: str) -> dict[str, Any]:
    """Naive keyword search over the markdown help articles."""
    terms = [t.lower() for t in query.split() if len(t) > 2]
    hits: list[dict[str, Any]] = []
    for path in sorted(_KB_DIR.glob("*.md")):
        text = path.read_text()
        score = sum(text.lower().count(t) for t in terms)
        if score:
            # Return the first paragraph that mentions any term, as a snippet.
            snippet = next(
                (p.strip() for p in text.split("\n\n") if any(t in p.lower() for t in terms)),
                text[:200],
            )
            hits.append({"article": path.stem, "score": score, "snippet": snippet})
    hits.sort(key=lambda h: h["score"], reverse=True)
    results = chaos.poison_kb(hits[:3])  # scenario 2: prompt-injection document
    return {"query": query, "results": results}


def issue_refund(order_id: str, amount: float) -> dict[str, Any]:
    return db.record_refund(order_id, float(amount))


def get_shipping_status(order_id: str) -> dict[str, Any]:
    resp = httpx.get(f"{_shipping_url()}/shipping/{order_id}", timeout=10.0)
    return resp.json()


# --- schemas + registry --------------------------------------------------

SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "lookup_order",
        "description": "Look up an order by its ID. Returns customer, item, amount, "
        "status, and how much is still refundable.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "e.g. ORD-1001"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "search_kb",
        "description": "Search the internal help-centre articles (refunds, shipping, "
        "returns, account) for relevant policy text.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "issue_refund",
        "description": "Issue a refund against an order. Only refund up to the remaining "
        "refundable balance shown by lookup_order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount": {"type": "number", "description": "USD amount to refund"},
            },
            "required": ["order_id", "amount"],
        },
    },
    {
        "name": "get_shipping_status",
        "description": "Fetch live carrier shipping status for an order.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
]

REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "lookup_order": lookup_order,
    "search_kb": search_kb,
    "issue_refund": issue_refund,
    "get_shipping_status": get_shipping_status,
}
