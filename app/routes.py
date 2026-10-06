

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent import handle_message
from app.database import get_db

router = APIRouter()




class InboundMessage(BaseModel):
    message_id: str
    rider_id: str
    text: str
    received_at: str = ""   # optional, vendor sends it but we default to empty


class MessageReply(BaseModel):
    reply: str



@router.post("/messages", response_model=MessageReply)
async def receive_message(msg: InboundMessage):
    """
    The messaging vendor POSTs each WhatsApp message here.
    We return {"reply": "..."} which the vendor forwards back to the rider.
    """
    async with await get_db() as db:
        reply = await handle_message(
            db=db,
            message_id=msg.message_id,
            rider_id=msg.rider_id,
            text=msg.text,
            received_at=msg.received_at,
        )
    return {"reply": reply}



@router.get("/trace/{rider_id}")
async def get_trace(rider_id: str):
    """
    Return every step the agent took for this rider, oldest first.
    Ops / engineers can use this to replay what happened.
    """
    async with await get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT step, detail, created_at FROM trace "
            "WHERE rider_id = ? ORDER BY created_at ASC",
            (rider_id,),
        )
    if not rows:
        raise HTTPException(status_code=404, detail="No trace found for this rider")
    return [dict(r) for r in rows]



@router.get("/ops/pending")
async def get_pending():
    """
    Return everything waiting for ops: escalated disputes and approvals.
    """
    async with await get_db() as db:
        rows = await db.execute_fetchall(
            """
            SELECT
                q.id         AS queue_id,
                q.dispute_id,
                q.rider_id,
                q.amount,
                q.reason,
                q.status,
                q.created_at,
                d.order_id,
                d.issue,
                d.expected_amt,
                d.paid_amt
            FROM ops_queue q
            JOIN disputes d ON d.id = q.dispute_id
            WHERE q.status = 'PENDING'
            ORDER BY q.created_at ASC
            """,
        )
    return [dict(r) for r in rows]


class OpsDecision(BaseModel):
    reviewed_by: str = "ops"


@router.post("/ops/{queue_id}/approve")
async def approve(queue_id: int, body: OpsDecision):
    """Ops approves a pending escalation (triggers payment)."""
    async with await get_db() as db:
        row = await db.execute_fetchall(
            "SELECT * FROM ops_queue WHERE id=? AND status='PENDING'", (queue_id,)
        )
        if not row:
            raise HTTPException(status_code=404, detail="Queue item not found or already resolved")
        item = dict(row[0])

        # Issue the payment via PaySwift
        from app import payswift
        try:
            txn = await payswift.issue_payment(
                item["rider_id"], item["dispute_id"], item["amount"]
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"PaySwift error: {e}")

        await db.execute(
            "UPDATE ops_queue SET status='APPROVED', reviewed_by=? WHERE id=?",
            (body.reviewed_by, queue_id),
        )
        await db.execute(
            "UPDATE disputes SET status='AUTO_PAID' WHERE id=?", (item["dispute_id"],)
        )
        await db.commit()
    return {"status": "approved", "transaction": txn}


@router.post("/ops/{queue_id}/reject")
async def reject(queue_id: int, body: OpsDecision):
    """Ops rejects a pending escalation."""
    async with await get_db() as db:
        updated = await db.execute(
            "UPDATE ops_queue SET status='REJECTED', reviewed_by=? "
            "WHERE id=? AND status='PENDING'",
            (body.reviewed_by, queue_id),
        )
        await db.commit()
        if updated.rowcount == 0:
            raise HTTPException(status_code=404, detail="Queue item not found or already resolved")
    return {"status": "rejected"}
