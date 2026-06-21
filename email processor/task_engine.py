import hashlib
import logging
import sqlite3
import sys
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Iterable

_dashboard_dir = str(Path(__file__).resolve().parent.parent / "dashboard")
if _dashboard_dir not in sys.path:
    sys.path.insert(0, _dashboard_dir)
from db_core import get_connection

from models import ExtractedDeadline, TaskStatus, Urgency

logger = logging.getLogger(__name__)


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.lower().strip().split())


def build_fingerprint(deadline: ExtractedDeadline, user_id: int) -> str:
    key = "|".join(
        [
            str(user_id),
            _normalize(deadline.title),
            _normalize(deadline.deadline_date),
            _normalize(deadline.assigned_to),
            _normalize(deadline.counterparty),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def priority_score(deadline: ExtractedDeadline, contact_importance: int = 50) -> float:
    urgency_weight = {
        Urgency.CRITICAL.value: 100,
        Urgency.HIGH.value: 70,
        Urgency.MEDIUM.value: 40,
        Urgency.LOW.value: 20,
    }.get(deadline.urgency.value, 20)
    confidence_weight = max(0.0, min(1.0, deadline.confidence)) * 20
    review_penalty = -10 if deadline.review_required else 0
    # Add a bonus if contact is important (>50), or penalty if <50
    contact_bonus = (contact_importance - 50)
    return urgency_weight + confidence_weight + review_penalty + contact_bonus


class TaskEngine:
    def __init__(self, db_path: str = "phase1_tasks.db"):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        return get_connection(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
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
            conn.commit()

    def upsert_tasks(
        self,
        tasks: Iterable[ExtractedDeadline],
        source_email_id: str,
        source_subject: str,
        source_sender: str,
        received_at: datetime,
        user_id: int,
    ) -> tuple[int, int]:
        created = 0
        updated = 0
        now = datetime.now(timezone.utc).isoformat()
        
        # Extract email from "Name <email@domain.com>" or "email@domain.com"
        import re
        match = re.search(r'<(.+?)>', source_sender)
        raw_email = match.group(1) if match else source_sender
        raw_email = raw_email.strip().lower()

        with self._connect() as conn:
            # Query contact relationships for this email
            rel_row = conn.execute(
                "SELECT importance FROM contact_relationships WHERE user_id = ? AND email_address = ?",
                (user_id, raw_email)
            ).fetchone()
            contact_importance = rel_row["importance"] if rel_row else 50

            for task in tasks:
                fp = build_fingerprint(task, user_id)
                score = priority_score(task, contact_importance=contact_importance)
                existing = conn.execute(
                    "SELECT id FROM tasks WHERE fingerprint = ? AND user_id = ?",
                    (fp, user_id),
                ).fetchone()

                payload = (
                    task.title,
                    task.deadline_type.value,
                    task.urgency.value,
                    task.source_quote,
                    float(task.confidence),
                    task.deadline_date,
                    task.assigned_to,
                    task.counterparty,
                    task.action_needed,
                    int(task.review_required),
                    source_email_id,
                    source_subject,
                    source_sender,
                    received_at.isoformat(),
                    score,
                )

                if existing:
                    conn.execute(
                        """
                        UPDATE tasks
                        SET title=?, deadline_type=?, urgency=?, source_quote=?, confidence=?,
                            deadline_date=?, assigned_to=?, counterparty=?, action_needed=?,
                            review_required=?, source_email_id=?, source_subject=?, source_sender=?,
                            received_at=?, priority=?, updated_at=?
                        WHERE fingerprint=? AND user_id=?
                        """,
                        payload + (now, fp, user_id),
                    )
                    updated += 1
                else:
                    cursor = conn.execute(
                        """
                        INSERT INTO tasks (
                            fingerprint, title, deadline_type, urgency, source_quote, confidence,
                            deadline_date, assigned_to, counterparty, action_needed, review_required,
                            source_email_id, source_subject, source_sender, received_at,
                            priority, status, created_at, updated_at, user_id
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
                        """,
                        (fp,) + payload + (TaskStatus.CREATED.value, now, now, user_id),
                    )
                    row = cursor.fetchone()
                    created += 1

            conn.commit()

        return created, updated

    def top_priorities(self, limit: int = 5) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT * FROM tasks
                WHERE status IN ('created', 'reviewed', 'in_progress', 'blocked')
                ORDER BY priority DESC, received_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def update_task_status(self, task_id: int, status: TaskStatus | str) -> bool:
        next_status = status.value if isinstance(status, TaskStatus) else str(status)
        allowed_statuses = {item.value for item in TaskStatus}
        if next_status not in allowed_statuses:
            raise ValueError(f"Invalid status '{next_status}'. Allowed: {sorted(allowed_statuses)}")

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (next_status, now, task_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_review_queue(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT * FROM tasks
                WHERE review_required = 1
                  AND status IN ('created', 'reviewed', 'in_progress', 'blocked')
                ORDER BY confidence ASC, priority DESC, received_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
