import traceback
from fastapi.responses import JSONResponse
from fastapi import Request
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
from fastapi import Body, FastAPI, HTTPException, Query, Depends, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import vector_store
import ingestion_service
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
def get_gcal_client(user_id: int):
    if not user_id:
        return None
    try:
        user = db.get_user_by_id(DB_PATH, user_id)
        if not user or not user.get("gmail_token"):
            return None
        from google_calendar import GoogleCalendarClient
        return GoogleCalendarClient(token_json=user["gmail_token"])
    except Exception as e:
        logger.warning("Google Calendar client unavailable for user %s: %s", user_id, e)
        return None


# ─── Email Service (lazy) ────────────────────────────────────────────────────

def get_email_service(user_id: int):
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from email_providers.gmail import GmailProvider
        from email_providers.registry import ProviderRegistry
        from email_service import EmailService

        registry = ProviderRegistry()
        gmail = GmailProvider(user_id=user_id)
        logger.info("Gmail provider configured=%s, authenticated=%s, creds_path=%s", 
                     gmail.is_configured(), gmail.is_authenticated(), gmail._credentials_path)
        registry.register(gmail)
        return EmailService(DB_PATH, registry, user_id=user_id)
    except Exception as e:
        logger.warning("Email processing service unavailable: %s", e, exc_info=True)
        return None


# ─── Background Scheduler ────────────────────────────────────────────────────

async def email_poll_loop():
    """Background task that periodically processes new emails."""
    if EMAIL_POLL_INTERVAL <= 0:
        logger.info("Email auto-polling is disabled (interval=0)")
        return

    await asyncio.sleep(60)
    logger.info("Email auto-poll started — interval: %d minutes", EMAIL_POLL_INTERVAL)

    while True:
        try:
            users = db.get_all_users(DB_PATH)
            for u in users:
                user_id = u["id"]
                try:
                    svc = get_email_service(user_id)
                    if svc:
                        logger.info("Auto-poll: processing new emails for user %d...", user_id)
                        result = await asyncio.to_thread(
                            svc.process_new_emails, trigger="scheduled"
                        )
                        logger.info("Auto-poll complete for user %d: %s", user_id, result)
                except Exception as e:
                    logger.error("Auto-poll error for user %d: %s", user_id, e)
        except Exception as e:
            logger.error("Auto-poll outer loop error: %s", e)

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

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"}
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ai-cheif-of-staff.onrender.com",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
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
# (Removed unauthenticated reset-db endpoint)


@app.get("/api/auth/status")
def auth_status():
    """Check if any account exists — tells frontend to show setup or login."""
    return {"has_account": auth.has_any_account(DB_PATH)}

@app.get("/api/debug/env")
def debug_env():
    import traceback
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    models = []
    error_str = ""
    if gemini_key:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            models = [m.name for m in client.models.list()]
        except Exception as e:
            error_str = traceback.format_exc()
            
    return {
        "gemini_key_exists": bool(gemini_key),
        "gemini_key_prefix": gemini_key[:5] if gemini_key else "",
        "google_creds_exists": bool(os.getenv("GOOGLE_CREDENTIALS_JSON")),
        "google_creds_type": "service_account" in os.getenv("GOOGLE_CREDENTIALS_JSON", ""),
        "anthropic_key_exists": bool(os.getenv("ANTHROPIC_API_KEY")),
        "available_models": models,
        "list_error": error_str
    }


@app.post("/api/auth/register")
def register(body: RegisterBody):
    """Create a new user account."""
    try:
        user = create_account(DB_PATH, body.name, body.email, body.password)
        from auth import create_token
        token = create_token(user["id"], user["email"], user["name"])
        return {"ok": True, "token": token, "user": user}
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


# ─── Settings endpoints ───────────────────────────────────────────────────────

class AutoSendBody(BaseModel):
    enabled: bool

@app.post("/api/settings/auto-send")
def set_auto_send(body: AutoSendBody, user: dict = Depends(get_current_user)):
    """Update auto_send_enabled for the current user."""
    conn = get_db(DB_PATH)
    try:
        conn.execute("UPDATE users SET auto_send_enabled = ? WHERE id = ?", (body.enabled, user["id"]))
        conn.commit()
        return {"ok": True, "auto_send_enabled": body.enabled}
    finally:
        conn.close()

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
        user_id=user["id"],
    )


