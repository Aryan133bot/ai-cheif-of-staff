"""Send approved reply drafts through Gmail."""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import db

logger = logging.getLogger(__name__)

_email_proc_dir = Path(__file__).resolve().parent.parent / "email processor"
if str(_email_proc_dir) not in sys.path:
    sys.path.insert(0, str(_email_proc_dir))


def _resolve_gmail_ids(db_path: str, draft: dict) -> tuple[str | None, str | None]:
    message_id = draft.get("gmail_message_id")
    thread_id = draft.get("gmail_thread_id")
    if message_id:
        return message_id, thread_id

    task_id = draft.get("task_id")
    if not task_id:
        return None, thread_id

    task = db.get_task_by_id(db_path, int(task_id))
    if not task:
        return None, thread_id
    return task.get("source_email_id"), thread_id


def send_approved_reply(db_path: str, draft: dict) -> dict:
    """Send a reply draft via Gmail. Returns the Gmail API send response."""
    from gmail_scopes import token_includes_send_scope
    from email_providers.gmail import GmailProvider

    token_path = _email_proc_dir / "gmail_token.json"
    if not token_includes_send_scope(str(token_path)):
        raise PermissionError(
            "Gmail send permission is missing. Disconnect and reconnect Gmail in Settings "
            "to authorize sending replies."
        )

    provider = GmailProvider()
    if not provider.is_authenticated():
        raise PermissionError("Gmail is not connected. Connect Gmail in Settings first.")

    body = (draft.get("edited_text") or draft.get("draft_text") or "").strip()
    if not body:
        raise ValueError("Reply body is empty.")

    gmail_message_id, gmail_thread_id = _resolve_gmail_ids(db_path, draft)
    if not gmail_message_id:
        raise ValueError(
            "This draft is not linked to a Gmail message. Re-process the email or "
            "create a new draft from a task that came from Gmail."
        )

    return provider.send_reply(
        to=draft["original_sender"],
        subject=draft["original_subject"],
        body=body,
        thread_id=gmail_thread_id,
        in_reply_to_message_id=gmail_message_id,
    )


def mark_draft_sent(db_path: str, draft_id: int, gmail_response: dict) -> dict | None:
    sent_at = datetime.now(timezone.utc).isoformat()
    return db.update_reply_draft(
        db_path,
        draft_id,
        {
            "status": "sent",
            "sent_at": sent_at,
            "gmail_sent_message_id": gmail_response.get("id"),
            "send_error": None,
        },
    )


def mark_draft_send_failed(db_path: str, draft_id: int, error: str) -> None:
    db.release_reply_draft_send_lock(db_path, draft_id, error=error)
