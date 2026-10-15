import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask_jwt_extended import create_access_token
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from app.app import create_app
from app.extensions import db
from app.models import (
    PlayerSanction,
    PlayerSanctionMatchConsumption,
    PlayerSanctionTeam,
)
from app.schemas.sanctions import PlayerSanctionCreateRequest
from app.services.sanctions import (
    SanctionServiceError,
    _overlapping_match_groups,
    _reconcile_locked_player_sanctions,
    create_match_origin_sanction,
)


TEST_CONFIG = {
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": "postgresql://test:test@localhost/test",
    "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
    "API_DOCS_ENABLED": True,
}
UTC = timezone.utc


def _request_data():
    return PlayerSanctionCreateRequest(
        title="  Serious misconduct  ",
        reason="  A disciplinary offense occurred.  ",
        suspension_matches=3,
    )


def _sanction():
    now = datetime(2026, 10, 15, 15, tzinfo=UTC)
    competition = SimpleNamespace(id=8, name="Spring Cup")
    source_match = SimpleNamespace(
        id=7,
        starts_at=datetime(2026, 10, 15, 14, tzinfo=UTC),
        ends_at=datetime(2026, 10, 15, 16, tzinfo=UTC),
        competition=competition,
    )
    player = SimpleNamespace(id=4, name="Player Four")
    admin = SimpleNamespace(id=1, name="Admin One", email="hidden@example.com")
    teams = [
        SimpleNamespace(team_id=2, team=SimpleNamespace(id=2, name="Two")),
        SimpleNamespace(team_id=1, team=SimpleNamespace(id=1, name="One")),
    ]
    return SimpleNamespace(
        id=9,
        player=player,
        title="Serious misconduct",
        reason="A disciplinary offense occurred.",
        suspension_matches=3,
        remaining_matches=3,
        status="active",
        source_match=source_match,
        frozen_team_entries=teams,
        created_by=admin,
        updated_by=admin,
        annulled_by=None,
        effective_at=source_match.ends_at,
        created_at=now,
        updated_at=now,
        annulled_at=None,
        served_at=None,
    )


