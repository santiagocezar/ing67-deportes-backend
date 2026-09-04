import importlib
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask_jwt_extended import create_access_token
from pydantic import ValidationError
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.app import create_app
from app.extensions import db
from app.models import Sport, Team
from app.schemas.sports import SportCreateRequest, SportUpdateRequest
from app.schemas.teams import (
    TeamCreateRequest,
    TeamListQuery,
    TeamListResponse,
)
from app.services.sports import SportInUseError, SportNotFoundError
from app.services.teams import (
    TEAM_NAME_CONSTRAINT,
    DuplicateTeamNameError,
    TeamDisabledError,
    TeamNotFoundError,
    TeamPage,
    create_team,
    list_teams,
    normalize_team_name,
    set_team_enabled,
)


TEST_CONFIG = {
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": "postgresql://test:test@localhost/test",
    "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
    "API_DOCS_ENABLED": True,
}


def _sport(sport_id=1, name="Fútbol", total=22, in_game=11):
    return SimpleNamespace(
        id=sport_id,
        name=name,
        max_players=total,
        max_players_in_game=in_game,
    )


def _team(
    team_id=1,
    name="Águilas FC",
    *,
    sport=None,
    gender="male",
    enabled=True,
    disabled_at=None,
):
    return SimpleNamespace(
        id=team_id,
        name=name,
        normalized_name="aguilas fc",
        sport_id=(sport or _sport()).id,
        sport=sport or _sport(),
        gender_category=gender,
        is_enabled=enabled,
        created_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        disabled_at=disabled_at,
        future_completeness="must-not-leak",
    )


