import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import Response
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.auth import create_session_token, decode_session_token, hash_password, verify_password
from app.governance import LoginInput, login


class DummyUser:
    id = 42
    token_version = 3


class AuthTests(unittest.TestCase):
    def test_password_hash_and_verify(self):
        encoded = hash_password("A-secure-test-value-123")
        self.assertTrue(verify_password("A-secure-test-value-123", encoded))
        self.assertFalse(verify_password("incorrect-value", encoded))
        self.assertNotIn("A-secure-test-value-123", encoded)

    def test_session_signature_rejects_tampering(self):
        with patch("app.auth.settings.auth_session_secret", "x" * 64):
            token = create_session_token(DummyUser())
            self.assertEqual(decode_session_token(token)["uid"], 42)
            body, signature = token.split(".", 1)
            self.assertIsNone(decode_session_token(body + "." + signature[:-1] + ("A" if signature[-1] != "A" else "B")))


class LoginSessionStub:
    def __init__(self, user):
        self.user = user
        self.commit_count = 0

    async def scalar(self, *_args, **_kwargs):
        return self.user

    async def commit(self):
        self.commit_count += 1


def make_request() -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/auth/login",
        "raw_path": b"/api/v1/auth/login",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
    })


class LoginRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_password_returns_401_and_clears_stale_cookie(self):
        user = SimpleNamespace(
            id=7,
            username="admin",
            is_active=True,
            password_hash=hash_password("correct-password-123"),
        )
        session = LoginSessionStub(user)
        with patch("app.governance.record_audit", new=AsyncMock()) as record_audit:
            result = await login(
                LoginInput(username="admin", password="wrong-password-123"),
                make_request(),
                Response(),
                session,
            )

        self.assertIsInstance(result, JSONResponse)
        self.assertEqual(result.status_code, 401)
        self.assertEqual(json.loads(result.body), {"detail": "用户名或密码错误"})
        self.assertEqual(result.headers["cache-control"], "no-store")
        cookie = result.headers.get("set-cookie", "")
        self.assertIn("aiops_session=", cookie)
        self.assertIn("Max-Age=0", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertEqual(session.commit_count, 1)
        record_audit.assert_awaited_once()

    async def test_unknown_user_still_executes_password_verifier(self):
        session = LoginSessionStub(None)
        with (
            patch("app.governance.record_audit", new=AsyncMock()),
            patch("app.governance.verify_password", return_value=False) as verifier,
        ):
            result = await login(
                LoginInput(username="missing-user", password="wrong-password-123"),
                make_request(),
                Response(),
                session,
            )

        self.assertEqual(result.status_code, 401)
        verifier.assert_called_once()
        self.assertTrue(verifier.call_args.args[1].startswith("pbkdf2_sha256$"))


if __name__ == "__main__":
    unittest.main()
