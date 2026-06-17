"""
Authentication module for the AI Chief of Staff dashboard.

Provides account-based authentication with bcrypt password hashing
and JWT token management. Accounts are stored in the local SQLite database.
"""

import logging
import os
import re
import sqlite3
import warnings
from datetime import datetime, timezone, timedelta
from db_core import get_connection

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# JWT configuration
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not JWT_SECRET_KEY:
    logger.warning("JWT_SECRET_KEY environment variable not set. Using insecure fallback. Please set this in production!")
    JWT_SECRET_KEY = "fallback-insecure-secret-key-change-in-prod-12345"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

security_scheme = HTTPBearer(auto_error=False)


# ─── Password utilities ─────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a password using bcrypt with a random salt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ─── JWT utilities ───────────────────────────────────────────────────────────

def create_token(user_id: int, email: str, name: str) -> str:
    """Create a JWT token with user info and expiry."""
    payload = {
        "sub": str(user_id),
        "email": email,
        "name": name,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    """Decode and validate a JWT token. Returns payload or None."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ─── Account management ─────────────────────────────────────────────────────

def _validate_email(email: str) -> bool:
    """Basic email format validation."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def has_any_account(db_path: str) -> bool:
    """Check if any user account exists in the database."""
    from db import get_db
    try:
        conn = get_db(db_path)
        try:
            row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
            return row[0] > 0
        finally:
            conn.close()
    except Exception:
        # Table doesn't exist yet or DB not initialized
        return False


def create_account(db_path: str, name: str, email: str, password: str) -> dict:
    """Create a new user account.

    Validates input, hashes the password, and inserts into the users table.
    Raises ValueError for invalid input or duplicate email.
    """
    # Validation
    name = name.strip()
    email = email.strip().lower()

    if not name:
        raise ValueError("Name is required.")
    if not _validate_email(email):
        raise ValueError("Please enter a valid email address.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    from db import get_db
    conn = get_db(db_path)
    try:
        # Check for duplicate email
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            raise ValueError("An account with this email already exists.")

        hashed = hash_password(password)
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "INSERT INTO users (name, email, password, created_at) VALUES (?, ?, ?, ?) RETURNING id",
            (name, email, hashed, now),
        )
        row = cursor.fetchone()
        user_id = row["id"] if isinstance(row, dict) else row[0]
        conn.commit()
        logger.info("Account created for %s (%s)", name, email)
        return {"id": user_id, "name": name, "email": email}
    finally:
        conn.close()


def authenticate(db_path: str, email: str, password: str) -> dict | None:
    """Authenticate a user by email and password.

    Returns user dict if valid, None if credentials are wrong.
    Updates last_login timestamp on success.
    """
    email = email.strip().lower()

    from db import get_db
    conn = get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, name, email, password FROM users WHERE email = ?", (email,)
        ).fetchone()
        if not row:
            return None

        if not verify_password(password, row["password"]):
            return None

        # Update last_login
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, row["id"]))
        conn.commit()

        logger.info("User %s logged in", row["email"])
        return {"id": row["id"], "name": row["name"], "email": row["email"]}
    finally:
        conn.close()


def change_password(db_path: str, user_id: int, current_password: str, new_password: str) -> bool:
    """Change a user's password. Requires the current password for verification."""
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters.")

    from db import get_db
    conn = get_db(db_path)
    try:
        row = conn.execute("SELECT password FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("User not found.")

        if not verify_password(current_password, row["password"]):
            raise ValueError("Current password is incorrect.")

        hashed = hash_password(new_password)
        conn.execute("UPDATE users SET password = ? WHERE id = ?", (hashed, user_id))
        conn.commit()
        logger.info("Password changed for user %d", user_id)
        return True
    finally:
        conn.close()


# ─── FastAPI dependency ──────────────────────────────────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> dict:
    """FastAPI dependency that extracts and validates the JWT from the request.

    Use as a dependency on any route that requires authentication:
        @app.get("/api/protected")
        def protected(user: dict = Depends(get_current_user)):
            ...
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "id": int(payload["sub"]),
        "email": payload["email"],
        "name": payload["name"],
    }
