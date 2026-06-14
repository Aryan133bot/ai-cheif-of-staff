import json
import pytest

from models import DeadlineType, ExtractedDeadline, Urgency
from classifier import DeadlineExtractor


@pytest.fixture
def extractor():
    return DeadlineExtractor()


class TestFallbackExtract:
    def test_extracts_from_bullet_email(self, extractor):
        body = (
            "Hi team, here are the action items:\n"
            "- Submit the quarterly report by Friday\n"
            "- Schedule a review meeting for next week\n"
            "- Share the budget allocation by end of day\n"
        )
        results = extractor._fallback_extract(body, "manager@company.com")
        assert len(results) >= 2
        assert all(isinstance(r, ExtractedDeadline) for r in results)

    def test_returns_empty_for_no_deadlines(self, extractor):
        body = "Thanks for the update! Everything looks great. Have a nice day."
        results = extractor._fallback_extract(body, "friend@example.com")
        assert results == []

    def test_extracts_meeting_type(self, extractor):
        body = "Please schedule a sync call for next Monday to discuss progress."
        results = extractor._fallback_extract(body, "boss@company.com")
        assert len(results) >= 1
        meeting_results = [r for r in results if r.deadline_type == DeadlineType.MEETING]
        assert len(meeting_results) >= 1


class TestInferDeadlineType:
    def test_meeting(self, extractor):
        assert extractor._infer_deadline_type("schedule a sync call") == DeadlineType.MEETING

    def test_payment(self, extractor):
        assert extractor._infer_deadline_type("please process the invoice") == DeadlineType.PAYMENT

    def test_decision(self, extractor):
        assert extractor._infer_deadline_type("need your approval on this") == DeadlineType.DECISION

    def test_follow_up(self, extractor):
        assert extractor._infer_deadline_type("follow up on the contract") == DeadlineType.FOLLOW_UP

    def test_delivery(self, extractor):
        assert extractor._infer_deadline_type("the delivery is scheduled") == DeadlineType.DELIVERY

    def test_default_is_task(self, extractor):
        assert extractor._infer_deadline_type("please send the report") == DeadlineType.TASK


class TestInferUrgency:
    def test_critical_for_today(self, extractor):
        assert extractor._infer_urgency("this is urgent, needed today", "today") == Urgency.CRITICAL

    def test_critical_for_urgent_keyword(self, extractor):
        assert extractor._infer_urgency("this is urgent please respond", None) == Urgency.CRITICAL

    def test_high_for_tomorrow(self, extractor):
        assert extractor._infer_urgency("please send it", "tomorrow") == Urgency.HIGH

    def test_low_for_no_deadline(self, extractor):
        assert extractor._infer_urgency("please send the report when ready", None) == Urgency.LOW


class TestExtractAssignee:
    def test_extracts_name(self, extractor):
        assert extractor._extract_assignee("Alice to send the report by Friday") == "Alice"

    def test_extracts_two_names(self, extractor):
        assert extractor._extract_assignee("Alice and Bob to review the contract") == "Alice and Bob"

    def test_returns_none_when_no_pattern(self, extractor):
        assert extractor._extract_assignee("send the report by Friday") is None


class TestParseResponse:
    def test_valid_json_array(self, extractor):
        raw = json.dumps([
            {
                "title": "Send report",
                "deadline_type": "task",
                "urgency": "high",
                "source_quote": "send the report by Friday",
                "confidence": 0.9,
                "deadline_date": "Friday",
                "assigned_to": None,
                "counterparty": None,
                "action_needed": "Send report",
            }
        ])
        results = extractor._parse_response(raw)
        assert len(results) == 1
        assert results[0].title == "Send report"
        assert results[0].urgency == Urgency.HIGH

    def test_json_wrapped_in_backticks(self, extractor):
        raw = '```json\n[{"title": "Test", "deadline_type": "task", "urgency": "low", "source_quote": "test", "confidence": 0.8}]\n```'
        results = extractor._parse_response(raw)
        assert len(results) == 1

    def test_invalid_json_returns_empty(self, extractor):
        results = extractor._parse_response("this is not json at all")
        assert results == []

    def test_low_confidence_items_filtered(self, extractor):
        raw = json.dumps([
            {
                "title": "Vague item",
                "deadline_type": "other",
                "urgency": "low",
                "source_quote": "maybe sometime",
                "confidence": 0.2,
            }
        ])
        results = extractor._parse_response(raw)
        assert results == []


class TestIsValidApiKey:
    def test_rejects_empty(self, extractor):
        assert extractor._is_valid_api_key("") is False

    def test_rejects_placeholder(self, extractor):
        assert extractor._is_valid_api_key("your-anthropic-key-here") is False

    def test_accepts_valid_prefix(self, extractor):
        assert extractor._is_valid_api_key("sk-ant-abc123") is True

    def test_rejects_wrong_prefix(self, extractor):
        assert extractor._is_valid_api_key("sk-wrong-prefix") is False
