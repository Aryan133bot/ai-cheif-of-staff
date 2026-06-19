import json
import logging
import re
from datetime import datetime
from pathlib import Path

from classifier import DeadlineExtractor
from models import RawEmail
from task_engine import TaskEngine

try:
    from filters import categorize_email, get_work_match_reasons
except Exception as _filter_import_err:
    import logging as _log
    _log.getLogger(__name__).warning(
        "filters.py could not be loaded (%s). All emails will be treated as relevant.",
        _filter_import_err,
    )
    categorize_email = lambda s, b: "work"  # fail-open: extract from everything
    get_work_match_reasons = lambda s, b: []

logger = logging.getLogger(__name__)


class EmailProcessor:
    def __init__(self, db_path: str = "phase1_tasks.db", user_id: int = None):
        self.extractor = DeadlineExtractor()
        self.task_engine = TaskEngine(db_path=db_path)
        self.user_id = user_id

    def process_email(self, email: RawEmail) -> dict:
        # Use the unified filters.py to classify email category
        category = categorize_email(email.subject, email.body)
        
        if category != "work":
            # Skip LLM extraction for non-work emails — saves API tokens
            return {
                "email_id": email.email_id,
                "extracted_count": 0,
                "created": 0,
                "updated": 0,
                "skipped": True,
            }

        # Secondary check: old prefilter labels for deadline-specific signals
        if not self.is_relevant_email(email):
            return {
                "email_id": email.email_id,
                "extracted_count": 0,
                "created": 0,
                "updated": 0,
                "skipped": True,
            }

        extracted = self.extractor.extract(
            email_subject=email.subject,
            email_body=email.body,
            sender=email.sender,
        )

        # Confidence gating: low-confidence items require manual review
        for item in extracted:
            item.review_required = item.confidence < 0.7

        created, updated = self.task_engine.upsert_tasks(
            tasks=extracted,
            source_email_id=email.email_id,
            source_subject=email.subject,
            source_sender=email.sender,
            received_at=email.received_at,
            user_id=self.user_id,
        )
        return {
            "email_id": email.email_id,
            "extracted_count": len(extracted),
            "created": created,
            "updated": updated,
        }

    def is_relevant_email(self, email: RawEmail) -> bool:
        return len(self.get_prefilter_labels(email)) > 0

    def get_prefilter_labels(self, email: RawEmail) -> list[str]:
        text = f"{email.subject}\n{email.body}".lower()
        labels: set[str] = set()
        date_time_patterns = [
            r"\b(today|tomorrow|tonight)\b",
            r"\b(this|next)\s+(week|month|quarter)\b",
            r"\b(mon|tue|wed|thu|fri|sat|sun)(day)?\b",
            # Month names — 'may' requires a following digit to avoid matching the verb
            r"\b(jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b",
            r"\bmay\s+\d{1,2}\b",
            # Time patterns — require am/pm to avoid matching '1:1' or version numbers
            r"\b\d{1,2}[:]\d{2}\s?(am|pm)\b",
            r"\b\d{1,2}\.\d{2}\s?(am|pm)\b",
            r"\b\d{1,2}\s?(am|pm)\b",
            r"\b\d{4}-\d{2}-\d{2}\b",
            r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
        ]
        has_time_signal = any(re.search(pattern, text) for pattern in date_time_patterns)
        has_scheduling_signal = any(
            token in text
            for token in {
                "schedule",
                "reschedule",
                "calendar",
                "invite",
                "invitation",
                "availability",
                "book",
                "slot",
                "tomorrow",
                "next ",
            }
        )

        category_keywords = {
            "meeting": {
                "meeting",
                "meet",
                "sync",
                "standup",
                "1:1",
                "one-on-one",
                "agenda",
                "invite",
                "invitation",
                "calendar",
                "reschedule",
                "availability",
                "call",
                "zoom",
                "teams",
                "gmeet",
            },
            "event": {
                "event",
                "webinar",
                "workshop",
                "conference",
                "session",
                "summit",
                "townhall",
                "kickoff",
                "demo day",
                "networking",
                "registration",
                "register",
            },
            "lunch": {
                "lunch",
                "breakfast",
                "dinner",
                "coffee chat",
                "meal",
                "brunch",
            },
            "project_deadline": {
                "deadline",
                "due",
                "overdue",
                "eod",
                "end of day",
                "deliver",
                "delivery",
                "submit",
                "milestone",
                "timeline",
                "eta",
                "ship",
                "launch",
            },
            "payment": {
                "invoice",
                "payment",
                "pay by",
                "bill",
                "renewal",
                "contract",
                "subscription",
                "receipt",
            },
            "follow_up": {
                "follow up",
                "follow-up",
                "action item",
                "decision",
                "approve",
                "approval",
                "confirm",
                "next steps",
            },
        }

        for label, keywords in category_keywords.items():
            if not any(keyword in text for keyword in keywords):
                continue

            # Avoid social false positives like "great meeting yesterday":
            # calendar-like categories need a time or scheduling cue.
            if label in {"meeting", "event", "lunch"} and not (has_time_signal or has_scheduling_signal):
                continue

            if label == "meeting" and "yesterday" in text and not (has_time_signal or has_scheduling_signal):
                continue

            labels.add(label)

        # If we only have a time signal but no explicit category, keep it as generic scheduling.
        if has_time_signal and not labels:
            labels.add("time_sensitive")

        return sorted(labels)

    def process_batch(self, emails: list[RawEmail]) -> list[dict]:
        results: list[dict] = []
        for email in emails:
            try:
                results.append(self.process_email(email))
            except Exception as exc:
                logger.error("Failed to process email '%s': %s", email.email_id, exc)
                results.append(
                    {
                        "email_id": email.email_id,
                        "extracted_count": 0,
                        "created": 0,
                        "updated": 0,
                        "error": str(exc),
                    }
                )
        return results


def load_emails_from_json(path: str) -> list[RawEmail]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Expected a JSON array in {path}, got {type(raw).__name__}")
    required_keys = {"email_id", "subject", "sender", "body", "received_at"}
    emails: list[RawEmail] = []
    for idx, item in enumerate(raw):
        missing = required_keys - set(item.keys())
        if missing:
            raise ValueError(f"Email at index {idx} is missing required keys: {sorted(missing)}")
        emails.append(
            RawEmail(
                email_id=item["email_id"],
                subject=item["subject"],
                sender=item["sender"],
                body=item["body"],
                received_at=datetime.fromisoformat(item["received_at"]),
            )
        )
    return emails
