import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask_jwt_extended import decode_token

from app.app import create_app
from app.services.auth import (
    RefreshTokenReuseError,
    SessionRevokedError,
    rotate_refresh_token,
    start_session,
)


TEST_CONFIG = {
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": "postgresql://test:test@localhost/test",
    "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
    "API_DOCS_ENABLED": False,
}


class AuthSessionServiceTests(unittest.TestCase):
    def setUp(self):
        with patch(
            "app.services.auth.is_token_revoked",
            return_value=False,
        ):
            self.app = create_app(TEST_CONFIG)
        self.user = SimpleNamespace(id=1, role="administrator")

    @patch("app.services.auth.db")
    def test_start_session_persists_only_refresh_metadata(self, mocked_db):
        with self.app.app_context():
            access_token, refresh_token = start_session(self.user)
            access_claims = decode_token(access_token)
            refresh_claims = decode_token(refresh_token)

        mocked_db.session.add.assert_called_once()
        mocked_db.session.commit.assert_called_once()
        auth_session = mocked_db.session.add.call_args.args[0]
        self.assertEqual(auth_session.user_id, self.user.id)
        self.assertEqual(
            auth_session.current_refresh_jti,
            refresh_claims["jti"],
        )
        self.assertEqual(access_claims["sid"], auth_session.id)
        self.assertNotEqual(access_token, refresh_token)

    @patch("app.services.auth.db")
    def test_refresh_rotation_replaces_the_current_jti(self, mocked_db):
        auth_session = SimpleNamespace(
            id="session-id",
            user=self.user,
            current_refresh_jti="presented-jti",
            expires_at=None,
            revoked_at=None,
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = auth_session
        mocked_db.session.execute.return_value = result

        with self.app.app_context():
            access_token, refresh_token = rotate_refresh_token(
                "session-id",
                "presented-jti",
            )
            refresh_claims = decode_token(refresh_token)

        self.assertTrue(access_token)
        self.assertEqual(
            auth_session.current_refresh_jti,
            refresh_claims["jti"],
        )
        self.assertNotEqual(
            auth_session.current_refresh_jti,
            "presented-jti",
        )
        mocked_db.session.commit.assert_called_once()

    @patch("app.services.auth.db")
    def test_refresh_reuse_revokes_the_whole_session(self, mocked_db):
        auth_session = SimpleNamespace(
            id="session-id",
            user=self.user,
            current_refresh_jti="newer-jti",
            expires_at=None,
            revoked_at=None,
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = auth_session
        mocked_db.session.execute.return_value = result

        with self.app.app_context():
            with self.assertRaises(RefreshTokenReuseError):
                rotate_refresh_token("session-id", "old-jti")

        self.assertIsNotNone(auth_session.revoked_at)
        mocked_db.session.commit.assert_called_once()

    @patch("app.services.auth.db")
    def test_revoked_session_cannot_rotate(self, mocked_db):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mocked_db.session.execute.return_value = result

        with self.app.app_context():
            with self.assertRaises(SessionRevokedError):
                rotate_refresh_token("missing-session", "old-jti")

        mocked_db.session.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()