@app.get("/api/tasks/priorities")
def task_priorities(limit: int = 10, user: dict = Depends(get_current_user)):
    return db.get_task_priorities(db_path=DB_PATH, limit=limit, user_id=user["id"])


@app.get("/api/tasks/review-queue")
def review_queue(limit: int = Query(default=50, le=200), user: dict = Depends(get_current_user)):
    return db.get_review_queue(db_path=DB_PATH, limit=limit, user_id=user["id"])


class ContactRelationshipCreate(BaseModel):
    email_address: str
    role: str
    importance: int = 50
    tone_preference: str = "professional"

class KnowledgeBaseCreate(BaseModel):
    title: str
    content: str
    status: str = "active"
    entry_id: int | None = None
    source: str = "Manual Entry"

class EmailExtractRequest(BaseModel):
    email_body: str

class UrlIngestRequest(BaseModel):
    url: str

class TaskCreate(BaseModel):
    title: str
    deadline_type: str = "task"
    urgency: str = "medium"
    source_quote: str = "Manually created"
    deadline_date: str | None = None
    assigned_to: str | None = None
    counterparty: str | None = None
    action_needed: str | None = None

@app.post("/api/tasks", status_code=201)
def create_task(body: TaskCreate, user: dict = Depends(get_current_user)):
    data = body.model_dump(exclude_none=True)
    return db.create_task(db_path=DB_PATH, data=data, user_id=user["id"])

