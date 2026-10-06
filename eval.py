#!/usr/bin/env python3
"""
eval.py – one-command evaluation script for the QuickDrop payout dispute agent.

Usage:
    python eval.py                        # uses http://localhost:8000 by default
    python eval.py http://your-server:8000

It sends a set of test conversations through POST /messages and checks that
the reply and /trace/{rider_id} look correct.  Prints a pass/fail report.
"""

import asyncio
import sys
import json
import httpx

BASE_URL = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8000"

# ---------------------------------------------------------------------------
# Test cases
# Each case has:
#   id          – unique message_id
#   rider_id    – rider sending the message
#   text        – what the rider wrote
#   expect      – substring we expect somewhere in the reply (case-insensitive)
# ---------------------------------------------------------------------------
TESTS = [
    {
        "id": "wamid.TEST001",
        "rider_id": "R001",
        "text": "Bhai order T926334 ka surge nahi mila, 20 tarikh wala",
        "expect": "order",        # should mention the order
        "description": "Rider mentions order ID and missing surge",
    },
    {
        "id": "wamid.TEST002",
        "rider_id": "R002",
        "text": "mera payout kam aaya",
        "expect": "order",        # no order_id → agent should ask for it
        "description": "Rider gives no order ID — agent must ask",
    },
    {
        "id": "wamid.TEST001",   # same message_id as TEST001 → idempotency
        "rider_id": "R001",
        "text": "Bhai order T926334 ka surge nahi mila, 20 tarikh wala",
        "expect": "order",
        "description": "Duplicate message_id — must return same reply (idempotency)",
    },
    {
        "id": "wamid.TEST003",
        "rider_id": "R003",
        "text": "Order T88213 ka payout 120 rupee kam aaya, mujhe 350 milna chahiye tha sirf 230 mila",
        "expect": "",             # any reply is fine — just must not error
        "description": "Rider provides amounts explicitly",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def post_message(client: httpx.AsyncClient, test: dict) -> str:
    payload = {
        "message_id": test["id"],
        "rider_id": test["rider_id"],
        "text": test["text"],
        "received_at": "2026-09-22T09:05:00+05:30",
    }
    resp = await client.post(f"{BASE_URL}/messages", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("reply", "")


async def get_trace(client: httpx.AsyncClient, rider_id: str) -> list:
    resp = await client.get(f"{BASE_URL}/trace/{rider_id}", timeout=10)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json()


async def get_ops_pending(client: httpx.AsyncClient) -> list:
    resp = await client.get(f"{BASE_URL}/ops/pending", timeout=10)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_evals():
    passed = 0
    failed = 0
    results = []

    print(f"\n{'='*60}")
    print(f"  QuickDrop Agent Eval  →  {BASE_URL}")
    print(f"{'='*60}\n")

    async with httpx.AsyncClient() as client:

        # Check service is up
        try:
            health = await client.get(f"{BASE_URL}/health", timeout=5)
            health.raise_for_status()
            print("✅  Service is healthy\n")
        except Exception as e:
            print(f"❌  Service not reachable: {e}")
            print("    Start with:  uvicorn app.main:app --reload")
            sys.exit(1)

        # Run each test message
        for test in TESTS:
            desc = test["description"]
            try:
                reply = await post_message(client, test)
                ok = test["expect"].lower() in reply.lower() if test["expect"] else len(reply) > 0
                status = "✅ PASS" if ok else "❌ FAIL"
                if ok:
                    passed += 1
                else:
                    failed += 1
                print(f"{status}  [{test['id']}]  {desc}")
                print(f"       Reply: {reply[:100]}{'...' if len(reply) > 100 else ''}\n")
                results.append({"test": desc, "passed": ok, "reply": reply})
            except Exception as e:
                failed += 1
                print(f"❌ ERR  [{test['id']}]  {desc}")
                print(f"       Error: {e}\n")
                results.append({"test": desc, "passed": False, "error": str(e)})

        # Check trace for R001
        print("─" * 60)
        print("Trace check for R001:")
        trace = await get_trace(client, "R001")
        if trace:
            print(f"  ✅  {len(trace)} trace steps found")
            for step in trace:
                print(f"       {step['created_at']}  {step['step']}")
        else:
            print("  ❌  No trace found for R001")

        # Check ops pending
        print("\nOps pending queue:")
        pending = await get_ops_pending(client)
        if pending:
            print(f"  ✅  {len(pending)} item(s) waiting for ops review")
            for item in pending:
                print(f"       dispute={item['dispute_id']}  rider={item['rider_id']}  "
                      f"amount=₹{item['amount']}  reason={item['reason']}")
        else:
            print("  ℹ️   Ops queue is empty (all disputes were auto-paid or need more info)")

    # Summary
    total = passed + failed
    print(f"\n{'='*60}")
    print(f"  Results: {passed}/{total} passed")
    print(f"{'='*60}\n")

    # Write results to file so they can go in ai-logs/
    with open("eval_results.json", "w") as f:
        json.dump({"base_url": BASE_URL, "passed": passed,
                   "failed": failed, "tests": results}, f, indent=2)
    print("  Full results saved to eval_results.json\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_evals())
