from dataclasses import dataclass
from datetime import datetime
from enum import Enum

# ── Deadline categories ─────────────────────────────────
class DeadlineType(Enum):
    TASK        = "task"         # "Please send the report by Friday"
    MEETING     = "meeting"      # "Let's meet Tuesday at 3pm"
    PAYMENT     = "payment"      # "Invoice due by March 15"
    FOLLOW_UP   = "follow_up"    # "I'll get back to you next week"
    DECISION    = "decision"     # "We need your answer by EOD"
    DELIVERY    = "delivery"     # "Shipment arrives Thursday"
    OTHER       = "other"        # Anything else with a date

# ── Urgency levels ───────────────────────────────────────
class Urgency(Enum):
    CRITICAL = "critical"    # Due today or overdue
    HIGH     = "high"        # Due within 48 hours
    MEDIUM   = "medium"      # Due within 7 days
    LOW      = "low"         # Due later or vague


class TaskStatus(Enum):
    CREATED     = "created"
    REVIEWED    = "reviewed"
    IN_PROGRESS = "in_progress"
    BLOCKED     = "blocked"
    COMPLETED   = "completed"
    DISMISSED   = "dismissed"

# ── A single extracted deadline ──────────────────────────
# Think of this as a row in a spreadsheet.
# Every deadline we find becomes one of these objects.
@dataclass
class ExtractedDeadline:
    title:          str              # Short description of the deadline
    deadline_type:  DeadlineType     # Category (task, meeting, payment...)
    urgency:        Urgency          # How urgent is it
    source_quote:   str              # Exact sentence from the email
    confidence:     float            # 0.0 to 1.0 — how sure is the AI
    
    # Optional fields — not every email has all of these
    deadline_date:  str | None = None   # "2025-05-10" or "Friday" or None
    assigned_to:    str | None = None   # Who needs to do it
    counterparty:   str | None = None   # Who sent or is involved
    action_needed:  str | None = None   # What action is required
    review_required: bool = False          # Needs human review before tracking

@dataclass
class RawEmail:
    email_id: str
    subject: str
    sender: str
    body: str
    received_at: datetime
    thread_id: str | None = None