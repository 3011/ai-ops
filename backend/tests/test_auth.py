import unittest
from unittest.mock import patch

from app.auth import create_session_token, decode_session_token, hash_password, verify_password


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


if __name__ == "__main__":
    unittest.main()
