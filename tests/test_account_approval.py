import unittest

from flask import jsonify
from flask_jwt_extended import decode_token
from sqlalchemy import event

from app.app import create_app
from app.authorization import roles_required
from app.extensions import db
from app.models import (
    ADMIN_USER_ROLE,
    REFEREE_USER_ROLE,
    USER_ROLE,
    User,
)
from app.services.users import create_user


class AccountApprovalTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
                "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            }
        )

        @self.app.get("/test/referee-business")
        @roles_required(REFEREE_USER_ROLE)
        def referee_business_endpoint():
            return jsonify(ok=True), 200

        with self.app.app_context():
            event.listen(
                db.engine,
                "connect",
                self._add_sqlite_functions,
                once=True,
            )
            db.create_all()
            create_user(
                {
                    "name": "Admin User",
                    "birthdate": "1990-01-01",
                    "email": "admin@example.com",
                    "password": "Password123",
                },
                role=ADMIN_USER_ROLE,
            )

        self.client = self.app.test_client()
        self.admin_tokens = self._login("admin@example.com")
        self.admin_headers = self._access_headers(self.admin_tokens)

    @staticmethod
    def _add_sqlite_functions(connection, _connection_record):
        connection.create_function("char_length", 1, len)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    @staticmethod
    def _signup_payload(
        email: str,
        requested_role: str = "referee",
    ) -> dict:
        return {
            "name": "Pending User",
            "birthdate": "1995-05-10",
            "email": email,
            "password": "Password123",
            "requested_role": requested_role,
        }

    def _signup(self, email: str, requested_role: str = "referee"):
        return self.client.post(
            "/auth/signup",
            json=self._signup_payload(email, requested_role),
        )

    def _login(self, email: str) -> dict:
        response = self.client.post(
            "/auth/login",
            json={"email": email, "password": "Password123"},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    @staticmethod
    def _access_headers(tokens: dict) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {tokens['access_token']}"
        }

    @staticmethod
    def _refresh_headers(tokens: dict) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {tokens['refresh_token']}"
        }

    def test_signup_creates_pending_users_for_both_requestable_roles(self):
        for index, requested_role in enumerate(
            ("referee", "federation_delegate")
        ):
            with self.subTest(requested_role=requested_role):
                response = self._signup(
                    f"pending{index}@example.com",
                    requested_role,
                )
                self.assertEqual(response.status_code, 201)
                user = response.get_json()["user"]
                self.assertEqual(user["role"], USER_ROLE)
                self.assertEqual(user["requested_role"], requested_role)
                self.assertTrue(user["is_active"])
                self.assertNotIn("password_hash", user)

    def test_signup_rejects_missing_invalid_and_privileged_roles(self):
        invalid_roles = (None, "user", "administrator", "coach")
        for index, requested_role in enumerate(invalid_roles):
            payload = self._signup_payload(f"invalid{index}@example.com")
            if requested_role is None:
                payload.pop("requested_role")
            else:
                payload["requested_role"] = requested_role
            with self.subTest(requested_role=requested_role):
                response = self.client.post("/auth/signup", json=payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.get_json()["error"]["code"],
                    "invalid_requested_role",
                )

    def test_signup_rejects_client_supplied_role(self):
        payload = self._signup_payload("escalation@example.com")
        payload["role"] = "administrator"

        response = self.client.post("/auth/signup", json=payload)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "validation_error",
        )

    def test_pending_user_can_use_auth_self_service_but_not_business(self):
        self._signup("pending@example.com")
        tokens = self._login("pending@example.com")
        headers = self._access_headers(tokens)

        me_response = self.client.get("/auth/me", headers=headers)
        business_response = self.client.get(
            "/test/referee-business",
            headers=headers,
        )

        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.get_json()["user"]["role"], USER_ROLE)
        self.assertEqual(business_response.status_code, 403)
        self.assertEqual(
            business_response.get_json()["error"]["code"],
            "approval_required",
        )

    def test_approval_updates_permissions_for_existing_access_token(self):
        signup = self._signup("approval@example.com").get_json()["user"]
        tokens = self._login("approval@example.com")
        headers = self._access_headers(tokens)

        approval = self.client.post(
            f"/users/{signup['id']}/approve",
            headers=self.admin_headers,
        )
        business = self.client.get(
            "/test/referee-business",
            headers=headers,
        )

        self.assertEqual(approval.status_code, 200)
        approved_user = approval.get_json()["user"]
        self.assertEqual(approved_user["role"], REFEREE_USER_ROLE)
        self.assertEqual(approved_user["requested_role"], REFEREE_USER_ROLE)
        self.assertEqual(business.status_code, 200)

        repeated = self.client.post(
            f"/users/{signup['id']}/approve",
            headers=self.admin_headers,
        )
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(
            repeated.get_json()["error"]["code"],
            "user_not_pending",
        )

    def test_stale_administrator_claim_cannot_override_current_role(self):
        admin_headers = dict(self.admin_headers)
        with self.app.app_context():
            admin = db.session.execute(
                db.select(User).where(User.email == "admin@example.com")
            ).scalar_one()
            admin.role = USER_ROLE
            admin.requested_role = REFEREE_USER_ROLE
            db.session.commit()

        response = self.client.get("/sports", headers=admin_headers)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "approval_required",
        )

    def test_disabled_referee_keeps_auth_self_service_and_can_be_enabled(self):
        with self.app.app_context():
            referee = create_user(
                {
                    "name": "Existing Referee",
                    "birthdate": "1990-01-01",
                    "email": "referee@example.com",
                    "password": "Password123",
                },
                role=REFEREE_USER_ROLE,
            )
            referee_id = referee.id
        referee_tokens = self._login("referee@example.com")
        referee_headers = self._access_headers(referee_tokens)

        disabled = self.client.post(
            f"/users/{referee_id}/disable",
            headers=self.admin_headers,
        )
        me_response = self.client.get("/auth/me", headers=referee_headers)
        business_response = self.client.get(
            "/test/referee-business",
            headers=referee_headers,
        )
        new_login = self.client.post(
            "/auth/login",
            json={
                "email": "referee@example.com",
                "password": "Password123",
            },
        )

        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(me_response.get_json()["user"]["is_active"])
        self.assertEqual(business_response.status_code, 403)
        self.assertEqual(
            business_response.get_json()["error"]["code"],
            "account_disabled",
        )
        self.assertEqual(new_login.status_code, 200)

        enabled = self.client.post(
            f"/users/{referee_id}/enable",
            headers=self.admin_headers,
        )
        restored_business = self.client.get(
            "/test/referee-business",
            headers=referee_headers,
        )
        self.assertEqual(enabled.status_code, 200)
        self.assertEqual(restored_business.status_code, 200)

    def test_refresh_rotation_uses_current_role_and_detects_reuse(self):
        user = self._signup("refresh@example.com").get_json()["user"]
        tokens = self._login("refresh@example.com")
        self.client.post(
            f"/users/{user['id']}/approve",
            headers=self.admin_headers,
        )

        refreshed = self.client.post(
            "/auth/refresh",
            headers=self._refresh_headers(tokens),
        )
        self.assertEqual(refreshed.status_code, 200)
        refreshed_tokens = refreshed.get_json()
        with self.app.app_context():
            claims = decode_token(refreshed_tokens["access_token"])
        self.assertEqual(claims["role"], REFEREE_USER_ROLE)

        reused = self.client.post(
            "/auth/refresh",
            headers=self._refresh_headers(tokens),
        )
        self.assertEqual(reused.status_code, 401)
        revoked_access = self.client.get(
            "/auth/me",
            headers=self._access_headers(refreshed_tokens),
        )
        self.assertEqual(revoked_access.status_code, 401)

    def test_only_administrator_can_review_and_manage_accounts(self):
        pending_referee = self._signup("review@example.com").get_json()["user"]
        pending_tokens = self._login("review@example.com")
        forbidden = self.client.get(
            "/users?role=user",
            headers=self._access_headers(pending_tokens),
        )
        self.assertEqual(forbidden.status_code, 403)

        listing = self.client.get(
            "/users?role=user&requested_role=referee",
            headers=self.admin_headers,
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(
            [user["id"] for user in listing.get_json()["users"]],
            [pending_referee["id"]],
        )

        self.client.post(
            f"/users/{pending_referee['id']}/approve",
            headers=self.admin_headers,
        )
        referee_delete = self.client.delete(
            f"/users/{pending_referee['id']}",
            headers=self.admin_headers,
        )
        self.assertEqual(referee_delete.status_code, 409)
        self.assertEqual(
            referee_delete.get_json()["error"]["code"],
            "active_user_delete_forbidden",
        )

        pending_delete = self._signup("delete@example.com").get_json()["user"]
        deleted = self.client.delete(
            f"/users/{pending_delete['id']}",
            headers=self.admin_headers,
        )
        self.assertEqual(deleted.status_code, 204)

        delegate = self._signup(
            "delegate@example.com",
            "federation_delegate",
        ).get_json()["user"]
        self.client.post(
            f"/users/{delegate['id']}/approve",
            headers=self.admin_headers,
        )
        delegate_delete = self.client.delete(
            f"/users/{delegate['id']}",
            headers=self.admin_headers,
        )
        self.assertEqual(delegate_delete.status_code, 204)

    def test_deleted_pending_account_cannot_refresh(self):
        pending = self._signup("deleted@example.com").get_json()["user"]
        tokens = self._login("deleted@example.com")
        self.client.delete(
            f"/users/{pending['id']}",
            headers=self.admin_headers,
        )

        response = self.client.post(
            "/auth/refresh",
            headers=self._refresh_headers(tokens),
        )

        self.assertEqual(response.status_code, 401)

    def test_create_admin_cli_still_creates_an_administrator(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(
            args=[
                "create-admin",
                "--name",
                "Second Admin",
                "--birthdate",
                "1990-01-01",
                "--email",
                "second-admin@example.com",
                "--password",
                "Password123",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            administrator = db.session.execute(
                db.select(User).where(
                    User.email == "second-admin@example.com"
                )
            ).scalar_one()
            self.assertEqual(administrator.role, ADMIN_USER_ROLE)
            self.assertIsNone(administrator.requested_role)


if __name__ == "__main__":
    unittest.main()
