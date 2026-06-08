"""SQLite job store.

Single shared connection with WAL journal mode so concurrent reads never
block writes. All operations are serialized by aiosqlite's internal thread
queue, so no additional locking is needed.

Lifecycle: call init() at startup and close() at shutdown (FastAPI lifespan).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).parent.parent / "research.db"

_conn: aiosqlite.Connection | None = None


async def init() -> None:
    global _conn
    _conn = await aiosqlite.connect(DB_PATH)
    _conn.row_factory = aiosqlite.Row
    await _conn.execute("PRAGMA journal_mode=WAL")
    await _conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id          TEXT PRIMARY KEY,
            question    TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'running',
            iteration   INTEGER NOT NULL DEFAULT 0,
            result      TEXT,
            error       TEXT,
            log_file    TEXT,
            output_file TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)
    # Jobs still marked 'running' at startup were orphaned by a prior crash —
    # mark them failed so clients polling for them get a definitive answer.
    now = datetime.now(timezone.utc).isoformat()
    await _conn.execute(
        "UPDATE jobs SET status = 'failed', error = 'Server restarted during job execution',"
        " updated_at = ? WHERE status = 'running'",
        (now,),
    )
    await _conn.commit()


async def close() -> None:
    global _conn
    if _conn:
        await _conn.close()
        _conn = None


async def create_job(job_id: str, question: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    await _conn.execute(
        "INSERT INTO jobs (id, question, status, iteration, created_at, updated_at)"
        " VALUES (?, ?, 'running', 0, ?, ?)",
        (job_id, question, now, now),
    )
    await _conn.commit()


async def get_job(job_id: str) -> dict | None:
    async with _conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    d = dict(row)
    if d.get("result"):
        d["result"] = json.loads(d["result"])
    return d


async def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    now = datetime.now(timezone.utc).isoformat()
    fields["updated_at"] = now
    if "result" in fields and fields["result"] is not None:
        if not isinstance(fields["result"], str):
            fields["result"] = json.dumps(fields["result"])
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = [*fields.values(), job_id]
    await _conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
    await _conn.commit()
