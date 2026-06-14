"""
AI Chief of Staff — Dashboard API Server

FastAPI application that serves the dashboard frontend and exposes
REST APIs for tasks, calendar events, reply drafts, email processing,
and user authentication.`
Usage:
    python server.py                     # starts on http://localhost:8000
    python server.py --port 3000         # custom port
    python server.py --log-level DEBUG   # verbose logging
"""

import argparse
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from reply_generator import generate_reply_text
from reply_sender import mark_draft_send_failed, mark_draft_sent, send_approved_reply
from auth import (
    has_any_account,
    create_account,
    authenticate,
    change_password,
    create_token,
    get_current_user,
)

# ─── Configuration ───────────────────────────────────────────────────────────

# Ensure email processor is importable
_email_proc_dir = Path(__file__).resolve().parent.parent / "email processor"
if str(_email_proc_dir) not in sys.path:
    sys.path.insert(0, str(_email_proc_dir))

# Load .env from the email processor directory (where the API key lives)
load_dotenv(_email_proc_dir / ".env")

# -- DevOps Cloud Hosting Helper --
# If deployed on Railway/Render, they provide secrets via environment variables.
# This writes the raw JSON string to the expected file path so OAuth works seamlessly.
_env_creds = os.getenv("GOOGLE_CREDENTIALS_JSON")
_creds_file_path = os.getenv("GMAIL_CREDENTIALS_PATH", str(_email_proc_dir / "credentials.json"))
if _env_creds:
    Path(_creds_file_path).write_text(_env_creds, encoding="utf-8")

logger = logging.getLogger(__name__)

DB_PATH = os.getenv(
    "DASHBOARD_DB_PATH",
    str(_email_proc_dir / "phase1_tasks.db"),
)

EMAIL_POLL_INTERVAL = int(os.getenv("EMAIL_POLL_INTERVAL_MINUTES", "15"))

from datetime import datetime, timezone, timedelta

# Initialize Google Calendar Client lazily/gracefully
_gcal_client = None

def get_gcal_client():
    global _gcal_client
    if _gcal_client is not None:
        return _gcal_client
    try:
        # Add dashboard dir to path to ensure it imports google_calendar
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from google_calendar import GoogleCalendarClient
        _gcal_client = GoogleCalendarClient()
        return _gcal_client
    except Exception as e:
        logger.warning("Google Calendar Sync is disabled/unconfigured: %s", e)
        return None


# ─── Email Service (lazy) ────────────────────────────────────────────────────

_email_service = None

def get_email_service():
    global _email_service
    if _email_service is not None:
        return _email_service
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from email_providers.gmail import GmailProvider
        from email_providers.registry import ProviderRegistry
        from email_service import EmailService

        registry = ProviderRegistry()
        registry.register(GmailProvider())
        _email_service = EmailService(DB_PATH, registry)
        return _email_service
    except Exception as e:
        logger.warning("Email processing service unavailable: %s", e)
        return None


# ─── Background Scheduler ────────────────────────────────────────────────────

async def email_poll_loop():
    """Background task that periodically processes new emails."""
    if EMAIL_POLL_INTERVAL <= 0:
        logger.info("Email auto-polling is disabled (interval=0)")
        return

    # Wait 60 seconds before first run to let server fully initialize
    await asyncio.sleep(60)
    logger.info("Email auto-poll started — interval: %d minutes", EMAIL_POLL_INTERVAL)

    while True:
        try:
            svc = get_email_service()
            if svc:
                logger.info("Auto-poll: processing new emails...")
                result = await asyncio.to_thread(
                    svc.process_new_emails, trigger="scheduled"
                )
                logger.info("Auto-poll complete: %s", result)
            else:
                logger.debug("Auto-poll skipped — email service not available")
        except Exception as e:
            logger.error("Auto-poll error: %s", e)

        await asyncio.sleep(EMAIL_POLL_INTERVAL * 60)


# ─── FastAPI app ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup: init DB, start background tasks. Shutdown: cancel tasks."""
    db.init_db(DB_PATH)
    logger.info("Dashboard API started — DB: %s", DB_PATH)

    # Launch background email polling
    poll_task = asyncio.create_task(email_poll_loop())

    yield

    # Cancel background task on shutdown
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="AI Chief of Staff — Dashboard API",
    version="2.0.0",
    description="Dashboard backend with authentication, email processing, task management, calendar, and reply drafting.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Pydantic models ────────────────────────────────────────────────────────

class RegisterBody(BaseModel):
    name: str
    email: str
    password: str

class LoginBody(BaseModel):
    email: str
    password: str

class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str

class StatusUpdate(BaseModel):
    status: str


