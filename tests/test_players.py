import importlib
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask_jwt_extended import create_access_token
from pydantic import ValidationError
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

from app.app import create_app
from app.extensions import db
from app.models import Player, team_players
from app.schemas.players import (
    PlayerCreateRequest,
    PlayerListQuery,
    PlayerListResponse,
    PlayerResponse,
    PlayerUpdateRequest,
)
from app.services.players import (
    PlayerDisabledError,
    PlayerNotFoundError,
    PlayerValidationError,
    TeamCapacityReachedError,
    TeamGenderMismatchError,
    TeamSportMismatchError,
    _lock_teams,
    _validate_team_assignments,
    _validated_team_ids,
    list_players,
    normalize_player_name,
    normalize_player_search,
    set_player_enabled,
    update_player,
)
from app.services.sports import (
    SportInUseError,
    SportNotFoundError,
    delete_sport,
)
from app.services.teams import (
    TeamDisabledError,
    TeamNotFoundError,
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
    name="Inter Miami",
    *,
    sport_id=1,
    gender="male",
    enabled=True,
):
    return SimpleNamespace(
        id=team_id,
        name=name,
        sport_id=sport_id,
        gender_category=gender,
        is_enabled=enabled,
    )


def _player(
    player_id=1,
    name="Lionel Messi",
    *,
    sport=None,
    gender="male",
    teams=None,
    enabled=True,
    disabled_at=None,
):
    return SimpleNamespace(
        id=player_id,
        name=name,
        normalized_name="lionel messi",
        sport=sport or _sport(),
        gender=gender,
        teams=[_team()] if teams is None else teams,
        is_enabled=enabled,
        created_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
        disabled_at=disabled_at,
        nationality="must-not-leak",
        dni="must-not-leak",
    )


