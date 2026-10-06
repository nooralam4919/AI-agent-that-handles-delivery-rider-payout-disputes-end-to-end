

import json
import os
from datetime import datetime, date

import aiosqlite
from openai import AsyncOpenAI

from app import payswift

AUTO_PAY_LIMIT = float(os.getenv("AUTO_PAY_LIMIT", "200"))

client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY", ""),
    base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
)
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


async def log_trace(db: aiosqlite.Connection, message_id: str, rider_id: str,
                    step: str, detail: str = ""):
    await db.execute(
        "INSERT INTO trace (message_id, rider_id, step, detail) VALUES (?, ?, ?, ?)",
        (message_id, rider_id, step, detail),
    )
    await db.commit()


async def extract_intent(history: list[dict], new_message: str) -> dict:
    """
    Ask the LLM to pull structured info from the conversation.
    Returns a dict with keys: order_id, issue, expected_amount, paid_amount, missing
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are a data-extraction assistant for a delivery company. "
                "From the rider conversation, extract a JSON object with these keys:\n"
                "  order_id       – the trip/order ID (string or null)\n"
                "  issue          – one sentence describing the problem\n"
                "  expected_amount – what the rider claims they should have received (number or null)\n"
                "  paid_amount    – what the rider says they actually received (number or null)\n"
                "  missing        – list of field names still needed (e.g. [\"order_id\"])\n"
                "Reply with ONLY the JSON object, no extra text."
            ),
        }
    ]

    for turn in history:
        messages.append({"role": "user", "content": turn["text"]})
    messages.append({"role": "user", "content": new_message})

    resp = await client.chat.completions.create(
        model=MODEL, messages=messages, temperature=0, max_tokens=300
    )
    raw = resp.choices[0].message.content.strip()


    if raw.startswith("```"):
        raw = "\n".join(l for l in raw.splitlines() if not l.startswith("```")).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"order_id": None, "issue": new_message, "expected_amount": None,
                "paid_amount": None, "missing": ["order_id"]}


async def generate_reply(outcome: str, order_id: str | None,
                         difference: float, extra: str = "") -> str:
    """Ask the LLM to write a friendly rider-facing reply."""
    prompt = (
        f"You are a polite support agent for QuickDrop, a delivery company. "
        f"Write a SHORT (2-3 sentence) WhatsApp reply to a rider. Be friendly and clear.\n\n"
        f"Situation: {outcome}. Order: {order_id}. Amount: ₹{difference:.2f}. {extra}"
    )
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=150,
    )
    return resp.choices[0].message.content.strip()

async def handle_message(db: aiosqlite.Connection, message_id: str,
                         rider_id: str, text: str, received_at: str) -> str:
    """
    Process one rider message end-to-end and return the reply text.
    """

    row = await db.execute_fetchall(
        "SELECT reply FROM messages WHERE message_id = ?", (message_id,)
    )
    if row and row[0]["reply"]:
        return row[0]["reply"]

    await db.execute(
        "INSERT OR IGNORE INTO messages (message_id, rider_id, text, received_at) VALUES (?,?,?,?)",
        (message_id, rider_id, text, received_at),
    )
    await db.commit()

    await log_trace(db, message_id, rider_id, "message_received", text[:120])

    history = await db.execute_fetchall(
        "SELECT text FROM messages WHERE rider_id = ? AND reply IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 5",
        (rider_id,),
    )

    intent = await extract_intent(list(history), text)
    await log_trace(db, message_id, rider_id, "intent_extracted", json.dumps(intent))

    order_id = intent.get("order_id")
    missing  = intent.get("missing", [])

    if not order_id or "order_id" in missing:
        reply = ("Hi! Could you please share the order ID for the trip you're "
                 "asking about? It usually looks like T followed by some numbers.")
        await log_trace(db, message_id, rider_id, "asked_for_info", "missing order_id")
        await _save_reply(db, message_id, reply)
        return reply

    try:
        ps_data = await payswift.get_payout(rider_id, order_id)
        paid_amt = float(ps_data.get("paid_amount", 0))
        await log_trace(db, message_id, rider_id, "payswift_checked",
                        f"paid={paid_amt}")
    except Exception as e:
        await log_trace(db, message_id, rider_id, "payswift_error", str(e))
        reply = ("Sorry, I couldn't fetch your payout details right now. "
                 "Please try again in a minute.")
        await _save_reply(db, message_id, reply)
        return reply


    expected_amt = (
        intent.get("expected_amount")
        or float(ps_data.get("expected_amount", paid_amt))
    )
    difference = max(expected_amt - paid_amt, 0.0)

    await log_trace(db, message_id, rider_id, "amounts_calculated",
                    f"expected={expected_amt} paid={paid_amt} diff={difference}")

    cur = await db.execute(
        "INSERT INTO disputes (rider_id, order_id, issue, expected_amt, paid_amt, difference) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (rider_id, order_id, intent.get("issue", text[:200]),
         expected_amt, paid_amt, difference),
    )
    dispute_id = cur.lastrowid
    await db.commit()

    if difference <= 0:
        # No shortfall
        await db.execute(
            "UPDATE disputes SET status='NO_DISCREPANCY' WHERE id=?", (dispute_id,)
        )
        await db.commit()
        await log_trace(db, message_id, rider_id, "outcome", "no_discrepancy")
        reply = await generate_reply("no discrepancy found — the payment was correct",
                                     order_id, 0)

    elif difference <= AUTO_PAY_LIMIT and not await _paid_today(db, rider_id):
        # Auto-pay path
        try:
            txn = await payswift.issue_payment(rider_id, order_id, difference)
            await db.execute(
                "UPDATE disputes SET status='AUTO_PAID' WHERE id=?", (dispute_id,)
            )
            await db.commit()
            await log_trace(db, message_id, rider_id, "outcome",
                            f"auto_paid txn={txn.get('transaction_id')}")
            reply = await generate_reply(
                f"corrective payment of ₹{difference:.2f} has been issued",
                order_id, difference,
            )
        except Exception as e:
            await log_trace(db, message_id, rider_id, "payment_error", str(e))
            reply = ("We found a discrepancy but had trouble sending the payment. "
                     "Our team has been notified and will sort this out shortly.")

    else:
        # Escalate to ops
        reason = _escalation_reason(difference, await _paid_today(db, rider_id))
        await db.execute(
            "INSERT INTO ops_queue (dispute_id, rider_id, amount, reason) VALUES (?,?,?,?)",
            (dispute_id, rider_id, difference, reason),
        )
        await db.execute(
            "UPDATE disputes SET status='ESCALATED' WHERE id=?", (dispute_id,)
        )
        await db.commit()
        await log_trace(db, message_id, rider_id, "outcome",
                        f"escalated reason={reason}")
        reply = await generate_reply(
            "dispute has been escalated to our ops team for manual review",
            order_id, difference,
        )

    await _save_reply(db, message_id, reply)
    return reply



async def _paid_today(db: aiosqlite.Connection, rider_id: str) -> bool:
    """Return True if this rider already got an auto-payment today."""
    rows = await db.execute_fetchall(
        "SELECT id FROM disputes WHERE rider_id=? AND status='AUTO_PAID' "
        "AND date(created_at)=date('now')",
        (rider_id,),
    )
    return len(rows) > 0


async def _save_reply(db: aiosqlite.Connection, message_id: str, reply: str):
    await db.execute(
        "UPDATE messages SET reply=? WHERE message_id=?", (reply, message_id)
    )
    await db.commit()


def _escalation_reason(difference: float, paid_today: bool) -> str:
    if difference > AUTO_PAY_LIMIT and paid_today:
        return f"amount ₹{difference:.2f} exceeds limit and rider already paid today"
    if difference > AUTO_PAY_LIMIT:
        return f"amount ₹{difference:.2f} exceeds auto-pay limit of ₹{AUTO_PAY_LIMIT}"
    return "rider already received a corrective payment today"
