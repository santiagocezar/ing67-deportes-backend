import importlib
import random
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask_jwt_extended import create_access_token
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.app import create_app
from app.extensions import db
from app.models import (
    Competition,
    CompetitionRosterPlayer,
    Match,
    Player,
    Sport,
    Team,
    User,
)
from app.schemas.competitions import (
    CompetitionCreateRequest,
    CompetitionListQuery,
    CompetitionUpdateRequest,
    ParticipantsRequest,
)
from app.schemas.matches import MatchUpdateRequest
from app.services.competition_state import (
    CompetitionServiceError,
    reconcile_competitions,
)
from app.services.competition_participants import build_participant_entries
from app.services.competitions import (
    create_competition,
    normalize_competition_name,
    set_competition_enabled,
)
from app.services.matches import (
    _has_overlap,
    _match_status,
    _round_robin_draw,
    _validate_schedule,
)
from app.services.referees import RefereePage
from app.services.sports import SportInUseError, delete_sport


TEST_CONFIG = {
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": "postgresql://test:test@localhost/test",
    "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
    "API_DOCS_ENABLED": True,
}
UTC = timezone.utc


def _team_requests(count=4):
    return [
        {"team_id": index, "player_ids": [100 + index]}
        for index in range(1, count + 1)
    ]


