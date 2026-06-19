"""Email processing service — orchestrates fetching, classifying, and storing tasks."""

import json
import logging
import os
import sys
from pathlib import Path

import db
from email_providers.base import FetchedEmail
from email_providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

# Ensure email processor is importable
_email_proc_dir = str(Path(__file__).resolve().parent.parent / "email processor")
if _email_proc_dir not in sys.path:
    sys.path.insert(0, _email_proc_dir)

from models import RawEmail

AUTO_REPLY_DRAFT = os.getenv("AUTO_REPLY_DRAFT", "1").strip().lower() in ("1", "true", "yes")


class EmailService:
    """Bridges email providers with the classification pipeline."""

    def __init__(self, db_path: str, registry: ProviderRegistry, user_id: int):
        self.db_path = db_path
        self.registry = registry
        self.user_id = user_id
        self._processor = None

    def _ensure_processor(self):
        """Lazily initialise the email processor."""
        if self._processor is None:
            from processor import EmailProcessor
            self._processor = EmailProcessor(self.db_path, self.user_id)

    def process_new_emails(
        self,
        trigger: str = "manual",
        max_results: int = 50,
        mode: str = "unread",
    ) -> dict:
        """Fetch emails from all providers and run through the processing pipeline.

        Returns a summary dict with counts and any errors.
        """
        run_id = db.create_processing_run(self.db_path, trigger=trigger, user_id=self.user_id)

        try:
            # Fetch from all connected providers
            fetched = self.registry.fetch_all(max_results=max_results, mode=mode)
            logger.info("Fetched %d emails across all providers", len(fetched))

            if not fetched:
                fetch_errors = getattr(self.registry, '_last_fetch_errors', [])
                db.complete_processing_run(
                    self.db_path, run_id,
                    emails_fetched=0, status="completed",
                    error_details=json.dumps(fetch_errors) if fetch_errors else None,
                    user_id=self.user_id,
                )
                return {
                    "ok": True,
                    "run_id": run_id,
                    "emails_fetched": 0,
                    "emails_processed": 0,
                    "emails_skipped": 0,
                    "tasks_created": 0,
                    "tasks_updated": 0,
                    "errors": len(fetch_errors),
                    "reply_drafts_created": 0,
                    "fetch_errors": fetch_errors,
                    "message": "No unread emails found in Primary inbox." if not fetch_errors else "; ".join(fetch_errors),
                }

            # Convert to RawEmail for the processor
            raw_emails = [
                RawEmail(
                    email_id=e.email_id,
                    subject=e.subject,
                    sender=e.sender,
                    body=e.body,
                    received_at=e.received_at,
                    thread_id=e.thread_id,
                )
                for e in fetched
            ]

            # Save ALL fetched emails to the database for visibility
            try:
                from filters import categorize_email
            except Exception as _fe:
                logger.warning(
                    "filters.py import failed (%s) — all emails will be labelled miscellaneous.", _fe
                )
                categorize_email = lambda s, b: "miscellaneous"

            email_records = [
                {
                    "email_id": e.email_id,
                    "subject": e.subject,
                    "sender": e.sender,
                    "body": e.body,
                    "received_at": str(e.received_at) if e.received_at else "",
                    "thread_id": e.thread_id,
                    "processing_status": "pending",
                    "category": categorize_email(e.subject, e.body),
                }
                for e in fetched
            ]
            db.save_fetched_emails(self.db_path, email_records, run_id, self.user_id)

            # Process through the pipeline
            self._ensure_processor()
            results = self._processor.process_batch(raw_emails)

            # Update processing status for each email
            conn = db.get_db(self.db_path)
            try:
                for email, result in zip(fetched, results):
                    if isinstance(result, dict):
                        if result.get("skipped"):
                            status = "skipped"
                        elif result.get("error"):
                            status = "error"
                        else:
                            status = "processed"
                    else:
                        status = "error"
                    conn.execute(
                        "UPDATE fetched_emails SET processing_status = ? WHERE email_id = ? AND run_id = ? AND user_id = ?",
                        (status, email.email_id, run_id, self.user_id),
                    )
                conn.commit()
            finally:
                conn.close()

            # Aggregate results
            processed = 0
            skipped = 0
            created = 0
            updated = 0
            errors = 0
            error_msgs = []

            for r in results:
                if isinstance(r, dict):
                    if r.get("skipped"):
                        skipped += 1
                    elif r.get("error"):
                        errors += 1
                        error_msgs.append(r.get("error", "Unknown error"))
                    else:
                        processed += 1
                        created += r.get("created", 0)
                        updated += r.get("updated", 0)

            db.complete_processing_run(
                self.db_path, run_id,
                emails_fetched=len(fetched),
                emails_processed=processed,
                emails_skipped=skipped,
                tasks_created=created,
                tasks_updated=updated,
                errors=errors,
                error_details=json.dumps(error_msgs) if error_msgs else None,
                status="completed",
            )

            drafts_created = 0
            if AUTO_REPLY_DRAFT and processed > 0:
                try:
                    from reply_automation import auto_create_reply_drafts_for_emails

                    processed_emails = [
                        email
                        for email, result in zip(raw_emails, results, strict=True)
                        if isinstance(result, dict)
                        and not result.get("skipped")
                        and not result.get("error")
                        and (
                            result.get("created", 0) > 0
                            or result.get("updated", 0) > 0
                        )
                    ]
                    drafts_created = auto_create_reply_drafts_for_emails(
                        self.db_path, processed_emails, self.user_id
                    )
                except Exception as draft_err:
                    logger.error("Auto reply draft creation failed: %s", draft_err)

            summary = {
                "ok": True,
                "run_id": run_id,
                "emails_fetched": len(fetched),
                "emails_processed": processed,
                "emails_skipped": skipped,
                "tasks_created": created,
                "tasks_updated": updated,
                "errors": errors,
                "reply_drafts_created": drafts_created,
            }
            logger.info("Processing run %d completed: %s", run_id, summary)
            return summary

        except Exception as e:
            logger.error("Processing run %d failed: %s", run_id, e)
            db.complete_processing_run(
                self.db_path, run_id,
                errors=1,
                error_details=json.dumps([str(e)]),
                status="failed",
                user_id=self.user_id,
            )
            return {
                "ok": False,
                "run_id": run_id,
                "error": str(e),
            }

    def get_status(self) -> dict:
        """Get the current status of the email processing system."""
        latest = db.get_latest_processing_run(self.db_path, user_id=self.user_id)
        return {
            "providers": self.registry.list_providers(),
            "last_run": dict(latest) if latest else None,
        }

    def get_history(self, limit: int = 20) -> list[dict]:
        """Get the recent processing run history."""
        return db.get_processing_history(self.db_path, limit=limit, user_id=self.user_id)
