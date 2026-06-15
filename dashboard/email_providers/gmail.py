"""Gmail provider — wraps the existing GmailClient from the email processor."""

import logging
import sys
from pathlib import Path

from .base import EmailProvider, FetchedEmail

logger = logging.getLogger(__name__)

# Add the email processor directory to sys.path so we can import from it
_email_proc_dir = str(Path(__file__).resolve().parent.parent.parent / "email processor")
if _email_proc_dir not in sys.path:
    sys.path.insert(0, _email_proc_dir)


import json

class GmailProvider(EmailProvider):
    """Gmail integration using the existing GmailClient."""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self._client = None
        import os
        env_creds = os.getenv("GMAIL_CREDENTIALS_PATH")
        self._credentials_path = Path(env_creds) if env_creds else Path(_email_proc_dir) / "credentials.json"

    @property
    def name(self) -> str:
        return "gmail"

    @property
    def display_name(self) -> str:
        return "Google Gmail"

    def is_configured(self) -> bool:
        """Check if credentials.json exists."""
        return self._credentials_path.exists()
        
    def _get_token(self) -> str | None:
        from db import get_user_gmail_token
        import os
        db_path = os.getenv("DASHBOARD_DB_PATH", "phase1_tasks.db")
        return get_user_gmail_token(db_path, self.user_id)

    def is_authenticated(self) -> bool:
        """Check if a valid token exists for this user."""
        token_json = self._get_token()
        if not token_json:
            return False
        try:
            from google.oauth2.credentials import Credentials
            from gmail_scopes import GMAIL_SCOPES
            creds = Credentials.from_authorized_user_info(json.loads(token_json), GMAIL_SCOPES)
            return creds is not None and (creds.valid or creds.refresh_token is not None)
        except Exception:
            return False

    def _ensure_client(self):
        """Lazily initialise the Gmail client."""
        if self._client is not None:
            return

        token_json = self._get_token()
        if not token_json or not self.is_authenticated():
            raise RuntimeError(
                "Gmail is not connected. Go to Settings → Connect Gmail to authorize access."
            )

        try:
            from gmail_client import GmailClient
            self._client = GmailClient(
                credentials_path=str(self._credentials_path),
                token_json=token_json,
            )
        except Exception as e:
            logger.error("Failed to initialise Gmail client: %s", e)
            raise RuntimeError(f"Gmail client initialisation failed: {e}") from e

    def fetch_emails(self, max_results: int = 50, mode: str = "unread") -> list[FetchedEmail]:
        """Fetch emails via the Gmail API."""
        self._ensure_client()
        try:
            raw_emails = self._client.fetch_recent(max_results=max_results, mode=mode)
        except Exception as e:
            logger.error("Gmail fetch failed: %s", e)
            raise RuntimeError(f"Failed to fetch emails from Gmail: {e}") from e

        return [
            FetchedEmail(
                email_id=e.email_id,
                subject=e.subject,
                sender=e.sender,
                body=e.body,
                received_at=e.received_at,
                thread_id=e.thread_id,
            )
            for e in raw_emails
        ]

    def send_reply(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        thread_id: str | None = None,
        in_reply_to_message_id: str | None = None,
    ) -> dict:
        """Send an approved reply through Gmail."""
        self._ensure_client()
        return self._client.send_reply(
            to=to,
            subject=subject,
            body=body,
            thread_id=thread_id,
            in_reply_to_message_id=in_reply_to_message_id,
        )

    def get_status(self) -> dict:
        """Enhanced status with Gmail-specific details."""
        import json

        base = super().get_status()
        base["credentials_path"] = str(self._credentials_path)
        base["token_path"] = "database"
        
        can_send = False
        token_json = self._get_token()
        if token_json:
            try:
                data = json.loads(token_json)
                scopes = data.get("scopes") or []
                if isinstance(scopes, str):
                    scopes = scopes.split()
                can_send = "https://www.googleapis.com/auth/gmail.send" in scopes
            except Exception:
                pass
                
        base["can_send"] = can_send
        return base
