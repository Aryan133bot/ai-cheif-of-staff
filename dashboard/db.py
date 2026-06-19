"""
Database access layer for the AI Chief of Staff dashboard.

Extends the existing email processor SQLite database with tables for
calendar events and reply drafts while keeping full access to the
existing tasks table.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Default path points to the email processor's database
DEFAULT_DB_PATH = str(
    (Path(__file__).resolve().parent.parent / "email processor" / "phase1_tasks.db")
)


from db_core import get_connection

def get_db(db_path: str = DEFAULT_DB_PATH):
    """Return a connection with Row factory and WAL mode for concurrent reads."""
    return get_connection(db_path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Schema bootstrap ───────────────────────────────────────────────────────

def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Create all tables if they don't exist. Safe to call on every startup."""
    conn = get_db(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL,
                email      TEXT    NOT NULL UNIQUE,
                password   TEXT    NOT NULL,
                gmail_token TEXT,
                created_at TEXT    NOT NULL,
                last_login TEXT
            )
            """
        )

        # Create the tasks table to prevent dashboard crashes on an empty DB
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                deadline_type TEXT NOT NULL,
                urgency TEXT NOT NULL,
                source_quote TEXT NOT NULL,
                confidence REAL NOT NULL,
                deadline_date TEXT,
                assigned_to TEXT,
                counterparty TEXT,
                action_needed TEXT,
                review_required INTEGER NOT NULL DEFAULT 0,
                source_email_id TEXT NOT NULL,
                source_subject TEXT NOT NULL,
                source_sender TEXT NOT NULL,
                received_at TEXT NOT NULL,
                priority REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS calendar_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT    NOT NULL,
                description     TEXT    DEFAULT '',
                start_time      TEXT    NOT NULL,
                end_time        TEXT    NOT NULL,
                all_day         INTEGER NOT NULL DEFAULT 0,
                event_type      TEXT    NOT NULL DEFAULT 'custom',
                urgency         TEXT    DEFAULT 'medium',
                linked_task_id  INTEGER,
                color           TEXT    DEFAULT '#6366f1',
                reminder_minutes INTEGER,
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL,
                user_id         INTEGER NOT NULL,
                FOREIGN KEY (linked_task_id) REFERENCES tasks(id) ON DELETE SET NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reply_drafts (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id               INTEGER,
                original_subject      TEXT    NOT NULL,
                original_sender       TEXT    NOT NULL,
                original_body         TEXT    NOT NULL,
                reply_intent          TEXT    NOT NULL DEFAULT 'follow_up',
                draft_text            TEXT    NOT NULL,
                edited_text           TEXT,
                status                TEXT    NOT NULL DEFAULT 'pending',
                model_used            TEXT    DEFAULT '',
                confidence            REAL   DEFAULT 0.0,
                gmail_message_id      TEXT,
                gmail_thread_id       TEXT,
                sent_at               TEXT,
                gmail_sent_message_id TEXT,
                send_error            TEXT,
                created_at            TEXT    NOT NULL,
                updated_at            TEXT    NOT NULL,
                user_id               INTEGER NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_processing_runs (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at       TEXT    NOT NULL,
                completed_at     TEXT,
                trigger          TEXT    NOT NULL DEFAULT 'manual',
                provider         TEXT    DEFAULT 'all',
                emails_fetched   INTEGER DEFAULT 0,
                emails_processed INTEGER DEFAULT 0,
                emails_skipped   INTEGER DEFAULT 0,
                tasks_created    INTEGER DEFAULT 0,
                tasks_updated    INTEGER DEFAULT 0,
                errors           INTEGER DEFAULT 0,
                error_details    TEXT,
                status           TEXT    NOT NULL DEFAULT 'running',
                user_id          INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fetched_emails (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id         TEXT    NOT NULL,
                subject          TEXT    NOT NULL DEFAULT '',
                sender           TEXT    NOT NULL DEFAULT '',
                body_preview     TEXT    DEFAULT '',
                received_at      TEXT,
                thread_id        TEXT,
                processing_status TEXT   NOT NULL DEFAULT 'skipped',
                category         TEXT    NOT NULL DEFAULT 'miscellaneous',
                run_id           INTEGER,
                created_at       TEXT    NOT NULL,
                user_id          INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (run_id) REFERENCES email_processing_runs(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_states (
                state      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        import os
        is_pg = bool(os.environ.get("DATABASE_URL"))
        
        def get_columns(table_name: str) -> list[str]:
            if is_pg:
                c = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table_name,))
                return [r["column_name"] for r in c.fetchall()]
            else:
                c = conn.execute(f"PRAGMA table_info({table_name})")
                return [r["name"] for r in c.fetchall()]

        columns = get_columns("calendar_events")
        if "gcal_event_id" not in columns:
            conn.execute("ALTER TABLE calendar_events ADD COLUMN gcal_event_id TEXT")

        fetched_columns = get_columns("fetched_emails")
        if "category" not in fetched_columns:
            conn.execute("ALTER TABLE fetched_emails ADD COLUMN category TEXT NOT NULL DEFAULT 'miscellaneous'")

        draft_columns = get_columns("reply_drafts")
        for col, ddl in (
            ("gmail_message_id", "ALTER TABLE reply_drafts ADD COLUMN gmail_message_id TEXT"),
            ("gmail_thread_id", "ALTER TABLE reply_drafts ADD COLUMN gmail_thread_id TEXT"),
            ("sent_at", "ALTER TABLE reply_drafts ADD COLUMN sent_at TEXT"),
            ("gmail_sent_message_id", "ALTER TABLE reply_drafts ADD COLUMN gmail_sent_message_id TEXT"),
            ("send_error", "ALTER TABLE reply_drafts ADD COLUMN send_error TEXT"),
        ):
            if col not in draft_columns:
                conn.execute(ddl)

        # Multi-tenant migrations
        for table in ["tasks", "calendar_events", "reply_drafts", "email_processing_runs", "fetched_emails"]:
            if "user_id" not in get_columns(table):
                # We default to 1 for existing data (assuming the first created user gets id 1)
                conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        
        if "gmail_token" not in get_columns("users"):
            conn.execute("ALTER TABLE users ADD COLUMN gmail_token TEXT")

        conn.commit()
        logger.info("Database schema initialised at %s", db_path)
    finally:
        conn.close()


# ─── Task queries ────────────────────────────────────────────────────────────

def get_all_tasks(
    db_path: str = DEFAULT_DB_PATH,
    status: str | None = None,
    urgency: str | None = None,
    deadline_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
    user_id: int = None,
) -> list[dict]:
    """Fetch tasks with optional filters."""
    conn = get_db(db_path)
    try:
        clauses = ["user_id = ?"] if user_id else []
        params: list = [user_id] if user_id else []
        if status:
            clauses.append("status = ?")
            params.append(status)
        else:
            clauses.append("status NOT IN ('completed', 'dismissed')")
            
        if urgency:
            clauses.append("urgency = ?")
            params.append(urgency)
        if deadline_type:
            clauses.append("deadline_type = ?")
            params.append(deadline_type)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT * FROM tasks
            {where}
            ORDER BY priority DESC, received_at DESC
            LIMIT ? OFFSET ?
        """
        params += [limit, offset]
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_task_by_id(db_path: str, task_id: int, user_id: int = None) -> dict | None:
    conn = get_db(db_path)
    try:
        if user_id:
            row = conn.execute("SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_task_priorities(db_path: str = DEFAULT_DB_PATH, limit: int = 10, user_id: int = None) -> list[dict]:
    conn = get_db(db_path)
    try:
        if user_id:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status IN ('created', 'reviewed', 'in_progress', 'blocked') AND user_id = ?
                ORDER BY priority DESC, received_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status IN ('created', 'reviewed', 'in_progress', 'blocked')
                ORDER BY priority DESC, received_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_review_queue(db_path: str = DEFAULT_DB_PATH, limit: int = 50, user_id: int = None) -> list[dict]:
    conn = get_db(db_path)
    try:
        where_user_and = "user_id = ? AND " if user_id else ""
        params = (user_id,) if user_id else ()
        
        rows = conn.execute(
            f"""
            SELECT * FROM tasks
            WHERE {where_user_and} review_required = 1
              AND status IN ('created', 'reviewed', 'in_progress', 'blocked')
            ORDER BY confidence ASC, priority DESC
            LIMIT ?
            """,
            params + (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_task(db_path: str, data: dict, user_id: int) -> dict:
    """Manually create a task. Compatible with SQLite < 3.35 (no RETURNING *)."""
    import hashlib
    conn = get_db(db_path)
    try:
        now = _now_iso()
        # Timestamp-salted fingerprint ensures no collisions across rapid manual entries
        fp_str = f"manual|{user_id}|{data['title']}|{now}"
        fp = hashlib.sha256(fp_str.encode("utf-8")).hexdigest()

        title = data["title"].strip()
        if not title:
            raise ValueError("Task title cannot be empty.")
        if len(title) > 500:
            raise ValueError("Task title is too long (max 500 characters).")

        cursor = conn.execute(
            """
            INSERT INTO tasks (
                fingerprint, title, deadline_type, urgency, source_quote, confidence,
                deadline_date, assigned_to, counterparty, action_needed, review_required,
                source_email_id, source_subject, source_sender, received_at,
                priority, status, created_at, updated_at, user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fp,
                title,
                data.get("deadline_type", "task"),
                data.get("urgency", "medium"),
                data.get("source_quote", "Manually created"),
                1.0,  # 100% confidence for manual entry
                data.get("deadline_date") or None,
                data.get("assigned_to") or None,
                data.get("counterparty") or None,
                data.get("action_needed") or None,
                0,
                "manual",
                "manual",
                "manual",
                now,
                50.0,  # default priority score
                "created",
                now,
                now,
                user_id,
            ),
        )
        conn.commit()
        # SQLite-version-safe: fetch by lastrowid
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def update_task_status(db_path: str, task_id: int, new_status: str, user_id: int = None) -> bool:
    allowed = {"created", "reviewed", "in_progress", "blocked", "completed", "dismissed"}
    if new_status not in allowed:
        raise ValueError(f"Invalid status '{new_status}'. Allowed: {sorted(allowed)}")
    conn = get_db(db_path)
    try:
        where_user = " AND user_id = ?" if user_id else ""
        params = (new_status, _now_iso(), task_id, user_id) if user_id else (new_status, _now_iso(), task_id)
        
        cursor = conn.execute(
            f"UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?{where_user}",
            params,
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_task_stats(db_path: str = DEFAULT_DB_PATH, user_id: int = None) -> dict:
    """Aggregate counts for the dashboard summary cards."""
    conn = get_db(db_path)
    try:
        where_user = "WHERE user_id = ?" if user_id else ""
        where_user_and = "WHERE user_id = ? AND " if user_id else "WHERE "
        params = (user_id,) if user_id else ()

        total = conn.execute(f"SELECT COUNT(*) FROM tasks {where_user}", params).fetchone()[0]
        
        by_urgency = {}
        for row in conn.execute(
            f"SELECT urgency, COUNT(*) as cnt FROM tasks "
            f"{where_user_and} status NOT IN ('completed','dismissed') GROUP BY urgency",
            params
        ):
            by_urgency[row["urgency"]] = row["cnt"]

        by_status = {}
        for row in conn.execute(f"SELECT status, COUNT(*) as cnt FROM tasks {where_user} GROUP BY status", params):
            by_status[row["status"]] = row["cnt"]

        by_type = {}
        for row in conn.execute(
            f"SELECT deadline_type, COUNT(*) as cnt FROM tasks "
            f"{where_user_and} status NOT IN ('completed','dismissed') GROUP BY deadline_type",
            params
        ):
            by_type[row["deadline_type"]] = row["cnt"]

        pending_reviews_query = (
            f"SELECT COUNT(*) FROM tasks {where_user_and} review_required = 1 "
            f"AND status IN ('created','reviewed','in_progress','blocked')"
        )
        pending_reviews = conn.execute(pending_reviews_query, params).fetchone()[0]

        return {
            "total": total,
            "active": total - by_status.get("completed", 0) - by_status.get("dismissed", 0),
            "by_urgency": by_urgency,
            "by_status": by_status,
            "by_type": by_type,
            "pending_reviews": pending_reviews,
        }
    finally:
        conn.close()


# ─── Calendar queries ────────────────────────────────────────────────────────

def get_calendar_events(
    db_path: str = DEFAULT_DB_PATH,
    start: str | None = None,
    end: str | None = None,
    user_id: int | None = None,
) -> list[dict]:
    conn = get_db(db_path)
    try:
        clauses = []
        params: list = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if start:
            clauses.append("end_time >= ?")
            params.append(start)
        if end:
            clauses.append("start_time <= ?")
            params.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM calendar_events {where} ORDER BY start_time ASC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_calendar_event(db_path: str, event_id: int, user_id: int = None) -> dict | None:
    conn = get_db(db_path)
    try:
        if user_id:
            row = conn.execute("SELECT * FROM calendar_events WHERE id = ? AND user_id = ?", (event_id, user_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_calendar_event(db_path: str, data: dict, user_id: int) -> dict:
    now = _now_iso()
    conn = get_db(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO calendar_events
                (user_id, title, description, start_time, end_time, all_day,
                 event_type, urgency, linked_task_id, color,
                 reminder_minutes, gcal_event_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
            """,
            (
                user_id,
                data["title"],
                data.get("description", ""),
                data["start_time"],
                data["end_time"],
                int(data.get("all_day", False)),
                data.get("event_type", "custom"),
                data.get("urgency", "medium"),
                data.get("linked_task_id"),
                data.get("color", "#6366f1"),
                data.get("reminder_minutes"),
                data.get("gcal_event_id"),
                now,
                now,
            ),
        )
        row = cursor.fetchone()
        event_id = row["id"] if isinstance(row, dict) else row[0]
        conn.commit()
        row = conn.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def update_calendar_event(db_path: str, event_id: int, data: dict, user_id: int = None) -> dict | None:
    now = _now_iso()
    conn = get_db(db_path)
    try:
        # Build dynamic SET clause from provided fields
        allowed = {
            "title", "description", "start_time", "end_time", "all_day",
            "event_type", "urgency", "linked_task_id", "color", "reminder_minutes",
            "gcal_event_id",
        }
        sets = []
        params: list = []
        for key, val in data.items():
            if key in allowed:
                sets.append(f"{key} = ?")
                params.append(val)
        if not sets:
            return None
        sets.append("updated_at = ?")
        params.append(now)
        params.append(event_id)
        
        where_user = " AND user_id = ?" if user_id else ""
        if user_id:
            params.append(user_id)

        conn.execute(
            f"UPDATE calendar_events SET {', '.join(sets)} WHERE id = ?{where_user}",
            params,
        )
        conn.commit()
        if user_id:
            row = conn.execute("SELECT * FROM calendar_events WHERE id = ? AND user_id = ?", (event_id, user_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_calendar_event(db_path: str, event_id: int, user_id: int = None) -> bool:
    conn = get_db(db_path)
    try:
        if user_id:
            cursor = conn.execute("DELETE FROM calendar_events WHERE id = ? AND user_id = ?", (event_id, user_id))
        else:
            cursor = conn.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def sync_tasks_to_calendar(db_path: str = DEFAULT_DB_PATH, user_id: int = None) -> int:
    """Create calendar events from tasks that have deadline dates but no linked event."""
    conn = get_db(db_path)
    try:
        where_user = " AND t.user_id = ?" if user_id else ""
        params = (user_id,) if user_id else ()
        
        # Find tasks with deadlines that don't already have a calendar event
        rows = conn.execute(
            f"""
            SELECT t.* FROM tasks t
            LEFT JOIN calendar_events ce ON ce.linked_task_id = t.id
            WHERE t.deadline_date IS NOT NULL
              AND t.status NOT IN ('completed', 'dismissed')
              AND ce.id IS NULL
              {where_user}
            """,
            params
        ).fetchall()

        now = _now_iso()
        created = 0
        for task in rows:
            # Try to parse deadline_date into a real datetime, fall back to today
            try:
                from dateutil import parser as dp
                parsed = dp.parse(task["deadline_date"], fuzzy=True)
                start = parsed.isoformat()
                end = parsed.replace(hour=min(parsed.hour + 1, 23)).isoformat()
            except (ValueError, OverflowError):
                # Can't parse — use today as placeholder
                today = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
                start = today.isoformat()
                end = today.replace(hour=10).isoformat()

            # Choose color based on urgency
            color_map = {
                "critical": "#ef4444",
                "high": "#f97316",
                "medium": "#6366f1",
                "low": "#64748b",
            }
            
            event_user_id = user_id if user_id else task.get("user_id")

            conn.execute(
                """
                INSERT INTO calendar_events
                    (user_id, title, description, start_time, end_time, all_day,
                     event_type, urgency, linked_task_id, color,
                     reminder_minutes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_user_id,
                    task["title"],
                    f"From email: {task['source_subject']}\nQuote: {task['source_quote']}",
                    start,
                    end,
                    0,
                    task["deadline_type"],
                    task["urgency"],
                    task["id"],
                    color_map.get(task["urgency"], "#6366f1"),
                    30,  # Default 30-min reminder
                    now,
                    now,
                ),
            )
            created += 1

        conn.commit()
        logger.info("Synced %d tasks to calendar events", created)
        return created
    finally:
        conn.close()


# ─── Reply draft queries ─────────────────────────────────────────────────────

def get_reply_drafts(
    db_path: str = DEFAULT_DB_PATH,
    status: str | None = None,
    limit: int = 50,
    user_id: int = None,
) -> list[dict]:
    conn = get_db(db_path)
    try:
        where_user = "WHERE user_id = ?" if user_id else ""
        where_user_and = "WHERE user_id = ? AND " if user_id else "WHERE "
        params = (user_id,) if user_id else ()
        
        if status:
            rows = conn.execute(
                f"SELECT * FROM reply_drafts {where_user_and} status = ? ORDER BY created_at DESC LIMIT ?",
                params + (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM reply_drafts {where_user} ORDER BY created_at DESC LIMIT ?",
                params + (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_reply_draft(db_path: str, draft_id: int, user_id: int = None) -> dict | None:
    conn = get_db(db_path)
    try:
        if user_id:
            row = conn.execute(
                "SELECT * FROM reply_drafts WHERE id = ? AND user_id = ?",
                (draft_id, user_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM reply_drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def has_pending_reply_draft(db_path: str, task_id: int) -> bool:
    """True if this task already has a draft awaiting approval."""
    return has_active_reply_draft_for_task(db_path, task_id)


def has_active_reply_draft_for_task(db_path: str, task_id: int) -> bool:
    """True if a draft exists that is not yet sent (avoids duplicates on re-process)."""
    conn = get_db(db_path)
    try:
        row = conn.execute(
            """
            SELECT 1 FROM reply_drafts
            WHERE task_id = ?
              AND status IN ('pending', 'approved', 'sending')
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def claim_reply_draft_for_sending(db_path: str, draft_id: int) -> dict | None:
    """
    Atomically mark a pending draft as 'sending' so only one approve request can proceed.
    Returns the draft row if claimed, else None.
    """
    now = _now_iso()
    conn = get_db(db_path)
    try:
        cursor = conn.execute(
            """
            UPDATE reply_drafts
            SET status = 'sending', updated_at = ?, send_error = NULL
            WHERE id = ? AND status IN ('pending', 'approved')
            """,
            (now, draft_id),
        )
        if cursor.rowcount == 0:
            return None
        conn.commit()
        row = conn.execute("SELECT * FROM reply_drafts WHERE id = ?", (draft_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def release_reply_draft_send_lock(db_path: str, draft_id: int, error: str | None = None) -> None:
    """Revert a draft from 'sending' back to pending after a failed send attempt."""
    data: dict = {"status": "pending"}
    if error:
        data["send_error"] = error[:500]
    update_reply_draft(db_path, draft_id, data)


def reset_stale_sending_drafts(db_path: str, stale_minutes: int = 10) -> int:
    """Unlock drafts stuck in 'sending' after a crash or timeout."""
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat()
    conn = get_db(db_path)
    try:
        cursor = conn.execute(
            """
            UPDATE reply_drafts
            SET status = 'pending',
                send_error = COALESCE(send_error, 'Previous send timed out — try again.')
            WHERE status = 'sending' AND updated_at < ?
            """,
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def get_tasks_by_source_email_id(db_path: str, source_email_id: str) -> list[dict]:
    conn = get_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE source_email_id = ?
            ORDER BY priority DESC, created_at DESC
            """,
            (source_email_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_reply_draft(db_path: str, data: dict, user_id: int) -> dict:
    now = _now_iso()
    conn = get_db(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO reply_drafts
                (task_id, original_subject, original_sender, original_body,
                 reply_intent, draft_text, edited_text, status,
                 model_used, confidence, gmail_message_id, gmail_thread_id,
                 created_at, updated_at, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
            """,
            (
                data.get("task_id"),
                data["original_subject"],
                data["original_sender"],
                data["original_body"][:2000],
                data.get("reply_intent", "follow_up"),
                data["draft_text"],
                data.get("edited_text"),
                data.get("status", "pending"),
                data.get("model_used", ""),
                data.get("confidence", 0.0),
                data.get("gmail_message_id"),
                data.get("gmail_thread_id"),
                now,
                now,
                user_id,
            ),
        )
        row = cursor.fetchone()
        draft_id = row["id"] if isinstance(row, dict) else row[0]
        conn.commit()
        row = conn.execute("SELECT * FROM reply_drafts WHERE id = ?", (draft_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def update_reply_draft(db_path: str, draft_id: int, data: dict, user_id: int = None) -> dict | None:
    now = _now_iso()
    conn = get_db(db_path)
    try:
        allowed = {
            "edited_text",
            "status",
            "draft_text",
            "gmail_message_id",
            "gmail_thread_id",
            "sent_at",
            "gmail_sent_message_id",
            "send_error",
        }
        sets = []
        params: list = []
        for key, val in data.items():
            if key in allowed:
                sets.append(f"{key} = ?")
                params.append(val)
        if not sets:
            return None
        sets.append("updated_at = ?")
        params.append(now)
        params.append(draft_id)
        
        where_clause = "WHERE id = ?"
        if user_id:
            where_clause += " AND user_id = ?"
            params.append(user_id)

        conn.execute(
            f"UPDATE reply_drafts SET {', '.join(sets)} {where_clause}",
            params,
        )
        conn.commit()
        
        if user_id:
            row = conn.execute("SELECT * FROM reply_drafts WHERE id = ? AND user_id = ?", (draft_id, user_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM reply_drafts WHERE id = ?", (draft_id,)).fetchone()
            
        return dict(row) if row else None
    finally:
        conn.close()


def delete_reply_draft(db_path: str, draft_id: int, user_id: int = None) -> bool:
    conn = get_db(db_path)
    try:
        if user_id:
            cursor = conn.execute("DELETE FROM reply_drafts WHERE id = ? AND user_id = ?", (draft_id, user_id))
        else:
            cursor = conn.execute("DELETE FROM reply_drafts WHERE id = ?", (draft_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ─── Email processing run queries ────────────────────────────────────────────

def create_processing_run(db_path: str, trigger: str = "manual", provider: str = "all", user_id: int = None) -> int:
    """Start a new processing run record. Returns the run ID."""
    conn = get_db(db_path)
    try:
        if user_id is None:
            raise ValueError("user_id is required")
        cursor = conn.execute(
            "INSERT INTO email_processing_runs (started_at, trigger, provider, status, user_id) VALUES (?, ?, ?, ?, ?) RETURNING id",
            (_now_iso(), trigger, provider, "running", user_id),
        )
        row = cursor.fetchone()
        run_id = row["id"] if isinstance(row, dict) else row[0]
        conn.commit()
        return run_id
    finally:
        conn.close()


def complete_processing_run(
    db_path: str,
    run_id: int,
    emails_fetched: int = 0,
    emails_processed: int = 0,
    emails_skipped: int = 0,
    tasks_created: int = 0,
    tasks_updated: int = 0,
    errors: int = 0,
    error_details: str | None = None,
    status: str = "completed",
    user_id: int = None,
) -> None:
    """Finalize a processing run with its results."""
    conn = get_db(db_path)
    try:
        conn.execute(
            """
            UPDATE email_processing_runs
            SET completed_at = ?, emails_fetched = ?, emails_processed = ?,
                emails_skipped = ?, tasks_created = ?, tasks_updated = ?,
                errors = ?, error_details = ?, status = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                _now_iso(), emails_fetched, emails_processed,
                emails_skipped, tasks_created, tasks_updated,
                errors, error_details, status, run_id, user_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_processing_history(db_path: str, limit: int = 20, user_id: int = None) -> list[dict]:
    """Fetch the most recent email processing runs."""
    conn = get_db(db_path)
    try:
        if user_id is None:
            raise ValueError("user_id is required")
        rows = conn.execute(
            "SELECT * FROM email_processing_runs WHERE user_id = ? ORDER BY started_at DESC LIMIT ?",
            (user_id, limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_latest_processing_run(db_path: str, user_id: int = None) -> dict | None:
    """Get the most recent completed processing run."""
    conn = get_db(db_path)
    try:
        if user_id is None:
            raise ValueError("user_id is required")
        row = conn.execute(
            "SELECT * FROM email_processing_runs WHERE status = 'completed' AND user_id = ? ORDER BY started_at DESC LIMIT 1",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_all_users(db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    conn = get_db(db_path)
    try:
        rows = conn.execute("SELECT * FROM users").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def update_user_gmail_token(db_path: str, user_id: int, token_json: str) -> bool:
    conn = get_db(db_path)
    try:
        conn.execute("UPDATE users SET gmail_token = ? WHERE id = ?", (token_json, user_id))
        conn.commit()
        return True
    finally:
        conn.close()

def get_user_gmail_token(db_path: str, user_id: int) -> str | None:
    conn = get_db(db_path)
    try:
        row = conn.execute("SELECT gmail_token FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["gmail_token"] if row else None
    finally:
        conn.close()


# ─── Fetched emails queries ──────────────────────────────────────────────────

def save_fetched_emails(db_path: str, emails: list[dict], run_id: int, user_id: int) -> None:
    """Save all fetched emails to the database for display.
    
    On re-ingest, the category and processing_status are updated so that
    improvements to the vocabulary bank take effect retroactively.
    """
    now = _now_iso()
    conn = get_db(db_path)
    try:
        for e in emails:
            email_id = e.get("email_id", "")
            subject = e.get("subject", "(no subject)")
            sender = e.get("sender", "")
            body_preview = (e.get("body", "") or "")[:500]
            received_at = e.get("received_at", "")
            thread_id = e.get("thread_id")
            processing_status = e.get("processing_status", "pending")
            category = e.get("category", "miscellaneous")

            existing = conn.execute(
                "SELECT id FROM fetched_emails WHERE email_id = ? AND user_id = ?",
                (email_id, user_id)
            ).fetchone()

            if existing:
                # Update category and status on re-ingest so vocabulary bank improvements apply
                conn.execute(
                    """
                    UPDATE fetched_emails
                    SET category = ?, processing_status = ?, run_id = ?
                    WHERE email_id = ? AND user_id = ?
                    """,
                    (category, processing_status, run_id, email_id, user_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO fetched_emails
                        (email_id, subject, sender, body_preview, received_at, thread_id,
                         processing_status, category, run_id, created_at, user_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        email_id,
                        subject,
                        sender,
                        body_preview,
                        received_at,
                        thread_id,
                        processing_status,
                        category,
                        run_id,
                        now,
                        user_id,
                    ),
                )
        conn.commit()
    finally:
        conn.close()



def get_fetched_emails(
    db_path: str,
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    category: str | None = None,
) -> list[dict]:
    """Get fetched emails for a user with pagination support."""
    conn = get_db(db_path)
    try:
        where = "WHERE user_id = ?"
        params: list = [user_id]
        if status:
            where += " AND processing_status = ?"
            params.append(status)
        if category:
            where += " AND category = ?"
            params.append(category)
        params.extend([limit, offset])
        rows = conn.execute(
            f"SELECT * FROM fetched_emails {where} ORDER BY received_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def save_oauth_state(db_path: str, state: str, user_id: int):
    conn = get_db(db_path)
    try:
        # Cleanup old states (older than 1 hour)
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        conn.execute("DELETE FROM oauth_states WHERE created_at < ?", (one_hour_ago,))
        
        conn.execute(
            "INSERT INTO oauth_states (state, user_id, created_at) VALUES (?, ?, ?)",
            (state, user_id, _now_iso())
        )
        conn.commit()
    finally:
        conn.close()

def get_user_for_oauth_state(db_path: str, state: str) -> int | None:
    conn = get_db(db_path)
    try:
        row = conn.execute("SELECT user_id FROM oauth_states WHERE state = ?", (state,)).fetchone()
        if row:
            conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            conn.commit()
            return row["user_id"] if isinstance(row, dict) else row[0]
        return None
    finally:
        conn.close()