class TeamApiTests(unittest.TestCase):
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

    def tearDown(self):
        self.blocklist_patch.stop()

    @staticmethod
    def _bearer(token):
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _create_body(gender="male"):
        return {
            "name": "Águilas FC",
            "sport_id": 1,
            "gender_category": gender,
        }

    def test_administrator_can_use_every_team_operation(self):
        team = _team()
        headers = self._bearer(self.admin_token)

        for gender in ("male", "female"):
            with (
                self.subTest(gender=gender),
                patch("app.routes.teams.create_team", return_value=team),
            ):
                response = self.client.post(
                    "/teams",
                    headers=headers,
                    json=self._create_body(gender),
                )
                self.assertEqual(response.status_code, 201)

        page = TeamPage([team], 1, 25, 1, 1)
        with patch("app.routes.teams.list_teams", return_value=page) as service:
            response = self.client.get("/teams", headers=headers)
        self.assertEqual(response.status_code, 200)
        TeamListResponse.model_validate(response.get_json())
        query = service.call_args.args[0]
        self.assertEqual((query.status, query.sort, query.page), (
            "enabled",
            "name_asc",
            1,
        ))
        public_team = response.get_json()["teams"][0]
        self.assertEqual(
            set(public_team),
            {
                "id",
                "name",
                "sport",
                "gender_category",
                "is_enabled",
                "created_at",
                "disabled_at",
            },
        )

        with patch("app.routes.teams.get_team", return_value=team):
            response = self.client.get("/teams/1", headers=headers)
        self.assertEqual(response.status_code, 200)

        with patch("app.routes.teams.update_team_name", return_value=team):
            response = self.client.put(
                "/teams/1",
                headers=headers,
                json={"name": "Águilas renovadas"},
            )
        self.assertEqual(response.status_code, 200)

        for endpoint in ("disable", "enable"):
            with (
                self.subTest(endpoint=endpoint),
                patch("app.routes.teams.set_team_enabled", return_value=team),
            ):
                response = self.client.patch(
                    f"/teams/1/{endpoint}",
                    headers=headers,
                )
                self.assertEqual(response.status_code, 200)

    def test_every_operation_requires_an_administrator_access_token(self):
        requests = (
            ("get", "/teams", None),
            ("post", "/teams", self._create_body()),
            ("get", "/teams/1", None),
            ("put", "/teams/1", {"name": "Nuevo nombre"}),
            ("patch", "/teams/1/disable", None),
            ("patch", "/teams/1/enable", None),
        )
        for method, url, body in requests:
            with self.subTest(method=method, url=url, role="anonymous"):
                response = self.client.open(url, method=method, json=body)
                self.assertEqual(response.status_code, 401)
            with self.subTest(method=method, url=url, role="referee"):
                response = self.client.open(
                    url,
                    method=method,
                    headers=self._bearer(self.referee_token),
                    json=body,
                )
                self.assertEqual(response.status_code, 403)

    def test_creation_maps_missing_sport_and_duplicate_identity(self):
        headers = self._bearer(self.admin_token)
        cases = (
            (SportNotFoundError("missing"), 404, "sport_not_found"),
            (
                DuplicateTeamNameError("duplicate"),
                409,
                "team_name_conflict",
            ),
        )
        for exception, status, code in cases:
            with (
                self.subTest(code=code),
                patch(
                    "app.routes.teams.create_team",
                    side_effect=exception,
                ),
            ):
                response = self.client.post(
                    "/teams",
                    headers=headers,
                    json=self._create_body(),
                )
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.get_json()["error"]["code"], code)

    def test_team_payloads_reject_invalid_or_immutable_fields(self):
        headers = self._bearer(self.admin_token)
        invalid_creates = (
            self._create_body("mixed"),
            {**self._create_body(), "unknown": "value"},
            {**self._create_body(), "sport_id": True},
        )
        for body in invalid_creates:
            with self.subTest(body=body):
                response = self.client.post(
                    "/teams",
                    headers=headers,
                    json=body,
                )
                self.assertEqual(response.status_code, 422)

        response = self.client.put(
            "/teams/1",
            headers=headers,
            json={"name": "Nuevo", "sport_id": 2},
        )
        self.assertEqual(response.status_code, 422)

    def test_disabled_team_cannot_be_renamed(self):
        with patch(
            "app.routes.teams.update_team_name",
            side_effect=TeamDisabledError("enable it first"),
        ):
            response = self.client.put(
                "/teams/1",
                headers=self._bearer(self.admin_token),
                json={"name": "Nuevo"},
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"]["code"], "team_disabled")

    def test_invalid_list_query_has_structured_query_errors(self):
        headers = self._bearer(self.admin_token)
        for query in (
            "page=0",
            "status=archived",
            "sort=name_desc",
            "gender_category=other",
            "sport_id=0",
        ):
            with self.subTest(query=query):
                response = self.client.get(f"/teams?{query}", headers=headers)
                self.assertEqual(response.status_code, 422)
                detail = response.get_json()["error"]["details"][0]
                self.assertTrue(detail["field"].startswith("query."))

    def test_nonexistent_sport_filter_returns_404(self):
        with patch(
            "app.routes.teams.list_teams",
            side_effect=SportNotFoundError("missing"),
        ):
            response = self.client.get(
                "/teams?sport_id=999",
                headers=self._bearer(self.admin_token),
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"]["code"], "sport_not_found")

    def test_team_delete_does_not_exist_and_sport_delete_is_protected(self):
        headers = self._bearer(self.admin_token)
        self.assertEqual(
            self.client.delete("/teams/1", headers=headers).status_code,
            405,
        )
        with patch(
            "app.routes.sports.delete_sport",
            side_effect=SportInUseError("referenced by disabled Team"),
        ):
            response = self.client.delete("/sports/1", headers=headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"]["code"], "sport_in_use")

    def test_openapi_documents_team_security_errors_and_sport_capacities(self):
        contract = self.client.get("/openapi.json").get_json()
        team_paths = {
            path: item
            for path, item in contract["paths"].items()
            if path.startswith("/teams")
        }
        self.assertEqual(
            set(team_paths),
            {
                "/teams",
                "/teams/{team_id}",
                "/teams/{team_id}/disable",
                "/teams/{team_id}/enable",
            },
        )
        for path_item in team_paths.values():
            for operation in path_item.values():
                self.assertEqual(
                    operation["security"],
                    [{"AccessTokenAuth": []}],
                )
                self.assertIn("401", operation["responses"])
                self.assertIn("403", operation["responses"])
                self.assertIn("422", operation["responses"])

        schemas = contract["components"]["schemas"]
        self.assertEqual(
            set(schemas["SportCreateRequest"]["required"]),
            {"name", "max_players", "max_players_in_game"},
        )
        self.assertIn(
            "max_players_in_game",
            schemas["SportResponse"]["properties"],
        )
        self.assertEqual(
            schemas["TeamCreateRequest"]["properties"][
                "gender_category"
            ]["enum"],
            ["male", "female"],
        )


class TeamDomainTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TEST_CONFIG)

    def test_name_normalization_preserves_display_case(self):
        display_name, normalized_name = normalize_team_name(
            "  Águilas   del   SUR  "
        )
        self.assertEqual(display_name, "Águilas del SUR")
        self.assertEqual(normalized_name, "aguilas del sur")

    def test_unique_identity_uses_name_sport_and_gender(self):
        constraint = next(
            item
            for item in Team.__table__.constraints
            if isinstance(item, UniqueConstraint)
            and item.name == TEAM_NAME_CONSTRAINT
        )
        self.assertEqual(
            [column.name for column in constraint.columns],
            ["normalized_name", "sport_id", "gender_category"],
        )

    def test_duplicate_integrity_error_maps_only_the_known_constraint(self):
        class DatabaseOrigin(Exception):
            def __init__(self, constraint):
                self.diag = SimpleNamespace(constraint_name=constraint)

        duplicate_error = IntegrityError(
            "INSERT",
            {},
            DatabaseOrigin(TEAM_NAME_CONSTRAINT),
        )
        unrelated_error = IntegrityError(
            "INSERT",
            {},
            DatabaseOrigin("some_other_constraint"),
        )
        data = TeamCreateRequest(
            name="Águilas",
            sport_id=1,
            gender_category="male",
        )
        persisted_sport = Sport(
            id=1,
            name="Fútbol",
            normalized_name="futbol",
            max_players=22,
            max_players_in_game=11,
        )
        with self.app.app_context():
            with (
                patch.object(db.session, "get", return_value=persisted_sport),
                patch.object(db.session, "add"),
                patch.object(db.session, "commit", side_effect=duplicate_error),
                patch.object(db.session, "rollback"),
                patch(
                    "app.services.teams._duplicate_team_id",
                    return_value=None,
                ),
            ):
                with self.assertRaises(DuplicateTeamNameError):
                    create_team(data)

            with (
                patch.object(db.session, "get", return_value=persisted_sport),
                patch.object(db.session, "add"),
                patch.object(db.session, "commit", side_effect=unrelated_error),
                patch.object(db.session, "rollback"),
                patch(
                    "app.services.teams._duplicate_team_id",
                    return_value=None,
                ),
            ):
                with self.assertRaises(IntegrityError):
                    create_team(data)

    def test_disable_enable_are_timestamp_consistent_and_idempotent(self):
        enabled_team = _team()
        with self.app.app_context(), patch(
            "app.services.teams.get_team",
            return_value=enabled_team,
        ), patch.object(db.session, "execute") as execute, patch.object(
            db.session,
            "commit",
        ) as commit:
            result = set_team_enabled(1, enabled=False)
            self.assertFalse(result.is_enabled)
            self.assertIsNotNone(result.disabled_at)
            commit.assert_called_once()
            sql = str(
                execute.call_args.args[0].compile(
                    dialect=postgresql.dialect()
                )
            )
            self.assertIn("DELETE FROM team_players", sql)

        disabled_at = enabled_team.disabled_at
        with self.app.app_context(), patch(
            "app.services.teams.get_team",
            return_value=enabled_team,
        ), patch.object(db.session, "commit") as commit:
            result = set_team_enabled(1, enabled=False)
            self.assertEqual(result.disabled_at, disabled_at)
            commit.assert_not_called()

        with self.app.app_context(), patch(
            "app.services.teams.get_team",
            return_value=enabled_team,
        ), patch.object(db.session, "commit") as commit:
            result = set_team_enabled(1, enabled=True)
            self.assertTrue(result.is_enabled)
            self.assertIsNone(result.disabled_at)
            commit.assert_called_once()

    def _capture_list_statements(self, query, *, total=0, teams=None):
        count_result = MagicMock()
        count_result.scalar_one.return_value = total
        team_result = MagicMock()
        team_result.scalars.return_value = teams or []
        with self.app.app_context(), patch.object(
            db.session,
            "get",
            return_value=_sport(),
        ), patch.object(
            db.session,
            "execute",
            side_effect=[count_result, team_result],
        ) as execute:
            page = list_teams(query)
        return page, [call.args[0] for call in execute.call_args_list]

    def test_listing_combines_filters_and_uses_database_pagination(self):
        query = TeamListQuery(
            search="ÁGUI",
            sport_id=1,
            gender_category="female",
            status="disabled",
            sort="created_at_desc",
            page=3,
        )
        page, statements = self._capture_list_statements(query, total=26)
        dialect = postgresql.dialect()
        count_sql = str(statements[0].compile(dialect=dialect))
        list_sql = str(statements[1].compile(dialect=dialect))

        self.assertIn("normalized_name LIKE", count_sql)
        self.assertIn("sport_id", count_sql)
        self.assertIn("gender_category", count_sql)
        self.assertIn("is_enabled IS false", count_sql)
        self.assertIn("ORDER BY teams.created_at DESC, teams.id DESC", list_sql)
        self.assertEqual((page.page, page.per_page), (3, 25))
        self.assertEqual((page.total_items, page.total_pages), (26, 2))
        self.assertEqual(page.teams, [])
        self.assertTrue(statements[1]._with_options)

    def test_listing_status_and_sort_contracts(self):
        cases = (
            ("enabled", "name_asc", "is_enabled IS true", "normalized_name ASC"),
            ("disabled", "name_asc", "is_enabled IS false", "normalized_name ASC"),
            ("all", "created_at_desc", None, "created_at DESC"),
        )
        for status, sort, status_sql, sort_sql in cases:
            with self.subTest(status=status, sort=sort):
                _, statements = self._capture_list_statements(
                    TeamListQuery(status=status, sort=sort)
                )
                sql = str(statements[1].compile(dialect=postgresql.dialect()))
                if status_sql is None:
                    self.assertNotIn("is_enabled IS", sql)
                else:
                    self.assertIn(status_sql, sql)
                self.assertIn(sort_sql, sql)

    def test_schema_requires_both_sport_capacities_and_makes_them_immutable(self):
        with self.assertRaises(ValidationError):
            SportCreateRequest(name="Fútbol", max_players=22)
        with self.assertRaises(ValidationError):
            SportUpdateRequest(name="Fútbol", max_players_in_game=11)


class TeamMigrationTests(unittest.TestCase):
    def test_migration_and_initializer_cover_known_and_unknown_sports(self):
        migration = importlib.import_module(
            "migrations.versions.a8c4e12f6b90_add_team_management"
        )
        with patch.object(migration, "op") as operation:
            migration.upgrade()
        executed_sql = " ".join(
            call.args[0]
            for call in operation.execute.call_args_list
        )
        self.assertIn("max_players_in_game = max_players", executed_sql)
        self.assertIn("max_players = 22", executed_sql)
        self.assertIn("max_players = 15", executed_sql)
        operation.create_table.assert_called_once()

        with patch.object(migration, "op") as operation:
            migration.downgrade()
        downgrade_sql = " ".join(
            call.args[0]
            for call in operation.execute.call_args_list
        )
        self.assertIn("LEAST(max_players, 20)", downgrade_sql)
        self.assertIn("max_players = 11", downgrade_sql)
        self.assertIn("max_players = 5", downgrade_sql)
        operation.drop_table.assert_called_once_with("teams")

        initializer = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "initialize_sports.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("('Fútbol', 'futbol', 22, 11)", initializer)
        self.assertIn("('Básquet', 'basquet', 15, 5)", initializer)
        self.assertIn("ON CONFLICT (normalized_name) DO NOTHING", initializer)


if __name__ == "__main__":
    unittest.main()
