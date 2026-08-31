from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.app import create_app
from app.extensions import db
from app.models import (
    ADMIN_USER_ROLE,
    FEDERATION_DELEGATE_USER_ROLE,
    REFEREE_USER_ROLE,
    Team,
)
from app.services.sports import create_sport
from app.services.teams import create_team, list_teams, set_team_enabled
from app.services.users import create_user


class TeamManagementTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
                "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            }
        )
        with self.app.app_context():
            event.listen(
                db.engine,
                "connect",
                self._configure_sqlite,
                once=True,
            )
            db.create_all()
            self._create_user(
                "Administrator",
                "admin@example.com",
                ADMIN_USER_ROLE,
            )
            self._create_user(
                "Federation Delegate",
                "delegate@example.com",
                FEDERATION_DELEGATE_USER_ROLE,
            )
            self._create_user(
                "Referee",
                "referee@example.com",
                REFEREE_USER_ROLE,
            )
            self._create_user(
                "Disabled Delegate",
                "disabled@example.com",
                FEDERATION_DELEGATE_USER_ROLE,
                is_active=False,
            )
            create_user(
                {
                    "name": "Pending User",
                    "birthdate": "1990-01-01",
                    "email": "pending@example.com",
                    "password": "Password123",
                    "requested_role": "federation_delegate",
                }
            )
            football = create_sport(self._sport_payload("Football", 11))
            basketball = create_sport(self._sport_payload("Basketball", 5))
            self.football_id = football.id
            self.basketball_id = basketball.id

        self.client = self.app.test_client()
        self.admin_headers = self._headers_for("admin@example.com")
        self.delegate_headers = self._headers_for("delegate@example.com")
        self.referee_headers = self._headers_for("referee@example.com")
        self.pending_headers = self._headers_for("pending@example.com")
        self.disabled_headers = self._headers_for("disabled@example.com")

    @staticmethod
    def _configure_sqlite(connection, _connection_record):
        connection.create_function("char_length", 1, len)
        connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _sport_payload(name: str, max_players: int) -> dict:
        return {
            "name": name,
            "max_players": max_players,
            "match_duration": 60,
            "resolution_methods": [
                {"code": "overtime", "name": "Tiempo extra"}
            ],
        }

    @staticmethod
    def _team_payload(
        name: str = "Boca Juniors",
        sport_id: int = 1,
        gender_category: str = "male",
    ) -> dict:
        return {
            "name": name,
            "sport_id": sport_id,
            "gender_category": gender_category,
        }

    @staticmethod
    def _create_user(
        name: str,
        email: str,
        role: str,
        *,
        is_active: bool = True,
    ) -> None:
        user = create_user(
            {
                "name": name,
                "birthdate": "1990-01-01",
                "email": email,
                "password": "Password123",
            },
            role=role,
        )
        if not is_active:
            user.is_active = False
            db.session.commit()

    def _headers_for(self, email: str) -> dict[str, str]:
        response = self.client.post(
            "/auth/login",
            json={"email": email, "password": "Password123"},
        )
        self.assertEqual(response.status_code, 200)
        return {
            "Authorization": (
                f"Bearer {response.get_json()['access_token']}"
            )
        }

    def _post_team(
        self,
        name: str,
        *,
        sport_id: int | None = None,
        gender_category: str = "male",
        headers: dict[str, str] | None = None,
    ):
        return self.client.post(
            "/teams",
            json=self._team_payload(
                name,
                sport_id or self.football_id,
                gender_category,
            ),
            headers=headers or self.admin_headers,
        )

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_administrator_and_delegate_can_create_team(self):
        for index, headers in enumerate(
            (self.admin_headers, self.delegate_headers)
        ):
            with self.subTest(index=index):
                response = self._post_team(
                    f"Authorized Team {index}",
                    headers=headers,
                )
                self.assertEqual(response.status_code, 201)
                team = response.get_json()["team"]
                self.assertTrue(team["is_enabled"])
                self.assertEqual(team["current_players_quantity"], 0)
                self.assertFalse(team["is_eligible_for_competition"])
                self.assertEqual(team["players"], [])
                self.assertEqual(team["sport"]["max_players"], 11)

    def test_team_access_rejects_unauthorized_accounts(self):
        cases = (
            (None, 401, "authentication_required"),
            (self.referee_headers, 403, "role_forbidden"),
            (self.pending_headers, 403, "approval_required"),
            (self.disabled_headers, 403, "account_disabled"),
        )
        for headers, status, error_code in cases:
            with self.subTest(error_code=error_code):
                response = self.client.get("/teams", headers=headers)
                self.assertEqual(response.status_code, status)
                self.assertEqual(
                    response.get_json()["error"]["code"],
                    error_code,
                )

    def test_create_validates_body_fields_name_gender_and_sport(self):
        malformed = self.client.post(
            "/teams",
            json=[],
            headers=self.admin_headers,
        )
        self.assertEqual(malformed.status_code, 400)

        invalid_payloads = (
            {"sport_id": self.football_id, "gender_category": "male"},
            {
                **self._team_payload(sport_id=self.football_id),
                "players": [1],
            },
            self._team_payload("", self.football_id),
            self._team_payload("x" * 101, self.football_id),
            self._team_payload(
                "Invalid Gender",
                self.football_id,
                "mixed",
            ),
            self._team_payload("Missing Sport", 9999),
            self._team_payload("Boolean Sport", True),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/teams",
                    json=payload,
                    headers=self.admin_headers,
                )
                expected_status = (
                    404 if payload.get("sport_id") == 9999 else 422
                )
                self.assertEqual(response.status_code, expected_status)

    def test_name_normalization_and_scoped_uniqueness(self):
        created = self._post_team(
            "  Bóca   Juniors  ",
            sport_id=self.football_id,
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["team"]["name"], "Bóca Juniors")

        for duplicate_name in ("Boca Juniors", "BOCA  JUNIORS"):
            with self.subTest(name=duplicate_name):
                conflict = self._post_team(
                    duplicate_name,
                    sport_id=self.football_id,
                )
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(
                    conflict.get_json()["error"]["code"],
                    "team_name_conflict",
                )

        other_sport = self._post_team(
            "Boca Juniors",
            sport_id=self.basketball_id,
        )
        other_gender = self._post_team(
            "Boca Juniors",
            sport_id=self.football_id,
            gender_category="female",
        )
        self.assertEqual(other_sport.status_code, 201)
        self.assertEqual(other_gender.status_code, 201)

        team_id = created.get_json()["team"]["id"]
        self.client.patch(
            f"/teams/{team_id}/disable",
            headers=self.admin_headers,
        )
        reserved_while_disabled = self._post_team(
            "Boca Juniors",
            sport_id=self.football_id,
        )
        self.assertEqual(reserved_while_disabled.status_code, 409)

    def test_database_constraints_protect_team_invariants(self):
        with self.app.app_context():
            first = create_team(
                self._team_payload("Constraint Team", self.football_id)
            )
            self.assertIsNotNone(first.created_at)

            duplicate = Team(
                name="CONSTRAINT TEAM",
                normalized_name="constraint team",
                sport_id=self.football_id,
                gender_category="male",
            )
            db.session.add(duplicate)
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

            invalid_teams = (
                Team(
                    name=" ",
                    normalized_name="blank",
                    sport_id=self.football_id,
                    gender_category="male",
                ),
                Team(
                    name="Bad Gender",
                    normalized_name="bad gender",
                    sport_id=self.football_id,
                    gender_category="mixed",
                ),
                Team(
                    name="Bad State",
                    normalized_name="bad state",
                    sport_id=self.football_id,
                    gender_category="female",
                    is_enabled=False,
                    disabled_at=None,
                ),
            )
            for team in invalid_teams:
                with self.subTest(name=team.name):
                    db.session.add(team)
                    with self.assertRaises(IntegrityError):
                        db.session.commit()
                    db.session.rollback()

    def test_list_detail_disable_and_enable_lifecycle(self):
        created = self._post_team("Lifecycle Team").get_json()["team"]
        team_id = created["id"]

        listed = self.client.get("/teams", headers=self.delegate_headers)
        detail = self.client.get(
            f"/teams/{team_id}",
            headers=self.delegate_headers,
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.get_json()["teams"]), 1)
        self.assertEqual(detail.get_json()["team"]["players"], [])

        disabled = self.client.patch(
            f"/teams/{team_id}/disable",
            headers=self.delegate_headers,
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.get_json()["team"]["is_enabled"])
        self.assertIsNotNone(disabled.get_json()["team"]["disabled_at"])

        default_list = self.client.get(
            "/teams",
            headers=self.delegate_headers,
        )
        disabled_list = self.client.get(
            "/teams?status=disabled",
            headers=self.delegate_headers,
        )
        self.assertEqual(default_list.get_json()["teams"], [])
        self.assertEqual(len(disabled_list.get_json()["teams"]), 1)

        repeated_disable = self.client.patch(
            f"/teams/{team_id}/disable",
            headers=self.admin_headers,
        )
        self.assertEqual(repeated_disable.status_code, 409)
        self.assertEqual(
            repeated_disable.get_json()["error"]["code"],
            "team_already_disabled",
        )

        enabled = self.client.patch(
            f"/teams/{team_id}/enable",
            headers=self.admin_headers,
        )
        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(enabled.get_json()["team"]["is_enabled"])
        self.assertIsNone(enabled.get_json()["team"]["disabled_at"])

        repeated_enable = self.client.patch(
            f"/teams/{team_id}/enable",
            headers=self.admin_headers,
        )
        self.assertEqual(repeated_enable.status_code, 409)
        self.assertEqual(
            repeated_enable.get_json()["error"]["code"],
            "team_already_enabled",
        )

    def test_rename_enforces_scope_immutability_and_enabled_state(self):
        first = self._post_team("First Team").get_json()["team"]
        self._post_team("Reserved Name")

        renamed = self.client.put(
            f"/teams/{first['id']}",
            json={"name": "  Renamed   Team "},
            headers=self.delegate_headers,
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.get_json()["team"]["name"], "Renamed Team")

        duplicate = self.client.put(
            f"/teams/{first['id']}",
            json={"name": "RÉSERVÉD NAME"},
            headers=self.admin_headers,
        )
        self.assertEqual(duplicate.status_code, 409)

        for field, value in (
            ("sport_id", self.basketball_id),
            ("gender_category", "female"),
            ("is_enabled", False),
            ("created_at", "2020-01-01T00:00:00Z"),
        ):
            with self.subTest(field=field):
                immutable = self.client.put(
                    f"/teams/{first['id']}",
                    json={"name": "Ignored", field: value},
                    headers=self.admin_headers,
                )
                self.assertEqual(immutable.status_code, 422)

        self.client.patch(
            f"/teams/{first['id']}/disable",
            headers=self.admin_headers,
        )
        disabled_edit = self.client.put(
            f"/teams/{first['id']}",
            json={"name": "Cannot Rename"},
            headers=self.admin_headers,
        )
        self.assertEqual(disabled_edit.status_code, 409)
        self.assertEqual(
            disabled_edit.get_json()["error"]["code"],
            "team_disabled",
        )

    def test_filters_search_and_and_combination_are_database_backed(self):
        first = self._post_team("Bóca Norte").get_json()["team"]
        self._post_team("Boca Sur", gender_category="female")
        self._post_team(
            "Boca Basket",
            sport_id=self.basketball_id,
        )
        river = self._post_team("River Plate").get_json()["team"]
        self.client.patch(
            f"/teams/{river['id']}/disable",
            headers=self.admin_headers,
        )

        response = self.client.get(
            (
                "/teams?search=boca"
                f"&sport_id={self.football_id}"
                "&gender_category=female&status=all"
            ),
            headers=self.delegate_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [team["name"] for team in response.get_json()["teams"]],
            ["Boca Sur"],
        )

        disabled = self.client.get(
            "/teams?status=disabled",
            headers=self.delegate_headers,
        )
        self.assertEqual(
            [team["id"] for team in disabled.get_json()["teams"]],
            [river["id"]],
        )
        self.assertNotEqual(first["id"], river["id"])

    def test_sort_pagination_empty_pages_and_invalid_queries(self):
        created_ids = []
        for index in range(27):
            response = self._post_team(f"Paged Team {index:02d}")
            self.assertEqual(response.status_code, 201)
            created_ids.append(response.get_json()["team"]["id"])

        second_page = self.client.get(
            "/teams?page=2",
            headers=self.admin_headers,
        )
        payload = second_page.get_json()
        self.assertEqual(len(payload["teams"]), 2)
        self.assertEqual(
            payload["pagination"],
            {
                "page": 2,
                "page_size": 25,
                "total_items": 27,
                "total_pages": 2,
            },
        )

        out_of_range = self.client.get(
            "/teams?page=3",
            headers=self.admin_headers,
        )
        self.assertEqual(out_of_range.status_code, 200)
        self.assertEqual(out_of_range.get_json()["teams"], [])

        name_desc = self.client.get(
            "/teams?sort=name&order=desc",
            headers=self.admin_headers,
        ).get_json()["teams"]
        self.assertEqual(name_desc[0]["name"], "Paged Team 26")

        created_asc = self.client.get(
            "/teams?sort=created_at&order=asc",
            headers=self.admin_headers,
        ).get_json()["teams"]
        created_desc = self.client.get(
            "/teams?sort=created_at&order=desc",
            headers=self.admin_headers,
        ).get_json()["teams"]
        self.assertEqual(created_asc[0]["id"], created_ids[0])
        self.assertEqual(created_desc[0]["id"], created_ids[-1])

        invalid_queries = (
            "page=0",
            "page=text",
            "sport_id=-1",
            "gender_category=mixed",
            "status=active",
            "sort=id",
            "order=sideways",
            "per_page=5",
            "is_eligible_for_competition=true",
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                response = self.client.get(
                    f"/teams?{query}",
                    headers=self.admin_headers,
                )
                self.assertEqual(response.status_code, 422)

    def test_team_queries_eager_load_sport_without_n_plus_one(self):
        self._post_team("Eager One")
        self._post_team("Eager Two")
        statements = []

        def record_statement(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        with self.app.app_context():
            event.listen(db.engine, "before_cursor_execute", record_statement)
            try:
                page = list_teams({"status": "all"})
                [team.to_summary_dict() for team in page.teams]
            finally:
                event.remove(
                    db.engine,
                    "before_cursor_execute",
                    record_statement,
                )
        self.assertEqual(len(statements), 2)

    def test_delegate_can_read_but_not_mutate_sports(self):
        sport_list = self.client.get(
            "/sports",
            headers=self.delegate_headers,
        )
        sport_detail = self.client.get(
            f"/sports/{self.football_id}",
            headers=self.delegate_headers,
        )
        sport_create = self.client.post(
            "/sports",
            json=self._sport_payload("Forbidden", 4),
            headers=self.delegate_headers,
        )
        referee_read = self.client.get(
            "/sports",
            headers=self.referee_headers,
        )
        self.assertEqual(sport_list.status_code, 200)
        self.assertEqual(sport_detail.status_code, 200)
        self.assertEqual(sport_create.status_code, 403)
        self.assertEqual(referee_read.status_code, 403)

    def test_referenced_sport_cannot_be_deleted_even_if_team_is_disabled(self):
        team = self._post_team("Sport Reference").get_json()["team"]
        self.client.patch(
            f"/teams/{team['id']}/disable",
            headers=self.admin_headers,
        )

        response = self.client.delete(
            f"/sports/{self.football_id}",
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "sport_in_use",
        )

    def test_no_team_deletion_and_no_roster_or_eligibility_inputs(self):
        team = self._post_team("Permanent Team").get_json()["team"]
        deleted = self.client.delete(
            f"/teams/{team['id']}",
            headers=self.admin_headers,
        )
        roster_input = self.client.post(
            "/teams",
            json={
                **self._team_payload("Roster Input", self.football_id),
                "current_players_quantity": 11,
            },
            headers=self.admin_headers,
        )
        eligibility_filter = self.client.get(
            "/teams?eligibility=eligible",
            headers=self.admin_headers,
        )
        self.assertEqual(deleted.status_code, 405)
        self.assertEqual(roster_input.status_code, 422)
        self.assertEqual(eligibility_filter.status_code, 422)

    def test_missing_team_returns_stable_404_codes(self):
        responses = (
            self.client.get("/teams/9999", headers=self.admin_headers),
            self.client.put(
                "/teams/9999",
                json={"name": "Missing"},
                headers=self.admin_headers,
            ),
            self.client.patch(
                "/teams/9999/disable",
                headers=self.admin_headers,
            ),
            self.client.patch(
                "/teams/9999/enable",
                headers=self.admin_headers,
            ),
        )
        for response in responses:
            self.assertEqual(response.status_code, 404)
            self.assertEqual(
                response.get_json()["error"]["code"],
                "team_not_found",
            )

    def test_state_change_rolls_back_when_commit_fails(self):
        with self.app.app_context():
            team = create_team(
                self._team_payload("Rollback Team", self.football_id)
            )
            team_id = team.id
            with patch.object(
                db.session,
                "commit",
                side_effect=SQLAlchemyError("database unavailable"),
            ):
                with self.assertRaises(SQLAlchemyError):
                    set_team_enabled(team_id, is_enabled=False)

            db.session.expire_all()
            persisted = db.session.get(Team, team_id)
            self.assertTrue(persisted.is_enabled)
            self.assertIsNone(persisted.disabled_at)

    def test_unexpected_team_failure_uses_safe_error_envelope(self):
        with patch(
            "app.routes.teams.create_team",
            side_effect=RuntimeError("internal detail"),
        ):
            response = self._post_team("Unexpected Failure")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.get_json(),
            {
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected server error occurred.",
                }
            },
        )
        self.assertNotIn("internal detail", response.get_data(as_text=True))

    def test_team_migration_is_on_current_revision_chain(self):
        migration = Path(
            "migrations/versions/d4f2a7c91b30_add_team_management.py"
        ).read_text(encoding="utf-8")
        self.assertIn('down_revision = "a6c8f4d2190e"', migration)
        self.assertIn('"teams"', migration)
        self.assertIn("fk_teams_sport_id_sports", migration)
        self.assertIn(
            "uq_teams_normalized_name_sport_gender",
            migration,
        )


if __name__ == "__main__":
    unittest.main()
