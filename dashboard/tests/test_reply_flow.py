"""Integration tests for reply draft DB and approval locking."""

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Dashboard modules
import sys

_dashboard = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_dashboard))

import db  # noqa: E402
from reply_generator import generate_reply_text  # noqa: E402


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    db.init_db(path)
    # tasks table is created by email processor on first use — mirror minimal schema
    conn = sqlite3.connect(path)
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
            user_id INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO tasks (
            fingerprint, title, deadline_type, urgency, source_quote, confidence,
            source_email_id, source_subject, source_sender, received_at,
            priority, created_at, updated_at, user_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "fp1",
            "Send proposal",
            "task",
            "high",
            "Please send by Friday",
            0.9,
            "gmail-msg-001",
            "Proposal update",
            "client@example.com",
            now,
            90.0,
            now,
            now,
            1,
        ),
    )
    conn.commit()
    conn.close()
    yield path
    Path(path).unlink(missing_ok=True)


def test_create_reply_draft_with_gmail_ids(db_path):
    draft = db.create_reply_draft(
        db_path,
        {
            "task_id": 1,
            "original_subject": "Proposal update",
            "original_sender": "client@example.com",
            "original_body": "Please send by Friday",
            "draft_text": "Hi, working on it.",
            "gmail_message_id": "gmail-msg-001",
            "gmail_thread_id": "thread-abc",
        },
        user_id=1,
    )
    assert draft["gmail_message_id"] == "gmail-msg-001"
    assert draft["gmail_thread_id"] == "thread-abc"
    assert draft["status"] == "pending"


def test_active_draft_prevents_duplicate(db_path):
    db.create_reply_draft(
        db_path,
        {
            "task_id": 1,
            "original_subject": "S",
            "original_sender": "a@b.com",
            "original_body": "body",
            "draft_text": "draft one",
        },
        user_id=1,
    )
    assert db.has_active_reply_draft_for_task(db_path, 1) is True


def test_claim_reply_draft_for_sending(db_path):
    draft = db.create_reply_draft(
        db_path,
        {
            "task_id": 1,
            "original_subject": "S",
            "original_sender": "a@b.com",
            "original_body": "body",
            "draft_text": "draft",
        },
    )
    claimed = db.claim_reply_draft_for_sending(db_path, draft["id"])
    assert claimed is not None
    assert claimed["status"] == "sending"
    assert db.claim_reply_draft_for_sending(db_path, draft["id"]) is None


def test_release_send_lock(db_path):
    draft = db.create_reply_draft(
        db_path,
        {
            "task_id": 1,
            "original_subject": "S",
            "original_sender": "a@b.com",
            "original_body": "body",
            "draft_text": "draft",
        },
    )
    db.claim_reply_draft_for_sending(db_path, draft["id"])
    db.release_reply_draft_send_lock(db_path, draft["id"], error="network error")
    row = db.get_reply_draft(db_path, draft["id"])
    assert row["status"] == "pending"
    assert "network" in row["send_error"]


def test_resolve_task_gmail_id(db_path):
    draft = db.create_reply_draft(
        db_path,
        {
            "task_id": 1,
            "original_subject": "S",
            "original_sender": "a@b.com",
            "original_body": "body",
            "draft_text": "draft",
        },
    )
    task = db.get_task_by_id(db_path, 1)
    assert task["source_email_id"] == "gmail-msg-001"
    assert draft["task_id"] == 1


def test_template_reply_fallback():
    text, model, confidence = generate_reply_text(
        "Invoice",
        "finance@vendor.com",
        "Due tomorrow",
        "acknowledge",
    )
    assert "Invoice" in text or "invoice" in text.lower()
    assert model == "template"
    assert confidence == 0.5
