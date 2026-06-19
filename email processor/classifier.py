import json
import logging
import os
import re
from datetime import datetime, timezone

from dateutil import parser as dateutil_parser
from dotenv import load_dotenv

from models import DeadlineType, ExtractedDeadline, Urgency

load_dotenv()  # Reads your .env file and loads the keys into the environment

logger = logging.getLogger(__name__)

# ── The prompt we send to the LLM ──────────────────────────────────────────
# This is the most important part of the whole system.
# A better prompt = better results. We'll improve this over time.
SYSTEM_PROMPT = """You are an expert email analyst for a busy executive.
Your job is to read emails and extract EVERY deadline, commitment, 
time-sensitive item, or action with a due date.

You must return a JSON array. Each item must have exactly these fields:
{
  "title": "Short 1-line description of what needs to happen",
  "deadline_type": one of ["task", "meeting", "payment", "follow_up", "decision", "delivery", "other"],
  "urgency": one of ["critical", "high", "medium", "low"],
  "source_quote": "The exact sentence from the email that contains the deadline",
  "confidence": a number from 0.0 to 1.0 (how sure you are this is a real deadline),
  "deadline_date": "The date or timeframe mentioned, e.g. 'Friday', '2025-05-10', 'next week', or null if vague",
  "assigned_to": "Who needs to do this action, or null if unclear",
  "counterparty": "Name or email of the other person involved, or null",
  "action_needed": "What specific action needs to be taken, or null"
}

Urgency rules:
- critical: overdue OR due today
- high: due within 48 hours
- medium: due within 7 days  
- low: due later than 7 days or timeframe is vague

If the email has NO deadlines at all, return an empty array: []

Return ONLY the JSON array. No explanation, no markdown, no backticks."""


# ── LLM provider detection ─────────────────────────────────────────────────

def _detect_llm_provider():
    """
    Detect which LLM to use based on available API keys.
    Priority: GEMINI_API_KEY (free) > ANTHROPIC_API_KEY (paid).
    Returns: ('gemini', key), ('anthropic', key), or (None, None)
    """
    gemini_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if gemini_key and gemini_key.lower() != "your-gemini-key-here":
        return "gemini", gemini_key

    anthropic_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if anthropic_key and anthropic_key.lower() != "your-anthropic-key-here" and anthropic_key.startswith("sk-ant-"):
        return "anthropic", anthropic_key

    return None, None


