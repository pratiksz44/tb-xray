"""Simple single-user login.

Username comes from config.yaml (auth.username); the password from the APP_PASSWORD environment variable, so
it is never stored in the repo. POST /api/login sets a signed, HttpOnly session cookie; require_user protects
the prediction endpoints. The signing key is random per process, so a restart or deploy signs everyone out.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Annotated

from fastapi import Cookie, HTTPException

from app.config import AUTH_USERNAME, SESSION_HOURS

COOKIE_NAME = "tbx_session"
SESSION_SECONDS = int(SESSION_HOURS * 3600)
_KEY = secrets.token_bytes(32)


def check_credentials(username: str, password: str) -> bool:
    expected = os.environ.get("APP_PASSWORD", "")
    user_ok = hmac.compare_digest(username.encode(), AUTH_USERNAME.encode())
    password_ok = hmac.compare_digest(password.encode(), expected.encode())
    return bool(expected) and user_ok and password_ok  # no APP_PASSWORD set = nobody can sign in


def _sign(payload: str) -> str:
    return hmac.new(_KEY, payload.encode(), hashlib.sha256).hexdigest()


def create_session(username: str) -> str:
    payload = f"{username}|{int(time.time()) + SESSION_SECONDS}"
    return f"{payload}|{_sign(payload)}"


def session_user(token: str | None) -> str | None:
    """The signed-in username, or None if the cookie is missing, tampered with or expired."""
    if not token:
        return None
    payload, _, signature = token.rpartition("|")
    if not hmac.compare_digest(signature, _sign(payload)):
        return None
    username, _, expires = payload.rpartition("|")
    if not expires.isdigit() or int(expires) < time.time():
        return None
    return username


def require_user(session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None) -> str:
    user = session_user(session)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    return user
