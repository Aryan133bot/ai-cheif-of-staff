import os
import sys

import pytest

# Allow imports from the parent email processor package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone

from models import RawEmail


@pytest.fixture
def sample_task_email():
    return RawEmail(
        email_id="test-001",
        subject="Proposal update needed",
        sender="client@example.com",
        body="Hi, please send the revised proposal by Friday. Also share the commercials this week.",
        received_at=datetime(2026, 5, 7, 11, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_payment_email():
    return RawEmail(
        email_id="test-002",
        subject="Invoice follow-up",
        sender="finance@vendor.com",
        body="Reminder: invoice INV-209 is due tomorrow. Please confirm payment timeline.",
        received_at=datetime(2026, 5, 7, 13, 30, tzinfo=timezone.utc),
    )


@pytest.fixture
def irrelevant_email():
    return RawEmail(
        email_id="test-003",
        subject="Great catching up",
        sender="friend@personal.com",
        body="Hey it was so good to see you at the party last night! Hope you had fun.",
        received_at=datetime(2026, 5, 7, 18, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test_tasks.db")