class DeadlineExtractor:

    def __init__(self):
        self.provider, self._api_key = _detect_llm_provider()
        self.client = None

        if self.provider == "gemini":
            try:
                from google import genai
                self.client = genai.Client(api_key=self._api_key)
                self.model = "gemini-3.5-flash"
                logger.info("Using Gemini Flash for deadline extraction (free tier)")
            except Exception as e:
                logger.error("Failed to initialise Gemini client: %s", e)
                self.provider = None
        elif self.provider == "anthropic":
            try:
                from anthropic import Anthropic
                self.client = Anthropic(api_key=self._api_key)
                self.model = "claude-haiku-4-5-20251001"
                logger.info("Using Claude Haiku for deadline extraction")
            except Exception as e:
                logger.error("Failed to initialise Anthropic client: %s", e)
                self.provider = None
        else:
            logger.warning(
                "No GEMINI_API_KEY or ANTHROPIC_API_KEY found — using rule-based fallback. "
                "Add a key to .env to enable AI extraction."
            )

    def extract(self, email_subject: str, email_body: str, sender: str) -> list[ExtractedDeadline]:
        """
        Send one email to the LLM and get back a list of deadlines.
        This is the core function — everything else calls this.
        """

        if len(email_body) > 4000:
            logger.warning(
                "Email body truncated from %d to 4000 chars for subject: %s",
                len(email_body),
                email_subject,
            )
            email_body = email_body[:4000]

        # Build the message we send to the LLM
        user_message = f"""FROM: {sender}
SUBJECT: {email_subject}

BODY:
{email_body}

Extract all deadlines from this email."""

        if not self.client:
            return self._fallback_extract(email_body, sender)

        try:
            if self.provider == "gemini":
                raw_text = self._call_gemini(user_message)
            else:
                raw_text = self._call_anthropic(user_message)

            return self._parse_response(raw_text)

        except Exception as e:
            logger.error("Error extracting from email '%s': %s", email_subject, e)
            err_str = str(e).lower()
            if "authentication" in err_str or "invalid" in err_str or "api_key" in err_str:
                self.client = None
            return self._fallback_extract(email_body, sender)

    def _call_gemini(self, user_message: str) -> str:
        """Call Gemini Flash and return the raw text response."""
        response = self.client.models.generate_content(
            model=self.model,
            contents=f"{SYSTEM_PROMPT}\n\n---\n\n{user_message}",
        )
        return response.text.strip()

    def _call_anthropic(self, user_message: str) -> str:
        """Call Claude and return the raw text response."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text.strip()

    def _parse_response(self, raw_text: str) -> list[ExtractedDeadline]:
        """
        Convert Claude's JSON text into ExtractedDeadline objects.
        The underscore prefix means this is an internal helper method.
        """
        try:
            # Sometimes Claude adds ```json ... ``` — strip it just in case
            clean = re.sub(r"```json|```", "", raw_text).strip()
            data = json.loads(clean)

            deadlines = []
            for item in data:
                # Skip low-confidence extractions (below 40%)
                if item.get("confidence", 0) < 0.4:
                    continue

                deadline = ExtractedDeadline(
                    title=item["title"],
                    deadline_type=self._safe_enum(
                        DeadlineType,
                        item.get("deadline_type", "other"),
                        DeadlineType.OTHER,
                    ),
                    urgency=self._safe_enum(
                        Urgency,
                        item.get("urgency", "low"),
                        Urgency.LOW,
                    ),
                    source_quote=item["source_quote"],
                    confidence=float(item["confidence"]),
                    deadline_date=item.get("deadline_date"),
                    assigned_to=item.get("assigned_to"),
                    counterparty=item.get("counterparty"),
                    action_needed=item.get("action_needed"),
                )
                deadlines.append(deadline)

            return deadlines

        except json.JSONDecodeError as e:
            logger.error("Failed to parse Claude response as JSON: %s", e)
            logger.debug("Raw response was: %s", raw_text[:300])
            return []

    def _safe_enum(self, enum_cls, raw_value, default):
        try:
            if raw_value is None:
                return default
            return enum_cls(raw_value)
        except (ValueError, TypeError):
            return default

    def _fallback_extract(self, email_body: str, sender: str) -> list[ExtractedDeadline]:
        """
        Rule-based fallback used when LLM is unavailable.
        Handles long operational emails with bullets, meetings, deadlines, and action items.
        """
        lines = [line.strip() for line in email_body.splitlines() if line.strip()]
        time_phrases = r"(today|tomorrow|tonight|eod|end of day|this week|next week|next monday|next tuesday|next wednesday|next thursday|next friday|monday|tuesday|wednesday|thursday|friday|saturday|sunday|[a-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?|\d{1,2}[:.]\d{2}\s?(?:am|pm)?|\d{1,2}\s?(?:am|pm)|\d{1,2}/\d{1,2}(?:/\d{2,4})?|[a-z]{3,9}\s+\d{1,2}\b)"
        patterns = [
            rf"\bby\s+{time_phrases}",
            rf"\bbefore\s+{time_phrases}",
            rf"\bdue\s+(?:on\s+)?{time_phrases}",
            rf"\b(?:meeting|call|sync|review)\s*:\s*.*?{time_phrases}",
            rf"\b(?:deadline|completion|submission|approval)\s*:\s*.*?{time_phrases}",
            rf"\bbetween\s+{time_phrases}\s*(?:-|to|–)\s*{time_phrases}",
        ]
        trigger_words = {
            "by ",
            "before ",
            "due ",
            "deadline",
            "meeting",
            "call",
            "sync",
            "review",
            "submit",
            "share",
            "send",
            "approval",
            "approve",
            "must",
            "need to",
            "schedule",
            "escalate",
            "critical",
            "action item",
            "completion",
            "submission",
        }

        extracted: list[ExtractedDeadline] = []
        seen: set[tuple[str, str | None]] = set()
        for line in lines:
            cleaned = re.sub(r"^[\*\-\u2022]\s*", "", line).strip()
            lower = cleaned.lower()
            if not any(k in lower for k in trigger_words):
                continue

            deadline_date = self._extract_deadline_date(lower, patterns)
            if not deadline_date and len(cleaned) < 25:
                continue

            deadline_type = self._infer_deadline_type(lower)
            urgency = self._infer_urgency(lower, deadline_date)
            key = (cleaned.lower(), deadline_date)
            if key in seen:
                continue
            seen.add(key)

            confidence = 0.55 if deadline_date else 0.45
            if deadline_type == DeadlineType.MEETING:
                confidence += 0.1
            elif deadline_type in {DeadlineType.DECISION, DeadlineType.FOLLOW_UP}:
                confidence += 0.05

            extracted.append(
                ExtractedDeadline(
                    title=cleaned[:120],
                    deadline_type=deadline_type,
                    urgency=urgency,
                    source_quote=cleaned,
                    confidence=min(confidence, 0.85),
                    deadline_date=deadline_date,
                    assigned_to=self._extract_assignee(cleaned),
                    counterparty=sender,
                    action_needed=cleaned[:200],
                )
            )

        return extracted

    def _extract_deadline_date(self, lower_line: str, patterns: list[str]) -> str | None:
        date_patterns = [
            r"\b(today|tomorrow|tonight|eod|end of day|this week|next week)\b",
            r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            r"\b(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?\b",
            r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
            r"\b\d{1,2}[:.]\d{2}\s?(?:am|pm)?\b",
            r"\b\d{1,2}\s?(?:am|pm)\b",
        ]
        for pattern in date_patterns:
            m = re.search(pattern, lower_line, flags=re.IGNORECASE)
            if m:
                return m.group(0)

        for pattern in patterns:
            match = re.search(pattern, lower_line, flags=re.IGNORECASE)
            if match:
                return match.group(0)
        return None

    def _infer_deadline_type(self, lower_line: str) -> DeadlineType:
        if any(k in lower_line for k in ["meeting", "call", "sync", "review meeting", "standup"]):
            return DeadlineType.MEETING
        if any(k in lower_line for k in ["invoice", "payment", "cost", "bill", "allocation"]):
            return DeadlineType.PAYMENT
        if any(k in lower_line for k in ["approval", "approve", "sign-off", "decision"]):
            return DeadlineType.DECISION
        if any(k in lower_line for k in ["follow up", "follow-up", "next steps", "escalate"]):
            return DeadlineType.FOLLOW_UP
        if any(k in lower_line for k in ["delivery", "deliver", "shipment"]):
            return DeadlineType.DELIVERY
        return DeadlineType.TASK

    def _infer_urgency(self, lower_line: str, deadline_date: str | None) -> Urgency:
        if any(k in lower_line for k in ["critical", "urgent", "asap", "immediate", "today", "eod"]):
            return Urgency.CRITICAL
        if deadline_date:
            dl = deadline_date.lower()
            if any(k in dl for k in ["tomorrow", "tonight"]):
                return Urgency.HIGH
            # Attempt to parse the deadline into an actual date for real distance calculation
            try:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                parsed = dateutil_parser.parse(dl, fuzzy=True, default=now)
                delta = (parsed - now).days
                if delta <= 0:
                    return Urgency.CRITICAL
                elif delta <= 2:
                    return Urgency.HIGH
                elif delta <= 7:
                    return Urgency.MEDIUM
                else:
                    return Urgency.LOW
            except (ValueError, OverflowError):
                return Urgency.MEDIUM
        return Urgency.LOW

    def _extract_assignee(self, line: str) -> str | None:
        owner_match = re.match(r"^([A-Z][a-z]+(?:\s+and\s+[A-Z][a-z]+)?)\s+to\s+", line)
        if owner_match:
            return owner_match.group(1)
        return None

    def _is_valid_api_key(self, api_key: str) -> bool:
        if not api_key:
            return False
        if api_key.lower() == "your-anthropic-key-here":
            return False
        # Current Anthropic API keys start with sk-ant-.
        return api_key.startswith("sk-ant-")