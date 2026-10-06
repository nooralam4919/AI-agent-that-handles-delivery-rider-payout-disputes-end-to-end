# app/database.py
# Simple SQLite setup using aiosqlite (no ORM, just plain SQL).
# Four tables:
#   messages        – every inbound rider message (idempotency key = message_id)
#   disputes        – one row per investigated dispute
#   ops_queue       – items waiting for ops approval
#   trace           – audit log of every step the agent took

import aiosqlite

DB_PATH = "quickdrop.db"


async def get_db() -> aiosqlite.Connection:
    """Open and return a database connection."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row   # rows behave like dicts
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db():
    """Create tables if they don't exist yet. Called once on startup."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                message_id  TEXT PRIMARY KEY,
                rider_id    TEXT NOT NULL,
                text        TEXT NOT NULL,
                received_at TEXT NOT NULL,
                reply       TEXT,           -- filled in once we respond
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS disputes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                rider_id        TEXT NOT NULL,
                order_id        TEXT,
                issue           TEXT NOT NULL,
                expected_amt    REAL DEFAULT 0,
                paid_amt        REAL DEFAULT 0,
                difference      REAL DEFAULT 0,
                -- OPEN | AUTO_PAID | ESCALATED | NO_DISCREPANCY
                status          TEXT DEFAULT 'OPEN',
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS ops_queue (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                dispute_id  INTEGER NOT NULL,
                rider_id    TEXT NOT NULL,
                amount      REAL NOT NULL,
                reason      TEXT NOT NULL,
                -- PENDING | APPROVED | REJECTED
                status      TEXT DEFAULT 'PENDING',
                reviewed_by TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS trace (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id  TEXT NOT NULL,
                rider_id    TEXT NOT NULL,
                step        TEXT NOT NULL,   -- e.g. "intent_extracted"
                detail      TEXT,            -- free-text or JSON snippet
                created_at  TEXT DEFAULT (datetime('now'))
            );
        """)
        await db.commit()
