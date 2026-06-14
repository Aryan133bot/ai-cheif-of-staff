import base64
import logging
import os
import re
import time
from email.mime.text import MIMEText
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from gmail_scopes import GMAIL_SCOPES
from models import RawEmail

load_dotenv()

logger = logging.getLogger(__name__)

SCOPES = GMAIL_SCOPES


def parse_sender_email(sender: str) -> str:
    """Extract an email address from a From header value."""
    match = re.search(r"<([^>]+)>", sender)
    if match:
        return match.group(1).strip()
    sender = sender.strip()
    if "@" in sender:
        return sender
    raise ValueError(f"Could not parse recipient email from: {sender!r}")


def _strip_html(html: str) -> str:
    no_script = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    no_tags = re.sub(r"<[^>]+>", " ", no_script)
    return re.sub(r"\s+", " ", no_tags).strip()


def _decode_part_data(data: str | None) -> str:
    if not data:
        return ""
    decoded = base64.urlsafe_b64decode(data.encode("utf-8"))
    return decoded.decode("utf-8", errors="ignore")


class GmailClient:
    def __init__(
        self,
        credentials_path: str | None = None,
        token_path: str | None = None,
    ):
        script_dir = Path(__file__).resolve().parent
        env_creds = os.getenv("GMAIL_CREDENTIALS_PATH", "").strip()
        source_path = credentials_path or env_creds or "credentials.json"
        
        env_token = os.getenv("GMAIL_TOKEN_PATH", "").strip()
        token_source = token_path or env_token or "gmail_token.json"

        creds_path = Path(source_path)
        if not creds_path.is_absolute():
            creds_path = (script_dir / creds_path).resolve()

        token_file = Path(token_source)
        if not token_file.is_absolute():
            token_file = (script_dir / token_file).resolve()

        self.credentials_path = creds_path
        self.token_path = token_file
        self.service = self._build_service()

    def _build_service(self):
        creds = None
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

        if not creds or not creds.valid:
            if not self.credentials_path.exists():
                raise FileNotFoundError(
                    f"Gmail OAuth client file not found: {self.credentials_path}. "
                    "Download OAuth credentials JSON from Google Cloud and place it there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
            self.token_path.write_text(creds.to_json(), encoding="utf-8")

        return build("gmail", "v1", credentials=creds)

    def _api_call_with_retry(self, call_fn, max_retries: int = 3):
        """Execute a Gmail API call with exponential backoff retry logic."""
        for attempt in range(max_retries):
            try:
                return call_fn()
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning(
                    "Gmail API call failed (attempt %d/%d): %s. Retrying in %ds...",
                    attempt + 1,
                    max_retries,
                    e,
                    wait,
                )
                time.sleep(wait)

    def fetch_recent(
        self,
        max_results: int = 10,
        mode: str = "unread",
        custom_query: str | None = None,
    ) -> list[RawEmail]:
        if custom_query:
            query = custom_query
        elif mode == "unread":
            query = "is:unread category:primary"
        elif mode == "recent":
            query = "newer_than:7d category:primary"
        else:
            raise ValueError("mode must be 'unread' or 'recent'")

        listing = self._api_call_with_retry(
            lambda: self.service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        time.sleep(0.1)
        message_refs = listing.get("messages", [])
        emails: list[RawEmail] = []
        for ref in message_refs:
            message = self._api_call_with_retry(
                lambda ref=ref: self.service.users()
                .messages()
                .get(userId="me", id=ref["id"], format="full")
                .execute()
            )
            time.sleep(0.1)
            emails.append(self._to_raw_email(message))
        return emails

    def _to_raw_email(self, message: dict) -> RawEmail:
        payload = message.get("payload", {})
        headers = {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers", [])}
        subject = headers.get("subject", "(no subject)")
        sender = headers.get("from", "(unknown sender)")

        body = self._extract_body(payload)
        internal_ms = int(message.get("internalDate", "0"))
        received_at = datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc)

        return RawEmail(
            email_id=message["id"],
            subject=subject,
            sender=sender,
            body=body,
            received_at=received_at,
            thread_id=message.get("threadId"),
        )

    def get_message_id_header(self, message_id: str) -> str | None:
        """Return the RFC Message-ID header for threading replies."""
        message = self._api_call_with_retry(
            lambda: self.service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["Message-ID"],
            )
            .execute()
        )
        payload = message.get("payload", {})
        for header in payload.get("headers", []):
            if header.get("name", "").lower() == "message-id":
                value = (header.get("value") or "").strip()
                return value or None
        return None

    def send_reply(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        thread_id: str | None = None,
        in_reply_to_message_id: str | None = None,
    ) -> dict:
        """Send a reply in-thread via the Gmail API."""
        recipient = parse_sender_email(to)
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"

        mime = MIMEText(body, "plain", "utf-8")
        mime["To"] = recipient
        mime["Subject"] = reply_subject

        if in_reply_to_message_id:
            message_id_header = self.get_message_id_header(in_reply_to_message_id)
            if message_id_header:
                mime["In-Reply-To"] = message_id_header
                mime["References"] = message_id_header

        raw_message = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")
        send_body: dict = {"raw": raw_message}
        if thread_id:
            send_body["threadId"] = thread_id

        sent = self._api_call_with_retry(
            lambda: self.service.users()
            .messages()
            .send(userId="me", body=send_body)
            .execute()
        )
        logger.info(
            "Sent Gmail reply to %s (thread=%s, message=%s)",
            recipient,
            sent.get("threadId"),
            sent.get("id"),
        )
        return sent

    def _extract_body(self, payload: dict) -> str:
        """Extract the best available body text from a Gmail message payload."""
        plain, html = self._collect_body_parts(payload)
        if plain:
            return plain
        if html:
            return _strip_html(html)
        return ""

    def _collect_body_parts(self, payload: dict) -> tuple[str, str]:
        """Recursively collect plain text and HTML body parts from a MIME payload."""
        mime_type = payload.get("mimeType", "")

        if mime_type == "text/plain":
            return _decode_part_data(payload.get("body", {}).get("data")), ""

        if mime_type == "text/html":
            return "", _decode_part_data(payload.get("body", {}).get("data"))

        plain_text = ""
        html_text = ""
        for part in payload.get("parts", []) or []:
            p, h = self._collect_body_parts(part)
            if p and not plain_text:
                plain_text = p
            if h and not html_text:
                html_text = h
            if plain_text:
                break

        return plain_text, html_text
