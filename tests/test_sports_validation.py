import unittest

from app.services.sports import (
    SportValidationError,
    _normalize_name,
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


class SportMaxPlayersValidationTests(unittest.TestCase):
    def test_accepts_range_boundaries(self):
        self.assertEqual(_validate_max_players(1), 1)
        self.assertEqual(_validate_max_players(20), 20)

    def test_rejects_values_outside_range(self):
        for value in (0, 21, -1):
            with self.subTest(value=value):
                with self.assertRaises(SportValidationError):
                    _validate_max_players(value)

    def test_rejects_non_integer_values(self):
        for value in (True, 11.0, "11", None):
            with self.subTest(value=value):
                with self.assertRaises(SportValidationError):
                    _validate_max_players(value)


if __name__ == "__main__":
    unittest.main()
