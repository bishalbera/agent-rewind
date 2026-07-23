
from __future__ import annotations

import os
import time

from fastapi import FastAPI, Response

app = FastAPI(title="mock-shipping")

# Deterministic per-order status, keyed to the seed data in db.py.
_STATUS: dict[str, dict] = {
    "ORD-1001": {"status": "delivered", "carrier": "UPS", "eta": None, "tracking": "1Z-1001"},
    "ORD-1002": {"status": "in_transit", "carrier": "FedEx", "eta": "2d", "tracking": "FX-1002"},
    "ORD-1003": {"status": "processing", "carrier": None, "eta": None, "tracking": None},
    "ORD-1004": {"status": "delivered", "carrier": "USPS", "eta": None, "tracking": "US-1004"},
    "ORD-1005": {"status": "in_transit", "carrier": "DHL", "eta": "1 day", "tracking": "DH-1005"},
    "ORD-1006": {"status": "cancelled", "carrier": None, "eta": None, "tracking": None},
    "ORD-1007": {"status": "delivered", "carrier": "UPS", "eta": None, "tracking": "1Z-1007"},
    "ORD-1008": {"status": "delivered", "carrier": "USPS", "eta": None, "tracking": "US-1008"},
}

# In-process toggle the chaos controller flips (also seeded from REWIND_CHAOS).
_armed: set[str] = set(s.strip() for s in os.getenv("REWIND_CHAOS", "").split(",") if s.strip())


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/_chaos/{scenario}")
def arm(scenario: str, on: bool = True) -> dict:
    """Arm/disarm a chaos scenario at runtime (used by the traffic generator)."""
    if on:
        _armed.add(scenario)
    else:
        _armed.discard(scenario)
    return {"armed": sorted(_armed)}


@app.get("/shipping/{order_id}")
def shipping(order_id: str) -> Response:
    import json

    status = _STATUS.get(order_id)
    if status is None:
        return Response(
            content=json.dumps({"error": "unknown order"}),
            media_type="application/json",
            status_code=404,
        )

    # Chaos scenario 1: return malformed JSON so the agent misparses.
    if "malformed_shipping" in _armed:
        broken = json.dumps(status)[:-2]  # truncate the closing braces
        return Response(content=broken, media_type="application/json")

    return Response(content=json.dumps(status), media_type="application/json")


def _maybe_delay() -> None:
    # Reserved for a shipping-side latency scenario; lookup_order owns the
    # primary timeout scenario (see tools.py / chaos/).
    if "slow_shipping" in _armed:
        time.sleep(5)