class CalendarEventCreate(BaseModel):
    title: str
    description: str = ""
    start_time: str
    end_time: str
    all_day: bool = False
    event_type: str = "custom"
    urgency: str = "medium"
    linked_task_id: int | None = None
    color: str = "#6366f1"
    reminder_minutes: int | None = None


class CalendarEventUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    all_day: bool | None = None
    event_type: str | None = None
    urgency: str | None = None
    linked_task_id: int | None = None
    color: str | None = None
    reminder_minutes: int | None = None


class ReplyDraftRequest(BaseModel):
    task_id: int | None = None
    original_subject: str
    original_sender: str
    original_body: str
    reply_intent: str = "follow_up"
    gmail_message_id: str | None = None
    gmail_thread_id: str | None = None


class ReplyDraftUpdate(BaseModel):
    edited_text: str | None = None
    status: str | None = None
    draft_text: str | None = None


class ApproveReplyBody(BaseModel):
    edited_text: str | None = None


# ─── Auth endpoints (no auth required) ──────────────────────────────────────

@app.get("/api/auth/status")
def auth_status():
    """Check if any account exists — tells frontend to show setup or login."""
    return {"has_account": has_any_account(DB_PATH)}


@app.post("/api/auth/register")
def register(body: RegisterBody):
    """Create a new account. Only works if no account exists yet."""
    if has_any_account(DB_PATH):
        raise HTTPException(status_code=403, detail="An account already exists. Use login instead.")
    try:
        user = create_account(DB_PATH, body.name, body.email, body.password)
        return {"ok": True, "user": user}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/login")
