import unittest

from app.services.sports import (
    SportValidationError,
    _normalize_name,
    _validate_capacities,
    _validate_max_players,
)


class SportNameNormalizationTests(unittest.TestCase):
    def test_normalizes_case_whitespace_and_accents_for_comparison(self):
        display_name, normalized_name = _normalize_name("  fÚTBOL   SALA ")

        self.assertEqual(display_name, "Fútbol sala")
        self.assertEqual(normalized_name, "futbol sala")

    def test_accented_and_unaccented_names_share_normalized_value(self):
        _, accented = _normalize_name("Fútbol")
        _, unaccented = _normalize_name("FUTBOL")

        self.assertEqual(accented, unaccented)

    def test_rejects_blank_name(self):
        with self.assertRaises(SportValidationError):
            _normalize_name("   ")


class SportCapacityValidationTests(unittest.TestCase):
    def test_accepts_positive_capacities_without_the_old_upper_limit(self):
        self.assertEqual(_validate_max_players(1), 1)
        self.assertEqual(_validate_max_players(22), 22)

    def test_rejects_non_positive_values(self):
        for value in (0, -1):
            with self.subTest(value=value):
                with self.assertRaises(SportValidationError):
                    _validate_max_players(value)

    def test_rejects_more_players_in_game_than_in_the_team_pool(self):
        with self.assertRaises(SportValidationError):
            _validate_capacities(10, 11)

    def test_accepts_valid_total_and_in_game_capacities(self):
        self.assertEqual(_validate_capacities(22, 11), (22, 11))

    def test_rejects_non_integer_values(self):
        for value in (True, 11.0, "11", None):
            with self.subTest(value=value):
                with self.assertRaises(SportValidationError):
                    _validate_max_players(value)


if __name__ == "__main__":
    unittest.main()
