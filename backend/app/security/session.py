from __future__ import annotations

import hashlib
import hmac
import secrets
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, Response

SESSION_COOKIE = "psych_local_session"


class LocalSessionManager:
    def __init__(
        self, timeout_seconds: int = 30 * 60, bootstrap_secret: bytes | None = None
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._bootstrap_token = (
            urlsafe_b64encode(bootstrap_secret).rstrip(b"=").decode("ascii")
            if bootstrap_secret is not None
            else None
        )
        self._sessions: dict[str, datetime] = {}

    def issue(self, response: Response, bootstrap_token: str | None = None) -> bool:
        if self._bootstrap_token is not None and not (
            bootstrap_token and hmac.compare_digest(bootstrap_token, self._bootstrap_token)
        ):
            return False
        token = secrets.token_urlsafe(32)
        self._sessions[self._digest(token)] = self._expiry()
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=self.timeout_seconds,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return True

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        digest = self._digest(token)
        expiry = self._sessions.get(digest)
        if expiry is None:
            return False
        if expiry <= datetime.now(UTC):
            self._sessions.pop(digest, None)
            return False
        self._sessions[digest] = self._expiry()
        return True

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _expiry(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self.timeout_seconds)


def origin_is_local(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return False
    settings = request.app.state.settings
    expected = {
        f"http://{settings.psych_local_host}:{settings.psych_local_port}",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    }
    return origin in expected


async def require_local_session(request: Request) -> None:
    if not request.app.state.settings.security_enabled:
        return
    manager: LocalSessionManager = request.app.state.local_session_manager
    if not manager.valid(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status_code=401, detail="LOCAL_SESSION_REQUIRED")
