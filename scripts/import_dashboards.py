
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

_DASHBOARDS = Path(__file__).resolve().parent.parent / "dashboards"


def main() -> int:
    base = os.getenv("SIGNOZ_API_URL", "http://localhost:8080").rstrip("/")
    key = os.getenv("SIGNOZ_API_KEY", "")
    if not key:
        print("SIGNOZ_API_KEY is not set — create one in SigNoz (Settings -> API Keys).")
        return 1

    headers = {"SIGNOZ-API-KEY": key, "Content-Type": "application/json"}
    client = httpx.Client(base_url=base, headers=headers, timeout=15.0)

    existing = client.get("/api/v1/dashboards").json().get("data", [])
    by_title = {d.get("data", {}).get("title"): d.get("id") for d in existing}

    files = sorted(_DASHBOARDS.glob("*.json"))
    if not files:
        print(f"no dashboards found in {_DASHBOARDS}")
        return 1

    for path in files:
        import json

        payload = json.loads(path.read_text())
        title = payload.get("title")
        dash_id = by_title.get(title)
        if dash_id:
            resp = client.put(f"/api/v1/dashboards/{dash_id}", json=payload)
            action = "updated"
        else:
            resp = client.post("/api/v1/dashboards", json=payload)
            action = "created"
        ok = resp.status_code in (200, 201)
        did = resp.json().get("data", {}).get("id", dash_id) if ok else "-"
        print(f"[{'ok' if ok else resp.status_code}] {action} {path.name} -> {did}")
        if ok:
            print(f"     view: {base}/dashboard/{did}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
