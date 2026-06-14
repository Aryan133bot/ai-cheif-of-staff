"""Auto-create reply drafts after email processing."""

import logging

import db
from reply_generator import generate_reply_text

logger = logging.getLogger(__name__)


def auto_create_reply_drafts_for_emails(db_path: str, emails) -> int:
    """
    For each processed email, create a pending reply draft per linked task
    when no pending draft already exists.
    """
    created = 0
    for email in emails:
        tasks = db.get_tasks_by_source_email_id(db_path, email.email_id)
        if not tasks:
            continue

        body = email.body or ""
        for task in tasks:
            task_id = task["id"]
            if db.has_active_reply_draft_for_task(db_path, task_id):
                continue

            draft_text, model_used, confidence = generate_reply_text(
                original_subject=task["source_subject"],
                original_sender=task["source_sender"],
                original_body=body or task.get("source_quote", ""),
                reply_intent="acknowledge",
            )

            db.create_reply_draft(
                db_path,
                {
                    "task_id": task_id,
                    "original_subject": task["source_subject"],
                    "original_sender": task["source_sender"],
                    "original_body": body[:2000] or task.get("source_quote", ""),
                    "reply_intent": "acknowledge",
                    "draft_text": draft_text,
                    "model_used": model_used,
                    "confidence": confidence,
                    "gmail_message_id": email.email_id,
                    "gmail_thread_id": getattr(email, "thread_id", None),
                },
            )
            created += 1
            logger.info(
                "Auto-created reply draft for task %s (email %s)",
                task_id,
                email.email_id,
            )

    return created
