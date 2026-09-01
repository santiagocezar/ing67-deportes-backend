import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask_jwt_extended import create_access_token, create_refresh_token

from app.app import create_app
from app.schemas.auth import (
    SignupRequest,
    SignupResponse,
    TokenResponse,
    UserEnvelope,
)
from app.schemas.sports import (
    SportEnvelope,
    SportListResponse,
)
from app.services.auth import RefreshTokenReuseError, SessionRevokedError
from app.services.sports import (
    DuplicateSportNameError,
    SportNotFoundError,
)
from sqlalchemy.exc import SQLAlchemyError


TEST_CONFIG = {
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": "postgresql://test:test@localhost/test",
    "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
    "API_DOCS_ENABLED": True,
}


def _user() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        name="Ana Example",
        birthdate=date(1995, 4, 20),
        role="administrator",
        email="ana@example.com",
        creation_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _sport(
    sport_id: int = 1,
    name: str = "Fútbol",
    max_players: int = 11,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=sport_id,
        name=name,
        max_players=max_players,
    )


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.blocklist_patch = patch(
            "app.services.auth.is_token_revoked",
            return_value=False,
        )
        self.blocklist_patch.start()
        self.app = create_app(TEST_CONFIG)
        self.client = self.app.test_client()

        with self.app.app_context():
            self.admin_token = create_access_token(
                identity="1",
                additional_claims={
                    "sid": "admin-session",
                    "role": "administrator",
                },
            )
            self.referee_token = create_access_token(
                identity="2",
                additional_claims={
                    "sid": "referee-session",
                    "role": "referee",
                },
            )
            self.refresh_token = create_refresh_token(
                identity="1",
                additional_claims={"sid": "admin-session"},
            )

    def tearDown(self):
        self.blocklist_patch.stop()

    @staticmethod
    def _bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_all_auth_success_responses_match_public_schemas(self):
        user = _user()
        signup_body = {
            "name": user.name,
            "birthdate": user.birthdate.isoformat(),
            "email": user.email,
            "password": "password123",
        }
        login_body = {
            "email": user.email,
            "password": "password123",
        }

        with patch("app.routes.users.create_user", return_value=user):
            response = self.client.post("/auth/signup", json=signup_body)
        self.assertEqual(response.status_code, 201)
        SignupResponse.model_validate(response.get_json())

        with (
            patch("app.routes.users.authenticate_user", return_value=user),
            patch(
                "app.routes.users.start_session",
                return_value=("access-token", "refresh-token"),
            ),
        ):
            response = self.client.post("/auth/login", json=login_body)
        self.assertEqual(response.status_code, 200)
        TokenResponse.model_validate(response.get_json())

        with patch(
            "app.routes.users.rotate_refresh_token",
            return_value=("new-access-token", "new-refresh-token"),
        ):
            response = self.client.post(
                "/auth/refresh",
                headers=self._bearer(self.refresh_token),
            )
        self.assertEqual(response.status_code, 200)
        TokenResponse.model_validate(response.get_json())

        with patch("app.routes.users.revoke_session") as revoke_session:
            response = self.client.delete(
                "/auth/logout",
                headers=self._bearer(self.admin_token),
            )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.data, b"")
        revoke_session.assert_called_once_with("admin-session")

        with patch("app.routes.users.get_user", return_value=user):
            response = self.client.get(
                "/auth/me",
                headers=self._bearer(self.admin_token),
            )
        self.assertEqual(response.status_code, 200)
        UserEnvelope.model_validate(response.get_json())

    def test_all_sport_success_responses_match_public_schemas(self):
        football = _sport()
        basketball = _sport(2, "Básquet", 5)
        headers = self._bearer(self.admin_token)

        with patch(
            "app.routes.sports.list_sports",
            return_value=[football, basketball],
        ):
            response = self.client.get("/sports", headers=headers)
        self.assertEqual(response.status_code, 200)
        SportListResponse.model_validate(response.get_json())

        with patch("app.routes.sports.create_sport", return_value=football):
            response = self.client.post(
                "/sports",
                headers=headers,
                json={"name": "FÚTBOL", "max_players": 11},
            )
        self.assertEqual(response.status_code, 201)
        SportEnvelope.model_validate(response.get_json())

        with patch("app.routes.sports.get_sport", return_value=football):
            response = self.client.get("/sports/1", headers=headers)
        self.assertEqual(response.status_code, 200)
        SportEnvelope.model_validate(response.get_json())

        renamed = _sport(name="Fútbol sala")
        with patch(
            "app.routes.sports.update_sport_name",
            return_value=renamed,
        ):
            response = self.client.put(
                "/sports/1",
                headers=headers,
                json={"name": "Fútbol sala"},
            )
        self.assertEqual(response.status_code, 200)
        SportEnvelope.model_validate(response.get_json())

        with patch("app.routes.sports.delete_sport") as delete_sport:
            response = self.client.delete("/sports/1", headers=headers)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.data, b"")
        delete_sport.assert_called_once_with(1)

    def test_missing_malformed_and_non_object_json_return_400(self):
        requests = [
            self.client.post("/auth/signup"),
            self.client.post(
                "/auth/signup",
                data="{",
                content_type="application/json",
            ),
            self.client.post("/auth/signup", json=[]),
            self.client.post(
                "/auth/signup",
                data="{}",
                content_type="text/plain",
            ),
        ]

        for response in requests:
            with self.subTest(response=response):
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.get_json()["error"]["code"],
                    "invalid_request",
                )

    def test_schema_failures_return_structured_422_without_input_values(self):
        payload = {
            "name": "Ana Example",
            "birthdate": "not-a-date",
            "email": "invalid-email",
            "password": ["never-return-this-value"],
            "unknown": "also-sensitive",
        }

        response = self.client.post("/auth/signup", json=payload)

        self.assertEqual(response.status_code, 422)
        error = response.get_json()["error"]
        self.assertEqual(error["code"], "validation_error")
        self.assertTrue(error["details"])
        for detail in error["details"]:
            self.assertEqual(
                set(detail),
                {"field", "message", "type"},
            )
        serialized = response.get_data(as_text=True)
        self.assertNotIn("never-return-this-value", serialized)
        self.assertNotIn("also-sensitive", serialized)

    def test_unknown_sport_field_and_boolean_player_limit_return_422(self):
        headers = self._bearer(self.admin_token)
        cases = [
            {"name": "Fútbol", "max_players": 11, "unknown": "value"},
            {"name": "Fútbol", "max_players": True},
            {"name": "Fútbol"},
        ]

        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/sports",
                    headers=headers,
                    json=payload,
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.get_json()["error"]["code"],
                    "validation_error",
                )

    def test_immutable_max_players_keeps_existing_error_code(self):
        response = self.client.put(
            "/sports/1",
            headers=self._bearer(self.admin_token),
            json={"name": "Fútbol", "max_players": 10},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "immutable_field",
        )

    def test_protected_sports_validate_authentication_before_body(self):
        response = self.client.post("/sports")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "authentication_required",
        )

        response = self.client.post(
            "/sports",
            headers=self._bearer(self.referee_token),
            json={"name": "Fútbol", "max_players": 11},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "administrator_required",
        )

    def test_not_found_and_conflict_errors_remain_compatible(self):
        headers = self._bearer(self.admin_token)
        with patch(
            "app.routes.sports.get_sport",
            side_effect=SportNotFoundError("The sport does not exist."),
        ):
            response = self.client.get("/sports/999", headers=headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "sport_not_found",
        )

        with patch(
            "app.routes.sports.create_sport",
            side_effect=DuplicateSportNameError(
                "A sport with that name already exists."
            ),
        ):
            response = self.client.post(
                "/sports",
                headers=headers,
                json={"name": "FUTBOL", "max_players": 11},
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "sport_name_conflict",
        )

    def test_refresh_reuse_and_revoked_errors_remain_compatible(self):
        headers = self._bearer(self.refresh_token)
        cases = [
            (
                RefreshTokenReuseError(
                    "Refresh token reuse was detected. Sign in again."
                ),
                "refresh_token_reused",
            ),
            (
                SessionRevokedError("The session is no longer active."),
                "session_revoked",
            ),
        ]

        for exception, expected_code in cases:
            with (
                self.subTest(expected_code=expected_code),
                patch(
                    "app.routes.users.rotate_refresh_token",
                    side_effect=exception,
                ),
            ):
                response = self.client.post(
                    "/auth/refresh",
                    headers=headers,
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response.get_json()["error"]["code"],
                    expected_code,
                )

    def test_access_and_refresh_token_types_are_not_interchangeable(self):
        response = self.client.post(
            "/auth/refresh",
            headers=self._bearer(self.admin_token),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "invalid_token",
        )

        response = self.client.get(
            "/auth/me",
            headers=self._bearer(self.refresh_token),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "invalid_token",
        )

    def test_expired_access_token_uses_the_existing_error_envelope(self):
        with self.app.app_context():
            expired_token = create_access_token(
                identity="1",
                additional_claims={
                    "sid": "expired-session",
                    "role": "administrator",
                },
                expires_delta=timedelta(seconds=-1),
            )

        response = self.client.get(
            "/auth/me",
            headers=self._bearer(expired_token),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "token_expired",
        )

    def test_database_errors_do_not_expose_internal_details(self):
        with patch(
            "app.routes.sports.create_sport",
            side_effect=SQLAlchemyError("private database detail"),
        ):
            response = self.client.post(
                "/sports",
                headers=self._bearer(self.admin_token),
                json={"name": "Vóley", "max_players": 6},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "service_unavailable",
        )
        self.assertNotIn(
            "private database detail",
            response.get_data(as_text=True),
        )

    def test_blocklist_database_failure_uses_safe_503_response(self):
        with patch(
            "app.services.auth.is_token_revoked",
            side_effect=SQLAlchemyError("private blocklist detail"),
        ):
            failing_app = create_app(TEST_CONFIG)
        with failing_app.app_context():
            token = create_access_token(
                identity="1",
                additional_claims={
                    "sid": "session-id",
                    "role": "administrator",
                },
            )

        response = failing_app.test_client().get(
            "/auth/me",
            headers=self._bearer(token),
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "service_unavailable",
        )
        self.assertNotIn(
            "private blocklist detail",
            response.get_data(as_text=True),
        )

    def test_openapi_contract_documents_paths_schemas_and_security(self):
        response = self.client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        contract = response.get_json()
        self.assertEqual(contract["openapi"], "3.1.0")
        self.assertEqual(contract["info"]["version"], "1.0.0")
        self.assertEqual(
            set(contract["paths"]),
            {
                "/auth/signup",
                "/auth/login",
                "/auth/refresh",
                "/auth/logout",
                "/auth/me",
                "/sports",
                "/sports/{sport_id}",
            },
        )

        operations = [
            operation
            for path_item in contract["paths"].values()
            for operation in path_item.values()
        ]
        operation_ids = [operation["operationId"] for operation in operations]
        self.assertEqual(len(operation_ids), 10)
        self.assertEqual(len(set(operation_ids)), 10)
        self.assertNotIn(
            "security",
            contract["paths"]["/auth/signup"]["post"],
        )
        self.assertEqual(
            contract["paths"]["/auth/refresh"]["post"]["security"],
            [{"RefreshTokenAuth": []}],
        )
        self.assertEqual(
            contract["paths"]["/sports"]["get"]["security"],
            [{"AccessTokenAuth": []}],
        )
        self.assertIn(
            "AccessTokenAuth",
            contract["components"]["securitySchemes"],
        )
        self.assertIn("SignupRequest", contract["components"]["schemas"])
        self.assertIn("TokenResponse", contract["components"]["schemas"])
        self.assertIn("SportResponse", contract["components"]["schemas"])
        self.assertIn("ErrorResponse", contract["components"]["schemas"])

        for operation in operations:
            self.assertTrue(
                any(status.startswith("2") for status in operation["responses"])
            )

    def test_swagger_and_openapi_can_be_disabled(self):
        disabled_app = create_app(
            {**TEST_CONFIG, "API_DOCS_ENABLED": False}
        )
        client = disabled_app.test_client()

        self.assertEqual(client.get("/openapi.json").status_code, 404)
        self.assertEqual(client.get("/docs").status_code, 404)

    def test_swagger_is_available_when_enabled(self):
        response = self.client.get("/docs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        self.assertIn("Swagger UI", response.get_data(as_text=True))

    def test_export_is_deterministic_and_matches_committed_contract(self):
        runner = self.app.test_cli_runner()

        first_result = runner.invoke(args=["export-openapi"])
        self.assertEqual(first_result.exit_code, 0, first_result.output)
        output_path = Path(first_result.output.strip())
        first_export = output_path.read_text(encoding="utf-8")

        second_result = runner.invoke(args=["export-openapi"])
        self.assertEqual(second_result.exit_code, 0, second_result.output)
        second_export = output_path.read_text(encoding="utf-8")

        self.assertEqual(first_export, second_export)
        expected = (
            json.dumps(
                self.app.api_doc,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + chr(10)
        )
        self.assertEqual(first_export, expected)

    def test_migration_and_application_cli_commands_remain_registered(self):
        commands = self.app.cli.commands
        for command in (
            "db",
            "init-db",
            "upgrade-db",
            "create-admin",
            "export-openapi",
        ):
            with self.subTest(command=command):
                self.assertIn(command, commands)

    def test_create_admin_uses_the_same_signup_schema(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(
            args=[
                "create-admin",
                "--name",
                "Ana Example",
                "--birthdate",
                "invalid-date",
                "--email",
                "ana@example.com",
                "--password",
                "password123",
            ]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("birthdate", result.output)

    def test_create_admin_passes_validated_data_to_the_service(self):
        with patch("app.services.users.create_user") as create_user:
            cli_app = create_app(TEST_CONFIG)
        runner = cli_app.test_cli_runner()

        result = runner.invoke(
            args=[
                "create-admin",
                "--name",
                "Ana Example",
                "--birthdate",
                "1995-04-20",
                "--email",
                "ANA@EXAMPLE.COM",
                "--password",
                "password123",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        request_model = create_user.call_args.args[0]
        self.assertIsInstance(request_model, SignupRequest)
        self.assertEqual(request_model.email, "ana@example.com")
        self.assertEqual(
            create_user.call_args.kwargs["role"],
            "administrator",
        )


if __name__ == "__main__":
    unittest.main()
