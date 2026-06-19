"""
filters.py — Email Work/Miscellaneous Classifier
=================================================
Uses a phrase-based scoring system against an 11-category professional vocabulary bank.

Design principles:
- NO single-word keywords (too many false positives)
- All phrases are multi-word or highly specific
- Regex patterns are pre-compiled for correctness and speed
- Scores multiple matches before classifying (threshold-based)
- None-safe for subject and body inputs
"""

import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pre-compiled regex patterns (Finance / Support have numeric-anchored phrases)
# ---------------------------------------------------------------------------
_REGEX_PATTERNS = [
    re.compile(r"payment\s+of\s+[\w\s]+\s+is\s+due\s+on", re.IGNORECASE),
    re.compile(r"invoice\s+#?\s*\w+\s+is\s+still\s+outstanding", re.IGNORECASE),
    re.compile(r"your\s+ticket\s+#?\s*\w+\s+has\s+been\s+received", re.IGNORECASE),
    re.compile(r"please\s+block\s+.{3,40}\s+on\s+your\s+calendar", re.IGNORECASE),
    re.compile(r"effective\s+.{3,40}\s+the\s+following\s+policy\s+will\s+be", re.IGNORECASE),
    re.compile(r"deadline\s+(for|of)\s+this\s+(task|project|deliverable)\s+is", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# 11-Category phrase bank — PHRASES ONLY (no single words)
# Each phrase must be specific enough not to match casual conversation.
# ---------------------------------------------------------------------------
WORK_PHRASES: list[str] = [
    # ── Communication & Correspondence ──────────────────────────────────────
    "i hope this email finds you well",
    "i wanted to touch base regarding",
    "as discussed in our last meeting",
    "please find the attached document for your reference",
    "kindly revert at your earliest convenience",
    "at your earliest convenience",
    "i am writing to bring to your attention",
    "looking forward to your response",
    "per our conversation",
    "as discussed",
    "further to our",
    "as per our discussion",
    "trust this email finds you",
    "hope you are doing well",
    "this is in reference to",

    # ── Formal Documentation ─────────────────────────────────────────────────
    "please find enclosed the signed agreement",
    "this is to formally confirm our understanding",
    "pursuant to our earlier discussion",
    "kindly acknowledge receipt of this email",
    "this letter serves as an official notice",
    "for your records, please retain a copy",
    "for official records",
    "pursuant to",
    "kindly acknowledge receipt",
    "duly signed and executed",
    "terms and conditions apply",

    # ── Project & Task Management ────────────────────────────────────────────
    "could you please share an update on the status",
    "there seems to be a blocker on our end",
    "attaching the updated project tracker for your review",
    "please complete the following action items",
    "we are on track to meet the milestone",
    "kindly escalate if there are any dependencies",
    "action items from the meeting",
    "deliverable is due",
    "project timeline",
    "sprint planning",
    "end of day",
    "end of week",
    "project status update",
    "pending your approval",
    "kindly review and revert",
    "kindly share your inputs",
    "please review the attached",
    "action required",
    "action needed",

    # ── Sales & Business Development ─────────────────────────────────────────
    "i wanted to follow up on my previous email",
    "i'd love to schedule a quick call to explore",
    "we help companies like yours achieve",
    "would you be open to a brief demo",
    "just bumping this up in your inbox",
    "looking forward to discussing how we can add value",
    "could you point me to the right person who handles",
    "value proposition",
    "decision maker",
    "free consultation",
    "product demo",
    "sales proposal",
    "commercial proposal",
    "business proposal",

    # ── Marketing & PR ───────────────────────────────────────────────────────
    "we're excited to announce the launch of",
    "we are excited to announce",
    "i'm reaching out regarding a potential collaboration",
    "we'd love to feature your brand",
    "this is an exclusive offer available only to our partners",
    "we believe this aligns perfectly with your audience",
    "please find our media kit attached",
    "would you be interested in co-hosting a webinar",
    "press release",
    "media coverage",
    "brand partnership",
    "co-marketing",
    "joint venture",

    # ── HR & People Operations ───────────────────────────────────────────────
    "we are pleased to extend an offer of employment",
    "please find your appointment letter attached",
    "this is a reminder to complete your onboarding documents",
    "your performance review is scheduled for",
    "we regret to inform you that your application has not moved forward",
    "kindly go through the updated leave policy",
    "please confirm your acceptance by replying to this email",
    "offer of employment",
    "joining date",
    "notice period",
    "probation period",
    "performance improvement plan",
    "employee handbook",
    "benefits and compensation",
    "annual leave policy",
    "resignation accepted",
    "relieving letter",
    "experience letter",

    # ── Finance & Accounting ──────────────────────────────────────────────────
    "please find the invoice attached for services rendered",
    "kindly process the payment at the earliest to avoid penalties",
    "please share the remittance advice once payment is initiated",
    "attached is the monthly financial statement for your review",
    "could you confirm receipt of the purchase order",
    "payment is overdue",
    "outstanding balance",
    "purchase order",
    "credit note",
    "proforma invoice",
    "payment terms",
    "due date for payment",
    "kindly clear the dues",
    "amount due",
    "late payment",
    "billing statement",

    # ── Customer Support ─────────────────────────────────────────────────────
    "we sincerely apologize for the inconvenience caused",
    "we are escalating this to our technical team for immediate resolution",
    "the issue has been resolved and the ticket has been closed",
    "we value your feedback and will use it to improve our services",
    "as a goodwill gesture, we'd like to offer you",
    "could you please share more details so we can assist you better",
    "your concern has been noted",
    "our support team will reach out",
    "we have escalated this internally",
    "please raise a support ticket",
    "customer complaint",
    "service level agreement",
    "resolution time",
    "refund has been initiated",
    "refund processed",

    # ── Scheduling & Logistics ────────────────────────────────────────────────
    "could you please share your availability for a call",
    "i'd like to schedule a meeting",
    "please find the calendar invite attached for your confirmation",
    "unfortunately, i need to reschedule our meeting",
    "the agenda for the call is as follows",
    "looking forward to connecting",
    "meeting invitation",
    "calendar invite",
    "please confirm your attendance",
    "dial-in details",
    "conference call details",
    "would you be available for a call",
    "would love to connect",
    "let's sync up",
    "let's schedule a call",
    "your availability for",
    "slot for a meeting",
    "meeting request",
    "kindly confirm your presence",

    # ── Knowledge Sharing ─────────────────────────────────────────────────────
    "attaching the updated sop for the team's reference",
    "circulating the latest industry report for everyone's awareness",
    "please go through the attached training material",
    "here's a quick summary of the key takeaways from the session",
    "feel free to share this further with relevant stakeholders",
    "this might be useful as we plan our strategy",
    "sharing this for your reference",
    "for your kind perusal",
    "standard operating procedure",
    "knowledge transfer",
    "training material",
    "best practices document",
    "process documentation",

    # ── Networking & Relationship Building ────────────────────────────────────
    "it was a pleasure meeting you at",
    "i'd love to reconnect over a quick call",
    "suggested i reach out to you regarding",
    "thank you so much for your time today",
    "i wanted to introduce myself and",
    "would love to stay in touch and explore opportunities",
    "happy to return the favour whenever you need",
    "mutual connection",
    "warm introduction",
    "it was great catching up",
    "following up from our conversation at",
    "reaching out on behalf of",
    "looking forward to collaborating",
]

# ---------------------------------------------------------------------------
# Universal professional closings
# ---------------------------------------------------------------------------
UNIVERSAL_CLOSINGS: list[str] = [
    "looking forward to hearing from you",
    "please revert at your earliest convenience",
    "feel free to reach out if you have any queries",
    "thank you for your time and consideration",
    "happy to jump on a call if needed",
    "your prompt response would be greatly appreciated",
    "just following up in case this got missed",
    "do let me know if you need any further information",
    "please don't hesitate to reach out",
    "awaiting your response",
    "looking forward to your reply",
    "warm regards",
    "best regards",
    "kind regards",
    "thanks and regards",
    "yours sincerely",
    "yours faithfully",
]

# ---------------------------------------------------------------------------
# Subject-line signals: strong indicators just from the subject
# ---------------------------------------------------------------------------
SUBJECT_SIGNALS: list[str] = [
    "action required",
    "action needed",
    "follow up",
    "follow-up",
    "invoice",
    "payment due",
    "meeting request",
    "calendar invite",
    "job offer",
    "offer letter",
    "appointment letter",
    "project update",
    "status update",
    "reminder:",
    "urgent:",
    "re: ",
    "fwd: ",
    "[external]",
    "request for proposal",
    "rfp",
    "purchase order",
    "contract",
    "agreement",
    "introduction -",
    "introduction:",
    "connecting you with",
]

# Scoring thresholds
# A single strong phrase match from body = 1 point
# A subject signal = 1 point  
# A regex match = 1 point
# Emails scoring >= WORK_THRESHOLD are classified as 'work'
WORK_THRESHOLD = 1  # At least one match required

def _score_email(subject_lower: str, body_lower: str) -> int:
    """
    Returns a work-relevance score.
    Higher = more likely to be a work email.
    """
    score = 0
    full_text = f"{subject_lower}\n{body_lower}"

    # Subject signals (faster check first)
    for signal in SUBJECT_SIGNALS:
        if signal in subject_lower:
            score += 1
            break  # one subject signal is enough for a point

    # Universal closings in body
    for closing in UNIVERSAL_CLOSINGS:
        if closing in body_lower:
            score += 1
            break  # one closing is enough for a point

    # Phrase matches across full text
    matches = 0
    for phrase in WORK_PHRASES:
        if phrase in full_text:
            matches += 1

    score += matches

    # Pre-compiled regex patterns (most specific — bonus weight)
    for pattern in _REGEX_PATTERNS:
        if pattern.search(full_text):
            score += 1

    return score


def categorize_email(subject: str | None, body: str | None) -> str:
    """
    Classifies an email as 'work' or 'miscellaneous'.
    
    Returns:
        'work'          — professional, actionable email
        'miscellaneous' — newsletters, promotions, alerts, spam
    """
    # Null-safe: treat None as empty string
    subject_lower = (subject or "").lower().strip()
    body_lower = (body or "").lower().strip()

    # Edge case: completely empty email
    if not subject_lower and not body_lower:
        return "miscellaneous"

    try:
        score = _score_email(subject_lower, body_lower)
        return "work" if score >= WORK_THRESHOLD else "miscellaneous"
    except Exception as exc:
        logger.warning("categorize_email failed, defaulting to miscellaneous: %s", exc)
        return "miscellaneous"


def get_work_match_reasons(subject: str | None, body: str | None) -> list[str]:
    """
    Debug helper: returns a list of phrases/patterns that triggered 'work' classification.
    Useful for auditing and improving the vocabulary bank.
    """
    subject_lower = (subject or "").lower().strip()
    body_lower = (body or "").lower().strip()
    full_text = f"{subject_lower}\n{body_lower}"

    reasons: list[str] = []

    for signal in SUBJECT_SIGNALS:
        if signal in subject_lower:
            reasons.append(f"[subject-signal] {signal!r}")

    for closing in UNIVERSAL_CLOSINGS:
        if closing in body_lower:
            reasons.append(f"[closing] {closing!r}")

    for phrase in WORK_PHRASES:
        if phrase in full_text:
            reasons.append(f"[phrase] {phrase!r}")

    for pattern in _REGEX_PATTERNS:
        if pattern.search(full_text):
            reasons.append(f"[regex] {pattern.pattern!r}")

    return reasons