def _create_payload(count=4):
    return {
        "name": "Tournament 2027",
        "sport_id": 1,
        "gender": "male",
        "starts_at": "2027-01-01T10:00:00Z",
        "ends_at": "2027-02-01T10:00:00Z",
        "teams": _team_requests(count),
        "referee_ids": list(range(1, count // 2 + 1)),
    }


def _competition():
    sport = SimpleNamespace(
        id=1,
        name="Football",
        max_players=22,
        max_players_in_game=11,
    )
    team_entries = []
    for index in range(1, 5):
        player = SimpleNamespace(
            id=100 + index,
            name=f"Player {index}",
            gender="male",
            is_enabled=True,
        )
        team = SimpleNamespace(
            id=index,
            name=f"Team {index}",
            gender_category="male",
            is_enabled=True,
        )
        team_entries.append(
            SimpleNamespace(
                team_id=index,
                team=team,
                roster_entries=[SimpleNamespace(player=player)],
            )
        )
    referees = [
        SimpleNamespace(
            referee=SimpleNamespace(
                id=index,
                name=f"Referee {index}",
                email=f"ref{index}@example.com",
            )
        )
        for index in (1, 2)
    ]
    return SimpleNamespace(
        id=1,
        name="Tournament 2027",
        sport=sport,
        gender="male",
        team_count=4,
        team_entries=team_entries,
        referee_entries=referees,
        starts_at=datetime(2027, 1, 1, 10, tzinfo=UTC),
        ends_at=datetime(2027, 2, 1, 10, tzinfo=UTC),
        status="scheduled",
        is_enabled=True,
        created_at=datetime(2026, 9, 15, tzinfo=UTC),
        disabled_at=None,
    )


def _match(competition=None):
    competition = competition or _competition()
    return SimpleNamespace(
        id=1,
        competition=competition,
        competition_id=competition.id,
        team_1_id=1,
        team_2_id=2,
        referee_id=1,
        referee=competition.referee_entries[0].referee,
        round_number=1,
        starts_at=None,
        ends_at=None,
        status="incomplete",
    )


class CompetitionSchemaTests(unittest.TestCase):
    def test_accepts_every_even_team_count_from_four_through_sixteen(self):
        for count in range(4, 17, 2):
            with self.subTest(count=count):
                request = CompetitionCreateRequest.model_validate(
                    _create_payload(count)
                )
                self.assertEqual(len(request.teams), count)
                self.assertEqual(len(request.referee_ids), count // 2)

    def test_rejects_invalid_counts_duplicates_and_unknown_fields(self):
        invalid = []
        for count in (3, 5, 17):
            payload = _create_payload(count)
            payload["referee_ids"] = list(range(1, count // 2 + 1))
            invalid.append(payload)
        duplicate_team = _create_payload()
        duplicate_team["teams"][1]["team_id"] = 1
        invalid.append(duplicate_team)
        duplicate_referee = _create_payload()
        duplicate_referee["referee_ids"] = [1, 1]
        invalid.append(duplicate_referee)
        unknown = _create_payload()
        unknown["phase"] = 1
        invalid.append(unknown)

        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    CompetitionCreateRequest.model_validate(payload)

    def test_rejects_naive_and_reversed_dates_with_stable_type(self):
        naive = _create_payload()
        naive["starts_at"] = "2027-01-01T10:00:00"
        with self.assertRaises(ValidationError):
            CompetitionCreateRequest.model_validate(naive)

        reversed_dates = _create_payload()
        reversed_dates["ends_at"] = reversed_dates["starts_at"]
        with self.assertRaises(ValidationError) as raised:
            CompetitionCreateRequest.model_validate(reversed_dates)
        self.assertEqual(
            raised.exception.errors()[0]["type"],
            "competition_date_range_invalid",
        )

    def test_update_accepts_only_name_and_dates(self):
        valid = {
            "name": "Updated",
            "starts_at": "2027-01-02T10:00:00Z",
            "ends_at": "2027-02-02T10:00:00Z",
        }
        CompetitionUpdateRequest.model_validate(valid)
        for extra_field in ("sport_id", "gender", "status", "is_enabled"):
            with self.subTest(extra_field=extra_field):
                with self.assertRaises(ValidationError):
                    CompetitionUpdateRequest.model_validate(
                        {**valid, extra_field: 1}
                    )

    def test_query_validates_ranges_and_enums(self):
        CompetitionListQuery(
            lifecycle_status="scheduled",
            availability="all",
            starts_from="2027-01-01T00:00:00Z",
            starts_to="2027-02-01T00:00:00Z",
        )
        with self.assertRaises(ValidationError):
            CompetitionListQuery(
                starts_from="2027-02-01T00:00:00Z",
                starts_to="2027-01-01T00:00:00Z",
            )

    def test_participant_contract_requires_matching_referee_count(self):
        with self.assertRaises(ValidationError):
            ParticipantsRequest(
                teams=_team_requests(),
                referee_ids=[1],
            )

    def test_match_contract_rejects_naive_reversed_and_unknown_fields(self):
        valid = {
            "starts_at": "2027-01-01T10:00:00Z",
            "ends_at": "2027-01-01T12:00:00Z",
        }
        MatchUpdateRequest.model_validate(valid)
        for payload in (
            {**valid, "starts_at": "2027-01-01T10:00:00"},
            {**valid, "ends_at": valid["starts_at"]},
            {**valid, "team_1_id": 3},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    MatchUpdateRequest.model_validate(payload)


class CompetitionDomainTests(unittest.TestCase):
    def test_name_normalization_preserves_display_and_normalizes_search(self):
        display, normalized = normalize_competition_name(
            "  Copa   Águila  "
        )
        self.assertEqual(display, "Copa Águila")
        self.assertEqual(normalized, "copa aguila")

    def test_models_define_critical_checks_and_uniqueness(self):
        competition_checks = {
            constraint.name
            for constraint in Competition.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        match_checks = {
            constraint.name
            for constraint in Match.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        match_unique = {
            constraint.name
            for constraint in Match.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertIn("ck_competitions_date_range", competition_checks)
        self.assertIn("ck_competitions_enabled_status", competition_checks)
        self.assertIn("ck_matches_schedule", match_checks)
        self.assertIn("ck_matches_team_order", match_checks)
        self.assertIn("uq_matches_competition_team_pair", match_unique)
        roster_unique = {
            constraint.name
            for constraint in CompetitionRosterPlayer.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        match_foreign_keys = {
            constraint.name
            for constraint in Match.__table__.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }
        self.assertIn(
            "uq_competition_roster_players_competition_player",
            roster_unique,
        )
        self.assertIn(
            "fk_matches_competition_referee",
            match_foreign_keys,
        )
        compiled = str(
            CreateTable(Match.__table__).compile(
                dialect=postgresql.dialect()
            )
        )
        self.assertIn("fk_matches_competition_team_1", compiled)

    def test_round_robin_draw_satisfies_every_supported_size(self):
        for team_count in range(4, 17, 2):
            with self.subTest(team_count=team_count):
                teams = list(range(1, team_count + 1))
                referees = list(range(100, 100 + team_count // 2))
                draw = _round_robin_draw(
                    teams,
                    referees,
                    random.Random(team_count),
                )
                self.assertEqual(len(draw), 3 * team_count // 2)
                opponents = {team_id: set() for team_id in teams}
                pairs = set()
                for round_number in range(1, 4):
                    round_matches = [
                        match
                        for match in draw
                        if match.round_number == round_number
                    ]
                    round_teams = [
                        team_id
                        for match in round_matches
                        for team_id in (match.team_1_id, match.team_2_id)
                    ]
                    self.assertCountEqual(round_teams, teams)
                    self.assertCountEqual(
                        [match.referee_id for match in round_matches],
                        referees,
                    )
                    for match in round_matches:
                        pair = (match.team_1_id, match.team_2_id)
                        self.assertNotIn(pair, pairs)
                        pairs.add(pair)
                        opponents[match.team_1_id].add(match.team_2_id)
                        opponents[match.team_2_id].add(match.team_1_id)
                        self.assertEqual(match.status, "incomplete")
                        self.assertIsNone(match.starts_at)
                self.assertTrue(
                    all(
                        len(team_opponents) == 3
                        for team_opponents in opponents.values()
                    )
                )

    def test_match_status_uses_exact_time_boundaries(self):
        start = datetime(2027, 1, 1, 10, tzinfo=UTC)
        end = start + timedelta(hours=2)
        match = SimpleNamespace(starts_at=start, ends_at=end)
        self.assertEqual(
            _match_status(match, start - timedelta(seconds=1)),
            "scheduled",
        )
        self.assertEqual(_match_status(match, start), "in_progress")
        self.assertEqual(_match_status(match, end), "finished")
        match.starts_at = match.ends_at = None
        self.assertEqual(_match_status(match, start), "incomplete")

    def test_competition_reconciliation_finishes_or_discards(self):
        now = datetime(2027, 2, 2, tzinfo=UTC)
        finished = SimpleNamespace(
            id=1,
            starts_at=datetime(2027, 1, 1, tzinfo=UTC),
            ends_at=datetime(2027, 2, 1, tzinfo=UTC),
            status="in_progress",
            is_enabled=True,
            disabled_at=None,
        )
        discarded = SimpleNamespace(
            id=2,
            starts_at=finished.starts_at,
            ends_at=finished.ends_at,
            status="in_progress",
            is_enabled=True,
            disabled_at=None,
        )
        result = MagicMock()
        result.scalars.return_value = [finished, discarded]
        with patch.object(db.session, "execute", return_value=result), patch(
            "app.services.competition_state._ended_competition_counts",
            return_value=({1: 4, 2: 4}, {1: (6, 6), 2: (6, 5)}),
        ):
            changed = reconcile_competitions(now)
        self.assertTrue(changed)
        self.assertEqual(finished.status, "finished")
        self.assertTrue(finished.is_enabled)
        self.assertEqual(discarded.status, "discarded")
        self.assertFalse(discarded.is_enabled)
        self.assertEqual(discarded.disabled_at, discarded.ends_at)

    def test_schedule_rejects_retroactive_and_outside_intervals(self):
        now = datetime(2027, 1, 5, tzinfo=UTC)
        competition = SimpleNamespace(
            starts_at=datetime(2027, 1, 1, tzinfo=UTC),
            ends_at=datetime(2027, 2, 1, tzinfo=UTC),
        )
        with self.assertRaises(CompetitionServiceError) as retroactive:
            _validate_schedule(
                competition,
                now,
                now + timedelta(hours=1),
                now,
            )
        self.assertEqual(
            retroactive.exception.code, "match_retroactive_schedule"
        )
        with self.assertRaises(CompetitionServiceError) as outside:
            _validate_schedule(
                competition,
                competition.ends_at - timedelta(hours=1),
                competition.ends_at + timedelta(hours=1),
                now,
            )
        self.assertEqual(outside.exception.code, "match_outside_competition")

    def test_overlap_check_distinguishes_team_and_referee(self):
        no_overlap = MagicMock()
        no_overlap.scalar_one_or_none.return_value = None
        team_overlap = MagicMock()
        team_overlap.scalar_one_or_none.return_value = 8
        referee_overlap = MagicMock()
        referee_overlap.scalar_one_or_none.return_value = 9
        start = datetime(2027, 1, 10, tzinfo=UTC)
        end = start + timedelta(hours=1)

        with patch.object(db.session, "execute", return_value=team_overlap):
            self.assertEqual(
                _has_overlap(1, [1, 2], 3, start, end),
                "team",
            )
        with patch.object(
            db.session,
            "execute",
            side_effect=[no_overlap, referee_overlap],
        ):
            self.assertEqual(
                _has_overlap(1, [1, 2], 3, start, end),
                "referee",
            )

    def test_soft_disable_and_enable_are_reversible_before_matches(self):
        now = datetime(2027, 1, 1, tzinfo=UTC)
        competition = SimpleNamespace(
            id=1,
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=5),
            status="scheduled",
            is_enabled=True,
            disabled_at=None,
        )
        with patch(
            "app.services.competitions.utc_now", return_value=now
        ), patch(
            "app.services.competitions.lock_competition",
            return_value=competition,
        ), patch(
            "app.services.competitions.reconcile_competitions",
            return_value=False,
        ), patch(
            "app.services.competitions.has_matches", return_value=False
        ), patch(
            "app.services.competitions._load_competition",
            return_value=competition,
        ), patch.object(db.session, "commit"):
            set_competition_enabled(1, enabled=False)
            self.assertFalse(competition.is_enabled)
            self.assertEqual(competition.status, "discarded")
            set_competition_enabled(1, enabled=True)
            self.assertTrue(competition.is_enabled)
            self.assertEqual(competition.status, "scheduled")
            self.assertIsNone(competition.disabled_at)

    def test_participant_builder_creates_historical_snapshots(self):
        sport = Sport(id=1, max_players=2, max_players_in_game=1)
        teams = [
            Team(
                id=index,
                sport_id=1,
                gender_category="male",
                is_enabled=True,
            )
            for index in range(1, 5)
        ]
        players = [
            Player(
                id=100 + index,
                sport_id=1,
                gender="male",
                is_enabled=True,
            )
            for index in range(1, 5)
        ]
        referees = [
            User(id=index, role="referee") for index in range(1, 3)
        ]
        memberships = MagicMock()
        memberships.all.return_value = [
            (index, 100 + index) for index in range(1, 5)
        ]
        request = ParticipantsRequest(
            teams=_team_requests(),
            referee_ids=[1, 2],
        )

        with patch(
            "app.services.competition_participants.lock_entities",
            side_effect=[players, teams, referees],
        ), patch.object(db.session, "execute", return_value=memberships):
            team_entries, referee_entries = build_participant_entries(
                request, sport, "male"
            )

        self.assertEqual(len(team_entries), 4)
        self.assertEqual(len(referee_entries), 2)
        self.assertEqual(
            team_entries[0].roster_entries[0].player.id,
            101,
        )

    def test_participant_builder_rejects_cross_team_player_reuse(self):
        sport = Sport(id=1, max_players=2, max_players_in_game=1)
        teams = [
            Team(
                id=index,
                sport_id=1,
                gender_category="male",
                is_enabled=True,
            )
            for index in range(1, 5)
        ]
        players = [
            Player(
                id=identifier,
                sport_id=1,
                gender="male",
                is_enabled=True,
            )
            for identifier in (101, 103, 104)
        ]
        referees = [
            User(id=index, role="referee") for index in range(1, 3)
        ]
        teams_payload = _team_requests()
        teams_payload[1]["player_ids"] = [101]
        request = ParticipantsRequest(
            teams=teams_payload,
            referee_ids=[1, 2],
        )
        memberships = MagicMock()
        memberships.all.return_value = [
            (1, 101),
            (2, 101),
            (3, 103),
            (4, 104),
        ]

        with patch(
            "app.services.competition_participants.lock_entities",
            side_effect=[players, teams, referees],
        ), patch.object(db.session, "execute", return_value=memberships):
            with self.assertRaises(CompetitionServiceError) as raised:
                build_participant_entries(request, sport, "male")
        self.assertEqual(
            raised.exception.code,
            "competition_player_already_registered",
        )

    def test_creation_rolls_back_on_commit_failure(self):
        request = CompetitionCreateRequest.model_validate(_create_payload())
        sport = Sport(id=1, max_players=2, max_players_in_game=1)
        now = datetime(2026, 9, 15, tzinfo=UTC)
        with patch(
            "app.services.competitions.utc_now", return_value=now
        ), patch.object(
            db.session, "get", return_value=sport
        ), patch(
            "app.services.competitions.build_participant_entries",
            return_value=([], []),
        ), patch.object(db.session, "add"), patch.object(
            db.session,
            "commit",
            side_effect=RuntimeError("failure"),
        ), patch.object(db.session, "rollback") as rollback:
            with self.assertRaises(RuntimeError):
                create_competition(request)
        rollback.assert_called_once()

    def test_migration_uses_safe_dependency_order(self):
        migration = importlib.import_module(
            "migrations.versions."
            "c7d8e9f0a1b2_add_competition_and_match_management"
        )
        with patch.object(migration, "op") as operation:
            migration.upgrade()
        created_tables = [
            call.args[0] for call in operation.create_table.call_args_list
        ]
        self.assertEqual(
            created_tables,
            [
                "competitions",
                "competition_teams",
                "competition_roster_players",
                "competition_referees",
                "matches",
            ],
        )
        with patch.object(migration, "op") as operation:
            migration.downgrade()
        dropped_tables = [
            call.args[0] for call in operation.drop_table.call_args_list
        ]
        self.assertEqual(dropped_tables, list(reversed(created_tables)))

    def test_sport_deletion_detects_competition_references(self):
        results = []
        for value in (SimpleNamespace(id=1), None, None, 7):
            result = MagicMock()
            result.scalar_one_or_none.return_value = value
            results.append(result)
        with patch.object(
            db.session,
            "execute",
            side_effect=results,
        ), patch.object(db.session, "rollback") as rollback:
            with self.assertRaises(SportInUseError):
                delete_sport(1)
        rollback.assert_called_once()


class CompetitionApiTests(unittest.TestCase):
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
                additional_claims={"sid": "admin", "role": "administrator"},
            )
            self.referee_token = create_access_token(
                identity="2",
                additional_claims={"sid": "referee", "role": "referee"},
            )

    def tearDown(self):
        self.blocklist_patch.stop()

    @staticmethod
    def _bearer(token):
        return {"Authorization": f"Bearer {token}"}

    def test_both_roles_can_read_but_only_admin_can_write(self):
        competition = _competition()
        for token in (self.admin_token, self.referee_token):
            with patch(
                "app.routes.competitions.get_competition",
                return_value=competition,
            ):
                response = self.client.get(
                    "/competitions/1", headers=self._bearer(token)
                )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/competitions",
            headers=self._bearer(self.referee_token),
            json=_create_payload(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "administrator_required",
        )

    def test_admin_creation_returns_only_public_contract(self):
        competition = _competition()
        with patch(
            "app.routes.competitions.create_competition",
            return_value=competition,
        ):
            response = self.client.post(
                "/competitions",
                headers=self._bearer(self.admin_token),
                json=_create_payload(),
            )
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertNotIn("normalized_name", payload)
        self.assertEqual(payload["team_count"], 4)
        self.assertEqual(len(payload["referees"]), 2)

    def test_invalid_date_range_returns_field_specific_error(self):
        payload = _create_payload()
        payload["ends_at"] = payload["starts_at"]
        response = self.client.post(
            "/competitions",
            headers=self._bearer(self.admin_token),
            json=payload,
        )
        self.assertEqual(response.status_code, 422)
        error = response.get_json()["error"]
        self.assertEqual(error["code"], "competition_date_range_invalid")
        self.assertEqual(error["details"][0]["field"], "body.ends_at")

    def test_protected_json_validation_preserves_400_behavior(self):
        response = self.client.post(
            "/competitions",
            headers=self._bearer(self.admin_token),
            json=[],
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "invalid_request",
        )

    def test_referee_lookup_is_admin_only(self):
        response = self.client.get(
            "/referees", headers=self._bearer(self.referee_token)
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_referee_lookup_exposes_only_selection_fields(self):
        referee = SimpleNamespace(
            id=5,
            name="Referee Five",
            email="ref5@example.com",
            password_hash="must-not-leak",
        )
        page = RefereePage([referee], 1, 25, 1, 1)
        with patch(
            "app.routes.referees.list_referees", return_value=page
        ):
            response = self.client.get(
                "/referees", headers=self._bearer(self.admin_token)
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.get_json()["referees"][0]),
            {"id", "name", "email"},
        )

    def test_match_reads_allow_both_roles_and_hide_referee_email(self):
        match = _match()
        for token in (self.admin_token, self.referee_token):
            with patch("app.routes.matches.get_match", return_value=match):
                response = self.client.get(
                    "/matches/1", headers=self._bearer(token)
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                set(response.get_json()["referee"]),
                {"id", "name"},
            )

    def test_randomization_and_scheduling_are_admin_only(self):
        headers = self._bearer(self.referee_token)
        self.assertEqual(
            self.client.post(
                "/competitions/1/matches/randomize",
                headers=headers,
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.put(
                "/matches/1",
                headers=headers,
                json={
                    "starts_at": "2027-01-01T10:00:00Z",
                    "ends_at": "2027-01-01T12:00:00Z",
                },
            ).status_code,
            403,
        )

    def test_match_range_error_uses_stable_code_and_field(self):
        response = self.client.put(
            "/matches/1",
            headers=self._bearer(self.admin_token),
            json={
                "starts_at": "2027-01-01T10:00:00Z",
                "ends_at": "2027-01-01T10:00:00Z",
            },
        )
        self.assertEqual(response.status_code, 422)
        error = response.get_json()["error"]
        self.assertEqual(error["code"], "match_date_range_invalid")
        self.assertEqual(error["details"][0]["field"], "body.ends_at")


if __name__ == "__main__":
    unittest.main()
