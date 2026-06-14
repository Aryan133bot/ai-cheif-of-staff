import logging
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

logger = logging.getLogger(__name__)

# Full calendar access is required to write, read, and delete events
SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarClient:
    def __init__(
        self,
        credentials_path: str | None = None,
        token_path: str = "calendar_token.json",
    ):
        script_dir = Path(__file__).resolve().parent
        env_creds = os.getenv("GMAIL_CREDENTIALS_PATH", "").strip() or "credentials.json"
        
        # Resolve credentials path (tries multiple standard locations)
        creds_path = Path(credentials_path or env_creds)
        if not creds_path.is_absolute():
            # Check dashboard folder first
            if (script_dir / creds_path).exists():
                creds_path = (script_dir / creds_path).resolve()
            else:
                # Fallback to email processor folder
                creds_path = (script_dir.parent / "email processor" / creds_path).resolve()

        # Resolve token path
        token_file = Path(token_path)
        if not token_file.is_absolute():
            token_file = (script_dir / token_file).resolve()

        self.credentials_path = creds_path
        self.token_path = token_file
        self.service = self._build_service()

    def _build_service(self):
        creds = None
        if self.token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
            except Exception as e:
                logger.error("Failed to load authorized calendar user: %s", e)

        # Refresh the token if expired
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.error("Failed to refresh calendar credentials: %s. Re-authenticating...", e)
                creds = None

        # Authenticate if valid credentials don't exist
        if not creds or not creds.valid:
            if not self.credentials_path.exists():
                raise FileNotFoundError(
                    f"Google Developer Client credentials file not found: {self.credentials_path}. "
                    "Make sure to place credentials.json in the email processor folder."
                )
            
            # Start local authentication server
            flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
            self.token_path.write_text(creds.to_json(), encoding="utf-8")

        return build("calendar", "v3", credentials=creds)

    def _api_call_with_retry(self, call_fn, max_retries: int = 3):
        """Execute a Google Calendar API call with exponential backoff retry logic."""
        for attempt in range(max_retries):
            try:
                return call_fn()
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning(
                    "Google Calendar API call failed (attempt %d/%d): %s. Retrying in %ds...",
                    attempt + 1,
                    max_retries,
                    e,
                    wait,
                )
                time.sleep(wait)

    def create_gcal_event(self, event_data: dict) -> str | None:
        """Create a new event in Google Calendar and return its ID."""
        event = self._build_event_resource(event_data)
        try:
            gcal_event = self._api_call_with_retry(
                lambda: self.service.events()
                .insert(calendarId="primary", body=event)
                .execute()
            )
            return gcal_event.get("id")
        except Exception as e:
            logger.error("Failed to create Google Calendar event: %s", e)
            return None

    def update_gcal_event(self, gcal_event_id: str, event_data: dict) -> bool:
        """Update an existing event on Google Calendar. Returns True if successful."""
        event = self._build_event_resource(event_data)
        try:
            self._api_call_with_retry(
                lambda: self.service.events()
                .update(calendarId="primary", eventId=gcal_event_id, body=event)
                .execute()
            )
            return True
        except Exception as e:
            logger.error("Failed to update Google Calendar event '%s': %s", gcal_event_id, e)
            return False

    def delete_gcal_event(self, gcal_event_id: str) -> bool:
        """Delete an event on Google Calendar. Returns True if successful."""
        try:
            self._api_call_with_retry(
                lambda: self.service.events()
                .delete(calendarId="primary", eventId=gcal_event_id)
                .execute()
            )
            return True
        except Exception as e:
            # If the event is already deleted on the server, return True (no-op)
            if "404" in str(e) or "410" in str(e):
                return True
            logger.error("Failed to delete Google Calendar event '%s': %s", gcal_event_id, e)
            return False

    def fetch_recent_events(self, start_iso: str, end_iso: str, max_results: int = 250) -> list:
        """Fetch list of events between two ISO timestamps."""
        try:
            result = self._api_call_with_retry(
                lambda: self.service.events()
                .list(
                    calendarId="primary",
                    timeMin=start_iso,
                    timeMax=end_iso,
                    singleEvents=True,
                    maxResults=max_results,
                )
                .execute()
            )
            return result.get("items", [])
        except Exception as e:
            logger.error("Failed to fetch Google Calendar events: %s", e)
            return []

    def _build_event_resource(self, event_data: dict) -> dict:
        """Formulate Google Calendar V3 Event resource representation."""
        is_all_day = bool(event_data.get("all_day", False))
        
        resource = {
            "summary": event_data["title"],
            "description": event_data.get("description", ""),
        }

        # Select color map ID for Google Calendar (1-11)
        # 1=Blue (Medium), 5=Amber (High/Warning), 11=Red (Critical), 8=Grey (Low)
        urgency_color_map = {
            "critical": "11", # bold red
            "high": "5",     # yellow
            "medium": "9",   # bold blue
            "low": "8",      # grey
        }
        resource["colorId"] = urgency_color_map.get(event_data.get("urgency", "medium"), "9")

        if is_all_day:
            resource["start"] = {"date": event_data["start_time"][:10]}
            resource["end"] = {"date": event_data["end_time"][:10]}
        else:
            # Standard ISO datetime formats
            resource["start"] = {"dateTime": event_data["start_time"], "timeZone": "UTC"}
            resource["end"] = {"dateTime": event_data["end_time"], "timeZone": "UTC"}

        return resource
