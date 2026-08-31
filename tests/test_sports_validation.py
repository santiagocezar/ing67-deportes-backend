import unittest

from app.services.sports import (
    SportValidationError,
    _validate_match_duration,
    _normalize_name,
    _validate_max_players,
    _validate_resolution_methods,
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


class SportMatchDurationValidationTests(unittest.TestCase):
    def test_accepts_positive_integer(self):
        self.assertEqual(_validate_match_duration(90), 90)

    def test_rejects_non_positive_values(self):
        for value in (0, -1):
            with self.subTest(value=value):
                with self.assertRaises(SportValidationError):
                    _validate_match_duration(value)

    def test_rejects_non_integer_values(self):
        for value in (True, 90.0, "90", None):
            with self.subTest(value=value):
                with self.assertRaises(SportValidationError):
                    _validate_match_duration(value)


class SportResolutionMethodsValidationTests(unittest.TestCase):
    def test_accepts_methods_and_preserves_order(self):
        methods = _validate_resolution_methods(
            [
                {"code": "penalty", "name": " Penales "},
                {"code": "extra_time", "name": "Tiempo   extra"},
            ]
        )

        self.assertEqual(
            methods,
            [
                {"code": "penalty", "name": "Penales"},
                {"code": "extra_time", "name": "Tiempo extra"},
            ],
        )

    def test_rejects_null_empty_scalar_and_nested_values(self):
        for value in (None, [], "penalty", 1, [None], [["penalty"]]):
            with self.subTest(value=value):
                with self.assertRaises(SportValidationError):
                    _validate_resolution_methods(value)

    def test_rejects_missing_or_unexpected_fields(self):
        invalid_methods = (
            [{"code": "penalty"}],
            [{"name": "Penales"}],
            [{"code": "penalty", "name": "Penales", "order": 1}],
        )
        for value in invalid_methods:
            with self.subTest(value=value):
                with self.assertRaises(SportValidationError):
                    _validate_resolution_methods(value)

    def test_rejects_blank_and_non_string_values(self):
        invalid_methods = (
            [{"code": "", "name": "Penales"}],
            [{"code": None, "name": "Penales"}],
            [{"code": "penalty", "name": "   "}],
            [{"code": "penalty", "name": 1}],
        )
        for value in invalid_methods:
            with self.subTest(value=value):
                with self.assertRaises(SportValidationError):
                    _validate_resolution_methods(value)

    def test_rejects_codes_that_are_not_snake_case(self):
        for code in ("Penalty", "extra-time", "extra time", "_penalty"):
            with self.subTest(code=code):
                with self.assertRaises(SportValidationError):
                    _validate_resolution_methods(
                        [{"code": code, "name": "Method"}]
                    )

    def test_rejects_duplicate_codes_and_normalized_names(self):
        with self.assertRaises(SportValidationError):
            _validate_resolution_methods(
                [
                    {"code": "penalty", "name": "Penales"},
                    {"code": "penalty", "name": "Tiros penales"},
                ]
            )

        with self.assertRaises(SportValidationError):
            _validate_resolution_methods(
                [
                    {"code": "penalty", "name": "Tiempo extra"},
                    {"code": "overtime", "name": "  TIEMPO   EXTRA "},
                ]
            )

if __name__ == "__main__":
    unittest.main()