class PlayerSanctionSchemaTests(unittest.TestCase):
    def test_normalizes_text_and_accepts_duration_boundaries(self):
        request = _request_data()
        self.assertEqual(request.title, "Serious misconduct")
        self.assertEqual(request.reason, "A disciplinary offense occurred.")
        for duration in (1, 10):
            with self.subTest(duration=duration):
                self.assertEqual(
                    PlayerSanctionCreateRequest(
                        title="Title",
                        reason="Reason",
                        suspension_matches=duration,
                    ).suspension_matches,
                    duration,
                )

    def test_rejects_invalid_text_duration_and_unknown_fields(self):
        valid = {
            "title": "Title",
            "reason": "Reason",
            "suspension_matches": 3,
        }
        invalid = (
            {**valid, "title": " "},
            {**valid, "reason": "\t"},
            {**valid, "title": "x" * 31},
            {**valid, "reason": "x" * 251},
            {**valid, "suspension_matches": 0},
            {**valid, "suspension_matches": 11},
            {**valid, "suspension_matches": True},
            {**valid, "unexpected": "field"},
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    PlayerSanctionCreateRequest.model_validate(payload)


class PlayerSanctionModelTests(unittest.TestCase):
    def test_persistence_metadata_declares_lifecycle_and_foreign_keys(self):
        checks = {
            item.name
            for item in PlayerSanction.__table__.constraints
            if isinstance(item, CheckConstraint)
        }
        self.assertTrue(
            {
                "ck_player_sanctions_title_not_blank",
                "ck_player_sanctions_reason_not_blank",
                "ck_player_sanctions_suspension_matches",
                "ck_player_sanctions_remaining_matches",
                "ck_player_sanctions_status",
                "ck_player_sanctions_lifecycle",
            }.issubset(checks)
        )
        foreign_keys = {
            item.name: item.ondelete
            for item in PlayerSanction.__table__.constraints
            if isinstance(item, ForeignKeyConstraint)
        }
        self.assertEqual(
            foreign_keys["fk_player_sanctions_player_id_players"], "RESTRICT"
        )
        self.assertEqual(
            foreign_keys["fk_player_sanctions_source_match_id_matches"], "RESTRICT"
        )
        self.assertIn(
            "uq_player_sanctions_id_player",
            {
                item.name
                for item in PlayerSanction.__table__.constraints
                if isinstance(item, UniqueConstraint)
            },
        )
        self.assertIn(
            "ix_player_sanctions_player_status_created_at",
            {item.name for item in PlayerSanction.__table__.indexes if isinstance(item, Index)},
        )
        self.assertEqual(
            len(PlayerSanctionTeam.__table__.primary_key.columns), 2
        )
        consumption_constraints = {
            item.name
            for item in PlayerSanctionMatchConsumption.__table__.constraints
        }
        self.assertIn(
            "fk_player_sanction_match_consumptions_sanction_player",
            consumption_constraints,
        )
        self.assertIn(
            "fk_player_sanction_match_consumptions_match_id_matches",
            consumption_constraints,
        )
        self.assertIn(
            "uq_player_sanction_match_consumptions_player_match",
            consumption_constraints,
        )
        self.assertIn(
            "ix_player_sanction_match_consumptions_match_id",
            {
                item.name
                for item in PlayerSanctionMatchConsumption.__table__.indexes
                if isinstance(item, Index)
            },
        )


class PlayerSanctionServiceTests(unittest.TestCase):
    def test_overlapping_groups_are_connected_and_adjacent_matches_split(self):
        start = datetime(2026, 10, 15, 10, tzinfo=UTC)
        matches = [
            SimpleNamespace(id=3, starts_at=start.replace(hour=11), ends_at=start.replace(hour=12)),
            SimpleNamespace(id=1, starts_at=start, ends_at=start.replace(hour=11)),
            SimpleNamespace(id=2, starts_at=start.replace(hour=10, minute=30), ends_at=start.replace(hour=11, minute=30)),
            SimpleNamespace(id=4, starts_at=start.replace(hour=12), ends_at=start.replace(hour=13)),
        ]
        groups = _overlapping_match_groups(matches)
        self.assertEqual([[match.id for match in group] for group in groups], [[1, 2, 3], [4]])

    @patch("app.services.sanctions.db")
    @patch("app.services.sanctions._candidate_matches")
    def test_reconciliation_serves_oldest_sanction_without_spilling_final_group(
        self, candidates, mocked_db
    ):
        start = datetime(2026, 10, 15, 10, tzinfo=UTC)
        first = SimpleNamespace(
            id=1,
            status="active",
            effective_at=start.replace(hour=9),
            remaining_matches=1,
            served_at=None,
        )
        second = SimpleNamespace(
            id=2,
            status="active",
            effective_at=start.replace(hour=9),
            remaining_matches=1,
            served_at=None,
        )
        final_group = [
            SimpleNamespace(id=9, starts_at=start, ends_at=start.replace(hour=11)),
            SimpleNamespace(id=10, starts_at=start.replace(hour=10, minute=30), ends_at=start.replace(hour=12)),
        ]
        candidates.side_effect = [final_group, final_group]

        active = _reconcile_locked_player_sanctions(
            SimpleNamespace(id=4),
            [first, second],
            set(),
            start.replace(hour=13),
        )

        self.assertTrue(active)
        self.assertEqual(first.status, "served")
        self.assertEqual(first.served_at, start.replace(hour=12))
        self.assertEqual(second.status, "active")
        self.assertEqual(second.remaining_matches, 1)
        self.assertEqual(mocked_db.session.add.call_count, 1)
        consumption = mocked_db.session.add.call_args.args[0]
        self.assertEqual((consumption.sanction_id, consumption.player_id, consumption.match_id), (1, 4, 9))

    @patch("app.services.sanctions.db")
    @patch("app.services.sanctions.get_sanction")
    @patch("app.services.sanctions.reconcile_player_sanctions")
    @patch("app.services.sanctions._locked_current_team_ids", return_value=[1, 3])
    @patch("app.services.sanctions._source_roster_team_id", return_value=2)
    @patch("app.services.sanctions.reconcile_matches")
    @patch("app.services.sanctions._lock_player")
    @patch("app.services.sanctions._lock_match")
    def test_creation_freezes_current_and_source_teams(
        self,
        lock_match,
        lock_player,
        _reconcile,
        _source_team,
        _current_teams,
        _reconcile_sanctions,
        get_sanction,
        mocked_db,
    ):
        match = SimpleNamespace(
            id=7,
            competition_id=8,
            team_1_id=1,
            team_2_id=2,
            referee_id=6,
            starts_at=datetime(2026, 10, 15, 14, tzinfo=UTC),
            ends_at=datetime(2026, 10, 15, 16, tzinfo=UTC),
            status="in_progress",
        )
        lock_match.return_value = match
        lock_player.return_value = SimpleNamespace(id=4, is_enabled=False)
        get_sanction.return_value = _sanction()

        result = create_match_origin_sanction(
            7,
            4,
            _request_data(),
            actor_user_id=6,
            actor_role="referee",
        )

        sanction = mocked_db.session.add.call_args.args[0]
        self.assertEqual(result.id, 9)
        self.assertEqual(sanction.remaining_matches, 3)
        self.assertEqual(sanction.effective_at, match.ends_at)
        self.assertEqual(
            [entry.team_id for entry in sanction.frozen_team_entries], [1, 2, 3]
        )
        mocked_db.session.commit.assert_called_once()

    @patch("app.services.sanctions.db")
    @patch("app.services.sanctions.reconcile_matches")
    @patch("app.services.sanctions._lock_player")
    @patch("app.services.sanctions._lock_match")
    def test_not_in_progress_rolls_back_without_writing(
        self, lock_match, lock_player, _reconcile, mocked_db
    ):
        lock_match.return_value = SimpleNamespace(
            id=7,
            status="finished",
            starts_at=datetime(2026, 10, 15, 14, tzinfo=UTC),
            ends_at=datetime(2026, 10, 15, 16, tzinfo=UTC),
        )
        lock_player.return_value = SimpleNamespace(id=4)
        with self.assertRaises(SanctionServiceError) as raised:
            create_match_origin_sanction(
                7,
                4,
                _request_data(),
                actor_user_id=1,
                actor_role="administrator",
            )
        self.assertEqual(raised.exception.code, "match_not_in_progress")
        mocked_db.session.rollback.assert_called_once()


class PlayerSanctionApiTests(unittest.TestCase):
    def setUp(self):
        self.blocklist_patch = patch(
            "app.services.auth.is_token_revoked", return_value=False
        )
        self.blocklist_patch.start()
        self.app = create_app(TEST_CONFIG)
        self.client = self.app.test_client()
        with self.app.app_context():
            self.admin_token = create_access_token(
                identity="1", additional_claims={"sid": "a", "role": "administrator"}
            )
            self.referee_token = create_access_token(
                identity="6", additional_claims={"sid": "r", "role": "referee"}
            )

    def tearDown(self):
        self.blocklist_patch.stop()

    @staticmethod
    def _headers(token):
        return {"Authorization": f"Bearer {token}"}

    def test_authorized_creation_uses_jwt_actor_and_final_response_shape(self):
        with patch(
            "app.routes.sanctions.create_match_origin_sanction",
            return_value=_sanction(),
        ) as create:
            response = self.client.post(
                "/matches/7/players/4/sanctions",
                headers=self._headers(self.admin_token),
                json={
                    "title": "Title",
                    "reason": "Reason",
                    "suspension_matches": 3,
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            create.call_args.kwargs,
            {"actor_user_id": 1, "actor_role": "administrator"},
        )
        payload = response.get_json()
        self.assertEqual([team["id"] for team in payload["frozen_teams"]], [1, 2])
        self.assertNotIn("email", payload["creator"])
        self.assertEqual(payload["remaining_matches"], 3)

    def test_payload_and_service_failures_use_safe_contracts(self):
        url = "/matches/7/players/4/sanctions"
        headers = self._headers(self.referee_token)
        for body in (None, [], {"title": "Title", "reason": "Reason", "suspension_matches": 3, "extra": 1}):
            with self.subTest(body=body):
                response = self.client.post(url, headers=headers, json=body)
                self.assertIn(response.status_code, {400, 422})

        with patch(
            "app.routes.sanctions.create_match_origin_sanction",
            side_effect=SanctionServiceError(
                "match_referee_required", "Assigned referee required.", 403
            ),
        ):
            response = self.client.post(
                url,
                headers=headers,
                json={"title": "Title", "reason": "Reason", "suspension_matches": 3},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"]["code"], "match_referee_required")

    def test_openapi_documents_creation_security_and_response(self):
        operation = self.client.get("/openapi.json").get_json()["paths"][
            "/matches/{match_id}/players/{player_id}/sanctions"
        ]["post"]
        self.assertEqual(operation["security"], [{"AccessTokenAuth": []}])
        self.assertIn("201", operation["responses"])
        self.assertIn("PlayerSanctionResponse", self.client.get("/openapi.json").get_json()["components"]["schemas"])


if __name__ == "__main__":
    unittest.main()