class PlayerSchemaTests(unittest.TestCase):
    def test_create_accepts_both_genders_and_zero_through_three_teams(self):
        for gender in ("male", "female"):
            for team_ids in ([], [1], [1, 2], [1, 2, 3]):
                with self.subTest(gender=gender, team_ids=team_ids):
                    request = PlayerCreateRequest(
                        name="Player",
                        sport_id=1,
                        gender=gender,
                        team_ids=team_ids,
                    )
                    self.assertEqual(request.team_ids, team_ids)

        request = PlayerCreateRequest(
            name="Unaffiliated",
            sport_id=1,
            gender="male",
        )
        self.assertEqual(request.team_ids, [])

    def test_team_ids_reject_duplicates_excess_and_non_positive_values(self):
        for team_ids in ([1, 1], [1, 2, 3, 4], [0], [-1], [True]):
            with self.subTest(team_ids=team_ids):
                with self.assertRaises(ValidationError):
                    PlayerCreateRequest(
                        name="Player",
                        sport_id=1,
                        gender="male",
                        team_ids=team_ids,
                    )

    def test_create_rejects_invalid_gender_ids_and_unknown_fields(self):
        invalid_payloads = (
            {"name": "Player", "sport_id": 1, "gender": "other"},
            {"name": "Player", "sport_id": 0, "gender": "male"},
            {"name": "Player", "sport_id": True, "gender": "male"},
            {
                "name": "Player",
                "sport_id": 1,
                "gender": "male",
                "dni": 123,
            },
            {
                "name": "Player",
                "sport_id": 1,
                "gender": "male",
                "nationality": "AR",
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    PlayerCreateRequest.model_validate(payload)

    def test_update_requires_exactly_name_and_team_ids(self):
        PlayerUpdateRequest(name="New name", team_ids=[])
        invalid_payloads = (
            {"name": "New name"},
            {"team_ids": []},
            {"name": "New name", "team_ids": [], "sport_id": 2},
            {"name": "New name", "team_ids": [], "gender": "female"},
            {"name": "New name", "team_ids": [], "is_enabled": False},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    PlayerUpdateRequest.model_validate(payload)

    def test_response_contract_exposes_only_approved_fields(self):
        payload = PlayerResponse.model_validate(_player()).model_dump(
            mode="json"
        )
        self.assertEqual(
            set(payload),
            {
                "id",
                "name",
                "sport",
                "gender",
                "teams",
                "is_enabled",
                "created_at",
                "disabled_at",
            },
        )
        self.assertEqual(set(payload["teams"][0]), {"id", "name"})
        self.assertNotIn("normalized_name", payload)
        self.assertNotIn("dni", payload)
        self.assertNotIn("nationality", payload)


class PlayerApiTests(unittest.TestCase):
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
    def _create_body():
        return {
            "name": "Lionel Messi",
            "sport_id": 1,
            "gender": "male",
            "team_ids": [1],
        }

    def test_administrator_can_use_every_player_operation(self):
        player = _player()
        headers = self._bearer(self.admin_token)

        with patch("app.routes.players.create_player", return_value=player):
            response = self.client.post(
                "/players",
                headers=headers,
                json=self._create_body(),
            )
        self.assertEqual(response.status_code, 201)
        PlayerResponse.model_validate(response.get_json())

        page = SimpleNamespace(
            players=[player],
            page=1,
            per_page=25,
            total_items=1,
            total_pages=1,
        )
        with patch(
            "app.routes.players.list_players",
            return_value=page,
        ) as service:
            response = self.client.get("/players", headers=headers)
        self.assertEqual(response.status_code, 200)
        PlayerListResponse.model_validate(response.get_json())
        query = service.call_args.args[0]
        self.assertEqual(
            (query.status, query.sort, query.page),
            ("enabled", "name_asc", 1),
        )

        disabled_player = _player(
            teams=[],
            enabled=False,
            disabled_at=datetime(2026, 9, 3, 13, 0, tzinfo=timezone.utc),
        )
        with patch(
            "app.routes.players.get_player",
            return_value=disabled_player,
        ):
            response = self.client.get("/players/1", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["is_enabled"])

        with patch("app.routes.players.update_player", return_value=player):
            response = self.client.put(
                "/players/1",
                headers=headers,
                json={"name": "Updated name", "team_ids": []},
            )
        self.assertEqual(response.status_code, 200)

        for endpoint in ("disable", "enable"):
            with (
                self.subTest(endpoint=endpoint),
                patch(
                    "app.routes.players.set_player_enabled",
                    return_value=player,
                ) as service,
            ):
                response = self.client.patch(
                    f"/players/1/{endpoint}",
                    headers=headers,
                )
                self.assertEqual(response.status_code, 200)
                service.assert_called_once_with(
                    1,
                    enabled=endpoint == "enable",
                )

    def test_every_operation_requires_an_administrator_access_token(self):
        requests = (
            ("get", "/players", None),
            ("post", "/players", self._create_body()),
            ("get", "/players/1", None),
            ("put", "/players/1", {"name": "New", "team_ids": []}),
            ("patch", "/players/1/disable", None),
            ("patch", "/players/1/enable", None),
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

    def test_required_json_body_errors_return_400_after_authorization(self):
        headers = self._bearer(self.admin_token)
        for method, url in (("post", "/players"), ("put", "/players/1")):
            requests = (
                self.client.open(url, method=method, headers=headers),
                self.client.open(
                    url,
                    method=method,
                    headers=headers,
                    data="{",
                    content_type="application/json",
                ),
                self.client.open(
                    url,
                    method=method,
                    headers=headers,
                    json=[],
                ),
                self.client.open(
                    url,
                    method=method,
                    headers=headers,
                    data="{}",
                    content_type="text/plain",
                ),
            )
            for response in requests:
                with self.subTest(method=method, response=response):
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(
                        response.get_json()["error"]["code"],
                        "invalid_request",
                    )

    def test_player_payload_shape_errors_return_422(self):
        headers = self._bearer(self.admin_token)
        invalid_creates = (
            {**self._create_body(), "gender": "other"},
            {**self._create_body(), "team_ids": [1, 1]},
            {**self._create_body(), "team_ids": [1, 2, 3, 4]},
            {**self._create_body(), "sport_id": True},
            {**self._create_body(), "nationality": "AR"},
        )
        for body in invalid_creates:
            with self.subTest(body=body):
                response = self.client.post(
                    "/players",
                    headers=headers,
                    json=body,
                )
                self.assertEqual(response.status_code, 422)

        immutable_updates = ("id", "sport_id", "gender", "is_enabled")
        for field in immutable_updates:
            with self.subTest(field=field):
                response = self.client.put(
                    "/players/1",
                    headers=headers,
                    json={"name": "New", "team_ids": [], field: 2},
                )
                self.assertEqual(response.status_code, 422)

    def test_list_query_validation_and_forwarding(self):
        headers = self._bearer(self.admin_token)
        invalid_queries = (
            "page=0",
            "status=archived",
            "sort=name_desc",
            "gender=other",
            "sport_id=0",
            "team_id=0",
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                response = self.client.get(
                    f"/players?{query}",
                    headers=headers,
                )
                self.assertEqual(response.status_code, 422)
                detail = response.get_json()["error"]["details"][0]
                self.assertTrue(detail["field"].startswith("query."))

        empty_page = SimpleNamespace(
            players=[],
            page=3,
            per_page=25,
            total_items=26,
            total_pages=2,
        )
        with patch(
            "app.routes.players.list_players",
            return_value=empty_page,
        ) as service:
            response = self.client.get(
                "/players?search=%C3%81ngel&sport_id=1&gender=female"
                "&team_id=2&status=all&sort=created_at_desc&page=3",
                headers=headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["players"], [])
        query = service.call_args.args[0]
        self.assertEqual(
            (
                query.search,
                query.sport_id,
                query.gender,
                query.team_id,
                query.status,
                query.sort,
                query.page,
            ),
            ("Ángel", 1, "female", 2, "all", "created_at_desc", 3),
        )

    def test_create_maps_domain_errors_to_stable_codes(self):
        cases = (
            (PlayerValidationError("invalid"), 422, "validation_error"),
            (SportNotFoundError("missing"), 404, "sport_not_found"),
            (TeamNotFoundError("missing"), 404, "team_not_found"),
            (TeamDisabledError("disabled"), 409, "team_disabled"),
            (
                TeamSportMismatchError("mismatch"),
                409,
                "team_sport_mismatch",
            ),
            (
                TeamGenderMismatchError("mismatch"),
                409,
                "team_gender_mismatch",
            ),
            (
                TeamCapacityReachedError("full"),
                409,
                "team_capacity_reached",
            ),
        )
        headers = self._bearer(self.admin_token)
        for exception, status, code in cases:
            with (
                self.subTest(code=code),
                patch(
                    "app.routes.players.create_player",
                    side_effect=exception,
                ),
            ):
                response = self.client.post(
                    "/players",
                    headers=headers,
                    json=self._create_body(),
                )
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.get_json()["error"]["code"], code)

    def test_update_and_state_map_player_errors(self):
        headers = self._bearer(self.admin_token)
        body = {"name": "Updated", "team_ids": []}
        cases = (
            (PlayerNotFoundError("missing"), 404, "player_not_found"),
            (PlayerDisabledError("disabled"), 409, "player_disabled"),
        )
        for exception, status, code in cases:
            with (
                self.subTest(code=code),
                patch(
                    "app.routes.players.update_player",
                    side_effect=exception,
                ),
            ):
                response = self.client.put(
                    "/players/1",
                    headers=headers,
                    json=body,
                )
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.get_json()["error"]["code"], code)

        with patch(
            "app.routes.players.set_player_enabled",
            side_effect=PlayerNotFoundError("missing"),
        ):
            response = self.client.patch(
                "/players/1/disable",
                headers=headers,
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "player_not_found",
        )

    def test_list_maps_missing_references(self):
        headers = self._bearer(self.admin_token)
        cases = (
            (SportNotFoundError("missing"), "sport_not_found"),
            (TeamNotFoundError("missing"), "team_not_found"),
        )
        for exception, code in cases:
            with (
                self.subTest(code=code),
                patch(
                    "app.routes.players.list_players",
                    side_effect=exception,
                ),
            ):
                response = self.client.get(
                    "/players?sport_id=999",
                    headers=headers,
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.get_json()["error"]["code"], code)

    def test_database_error_is_safe_and_player_delete_does_not_exist(self):
        headers = self._bearer(self.admin_token)
        with patch(
            "app.routes.players.create_player",
            side_effect=SQLAlchemyError("private database detail"),
        ):
            response = self.client.post(
                "/players",
                headers=headers,
                json=self._create_body(),
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "service_unavailable",
        )
        self.assertNotIn("private database detail", response.get_data(as_text=True))
        self.assertEqual(
            self.client.delete("/players/1", headers=headers).status_code,
            405,
        )

    def test_openapi_documents_player_contract_and_security(self):
        contract = self.client.get("/openapi.json").get_json()
        player_paths = {
            path: item
            for path, item in contract["paths"].items()
            if path.startswith("/players")
        }
        self.assertEqual(
            set(player_paths),
            {
                "/players",
                "/players/{player_id}",
                "/players/{player_id}/disable",
                "/players/{player_id}/enable",
            },
        )
        for path_item in player_paths.values():
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
            set(schemas["PlayerCreateRequest"]["required"]),
            {"name", "sport_id", "gender"},
        )
        self.assertEqual(
            set(schemas["PlayerUpdateRequest"]["required"]),
            {"name", "team_ids"},
        )
        self.assertEqual(
            schemas["PlayerCreateRequest"]["properties"]["gender"]["enum"],
            ["male", "female"],
        )
        create_team_ids = schemas["PlayerCreateRequest"]["properties"][
            "team_ids"
        ]
        self.assertEqual(create_team_ids["default"], [])
        self.assertTrue(create_team_ids["uniqueItems"])
        self.assertEqual(create_team_ids["maxItems"], 3)
        self.assertNotIn("delete", contract["paths"]["/players/{player_id}"])


class PlayerDomainTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TEST_CONFIG)

    def test_name_normalization_preserves_display_and_normalizes_search(self):
        display, normalized = normalize_player_name(
            "  Ángel   DEL   Río  "
        )
        self.assertEqual(display, "Ángel DEL Río")
        self.assertEqual(normalized, "angel del rio")
        self.assertEqual(normalize_player_search("  ÁNGEL  "), "angel")
        self.assertIsNone(normalize_player_search("   "))

    def test_name_normalization_rejects_invalid_values(self):
        for value in (None, 1, "", "   ", "x" * 101):
            with self.subTest(value=value):
                with self.assertRaises(PlayerValidationError):
                    normalize_player_name(value)

    def test_model_has_only_approved_columns_and_constraints(self):
        self.assertEqual(
            set(Player.__table__.columns.keys()),
            {
                "id",
                "name",
                "normalized_name",
                "gender",
                "sport_id",
                "is_enabled",
                "created_at",
                "disabled_at",
            },
        )
        self.assertNotIn("dni", Player.__table__.columns)
        self.assertNotIn("nationality", Player.__table__.columns)
        checks = {
            constraint.name
            for constraint in Player.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        self.assertTrue(
            {
                "ck_players_name_not_blank",
                "ck_players_gender",
                "ck_players_enabled_disabled_at",
            }.issubset(checks)
        )

        self.assertEqual(
            [column.name for column in team_players.primary_key.columns],
            ["team_id", "player_id"],
        )
        self.assertEqual(
            {foreign_key.constraint.name for foreign_key in team_players.foreign_keys},
            {
                "fk_team_players_team_id_teams",
                "fk_team_players_player_id_players",
            },
        )

    def test_service_revalidates_team_ids(self):
        self.assertEqual(_validated_team_ids([3, 1, 2]), [1, 2, 3])
        for team_ids in ([1, 1], [1, 2, 3, 4], [0], [True]):
            with self.subTest(team_ids=team_ids):
                with self.assertRaises(PlayerValidationError):
                    _validated_team_ids(team_ids)

    def test_team_locks_are_ordered_and_use_for_update(self):
        result = MagicMock()
        result.scalars.return_value = [_team(1), _team(2)]
        with self.app.app_context(), patch.object(
            db.session,
            "execute",
            return_value=result,
        ) as execute:
            teams = _lock_teams([1, 2])

        statement = execute.call_args.args[0]
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertEqual([team.id for team in teams], [1, 2])
        self.assertIn("ORDER BY teams.id", sql)
        self.assertIn("FOR UPDATE", sql)

    def test_team_assignment_rules_and_capacity_are_enforced(self):
        sport = _sport(total=1)
        cases = (
            (_team(enabled=False), TeamDisabledError),
            (_team(sport_id=2), TeamSportMismatchError),
            (_team(gender="female"), TeamGenderMismatchError),
        )
        for team, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                with self.assertRaises(expected_error):
                    _validate_team_assignments(
                        [team],
                        sport=sport,
                        gender="male",
                        new_team_ids={team.id},
                    )

        result = MagicMock()
        result.all.return_value = [(1, 1)]
        with self.app.app_context(), patch.object(
            db.session,
            "execute",
            return_value=result,
        ):
            with self.assertRaises(TeamCapacityReachedError):
                _validate_team_assignments(
                    [_team()],
                    sport=sport,
                    gender="male",
                    new_team_ids={1},
                )

    def test_update_locks_current_and_requested_teams_before_replacement(self):
        player = _player(teams=[_team(1)])
        current_memberships = MagicMock()
        current_memberships.scalars.return_value = [1]
        locked_teams = [_team(1), _team(2)]
        request = PlayerUpdateRequest(name="New name", team_ids=[2])

        with self.app.app_context(), patch(
            "app.services.players._lock_player",
            return_value=player,
        ), patch.object(
            db.session,
            "execute",
            return_value=current_memberships,
        ), patch(
            "app.services.players._lock_teams",
            return_value=locked_teams,
        ) as lock_teams, patch(
            "app.services.players._validate_team_assignments",
        ) as validate, patch.object(db.session, "commit"):
            result = update_player(1, request)

        lock_teams.assert_called_once_with([1, 2])
        self.assertEqual(validate.call_args.kwargs["new_team_ids"], {2})
        self.assertEqual([team.id for team in result.teams], [2])

    def test_update_rolls_back_on_database_failure(self):
        player = _player(teams=[])
        current_memberships = MagicMock()
        current_memberships.scalars.return_value = []
        request = PlayerUpdateRequest(name="New name", team_ids=[])

        with self.app.app_context(), patch(
            "app.services.players._lock_player",
            return_value=player,
        ), patch.object(
            db.session,
            "execute",
            return_value=current_memberships,
        ), patch(
            "app.services.players._lock_teams",
            return_value=[],
        ), patch.object(
            db.session,
            "commit",
            side_effect=SQLAlchemyError("failure"),
        ), patch.object(db.session, "rollback") as rollback:
            with self.assertRaises(SQLAlchemyError):
                update_player(1, request)

        rollback.assert_called_once()

    def test_sport_deletion_detects_player_references(self):
        sport_result = MagicMock()
        sport_result.scalar_one_or_none.return_value = _sport()
        team_result = MagicMock()
        team_result.scalar_one_or_none.return_value = None
        player_result = MagicMock()
        player_result.scalar_one_or_none.return_value = 1

        with self.app.app_context(), patch.object(
            db.session,
            "execute",
            side_effect=[sport_result, team_result, player_result],
        ), patch.object(db.session, "rollback") as rollback:
            with self.assertRaises(SportInUseError):
                delete_sport(1)

        rollback.assert_called_once()

    def _capture_list_statements(self, query, *, total=0):
        count_result = MagicMock()
        count_result.scalar_one.return_value = total
        players_result = MagicMock()
        players_result.scalars.return_value = []
        with self.app.app_context(), patch.object(
            db.session,
            "get",
            return_value=SimpleNamespace(id=1),
        ), patch.object(
            db.session,
            "execute",
            side_effect=[count_result, players_result],
        ) as execute:
            page = list_players(query)
        return page, [call.args[0] for call in execute.call_args_list]

    def test_listing_combines_filters_and_uses_database_pagination(self):
        query = PlayerListQuery(
            search="ÁNGEL",
            sport_id=1,
            gender="female",
            team_id=2,
            status="disabled",
            sort="created_at_desc",
            page=3,
        )
        page, statements = self._capture_list_statements(query, total=26)
        dialect = postgresql.dialect()
        count_sql = str(statements[0].compile(dialect=dialect))
        list_sql = str(statements[1].compile(dialect=dialect))

        for fragment in (
            "normalized_name LIKE",
            "sport_id",
            "gender",
            "team_players",
            "is_enabled IS false",
        ):
            self.assertIn(fragment, count_sql)
        self.assertIn(
            "ORDER BY players.created_at DESC, players.id DESC",
            list_sql,
        )
        self.assertEqual((page.page, page.per_page), (3, 25))
        self.assertEqual((page.total_items, page.total_pages), (26, 2))
        self.assertEqual(page.players, [])
        self.assertTrue(statements[1]._with_options)

    def test_player_state_cleanup_is_idempotent_and_never_restores_teams(self):
        player = _player(teams=[_team()])
        with self.app.app_context(), patch(
            "app.services.players._lock_player",
            return_value=player,
        ), patch.object(db.session, "commit") as commit:
            result = set_player_enabled(1, enabled=False)
            self.assertFalse(result.is_enabled)
            self.assertEqual(result.teams, [])
            self.assertIsNotNone(result.disabled_at)
            commit.assert_called_once()

        disabled_at = player.disabled_at
        with self.app.app_context(), patch(
            "app.services.players._lock_player",
            return_value=player,
        ), patch.object(db.session, "commit") as commit:
            result = set_player_enabled(1, enabled=False)
            self.assertEqual(result.disabled_at, disabled_at)
            commit.assert_not_called()

        with self.app.app_context(), patch(
            "app.services.players._lock_player",
            return_value=player,
        ), patch.object(db.session, "commit") as commit:
            result = set_player_enabled(1, enabled=True)
            self.assertTrue(result.is_enabled)
            self.assertIsNone(result.disabled_at)
            self.assertEqual(result.teams, [])
            commit.assert_called_once()


class PlayerMigrationTests(unittest.TestCase):
    def test_migration_creates_and_drops_tables_in_safe_order(self):
        migration = importlib.import_module(
            "migrations.versions.b4e6c1d2a9f0_add_player_management"
        )
        with patch.object(migration, "op") as operation:
            migration.upgrade()
        self.assertEqual(
            [call.args[0] for call in operation.create_table.call_args_list],
            ["players", "team_players"],
        )
        player_columns = {
            item.name
            for item in operation.create_table.call_args_list[0].args[1:]
            if hasattr(item, "name")
        }
        self.assertNotIn("dni", player_columns)
        self.assertNotIn("nationality", player_columns)

        with patch.object(migration, "op") as operation:
            migration.downgrade()
        self.assertEqual(
            [call.args[0] for call in operation.drop_table.call_args_list],
            ["team_players", "players"],
        )


if __name__ == "__main__":
    unittest.main()
