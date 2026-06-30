"""Outlook provider using Microsoft Graph API."""

import logging
import json
import os
import requests
from datetime import datetime, timezone
from dateutil import parser as dp

from .base import EmailProvider, FetchedEmail

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

class OutlookProvider(EmailProvider):
    """Outlook integration via Microsoft Graph API."""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self._client_id = os.getenv("OUTLOOK_CLIENT_ID")
        self._client_secret = os.getenv("OUTLOOK_CLIENT_SECRET")

    @property
    def name(self) -> str:
        return "outlook"

    @property
    def display_name(self) -> str:
        return "Microsoft Outlook"

    def is_configured(self) -> bool:
        """Check if OUTLOOK_CLIENT_ID and OUTLOOK_CLIENT_SECRET are set."""
        return bool(self._client_id and self._client_secret)

    def _get_token_json(self) -> str | None:
        from db import get_user_outlook_token, DEFAULT_DB_PATH
        db_path = os.getenv("DB_PATH", DEFAULT_DB_PATH)
        return get_user_outlook_token(db_path, self.user_id)

    def _get_access_token(self) -> str | None:
        """Retrieve the access token. Refresh it if expired."""
        token_str = self._get_token_json()
        if not token_str:
            return None
            
        try:
            token_data = json.loads(token_str)
            expires_at = token_data.get("expires_at", 0)
            
            # If expired (with a 5 min buffer), refresh it
            if datetime.now(timezone.utc).timestamp() > (expires_at - 300):
                return self._refresh_token(token_data)
                
            return token_data.get("access_token")
        except Exception as e:
            logger.error("Failed to parse or refresh Outlook token: %s", e)
            return None

    def _refresh_token(self, token_data: dict) -> str | None:
        """Use refresh_token to get a new access token."""
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            return None
            
        token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        payload = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        res = requests.post(token_url, data=payload, timeout=10)
        res.raise_for_status()
        new_token_data = res.json()
        
        # Calculate new expires_at
        expires_in = new_token_data.get("expires_in", 3600)
        new_token_data["expires_at"] = datetime.now(timezone.utc).timestamp() + expires_in
        
        # Save to db
        from db import update_user_outlook_token, DEFAULT_DB_PATH
        db_path = os.getenv("DB_PATH", DEFAULT_DB_PATH)
        
        # Keep old refresh token if the response didn't include a new one
        if "refresh_token" not in new_token_data:
            new_token_data["refresh_token"] = refresh_token
            
        update_user_outlook_token(db_path, self.user_id, json.dumps(new_token_data))
        
        return new_token_data.get("access_token")

    def is_authenticated(self) -> bool:
        """Check if a valid or refreshable token exists."""
        return self._get_access_token() is not None

    def fetch_emails(self, max_results: int = 50, mode: str = "unread") -> list[FetchedEmail]:
        """Fetch emails via Microsoft Graph API."""
        access_token = self._get_access_token()
        if not access_token:
            raise RuntimeError("Outlook is not authenticated. Please connect via Settings.")
            
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Prefer": 'outlook.body-content-type="text"'
        }
        
        # Query params
        # Get Inbox folder messages
        url = f"{GRAPH_API_BASE}/me/mailFolders/inbox/messages"
        
        params = {
            "$top": str(max_results),
            "$select": "id,subject,from,bodyPreview,receivedDateTime,conversationId,isRead,body",
            "$orderby": "receivedDateTime desc"
        }
        
        if mode == "unread":
            params["$filter"] = "isRead eq false"

        res = requests.get(url, headers=headers, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()
        
        messages = data.get("value", [])
        
        fetched = []
        for msg in messages:
            # Parse sender
            sender_obj = msg.get("from", {}).get("emailAddress", {})
            sender_name = sender_obj.get("name", "")
            sender_addr = sender_obj.get("address", "")
            sender = f"{sender_name} <{sender_addr}>" if sender_name else sender_addr
            
            # Parse date
            try:
                dt = dp.parse(msg.get("receivedDateTime"))
            except Exception:
                dt = datetime.now(timezone.utc)
                
            # Body extraction
            body_content = msg.get("body", {}).get("content", "").strip()
            if not body_content:
                body_content = msg.get("bodyPreview", "")
                
            fetched.append(
                FetchedEmail(
                    email_id=msg["id"],
                    subject=msg.get("subject", "(No Subject)"),
                    sender=sender,
                    body=body_content,
                    received_at=dt,
                    thread_id=msg.get("conversationId"),
                )
            )
            
        return fetched

    def send_reply(self, to: str, subject: str, body: str, *, thread_id: str = None, in_reply_to_message_id: str = None) -> dict:
        """Send a reply to an existing email via Graph API."""
        access_token = self._get_access_token()
        if not access_token:
            raise RuntimeError("Outlook is not authenticated.")
            
        message_id = in_reply_to_message_id
        if not message_id:
            raise ValueError("Outlook requires in_reply_to_message_id to send a reply.")
            
        url = f"{GRAPH_API_BASE}/me/messages/{message_id}/reply"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "message": {
                "body": {
                    "contentType": "Text",
                    "content": body
                }
            }
        }
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        res.raise_for_status()
        
        # Outlook /reply returns 202 Accepted with no body, so we mock a response dict
        return {"id": message_id, "status": "sent"}
