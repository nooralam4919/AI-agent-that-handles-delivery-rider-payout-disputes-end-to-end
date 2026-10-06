# QuickDrop – Rider Payout Dispute Agent

An AI agent that handles delivery-rider payout disputes end-to-end via WhatsApp-style messaging.

---

## How to run

**1. Clone and set up**
```bash
git clone <your-repo>
cd quickdrop
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY (Groq free tier works fine)
```

**2. Run with Docker (recommended)**
```bash
docker compose up
```
The API is available at `http://localhost:8000`.  
The PaySwift sandbox runs at `http://localhost:8080`.

**3. Run locally (without Docker)**
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**4. Run evals**
```bash
python eval.py                        # tests against http://localhost:8000
python eval.py http://other-host:8000 # or a custom URL
```

---

## API

| Method | Path | What it does |
|--------|------|--------------|
| `POST` | `/messages` | Rider sends a message; agent replies |
| `GET`  | `/trace/{rider_id}` | Every step the agent took for this rider |
| `GET`  | `/ops/pending` | Disputes waiting for ops approval |
| `POST` | `/ops/{id}/approve` | Ops approves and triggers payment |
| `POST` | `/ops/{id}/reject` | Ops rejects a dispute |
| `GET`  | `/health` | Health check |

**POST /messages example:**
```json
{
  "message_id": "wamid.KAZ5TCF7RARUVH",
  "rider_id": "R003",
  "text": "Bhai order T926334 ka surge nahi mila, 20 tarikh wala",
  "received_at": "2026-09-22T09:05:00+05:30"
}
```
Response:
```json
{ "reply": "Hi! I checked order T926334 and found a discrepancy of ₹45.00 ..." }
```

---

## Agent design

### What the model decides
- Extract the **order ID, issue summary, and amounts** from the rider's free-text message (Hindi/English mix)
- Generate a **friendly reply** for the rider (auto-pay confirmation, escalation notice, or clarification request)

### What the code decides
- Whether to **auto-pay** (≤ ₹200, first time today for that rider) or **escalate to ops**
- **Idempotency** — same `message_id` always returns the same reply
- **Conversation history** — last 5 messages from the rider are passed to the LLM for context

### Tools the agent uses
| Tool | What it does |
|------|-------------|
| `payswift.get_payout()` | Fetches what was actually paid to the rider for an order |
| `payswift.issue_payment()` | Sends a corrective payment (auto-pay path only) |
| SQLite DB | Stores messages, disputes, ops queue, and trace log |

### Flow (step by step)
```
Rider message arrives
    ↓
1. Idempotency check (already processed? return cached reply)
2. Save message, log trace
3. LLM extracts: order_id, issue, expected/paid amounts
4. Missing order_id? → ask rider for it
5. Call PaySwift to get actual paid amount
6. Calculate difference = expected − paid
7. Decide:
     difference ≤ 0          → NO_DISCREPANCY, explain to rider
     ≤ ₹200 & first today    → AUTO_PAY via PaySwift, confirm to rider
     else                    → ESCALATE, add to ops queue, notify rider
8. LLM generates friendly reply text
9. Save reply, return to vendor
```

---

## Assumptions

- The PaySwift sandbox is the source of truth for what was paid (per the Finance note)
- If PaySwift has an `expected_amount` field, we use it; otherwise we trust the rider's claim
- Auto-pay limit is ₹200 (configurable via `AUTO_PAY_LIMIT` env var)
- Conversation memory is in-memory per session; trace log in SQLite is the permanent record
- The ops UI is the `/ops/pending` endpoint + approve/reject — a full HTML page was not built (see "What I skipped")

---

## Eval results

Run `python eval.py` after starting the service. Results are saved to `eval_results.json`.

Test cases:
1. Rider mentions order ID + missing surge → agent investigates
2. Rider gives no order ID → agent asks for it
3. Duplicate message ID → same reply returned (idempotency)
4. Rider provides explicit amounts → agent calculates difference

---

## What I skipped

- **HTML ops dashboard** — ops can use the JSON API directly (`GET /ops/pending`, `POST /ops/{id}/approve`)
- **Persistent conversation memory** — history is rebuilt from the `messages` table on each request; good enough for this scale
- **Authentication** — no API keys on the endpoints (not in scope for the assignment)
- **Postgres** — SQLite is simpler and sufficient; swap `aiosqlite` for `asyncpg` + SQLAlchemy to upgrade
