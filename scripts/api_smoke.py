"""End-to-end smoke test of the deployed API, called the way the Blazor app calls it.

    uv run python scripts/api_smoke.py https://<host>/api/v1
"""
import json
import re
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8010/api/v1"
QUESTIONS = [
    "What does Section 8-137 say about automatic increases after retirement?",
    "An employee who joined in 2015 is 62, earns $80,000 and has 11 years of service. "
    "What is their annuity if they retire now?",
    "What if they wait until 67?",
]

with httpx.Client(timeout=180) as client:
    s = client.post(f"{BASE}/sessions").json()
    sid, token = s["session_id"], s["session_token"]
    headers = {"X-Session-Token": token, "Accept": "text/event-stream"}
    for q in QUESTIONS:
        t0 = time.perf_counter()
        first = None
        raw = ""
        with client.stream("POST", f"{BASE}/sessions/{sid}/messages", json={"content": q}, headers=headers) as r:
            r.raise_for_status()
            for chunk in r.iter_text():
                raw += chunk
                if first is None and "event: token" in raw:
                    first = time.perf_counter() - t0
        events = re.findall(r"event: (\w+)\ndata: (.*)\n\n", raw)
        kinds = [k for k, _ in events]
        answer = "".join(json.loads(d).get("delta", "") for k, d in events if k == "token")
        for k, d in events:
            if k == "replace":
                answer = json.loads(d)["text"]
        done = [json.loads(d) for k, d in events if k == "done"][-1]
        cards = [t for t in done["tool_calls"] if t.get("type") == "card"]
        print(f"\nQ: {q[:70]}")
        print(f"   first word {first:.1f}s, total {time.perf_counter() - t0:.1f}s; events "
              f"{ {k: kinds.count(k) for k in dict.fromkeys(kinds)} }")
        shown = [(c.get("annual_amount"), c.get("section"), [x.get("label") for x in c.get("scenarios") or []])
                 for c in cards]
        print(f"   verified {done['verified_citations']}, guardrail {done['guardrail_passed']}, cards {shown}")
        print("   answer:", answer[:200].replace("\n", " / "))
    msgs = client.get(f"{BASE}/sessions/{sid}/messages", headers={"X-Session-Token": token}).json()
    print(f"\nreload: {len(msgs)} messages, cards on reload: "
          f"{sum(1 for m in msgs for t in (m.get('tool_calls') or []) if t.get('type') == 'card')}")
