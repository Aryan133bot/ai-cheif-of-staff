import pytest
from datetime import datetime, timezone

from models import DeadlineType, ExtractedDeadline, TaskStatus, Urgency
from task_engine import TaskEngine, build_fingerprint, priority_score


def _make_deadline(**overrides) -> ExtractedDeadline:
    defaults = dict(
        title="Send revised proposal",
        deadline_type=DeadlineType.TASK,
        urgency=Urgency.MEDIUM,
        source_quote="please send the revised proposal by Friday",
        confidence=0.75,
        deadline_date="Friday",
        assigned_to=None,
        counterparty="client@example.com",
        action_needed="Send proposal",
    )
    defaults.update(overrides)
    return ExtractedDeadline(**defaults)


class TestBuildFingerprint:
    def test_consistent_hash(self):
        d = _make_deadline()
        assert build_fingerprint(d) == build_fingerprint(d)

    def test_different_titles_produce_different_hashes(self):
        d1 = _make_deadline(title="Send proposal")
        d2 = _make_deadline(title="Review contract")
        assert build_fingerprint(d1) != build_fingerprint(d2)

    def test_normalizes_whitespace_and_case(self):
        d1 = _make_deadline(title="Send  Proposal")
        d2 = _make_deadline(title="send proposal")
        assert build_fingerprint(d1) == build_fingerprint(d2)


class TestPriorityScore:
    def test_critical_higher_than_low(self):
        critical = _make_deadline(urgency=Urgency.CRITICAL)
        low = _make_deadline(urgency=Urgency.LOW)
        assert priority_score(critical) > priority_score(low)

    def test_high_higher_than_medium(self):
        high = _make_deadline(urgency=Urgency.HIGH)
        medium = _make_deadline(urgency=Urgency.MEDIUM)
        assert priority_score(high) > priority_score(medium)

    def test_review_penalty(self):
        normal = _make_deadline(review_required=False)
        review = _make_deadline(review_required=True)
        assert priority_score(normal) > priority_score(review)

    def test_higher_confidence_means_higher_score(self):
        high_conf = _make_deadline(confidence=0.95)
        low_conf = _make_deadline(confidence=0.45)
        assert priority_score(high_conf) > priority_score(low_conf)


class TestTaskEngine:
    def test_init_creates_table(self, temp_db):
        engine = TaskEngine(db_path=temp_db)
        with engine._connect() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
            assert cursor.fetchone() is not None

    def test_upsert_creates_new_tasks(self, temp_db):
        engine = TaskEngine(db_path=temp_db)
        tasks = [_make_deadline(), _make_deadline(title="Pay invoice")]
        created, updated = engine.upsert_tasks(
            tasks=tasks,
            source_email_id="e-001",
            source_subject="Test",
            source_sender="sender@test.com",
            received_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        )
        assert created == 2
        assert updated == 0

    def test_upsert_updates_on_duplicate_fingerprint(self, temp_db):
        engine = TaskEngine(db_path=temp_db)
        task = _make_deadline()
        received = datetime(2026, 5, 7, tzinfo=timezone.utc)

        engine.upsert_tasks(
            tasks=[task],
            source_email_id="e-001",
            source_subject="Test",
            source_sender="s@test.com",
            received_at=received,
        )
        # Same task again — should update, not create
        created, updated = engine.upsert_tasks(
            tasks=[task],
            source_email_id="e-002",
            source_subject="Test 2",
            source_sender="s@test.com",
            received_at=received,
        )
        assert created == 0
        assert updated == 1

    def test_top_priorities_returns_sorted(self, temp_db):
        engine = TaskEngine(db_path=temp_db)
        tasks = [
            _make_deadline(title="Low task", urgency=Urgency.LOW),
            _make_deadline(title="Critical task", urgency=Urgency.CRITICAL),
        ]
        engine.upsert_tasks(
            tasks=tasks,
            source_email_id="e-001",
            source_subject="Test",
            source_sender="s@test.com",
            received_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        )
        top = engine.top_priorities(limit=5)
        assert len(top) == 2
        assert top[0]["title"] == "Critical task"

    def test_update_task_status(self, temp_db):
        engine = TaskEngine(db_path=temp_db)
        engine.upsert_tasks(
            tasks=[_make_deadline()],
            source_email_id="e-001",
            source_subject="Test",
            source_sender="s@test.com",
            received_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        )
        top = engine.top_priorities(limit=1)
        task_id = top[0]["id"]
        result = engine.update_task_status(task_id, TaskStatus.COMPLETED)
        assert result is True
        # Completed tasks should not appear in top priorities
        assert len(engine.top_priorities(limit=5)) == 0

    def test_update_task_status_invalid_raises(self, temp_db):
        engine = TaskEngine(db_path=temp_db)
        with pytest.raises(ValueError, match="Invalid status"):
            engine.update_task_status(1, "nonexistent_status")

    def test_get_review_queue(self, temp_db):
        engine = TaskEngine(db_path=temp_db)
        review_task = _make_deadline(title="Needs review", confidence=0.5, review_required=True)
        normal_task = _make_deadline(title="No review", confidence=0.9, review_required=False)
        engine.upsert_tasks(
            tasks=[review_task, normal_task],
            source_email_id="e-001",
            source_subject="Test",
            source_sender="s@test.com",
            received_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        )
        queue = engine.get_review_queue()
        assert len(queue) == 1
        assert queue[0]["title"] == "Needs review"
