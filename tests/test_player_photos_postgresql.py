import io
import os
import shutil
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from flask_migrate import downgrade, stamp, upgrade
from PIL import Image
from sqlalchemy import create_engine, func, inspect, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.app import create_app
from app.extensions import db
from app.models import (
    Competition,
    CompetitionRosterPlayer,
    CompetitionTeam,
    Player,
    PlayerPhoto,
    Sport,
    Team,
    team_players,
)
from app.services.player_photos import (
    PlayerPhotoLimitReachedError,
    add_player_photo,
    delete_player_photo,
)
from app.services.players import PlayerDisabledError, set_player_enabled


TEST_DATABASE_URL = os.getenv("PLAYER_PHOTO_TEST_DATABASE_URL")


def _synthetic_png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (400, 400), color=(30, 90, 150)).save(
        output,
        format="PNG",
    )
    return output.getvalue()


PNG_BYTES = _synthetic_png()


@unittest.skipUnless(
    TEST_DATABASE_URL,
    "PLAYER_PHOTO_TEST_DATABASE_URL is not configured",
)
class PlayerPhotoPostgreSQLIntegrationTests(unittest.TestCase):
    """Destructive only inside a generated schema in an explicit test DB."""

    @classmethod
    def setUpClass(cls):
        cls.schema = f"player_photo_test_{uuid.uuid4().hex}"
        cls.admin_engine = create_engine(
            TEST_DATABASE_URL,
            isolation_level="AUTOCOMMIT",
        )
        with cls.admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')

        cls.temp_dir = Path(tempfile.mkdtemp())
        cls.app = create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": TEST_DATABASE_URL,
                "SQLALCHEMY_ENGINE_OPTIONS": {
                    "connect_args": {
                        "options": f"-csearch_path={cls.schema}",
                    }
                },
                "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
                "API_DOCS_ENABLED": False,
                "PLAYER_PHOTOS_DIR": str(cls.temp_dir),
            }
        )
        try:
            with cls.app.app_context():
                db.create_all()
                stamp(revision="head")
        except Exception:
            with cls.admin_engine.connect() as connection:
                connection.exec_driver_sql(
                    f'DROP SCHEMA "{cls.schema}" CASCADE'
                )
            cls.admin_engine.dispose()
            shutil.rmtree(cls.temp_dir)
            raise

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        with cls.admin_engine.connect() as connection:
            connection.exec_driver_sql(
                f'DROP SCHEMA "{cls.schema}" CASCADE'
            )
        cls.admin_engine.dispose()
        shutil.rmtree(cls.temp_dir)

    def setUp(self):
        with self.app.app_context():
            db.session.execute(text("TRUNCATE TABLE sports CASCADE"))
            db.session.commit()
            sport = Sport(
                name="Integration Sport",
                normalized_name="integration sport",
                max_players=22,
                max_players_in_game=11,
            )
            db.session.add(sport)
            db.session.flush()
            player = Player(
                name="Integration Player",
                normalized_name="integration player",
                gender="male",
                sport_id=sport.id,
                is_enabled=True,
            )
            team = Team(
                name="Integration Team",
                normalized_name="integration team",
                sport=sport,
                gender_category="male",
                is_enabled=True,
            )
            player.teams = [team]
            now = datetime.now(timezone.utc)
            competition = Competition(
                name="Historical Competition",
                normalized_name="historical competition",
                sport=sport,
                gender="male",
                starts_at=now + timedelta(days=1),
                ends_at=now + timedelta(days=2),
                status="scheduled",
                is_enabled=True,
            )
            competition_team = CompetitionTeam(team=team)
            competition_team.roster_entries = [
                CompetitionRosterPlayer(player=player)
            ]
            competition.team_entries = [competition_team]
            db.session.add(competition)
            db.session.commit()
            self.player_id = player.id
            self.team_id = team.id
            self.competition_id = competition.id
        for entry in self.temp_dir.iterdir():
            if entry.is_file():
                entry.unlink()

    def test_migration_creates_the_real_foreign_key(self):
        with self.app.app_context():
            db.session.remove()
            downgrade(revision="c7d8e9f0a1b2")
            self.assertFalse(inspect(db.engine).has_table("player_photos"))
            upgrade()
            foreign_keys = inspect(db.engine).get_foreign_keys(
                "player_photos"
            )
        self.assertEqual(len(foreign_keys), 1)
        self.assertEqual(
            foreign_keys[0]["name"],
            "fk_player_photos_player_id_players",
        )
        self.assertEqual(foreign_keys[0]["referred_table"], "players")
        self.assertEqual(
            foreign_keys[0]["options"].get("ondelete"),
            "RESTRICT",
        )

        with self.app.app_context():
            db.session.add(
                PlayerPhoto(
                    player_id=self.player_id,
                    file_name=f"{'9' * 32}.png",
                    content_type="image/gif",
                )
            )
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

            db.session.add(
                PlayerPhoto(
                    player_id=2_000_000_000,
                    file_name=f"{'8' * 32}.png",
                    content_type="image/png",
                )
            )
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_two_concurrent_uploads_cannot_both_create_the_third_photo(self):
        with self.app.app_context():
            for index in range(2):
                file_name = f"{index + 1:032x}.png"
                db.session.add(
                    PlayerPhoto(
                        player_id=self.player_id,
                        file_name=file_name,
                        content_type="image/png",
                    )
                )
                (self.temp_dir / file_name).write_bytes(PNG_BYTES)
            db.session.commit()

        from app.services import player_photos as service

        original_lock = service._lock_enabled_player
        barrier = threading.Barrier(2)
        results: list[str] = []
        results_lock = threading.Lock()

        def synchronized_lock(player_id: int):
            barrier.wait(timeout=10)
            return original_lock(player_id)

        def upload() -> None:
            outcome = "unexpected"
            with self.app.app_context():
                try:
                    add_player_photo(
                        self.player_id,
                        file_name="photo.png",
                        content=PNG_BYTES,
                    )
                    outcome = "created"
                except PlayerPhotoLimitReachedError:
                    outcome = "limited"
                finally:
                    db.session.remove()
            with results_lock:
                results.append(outcome)

        with (
            patch(
                "app.services.player_photos._face_locations",
                return_value=[(100, 300, 300, 100)],
            ),
            patch(
                "app.services.player_photos._lock_enabled_player",
                side_effect=synchronized_lock,
            ),
        ):
            threads = [threading.Thread(target=upload) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertCountEqual(results, ["created", "limited"])
        with self.app.app_context():
            count = db.session.execute(
                db.select(func.count(PlayerPhoto.id)).where(
                    PlayerPhoto.player_id == self.player_id
                )
            ).scalar_one()
        self.assertEqual(count, 3)
        self.assertEqual(
            len(
                [
                    path
                    for path in self.temp_dir.iterdir()
                    if path.suffix == ".png"
                ]
            ),
            3,
        )
        self.assertFalse(
            any(path.name.endswith(".pending") for path in self.temp_dir.iterdir())
        )

    def test_upload_and_disable_share_a_compatible_player_lock(self):
        file_name = f"{'2' * 32}.png"
        (self.temp_dir / file_name).write_bytes(PNG_BYTES)
        with self.app.app_context():
            db.session.add(
                PlayerPhoto(
                    player_id=self.player_id,
                    file_name=file_name,
                    content_type="image/png",
                )
            )
            db.session.commit()

        from app.services import player_photos as photo_service
        from app.services import players as player_service

        original_upload_lock = photo_service._lock_enabled_player
        original_state_lock = player_service._lock_player
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        outcomes_lock = threading.Lock()

        def upload_lock(player_id: int):
            barrier.wait(timeout=10)
            return original_upload_lock(player_id)

        def state_lock(player_id: int):
            barrier.wait(timeout=10)
            return original_state_lock(player_id)

        def upload() -> None:
            outcome = "unexpected"
            with self.app.app_context():
                try:
                    add_player_photo(
                        self.player_id,
                        file_name="new.png",
                        content=PNG_BYTES,
                    )
                    outcome = "created"
                except PlayerDisabledError:
                    outcome = "disabled"
                finally:
                    db.session.remove()
            with outcomes_lock:
                outcomes.append(outcome)

        def disable() -> None:
            with self.app.app_context():
                set_player_enabled(self.player_id, enabled=False)
                db.session.remove()

        with (
            patch(
                "app.services.player_photos._face_locations",
                return_value=[(100, 300, 300, 100)],
            ),
            patch(
                "app.services.player_photos._lock_enabled_player",
                side_effect=upload_lock,
            ),
            patch(
                "app.services.players._lock_player",
                side_effect=state_lock,
            ),
        ):
            upload_thread = threading.Thread(target=upload)
            disable_thread = threading.Thread(target=disable)
            upload_thread.start()
            disable_thread.start()
            upload_thread.join(timeout=20)
            disable_thread.join(timeout=20)

        self.assertFalse(upload_thread.is_alive())
        self.assertFalse(disable_thread.is_alive())
        self.assertIn(outcomes, (["created"], ["disabled"]))
        with self.app.app_context():
            player = db.session.get(Player, self.player_id)
            photo_count = db.session.execute(
                db.select(func.count(PlayerPhoto.id)).where(
                    PlayerPhoto.player_id == self.player_id
                )
            ).scalar_one()
        self.assertFalse(player.is_enabled)
        self.assertEqual(photo_count, 0)
        self.assertEqual(list(self.temp_dir.iterdir()), [])

    def test_delete_rollback_and_success_preserve_consistency(self):
        file_name = f"{'a' * 32}.png"
        file_path = self.temp_dir / file_name
        file_path.write_bytes(PNG_BYTES)
        with self.app.app_context():
            photo = PlayerPhoto(
                player_id=self.player_id,
                file_name=file_name,
                content_type="image/png",
            )
            db.session.add(photo)
            db.session.commit()
            photo_id = photo.id

            with patch.object(
                db.session,
                "commit",
                side_effect=SQLAlchemyError("forced rollback"),
            ):
                with self.assertRaises(SQLAlchemyError):
                    delete_player_photo(self.player_id, photo_id)

            self.assertIsNotNone(db.session.get(PlayerPhoto, photo_id))
            self.assertTrue(file_path.is_file())
            self.assertFalse(
                file_path.with_name(f"{file_name}.deleting").exists()
            )

            delete_player_photo(self.player_id, photo_id)
            roster_count = db.session.execute(
                db.select(func.count(CompetitionRosterPlayer.player_id)).where(
                    CompetitionRosterPlayer.competition_id
                    == self.competition_id,
                    CompetitionRosterPlayer.player_id == self.player_id,
                )
            ).scalar_one()
            self.assertIsNone(db.session.get(PlayerPhoto, photo_id))
            self.assertEqual(roster_count, 1)

        self.assertFalse(file_path.exists())
        self.assertFalse(
            file_path.with_name(f"{file_name}.deleting").exists()
        )

    def test_disable_removes_current_photos_but_preserves_roster_snapshot(self):
        file_names = [f"{value:032x}.png" for value in (3, 4, 5)]
        with self.app.app_context():
            for file_name in file_names:
                (self.temp_dir / file_name).write_bytes(PNG_BYTES)
                db.session.add(
                    PlayerPhoto(
                        player_id=self.player_id,
                        file_name=file_name,
                        content_type="image/png",
                    )
                )
            db.session.commit()
            set_player_enabled(self.player_id, enabled=False)
            player = db.session.get(Player, self.player_id)
            roster_count = db.session.execute(
                db.select(func.count(CompetitionRosterPlayer.player_id)).where(
                    CompetitionRosterPlayer.competition_id
                    == self.competition_id,
                    CompetitionRosterPlayer.player_id == self.player_id,
                )
            ).scalar_one()
            photo_count = db.session.execute(
                db.select(func.count(PlayerPhoto.id)).where(
                    PlayerPhoto.player_id == self.player_id
                )
            ).scalar_one()
            membership_count = db.session.execute(
                db.select(func.count()).select_from(team_players).where(
                    team_players.c.player_id == self.player_id
                )
            ).scalar_one()
            self.assertFalse(player.is_enabled)
            self.assertEqual(photo_count, 0)
            self.assertEqual(membership_count, 0)
            self.assertEqual(roster_count, 1)
            set_player_enabled(self.player_id, enabled=True)
            self.assertEqual(
                db.session.execute(
                    db.select(func.count(PlayerPhoto.id)).where(
                        PlayerPhoto.player_id == self.player_id
                    )
                ).scalar_one(),
                0,
            )
        self.assertEqual(list(self.temp_dir.iterdir()), [])

    def test_failed_disable_restores_state_membership_row_and_file(self):
        file_name = f"{'6' * 32}.png"
        file_path = self.temp_dir / file_name
        file_path.write_bytes(PNG_BYTES)
        with self.app.app_context():
            photo = PlayerPhoto(
                player_id=self.player_id,
                file_name=file_name,
                content_type="image/png",
            )
            db.session.add(photo)
            db.session.commit()
            photo_id = photo.id
            with patch.object(
                db.session,
                "commit",
                side_effect=SQLAlchemyError("forced rollback"),
            ):
                with self.assertRaises(SQLAlchemyError):
                    set_player_enabled(self.player_id, enabled=False)

            player = db.session.get(Player, self.player_id)
            membership_count = db.session.execute(
                db.select(func.count()).select_from(team_players).where(
                    team_players.c.player_id == self.player_id
                )
            ).scalar_one()
            self.assertTrue(player.is_enabled)
            self.assertIsNotNone(db.session.get(PlayerPhoto, photo_id))
            self.assertEqual(membership_count, 1)
            self.assertTrue(file_path.is_file())
            self.assertFalse(
                file_path.with_name(f"{file_name}.deleting").exists()
            )


if __name__ == "__main__":
    unittest.main()
