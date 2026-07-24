
from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

from rewind.recorder import Recorder, shutdown  # noqa: E402
from support_agent import db  # noqa: E402
from support_agent.agent import run_session  # noqa: E402

_FIXTURES = Path(__file__).parent / "fixtures" / "queries.json"


def _arm(scenario: str, on: bool) -> None:
    url = os.getenv("SHIPPING_SERVICE_URL", "http://localhost:8900")
    try:
        httpx.post(f"{url}/_chaos/{scenario}", params={"on": on}, timeout=5.0)
    except httpx.HTTPError:
        pass  

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate agent traffic into SigNoz.")
    parser.add_argument("-n", "--count", type=int, default=30)
    parser.add_argument("--failure-rate", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=7, help="RNG seed for reproducibility.")
    parser.add_argument("--no-reseed-db", action="store_true", help="Skip re-seeding SQLite.")
    args = parser.parse_args()

    if not args.no_reseed_db:
        db.seed()

    queries = json.loads(_FIXTURES.read_text())
    rng = random.Random(args.seed)

    recorder = Recorder()
    ok = 0
    failed_armed = 0
    trace_ids: list[str] = []

    for i in range(args.count):
        query = rng.choice(queries)
        inject = rng.random() < args.failure_rate
        chaos = "malformed_shipping" if inject else None
        if inject:
            _arm("malformed_shipping", True)
            failed_armed += 1
        try:
            result = run_session(query, recorder=recorder, chaos=chaos)
            trace_ids.append(result.trace_id)
            ok += 1
            marker = " [chaos]" if inject else ""
            print(f"[{i + 1:>2}/{args.count}] {result.trace_id}{marker}  {query[:52]}")
        except Exception as exc:  # noqa: BLE001 - keep the run going
            print(f"[{i + 1:>2}/{args.count}] ERROR {type(exc).__name__}: {exc}")
        finally:
            if inject:
                _arm("malformed_shipping", False)
        time.sleep(0.3)  

    shutdown()
    print(
        f"\ndone: {ok}/{args.count} sessions, {failed_armed} with injected chaos. "
        f"Traces are in SigNoz (service=support-agent)."
    )
    if trace_ids:
        print(f"example trace_id for replay: {trace_ids[0]}")


if __name__ == "__main__":
    main()
