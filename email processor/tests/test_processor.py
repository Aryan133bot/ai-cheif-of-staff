import json
import pytest
from datetime import datetime, timezone

from models import RawEmail
from processor import EmailProcessor, load_emails_from_json


class TestIsRelevantEmail:
    def test_task_email_is_relevant(self, sample_task_email):
        processor = EmailProcessor.__new__(EmailProcessor)
        processor.extractor = None
        processor.task_engine = None
        assert processor.is_relevant_email(sample_task_email) is True

    def test_payment_email_is_relevant(self, sample_payment_email):
        processor = EmailProcessor.__new__(EmailProcessor)
        processor.extractor = None
        processor.task_engine = None
        assert processor.is_relevant_email(sample_payment_email) is True

    def test_irrelevant_email_is_skipped(self, irrelevant_email):
        processor = EmailProcessor.__new__(EmailProcessor)
        processor.extractor = None
        processor.task_engine = None
        assert processor.is_relevant_email(irrelevant_email) is False

    def test_may_verb_does_not_match(self):
        """Bug #4: 'you may want to consider' should NOT trigger the month regex."""
        processor = EmailProcessor.__new__(EmailProcessor)
        processor.extractor = None
        processor.task_engine = None
        email = RawEmail(
            email_id="test-may-verb",
            subject="Some thoughts",
            sender="alice@example.com",
            body="You may want to consider a different approach to this problem.",
            received_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        )
        assert processor.is_relevant_email(email) is False

    def test_may_with_date_does_match(self):
        """'May 15' should be detected as a real month-date."""
        processor = EmailProcessor.__new__(EmailProcessor)
        processor.extractor = None
        processor.task_engine = None
        email = RawEmail(
            email_id="test-may-date",
            subject="Deadline reminder",
            sender="boss@example.com",
            body="The report is due May 15. Please submit on time.",
            received_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        )
        assert processor.is_relevant_email(email) is True

    def test_one_on_one_without_time_no_false_positive(self):
        """Bug #5: '1:1' alone should NOT trigger as a time signal."""
        processor = EmailProcessor.__new__(EmailProcessor)
        processor.extractor = None
        processor.task_engine = None
        email = RawEmail(
            email_id="test-1on1",
            subject="Great catching up",
            sender="friend@example.com",
            body="It was nice to have a 1:1 chat with you about life.",
            received_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        )
        # Should not be relevant since there's no real date/time or scheduling signal
        assert processor.is_relevant_email(email) is False

    def test_real_time_does_match(self):
        """'3:00 pm' should be detected as a real time."""
        processor = EmailProcessor.__new__(EmailProcessor)
        processor.extractor = None
        processor.task_engine = None
        email = RawEmail(
            email_id="test-real-time",
            subject="Meeting tomorrow",
            sender="boss@example.com",
            body="Let's meet at 3:00 pm to discuss the project.",
            received_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        )
        assert processor.is_relevant_email(email) is True


class TestGetPrefilterLabels:
    def test_detects_payment_category(self, sample_payment_email):
        processor = EmailProcessor.__new__(EmailProcessor)
        processor.extractor = None
        processor.task_engine = None
        labels = processor.get_prefilter_labels(sample_payment_email)
        assert "payment" in labels

    def test_detects_project_deadline(self, sample_task_email):
        processor = EmailProcessor.__new__(EmailProcessor)
        processor.extractor = None
        processor.task_engine = None
        labels = processor.get_prefilter_labels(sample_task_email)
        # "by Friday" triggers time_sensitive or project_deadline
        assert len(labels) > 0


class TestLoadEmailsFromJson:
    def test_valid_json(self, tmp_path):
        data = [
            {
                "email_id": "e-001",
                "subject": "Test",
                "sender": "a@b.com",
                "body": "Hello",
                "received_at": "2026-05-07T11:00:00",
            }
        ]
        path = tmp_path / "emails.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        emails = load_emails_from_json(str(path))
        assert len(emails) == 1
        assert emails[0].email_id == "e-001"

    def test_non_array_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text('{"not": "an array"}', encoding="utf-8")
        with pytest.raises(ValueError, match="Expected a JSON array"):
            load_emails_from_json(str(path))

    def test_missing_keys_raises(self, tmp_path):
        data = [{"email_id": "e-001", "subject": "Test"}]
        path = tmp_path / "incomplete.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="missing required keys"):
            load_emails_from_json(str(path))


class TestProcessBatch:
    def test_handles_exceptions_without_crashing(self, temp_db):
        processor = EmailProcessor(db_path=temp_db)
        # Create an email that will process fine
        good_email = RawEmail(
            email_id="batch-001",
            subject="Invoice due tomorrow",
            sender="finance@test.com",
            body="Invoice #123 is due tomorrow. Please pay.",
            received_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        )
        results = processor.process_batch([good_email])
        assert len(results) == 1
        assert "error" not in results[0]
