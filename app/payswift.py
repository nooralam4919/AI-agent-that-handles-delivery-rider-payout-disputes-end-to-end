

import os
import httpx

PAYSWIFT_URL = os.getenv("PAYSWIFT_URL", "http://localhost:8080")


async def get_payout(rider_id: str, order_id: str) -> dict:
    """
    Ask PaySwift what was actually paid to this rider for this order.
    Returns a dict like: {"order_id": "T926334", "paid_amount": 85.0, ...}
    Raises httpx.HTTPError if the request fails.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{PAYSWIFT_URL}/payouts/{order_id}",
            params={"rider_id": rider_id},
        )
        resp.raise_for_status()
        return resp.json()


async def issue_payment(rider_id: str, order_id: str, amount: float) -> dict:
    """
    Tell PaySwift to send a corrective payment to the rider.
    Returns a dict like: {"transaction_id": "TXN-999", "status": "ok"}
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{PAYSWIFT_URL}/payments",
            json={"rider_id": rider_id, "order_id": order_id, "amount": amount},
        )
        resp.raise_for_status()
        return resp.json()
