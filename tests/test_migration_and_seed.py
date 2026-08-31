import importlib
import unittest
from pathlib import Path

from app.models import Sport, User


class MigrationAndSeedTests(unittest.TestCase):
    def test_current_model_contains_required_constraints(self):
        sport_constraints = {
            constraint.name for constraint in Sport.__table__.constraints
        }
        user_constraints = {
            constraint.name for constraint in User.__table__.constraints
        }

        self.assertIn("ck_sports_match_duration_positive", sport_constraints)
        self.assertIn(
            "ck_sports_resolution_methods_non_empty_array",
            sport_constraints,
        )
        self.assertIn("ck_users_valid_role", user_constraints)
        self.assertIn("ck_users_valid_requested_role", user_constraints)

    def test_migration_is_at_the_expected_head(self):
        migration = importlib.import_module(
            "migrations.versions."
            "a6c8f4d2190e_add_account_approval_and_sport_config"
        )

        self.assertEqual(migration.revision, "a6c8f4d2190e")
        self.assertEqual(migration.down_revision, "3e22b5f59faa")

    def test_seed_contains_approved_configuration_and_upsert(self):
        repository_root = Path(__file__).resolve().parents[1]
        seed_sql = (
            repository_root / "scripts" / "initialize_sports.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("'Fútbol'", seed_sql)
        self.assertIn("'Básquet'", seed_sql)
        self.assertIn("90", seed_sql)
        self.assertIn("40", seed_sql)
        self.assertIn('"code":"penalty"', seed_sql)
        self.assertIn('"code":"overtime"', seed_sql)
        self.assertIn("ON CONFLICT (normalized_name) DO UPDATE", seed_sql)


if __name__ == "__main__":
    unittest.main()