def login(body: LoginBody):
    """Authenticate with email + password, returns JWT."""
    user = authenticate(DB_PATH, body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = create_token(user["id"], user["email"], user["name"])
    return {"ok": True, "token": token, "user": user}


# ─── Auth endpoints (auth required) ─────────────────────────────────────────

@app.get("/api/auth/me")
def get_me(user: dict = Depends(get_current_user)):
    """Get current user info."""
    return user


@app.post("/api/auth/change-password")
def update_password(body: ChangePasswordBody, user: dict = Depends(get_current_user)):
    """Change the current user's password."""
    try:
        change_password(DB_PATH, user["id"], body.current_password, body.new_password)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─── Task endpoints ─────────────────────────────────────────────────────────

@app.get("/api/tasks")
def list_tasks(
    status: str | None = None,
    urgency: str | None = None,
    deadline_type: str | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    return db.get_all_tasks(
        db_path=DB_PATH,
        status=status,
        urgency=urgency,
        deadline_type=deadline_type,
        limit=limit,
        offset=offset,
    )


@app.get("/api/tasks/priorities")
def task_priorities(limit: int = Query(default=10, le=50), user: dict = Depends(get_current_user)):
    return db.get_task_priorities(db_path=DB_PATH, limit=limit)


@app.get("/api/tasks/review-queue")
def review_queue(limit: int = Query(default=50, le=200), user: dict = Depends(get_current_user)):
    return db.get_review_queue(db_path=DB_PATH, limit=limit)


@app.patch("/api/tasks/{task_id}/status")
def update_status(task_id: int, body: StatusUpdate, user: dict = Depends(get_current_user)):
    try:
        ok = db.update_task_status(DB_PATH, task_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return {"ok": True, "task_id": task_id, "status": body.status}


@app.get("/api/stats")
def dashboard_stats(user: dict = Depends(get_current_user)):
    return db.get_task_stats(db_path=DB_PATH)


# ─── Calendar endpoints ─────────────────────────────────────────────────────

@app.get("/api/calendar/events")
def list_events(start: str | None = None, end: str | None = None, user: dict = Depends(get_current_user)):
    return db.get_calendar_events(db_path=DB_PATH, start=start, end=end)


@app.post("/api/calendar/events", status_code=201)
def create_event(body: CalendarEventCreate, user: dict = Depends(get_current_user)):
    data = body.model_dump(exclude_none=True)

    # Pre-push to Google Calendar if unconfigured fallback is active
    gcal = get_gcal_client()
    if gcal:
        gcal_id = gcal.create_gcal_event(data)
        if gcal_id:
            data["gcal_event_id"] = gcal_id

    return db.create_calendar_event(db_path=DB_PATH, data=data)


@app.patch("/api/calendar/events/{event_id}")
def update_event(event_id: int, body: CalendarEventUpdate, user: dict = Depends(get_current_user)):
    data = body.model_dump(exclude_none=True)

    # Retrieve current event details
    current = db.get_calendar_event(DB_PATH, event_id)
    if not current:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    gcal = get_gcal_client()
    if gcal:
        gcal_id = current.get("gcal_event_id")
        merged = {**dict(current), **data}
        if gcal_id:
            ok = gcal.update_gcal_event(gcal_id, merged)
            if not ok:
                # Re-create if remote event was deleted
                new_gcal_id = gcal.create_gcal_event(merged)
                if new_gcal_id:
                    data["gcal_event_id"] = new_gcal_id
        else:
            # Upload local event
            new_gcal_id = gcal.create_gcal_event(merged)
            if new_gcal_id:
                data["gcal_event_id"] = new_gcal_id

    result = db.update_calendar_event(db_path=DB_PATH, event_id=event_id, data=data)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return result


@app.delete("/api/calendar/events/{event_id}")
def delete_event(event_id: int, user: dict = Depends(get_current_user)):
    current = db.get_calendar_event(DB_PATH, event_id)
    if not current:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    gcal = get_gcal_client()
    if gcal:
        gcal_id = current.get("gcal_event_id")
        if gcal_id:
            gcal.delete_gcal_event(gcal_id)

    ok = db.delete_calendar_event(db_path=DB_PATH, event_id=event_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return {"ok": True}


@app.post("/api/calendar/sync-tasks")
def sync_tasks(user: dict = Depends(get_current_user)):
    """Import task deadlines as calendar events."""
    created = db.sync_tasks_to_calendar(db_path=DB_PATH)
    return {"ok": True, "events_created": created}


@app.post("/api/calendar/sync-gcal")
def sync_google_calendar(user: dict = Depends(get_current_user)):
    """Perform a full two-way synchronization with Google Calendar."""
    gcal = get_gcal_client()
    if not gcal:
        raise HTTPException(
            status_code=400,
            detail="Google Calendar integration is not configured. Place credentials.json in the email processor folder to begin."
        )

    # 1. Sync any tasks that have deadlines to local events first
    local_tasks_synced = db.sync_tasks_to_calendar(db_path=DB_PATH)

    # 2. Upload any local events that don't have a gcal_event_id, and push local updates
    local_events = db.get_calendar_events(db_path=DB_PATH)
    uploaded = 0
    updated_remote = 0

    for event in local_events:
        gcal_id = event.get("gcal_event_id")
        if not gcal_id:
            new_id = gcal.create_gcal_event(dict(event))
            if new_id:
                db.update_calendar_event(DB_PATH, event["id"], {"gcal_event_id": new_id})
                uploaded += 1
        else:
            gcal.update_gcal_event(gcal_id, dict(event))
            updated_remote += 1

    # 3. Pull recent updates from Google Calendar (last 30 days to next 60 days)
    now_dt = datetime.now(timezone.utc)
    start_iso = (now_dt - timedelta(days=30)).isoformat()
    end_iso = (now_dt + timedelta(days=60)).isoformat()

    gcal_events = gcal.fetch_recent_events(start_iso, end_iso)
    downloaded = 0

    existing_gcal_ids = {e["gcal_event_id"] for e in local_events if e.get("gcal_event_id")}

    for g_event in gcal_events:
        g_id = g_event.get("id")
        if g_id not in existing_gcal_ids:
            title = g_event.get("summary", "(no title)")
            desc = g_event.get("description", "")

            start_info = g_event.get("start", {})
            end_info = g_event.get("end", {})

            start_time = start_info.get("dateTime") or start_info.get("date")
            end_time = end_info.get("dateTime") or end_info.get("date")
            all_day = 1 if "date" in start_info else 0

            if not start_time or not end_time:
                continue

            db.create_calendar_event(DB_PATH, {
                "title": title,
                "description": desc,
                "start_time": start_time,
                "end_time": end_time,
                "all_day": all_day,
                "event_type": "custom",
                "urgency": "medium",
                "color": "#3B82F6",
                "gcal_event_id": g_id,
            })
            downloaded += 1

    return {
        "ok": True,
        "local_tasks_synced": local_tasks_synced,
        "uploaded_to_gcal": uploaded,
        "updated_on_gcal": updated_remote,
        "downloaded_from_gcal": downloaded,
    }


# ─── Email processing endpoints ─────────────────────────────────────────────

@app.post("/api/emails/process")
def process_emails(user: dict = Depends(get_current_user)):
    """Manually trigger email fetch and processing."""
    svc = get_email_service()
    if not svc:
        raise HTTPException(
            status_code=400,
            detail="Email processing service is not available. Check that credentials.json exists."
        )
    result = svc.process_new_emails(trigger="manual")
    return result


@app.get("/api/emails/status")
def email_status(user: dict = Depends(get_current_user)):
    """Get current email processing status and provider info."""
    svc = get_email_service()
    if not svc:
        return {
            "providers": [],
            "last_run": None,
            "auto_poll_enabled": EMAIL_POLL_INTERVAL > 0,
            "poll_interval_minutes": EMAIL_POLL_INTERVAL,
        }
    status = svc.get_status()
    status["auto_poll_enabled"] = EMAIL_POLL_INTERVAL > 0
    status["poll_interval_minutes"] = EMAIL_POLL_INTERVAL
    return status


@app.get("/api/emails/history")
def email_history(limit: int = Query(default=20, le=100), user: dict = Depends(get_current_user)):
    """Get recent email processing run history."""
    svc = get_email_service()
    if not svc:
        return []
    return svc.get_history(limit=limit)


# ─── Gmail OAuth ─────────────────────────────────────────────────────────────

# Global to store oauth flows temporarily for PKCE code verifier matching
_oauth_flows = {}

@app.get("/api/gmail/auth-url")
def connect_gmail(request: Request, user: dict = Depends(get_current_user)):
    """Generate the OAuth authorization URL to redirect the user."""
    creds_path = Path(os.getenv("GMAIL_CREDENTIALS_PATH", _email_proc_dir / "credentials.json"))
    token_path = Path(os.getenv("GMAIL_TOKEN_PATH", _email_proc_dir / "gmail_token.json"))

    if not creds_path.exists():
        raise HTTPException(
            status_code=400,
            detail="credentials.json not found. Download OAuth credentials from Google Cloud Console and place in the 'email processor' folder.",
        )

    sys.path.insert(0, str(_email_proc_dir))
    from gmail_scopes import GMAIL_SCOPES, token_includes_send_scope

    if token_path.exists() and token_includes_send_scope(str(token_path)):
        try:
            from google.oauth2.credentials import Credentials

            creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
            if creds and (creds.valid or creds.refresh_token):
                return {"ok": True, "message": "Gmail is already connected."}
        except Exception:
            pass
    elif token_path.exists():
        logger.info("Gmail token missing send scope — re-authorizing for reply sending")
        token_path.unlink(missing_ok=True)

    try:
        from google_auth_oauthlib.flow import Flow
        
        # Disable HTTPS requirement for oauthlib when running locally
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

        # Must exactly match the Authorized Redirect URI in Google Cloud Console
        # For Desktop apps, http://localhost:8000/api/gmail/callback is valid.
        redirect_uri = str(request.base_url).rstrip("/") + "/api/gmail/callback"
        
        flow = Flow.from_client_secrets_file(str(creds_path), scopes=GMAIL_SCOPES)
        flow.redirect_uri = redirect_uri
        
        auth_url, state = flow.authorization_url(prompt="consent", access_type="offline")
        _oauth_flows[state] = flow
        
        return {"ok": True, "auth_url": auth_url}
    except Exception as e:
        logger.error("Failed to generate auth url: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to start Gmail auth: {str(e)}")


@app.get("/api/gmail/callback")
def gmail_callback(request: Request, state: str = None, code: str = None, error: str = None):
    """Handle the OAuth callback from Google."""
    if error:
        return RedirectResponse(url=f"/#settings?error={error}")
    if not code or not state:
        return RedirectResponse(url="/#settings?error=no_code_provided")

    creds_path = Path(os.getenv("GMAIL_CREDENTIALS_PATH", _email_proc_dir / "credentials.json"))
    token_path = Path(os.getenv("GMAIL_TOKEN_PATH", _email_proc_dir / "gmail_token.json"))

    sys.path.insert(0, str(_email_proc_dir))
    from gmail_scopes import GMAIL_SCOPES

    try:
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
        flow = _oauth_flows.pop(state, None)
        if not flow:
            logger.error("OAuth flow state not found or expired")
            return RedirectResponse(url="/#settings?error=session_expired")
        
        # Fetch the token using the full authorization response URL
        flow.fetch_token(authorization_response=str(request.url))
        
        creds = flow.credentials
        token_path.write_text(creds.to_json(), encoding="utf-8")

        global _email_service
        _email_service = None
        
        logger.info("Gmail connected successfully via web callback")
        return RedirectResponse(url="/#settings")
    except Exception as e:
        logger.error("OAuth callback failed: %s", e)
        return RedirectResponse(url=f"/#settings?error={str(e)}")


@app.post("/api/gmail/disconnect")
def disconnect_gmail(user: dict = Depends(get_current_user)):
    """Remove Gmail token to disconnect the account."""
    token_path = _email_proc_dir / "gmail_token.json"
    if token_path.exists():
        token_path.unlink()
        global _email_service
        _email_service = None
        logger.info("Gmail disconnected — token removed")
    return {"ok": True, "message": "Gmail disconnected."}


# ─── Reply engine endpoints ─────────────────────────────────────────────────

def _resolve_draft_gmail_ids(body: ReplyDraftRequest) -> tuple[str | None, str | None]:
    if body.gmail_message_id:
        return body.gmail_message_id, body.gmail_thread_id
    if body.task_id:
        task = db.get_task_by_id(DB_PATH, body.task_id)
        if task:
            return task.get("source_email_id"), body.gmail_thread_id
    return None, body.gmail_thread_id


@app.post("/api/replies/draft", status_code=201)
def generate_reply(body: ReplyDraftRequest, user: dict = Depends(get_current_user)):
    """Generate an AI reply draft and store it for approval."""
    gmail_message_id, gmail_thread_id = _resolve_draft_gmail_ids(body)
    draft_text, model_used, confidence = generate_reply_text(
        original_subject=body.original_subject,
        original_sender=body.original_sender,
        original_body=body.original_body,
        reply_intent=body.reply_intent,
    )

    data = {
        "task_id": body.task_id,
        "original_subject": body.original_subject,
        "original_sender": body.original_sender,
        "original_body": body.original_body,
        "reply_intent": body.reply_intent,
        "draft_text": draft_text,
        "model_used": model_used,
        "confidence": confidence,
        "gmail_message_id": gmail_message_id,
        "gmail_thread_id": gmail_thread_id,
    }
    return db.create_reply_draft(db_path=DB_PATH, data=data)


@app.get("/api/replies")
def list_replies(status: str | None = None, limit: int = Query(default=50, le=200), user: dict = Depends(get_current_user)):
    return db.get_reply_drafts(db_path=DB_PATH, status=status, limit=limit)


@app.patch("/api/replies/{draft_id}")
def update_reply(draft_id: int, body: ReplyDraftUpdate, user: dict = Depends(get_current_user)):
    data = body.model_dump(exclude_none=True)
    result = db.update_reply_draft(db_path=DB_PATH, draft_id=draft_id, data=data)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Draft {draft_id} not found")
    return result


@app.post("/api/replies/{draft_id}/approve")
def approve_reply(
    draft_id: int,
    body: ApproveReplyBody = Body(default_factory=ApproveReplyBody),
    user: dict = Depends(get_current_user),
):
    """Approve a draft and send it via Gmail."""
    db.reset_stale_sending_drafts(DB_PATH)

    existing = db.get_reply_draft(DB_PATH, draft_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Draft {draft_id} not found")
    if existing.get("status") == "sent":
        raise HTTPException(status_code=400, detail="This reply was already sent.")
    if existing.get("status") == "sending":
        raise HTTPException(status_code=409, detail="This reply is already being sent.")

    draft = db.claim_reply_draft_for_sending(DB_PATH, draft_id)
    if draft is None:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot send draft with status '{existing.get('status')}'.",
        )

    if body.edited_text is not None:
        text = body.edited_text.strip()
        if not text:
            db.release_reply_draft_send_lock(DB_PATH, draft_id, error="Reply body cannot be empty.")
            raise HTTPException(status_code=400, detail="Reply body cannot be empty.")
        updated = db.update_reply_draft(DB_PATH, draft_id, {"edited_text": text})
        if updated:
            draft = updated

    try:
        gmail_response = send_approved_reply(DB_PATH, draft)
        result = mark_draft_sent(DB_PATH, draft_id, gmail_response)
        if result is None:
            raise RuntimeError("Failed to update draft status after send.")
    except (PermissionError, ValueError) as e:
        mark_draft_send_failed(DB_PATH, draft_id, str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        mark_draft_send_failed(DB_PATH, draft_id, str(e))
        logger.error("Failed to send reply draft %s: %s", draft_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to send reply: {e}") from e

    return {"ok": True, "draft": result, "message": "Reply sent via Gmail."}


@app.delete("/api/replies/{draft_id}")
def discard_reply(draft_id: int, user: dict = Depends(get_current_user)):
    ok = db.delete_reply_draft(DB_PATH, draft_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Draft {draft_id} not found")
    return {"ok": True}


# ─── Static file serving ────────────────────────────────────────────────────

static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def serve_index():
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Dashboard frontend not built yet. API is running.", "docs": "/docs"}



# ─── CLI entry point ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI Chief of Staff — Dashboard Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    import uvicorn
    host = os.getenv("HOST", args.host)
    port = int(os.getenv("PORT", args.port))
    uvicorn.run(app, host=host, port=port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
