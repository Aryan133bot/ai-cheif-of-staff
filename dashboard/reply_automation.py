"""Auto-create reply drafts after email processing."""

import logging

import db
from reply_generator import generate_reply_text

logger = logging.getLogger(__name__)


def auto_create_reply_drafts_for_emails(db_path: str, emails, user_id: int) -> int:
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

            draft_text, model_used, confidence, auto_send_eligible = generate_reply_text(
                original_subject=task["source_subject"],
                original_sender=task["source_sender"],
                original_body=body or task.get("source_quote", ""),
                reply_intent="acknowledge",
            )
            
            # Check if user has auto_send enabled
            user = db.get_user_by_id(db_path, user_id)
            is_auto_send_enabled = user.get("auto_send_enabled", False) if user else False
            
            is_auto_sent = False
            status = "pending"

            draft = db.create_reply_draft(
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
                    "status": status,
                    "is_auto_sent": is_auto_sent,
                },
                user_id=user_id,
            )
            draft_id = draft["id"]
            
            if is_auto_send_enabled and auto_send_eligible and confidence > 0.7:
                try:
                    from reply_sender import send_approved_reply, mark_draft_sent, mark_draft_send_failed
                    draft_to_send = db.get_reply_draft(db_path, draft_id, user_id=user_id)
                    if draft_to_send:
                        gmail_resp = send_approved_reply(db_path, draft_to_send, user_id=user_id)
                        db.update_reply_draft(db_path, draft_id, {"is_auto_sent": True}, user_id=user_id)
                        mark_draft_sent(db_path, draft_id, gmail_resp, user_id=user_id)
                        logger.info("Auto-sent reply for task %s", task_id)
                except Exception as e:
                    logger.error("Auto-send failed for task %s: %s", task_id, e)
                    # It falls back to pending automatically since mark_draft_sent isn't called

            created += 1
            logger.info(
                "Auto-created reply draft for task %s (email %s)",
                task_id,
                email.email_id,
            )

    return created