@app.patch("/api/tasks/{task_id}/status")
def update_status(task_id: int, body: StatusUpdate, user: dict = Depends(get_current_user)):
    try:
        ok = db.update_task_status(DB_PATH, task_id, body.status, user_id=user["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return {"ok": True, "task_id": task_id, "status": body.status}


@app.get("/api/stats")
def dashboard_stats(user: dict = Depends(get_current_user)):
    return db.get_task_stats(db_path=DB_PATH, user_id=user["id"])


# ─── Calendar endpoints ─────────────────────────────────────────────────────

@app.get("/api/calendar/events")
def list_events(start: str | None = None, end: str | None = None, user: dict = Depends(get_current_user)):
    return db.get_calendar_events(db_path=DB_PATH, start=start, end=end, user_id=user["id"])


@app.post("/api/calendar/events", status_code=201)
def create_event(body: CalendarEventCreate, user: dict = Depends(get_current_user)):
    data = body.model_dump(exclude_none=True)

    # Pre-push to Google Calendar if unconfigured fallback is active
    gcal = get_gcal_client(user["id"])
    if gcal:
        gcal_id = gcal.create_gcal_event(data)
        if gcal_id:
            data["gcal_event_id"] = gcal_id

    return db.create_calendar_event(db_path=DB_PATH, data=data, user_id=user["id"])


@app.patch("/api/calendar/events/{event_id}")
def update_event(event_id: int, body: CalendarEventUpdate, user: dict = Depends(get_current_user)):
    data = body.model_dump(exclude_none=True)

    # Retrieve current event details
    current = db.get_calendar_event(DB_PATH, event_id, user_id=user["id"])
    if not current:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    gcal = get_gcal_client(user["id"])
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

    result = db.update_calendar_event(db_path=DB_PATH, event_id=event_id, data=data, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return result


@app.delete("/api/calendar/events/{event_id}")
def delete_event(event_id: int, user: dict = Depends(get_current_user)):
    current = db.get_calendar_event(DB_PATH, event_id, user_id=user["id"])
    if not current:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    gcal = get_gcal_client(user["id"])
    if gcal:
        gcal_id = current.get("gcal_event_id")
        if gcal_id:
            gcal.delete_gcal_event(gcal_id)

    ok = db.delete_calendar_event(db_path=DB_PATH, event_id=event_id, user_id=user["id"])
    if not ok:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return {"ok": True}


@app.post("/api/calendar/sync-tasks")
def sync_tasks(user: dict = Depends(get_current_user)):
    """Import task deadlines as calendar events."""
    created = db.sync_tasks_to_calendar(db_path=DB_PATH, user_id=user["id"])
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
    local_tasks_synced = db.sync_tasks_to_calendar(db_path=DB_PATH, user_id=user["id"])

    # 2. Upload any local events that don't have a gcal_event_id, and push local updates
    local_events = db.get_calendar_events(db_path=DB_PATH, user_id=user["id"])
    uploaded = 0
    updated_remote = 0

    for event in local_events:
        gcal_id = event.get("gcal_event_id")
        if not gcal_id:
            new_id = gcal.create_gcal_event(dict(event))
            if new_id:
                db.update_calendar_event(DB_PATH, event["id"], {"gcal_event_id": new_id}, user_id=user["id"])
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
            }, user_id=user["id"])
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
    svc = get_email_service(user["id"])
    if not svc:
        raise HTTPException(
            status_code=400,
            detail="Email processing service is not available. Check that credentials.json exists."
        )
    try:
        result = svc.process_new_emails(trigger="manual")
        return result
    except Exception as e:
        logger.error("Email processing failed for user %d: %s", user["id"], e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Email processing error: {str(e)}")


@app.get("/api/emails/status")
def email_status(user: dict = Depends(get_current_user)):
    """Get current email processing status and provider info."""
    svc = get_email_service(user["id"])
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
    svc = get_email_service(user["id"])
    if not svc:
        return []
    return svc.get_history(limit=limit)


@app.get("/api/emails/fetched")
def get_fetched_emails_list(
    limit: int = Query(default=51, le=501),
    offset: int = Query(default=0, ge=0),
    status: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    user: dict = Depends(get_current_user),
):
    """Get fetched emails for the current user with pagination support."""
    return db.get_fetched_emails(
        db_path=DB_PATH,
        user_id=user["id"],
        limit=limit,
        offset=offset,
        status=status,
        category=category,
        tag=tag,
    )

@app.get("/api/emails/tags")
def get_email_tags_list(user: dict = Depends(get_current_user)):
    """Get all unique categories/tags (deadline_types) associated with the user's emails."""
    return db.get_email_tags(db_path=DB_PATH, user_id=user["id"])


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
        
        # Must exactly match the Authorized Redirect URI in Google Cloud Console
        # For Desktop apps, http://localhost:8000/api/gmail/callback is valid.
        redirect_uri = str(request.base_url).rstrip("/") + "/api/gmail/callback"
        
        flow = Flow.from_client_secrets_file(str(creds_path), scopes=GMAIL_SCOPES)
        flow.redirect_uri = redirect_uri

        auth_url, state = flow.authorization_url(prompt="consent", access_type="offline")
        db.save_oauth_state(DB_PATH, state, user["id"])
        
        # Save code_verifier for PKCE validation in the callback
        _oauth_flows[state] = getattr(flow, "code_verifier", None)
        
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

    sys.path.insert(0, str(_email_proc_dir))
    from gmail_scopes import GMAIL_SCOPES

    import urllib.parse
    try:
        user_id = db.get_user_for_oauth_state(DB_PATH, state)
        if not user_id:
            logger.error("OAuth flow state not found or expired")
            return RedirectResponse(url="/#settings?error=session_expired")
            
        creds_path = Path(os.getenv("GMAIL_CREDENTIALS_PATH", _email_proc_dir / "credentials.json"))
        if not creds_path.exists():
            return RedirectResponse(url="/#settings?error=missing_credentials")
            
        from google_auth_oauthlib.flow import Flow
        redirect_uri = str(request.base_url).rstrip("/") + "/api/gmail/callback"
        flow = Flow.from_client_secrets_file(str(creds_path), scopes=GMAIL_SCOPES)
        flow.redirect_uri = redirect_uri
        
        # Restore PKCE code verifier
        if state in _oauth_flows and _oauth_flows[state]:
            flow.code_verifier = _oauth_flows.pop(state)
        
        # Fetch the token using the full authorization response URL
        flow.fetch_token(authorization_response=str(request.url))
        
        creds = flow.credentials
        db.update_user_gmail_token(DB_PATH, user_id, creds.to_json())
        
        logger.info("Gmail connected successfully via web callback for user %d", user_id)
        return RedirectResponse(url="/#settings")
    except Exception as e:
        logger.error("OAuth callback failed: %s", e)
        return RedirectResponse(url=f"/#settings?error={urllib.parse.quote(str(e))}")


@app.post("/api/gmail/disconnect")
def disconnect_gmail(user: dict = Depends(get_current_user)):
    """Remove Gmail token to disconnect the account."""
    db.update_user_gmail_token(DB_PATH, user["id"], None)
    logger.info("Gmail disconnected for user %d", user["id"])
    return {"ok": True, "message": "Gmail disconnected."}


# ─── Reply engine endpoints ─────────────────────────────────────────────────

def _resolve_draft_gmail_ids(body: ReplyDraftRequest, user_id: int) -> tuple[str | None, str | None]:
    if body.gmail_message_id:
        return body.gmail_message_id, body.gmail_thread_id
    if body.task_id:
        task = db.get_task_by_id(DB_PATH, body.task_id, user_id=user_id)
        if task:
            return task.get("source_email_id"), body.gmail_thread_id
    return None, body.gmail_thread_id


@app.post("/api/replies/draft", status_code=201)
def generate_reply(body: ReplyDraftRequest, user: dict = Depends(get_current_user)):
    """Generate an AI reply draft and store it for approval."""
    gmail_message_id, gmail_thread_id = _resolve_draft_gmail_ids(body, user["id"])
    import re
    match = re.search(r'<(.+?)>', body.original_sender)
    raw_email = match.group(1) if match else body.original_sender
    raw_email = raw_email.strip().lower()

    contact_role = None
    tone_preference = None
    knowledge_context = ""
    with db.get_db(DB_PATH) as conn:
        rel = conn.execute(
            "SELECT role, tone_preference FROM contact_relationships WHERE user_id = ? AND email_address = ?",
            (user["id"], raw_email)
        ).fetchone()
        if rel:
            contact_role = rel["role"]
            tone_preference = rel["tone_preference"]
            
        relevant_facts = vector_store.query_collection(user_id=user["id"], query=body.original_body, n_results=5)
        if relevant_facts:
            knowledge_context = "KNOWLEDGE BASE CONTEXT:\n" + "\n\n".join(relevant_facts)

    draft_text, model_used, confidence, auto_send_eligible = generate_reply_text(
        original_subject=body.original_subject,
        original_sender=body.original_sender,
        original_body=body.original_body,
        reply_intent=body.reply_intent,
        contact_role=contact_role,
        tone_preference=tone_preference,
        knowledge_context=knowledge_context,
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
    return db.create_reply_draft(db_path=DB_PATH, data=data, user_id=user["id"])


@app.get("/api/relationships")
def list_relationships(user: dict = Depends(get_current_user)):
    return db.get_contact_relationships(db_path=DB_PATH, user_id=user["id"])

@app.post("/api/relationships", status_code=201)
def upsert_relationship(body: ContactRelationshipCreate, user: dict = Depends(get_current_user)):
    return db.upsert_contact_relationship(
        db_path=DB_PATH,
        user_id=user["id"],
        email_address=body.email_address,
        role=body.role,
        importance=body.importance,
        tone_preference=body.tone_preference
    )

@app.delete("/api/relationships/{rel_id}")
def delete_relationship(rel_id: int, user: dict = Depends(get_current_user)):
    success = db.delete_contact_relationship(db_path=DB_PATH, user_id=user["id"], rel_id=rel_id)
    if not success:
        raise HTTPException(status_code=404, detail="Relationship not found")
    return {"status": "deleted"}

@app.get("/api/knowledge")
def list_knowledge(user: dict = Depends(get_current_user)):
    return db.get_knowledge_base_entries(db_path=DB_PATH, user_id=user["id"])

@app.post("/api/knowledge", status_code=201)
def upsert_knowledge(body: KnowledgeBaseCreate, user: dict = Depends(get_current_user)):
    entry = db.upsert_knowledge_base_entry(
        db_path=DB_PATH,
        user_id=user["id"],
        title=body.title,
        content=body.content,
        entry_id=body.entry_id,
        status=body.status,
        source=body.source
    )
    if entry.get("status") == "active":
        vector_store.add_to_collection(user["id"], entry["id"], entry["title"], entry["content"])
    elif entry.get("status") == "draft" and body.entry_id:
        vector_store.delete_from_collection(user["id"], entry["id"])
    return entry

from google import genai
_dedup_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

def _is_duplicate_fact(user_id: int, title: str, content: str) -> bool:
    query_text = f"Title: {title}\nContent: {content}"
    similar_facts = vector_store.query_collection(user_id=user_id, query=query_text, n_results=3)
    if not similar_facts:
        return False
        
    prompt = f"""
    You are managing a Knowledge Base. We are trying to add a NEW FACT.
    Please check if this NEW FACT is already covered by the EXISTING FACTS.
    If it is substantially the same or a duplicate, reply exactly with: DUPLICATE
    If it is new, different, or updates old info, reply exactly with: KEEP
    
    NEW FACT:
    Title: {title}
    Content: {content}
    
    EXISTING FACTS:
    {chr(10).join(similar_facts)}
    """
    try:
        response = _dedup_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        return "DUPLICATE" in response.text.upper()
    except:
        return False

@app.post("/api/knowledge/extract")
def extract_email_knowledge(body: EmailExtractRequest, user: dict = Depends(get_current_user)):
    facts = ingestion_service.extract_facts_from_email(body.email_body)
    entries = []
    for f in facts:
        if _is_duplicate_fact(user["id"], f["title"], f["content"]):
            continue
        entry = db.upsert_knowledge_base_entry(DB_PATH, user["id"], title=f["title"], content=f["content"], status="draft", source="Extracted from Email")
        entries.append(entry)
    return {"facts": entries}

@app.post("/api/knowledge/ingest-url")
def ingest_url_endpoint(body: UrlIngestRequest, user: dict = Depends(get_current_user)):
    facts = ingestion_service.ingest_url(body.url)
    entries = []
    source_str = f"Scraped from URL: {body.url}"
    for f in facts:
        if _is_duplicate_fact(user["id"], f["title"], f["content"]):
            continue
        entry = db.upsert_knowledge_base_entry(DB_PATH, user["id"], title=f["title"], content=f["content"], status="draft", source=source_str)
        entries.append(entry)
    return {"facts": entries}

@app.post("/api/knowledge/upload-doc")
async def upload_doc_endpoint(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    content = await file.read()
    facts = ingestion_service.ingest_document(content, file.filename)
    entries = []
    source_str = f"Uploaded Document: {file.filename}"
    for f in facts:
        if _is_duplicate_fact(user["id"], f["title"], f["content"]):
            continue
        entry = db.upsert_knowledge_base_entry(DB_PATH, user["id"], title=f["title"], content=f["content"], status="draft", source=source_str)
        entries.append(entry)
    return {"facts": entries}

@app.delete("/api/knowledge/{entry_id}")
def delete_knowledge(entry_id: int, user: dict = Depends(get_current_user)):
    success = db.delete_knowledge_base_entry(db_path=DB_PATH, user_id=user["id"], entry_id=entry_id)
    if not success:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    vector_store.delete_from_collection(user["id"], entry_id)
    return {"status": "deleted"}

@app.get("/api/replies")
def list_replies(status: str | None = None, limit: int = Query(default=50, le=200), user: dict = Depends(get_current_user)):
    return db.get_reply_drafts(db_path=DB_PATH, status=status, limit=limit, user_id=user["id"])


@app.patch("/api/replies/{draft_id}")
def update_reply(draft_id: int, body: ReplyDraftUpdate, user: dict = Depends(get_current_user)):
    data = body.model_dump(exclude_none=True)
    result = db.update_reply_draft(db_path=DB_PATH, draft_id=draft_id, data=data, user_id=user["id"])
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

    existing = db.get_reply_draft(DB_PATH, draft_id, user_id=user["id"])
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
        gmail_response = send_approved_reply(DB_PATH, draft, user_id=user["id"])
        result = mark_draft_sent(DB_PATH, draft_id, gmail_response, user_id=user["id"])
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
    ok = db.delete_reply_draft(DB_PATH, draft_id, user_id=user["id"])
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
