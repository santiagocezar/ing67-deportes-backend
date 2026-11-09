import unittest
from types import SimpleNamespace as Object
from unittest.mock import patch

from flask_jwt_extended import create_access_token

from app.app import create_app
from app.errors import localized_error_message
from app.services.competitions import roster_import_options
from app.services.competition_state import CompetitionServiceError

CONFIG = {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "postgresql://test:test@localhost/test",
          "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-bytes", "API_DOCS_ENABLED": True}


class RosterImportTests(unittest.TestCase):
    def test_import_errors_have_specific_localized_messages(self):
        for code in (
            "import_same_competition",
            "import_source_not_finished",
            "import_competition_mismatch",
        ):
            with self.subTest(code=code):
                self.assertNotEqual(
                    localized_error_message(code),
                    localized_error_message("unknown_error"),
                )

    def setUp(self):
        self.revoked = patch("app.services.auth.is_token_revoked", return_value=False)
        self.revoked.start()
        self.app = create_app(CONFIG)
        self.context = self.app.app_context()
        self.context.push()
        self.client = self.app.test_client()
        self.admin = create_access_token(identity="1", additional_claims={"sid": "a", "role": "administrator"})
        self.referee = create_access_token(identity="2", additional_claims={"sid": "b", "role": "referee"})

    def tearDown(self):
        self.revoked.stop()
        self.context.pop()

    def request(self, token, suffix="?source_competition_id=2"):
        return self.client.get("/competitions/1/roster-import-options" + suffix,
                               headers={"Authorization": f"Bearer {token}"})

    def test_contract_and_authorization(self):
        self.assertEqual(self.client.get("/competitions/1/roster-import-options?source_competition_id=2").status_code, 401)
        self.assertEqual(self.request(self.referee).status_code, 403)
        self.assertEqual(self.request(self.admin, "?source_competition_id=0").status_code, 422)
        self.assertEqual(self.request(self.admin, "?source_competition_id=2&extra=1").status_code, 422)
        with patch("app.routes.competitions.roster_import_options", return_value={
            "source": {"id": 2, "name": "Old"}, "destination": {"id": 1, "name": "New"}, "teams": []
        }) as service:
            self.assertEqual(self.request(self.admin).get_json()["teams"], [])
            service.assert_called_once_with(1, 2)

    def test_eligible_roster_and_whole_roster_exclusions(self):
        sport = Object(max_players_in_game=2, max_players=4)
        players = [Object(id=i, name=str(i), sport_id=1, gender="male", is_enabled=True) for i in (1, 2)]
        team = Object(id=10, name="Team", is_enabled=True, sport_id=1, gender_category="male")
        entry = Object(team_id=10, team=team, roster_entries=[Object(player=p) for p in players])
        destination = Object(id=1, name="New", is_enabled=True, sport_id=1, sport=sport, gender="male", team_entries=[entry])
        source = Object(id=2, name="Old", status="finished", sport_id=1, gender="male", team_entries=[entry])
        with patch("app.services.competitions.lock_competition"), \
             patch("app.services.competitions.reconcile_competitions"), \
             patch("app.services.competitions.has_matches", return_value=False), \
             patch("app.services.competitions._load_competition", side_effect=[destination, source] * 3), \
             patch("app.services.competitions.db.session.execute") as execute, \
             patch("app.services.competitions.db.session.commit"), \
             patch("app.services.sanctions.reconcile_player_sanctions_batch", side_effect=[{1: False, 2: False}, {1: False, 2: True}, {1: False, 2: False}]):
            execute.return_value.all.return_value = [(10, 1), (10, 2)]
            self.assertEqual([team["team_id"] for team in roster_import_options(1, 2)["teams"]], [10])
            self.assertEqual(roster_import_options(1, 2)["teams"], [])
            execute.return_value.all.return_value = [(10, 1)]
            self.assertEqual(roster_import_options(1, 2)["teams"], [])

    def test_destination_with_matches_is_rejected(self):
        destination = Object(is_enabled=True)
        with patch("app.services.competitions.lock_competition"), \
             patch("app.services.competitions.reconcile_competitions"), \
             patch("app.services.competitions._load_competition", side_effect=[destination, Object()]), \
             patch("app.services.competitions.has_matches", return_value=True), \
             patch("app.services.competitions.db.session.rollback"):
            with self.assertRaises(CompetitionServiceError) as error:
                roster_import_options(1, 2)
            self.assertEqual(error.exception.code, "competition_has_matches")

    def test_source_must_be_finished_and_compatible(self):
        destination = Object(is_enabled=True, sport_id=1, gender="male")
        cases = [
            (Object(status="scheduled", sport_id=1, gender="male"), "import_source_not_finished"),
            (Object(status="finished", sport_id=2, gender="male"), "import_competition_mismatch"),
            (Object(status="finished", sport_id=1, gender="female"), "import_competition_mismatch"),
        ]
        for source, code in cases:
            with self.subTest(code=code, source=source), \
                 patch("app.services.competitions.lock_competition"), \
                 patch("app.services.competitions.reconcile_competitions"), \
                 patch("app.services.competitions._load_competition", side_effect=[destination, source]), \
                 patch("app.services.competitions.has_matches", return_value=False), \
                 patch("app.services.competitions.db.session.rollback"):
                with self.assertRaises(CompetitionServiceError) as error:
                    roster_import_options(1, 2)
                self.assertEqual(error.exception.code, code)
