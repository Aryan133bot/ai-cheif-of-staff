"""OAuth scopes used for Gmail integration."""

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def token_includes_send_scope(token_path: str) -> bool:
    """Return True if the saved OAuth token authorizes sending mail."""
    from pathlib import Path

    import json

    path = Path(token_path)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    scopes = data.get("scopes") or []
    if isinstance(scopes, str):
        scopes = scopes.split()
    return GMAIL_SEND_SCOPE in scopes
