from models import DeadlineType, ExtractedDeadline, RawEmail, TaskStatus, Urgency
from datetime import datetime, timezone


class TestDeadlineType:
    def test_all_values_exist(self):
        expected = {"task", "meeting", "payment", "follow_up", "decision", "delivery", "other"}
        actual = {dt.value for dt in DeadlineType}
        assert actual == expected

    def test_from_value(self):
        assert DeadlineType("task") == DeadlineType.TASK
        assert DeadlineType("meeting") == DeadlineType.MEETING


class TestUrgency:
    def test_all_values_exist(self):
        expected = {"critical", "high", "medium", "low"}
        actual = {u.value for u in Urgency}
        assert actual == expected


class TestTaskStatus:
    def test_all_values_exist(self):
        expected = {"created", "reviewed", "in_progress", "blocked", "completed", "dismissed"}
        actual = {s.value for s in TaskStatus}
        assert actual == expected


class TestExtractedDeadline:
    def test_defaults(self):
        d = ExtractedDeadline(
            title="Test",
            deadline_type=DeadlineType.TASK,
            urgency=Urgency.LOW,
            source_quote="source",
            confidence=0.8,
        )
        assert d.deadline_date is None
        assert d.assigned_to is None
        assert d.counterparty is None
        assert d.action_needed is None
        assert d.review_required is False

    def test_with_optional_fields(self):
        d = ExtractedDeadline(
            title="Test",
            deadline_type=DeadlineType.MEETING,
            urgency=Urgency.HIGH,
            source_quote="source",
            confidence=0.9,
            deadline_date="Friday",
            assigned_to="Alice",
            counterparty="Bob",
            action_needed="Send report",
            review_required=True,
        )
        assert d.deadline_date == "Friday"
        assert d.assigned_to == "Alice"
        assert d.review_required is True


class TestRawEmail:
    def test_construction(self):
        email = RawEmail(
            email_id="e-001",
            subject="Test Subject",
            sender="sender@test.com",
            body="Test body",
            received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert email.email_id == "e-001"
        assert email.subject == "Test Subject"
