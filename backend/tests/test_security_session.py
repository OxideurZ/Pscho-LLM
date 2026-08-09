from types import SimpleNamespace

from fastapi import Request
from starlette.responses import Response

from backend.app.security.session import LocalSessionManager, origin_is_local


def _request(headers: dict[str, str]) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=SimpleNamespace(psych_local_host="127.0.0.1", psych_local_port=8000)
        )
    )
    scope = {
        "type": "http",
        "app": app,
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
    }
    return Request(scope)


def test_session_cookie_is_http_only_and_rotates_expiry() -> None:
    manager = LocalSessionManager(timeout_seconds=60)
    response = Response()
    manager.issue(response)
    cookie = response.headers["set-cookie"]
    token = cookie.split("psych_local_session=", 1)[1].split(";", 1)[0]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert manager.valid(token)
    assert not manager.valid("wrong-token")


def test_origin_policy_accepts_only_local_frontends() -> None:
    assert origin_is_local(_request({"origin": "http://127.0.0.1:5173"}))
    assert not origin_is_local(_request({"origin": "https://evil.example"}))
