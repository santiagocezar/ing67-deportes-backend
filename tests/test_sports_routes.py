import unittest

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.app import create_app
from app.extensions import db
from app.models import ADMIN_USER_ROLE, Sport
from app.services.users import create_user


class SportRouteTests(unittest.TestCase):
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
                self._add_sqlite_functions,
                once=True,
            )
            db.create_all()
            create_user(
                {
                    "name": "Admin User",
                    "birthdate": "1990-01-01",
                    "email": "admin@example.com",
                    "password": "Password123",
                },
                role=ADMIN_USER_ROLE,
            )
        self.client = self.app.test_client()
        login = self.client.post(
            "/auth/login",
            json={"email": "admin@example.com", "password": "Password123"},
        )
        self.headers = {
            "Authorization": f"Bearer {login.get_json()['access_token']}"
        }

    @staticmethod
    def _add_sqlite_functions(connection, _connection_record):
        connection.create_function("char_length", 1, len)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    @staticmethod
    def _sport_payload():
        return {
            "name": "Handball",
            "max_players": 7,
            "match_duration": 60,
            "resolution_methods": [
                {"code": "penalty", "name": "Penales"},
                {"code": "overtime", "name": "Tiempo extra"},
            ],
        }

    def test_create_and_serialize_complete_sport_configuration(self):
        response = self.client.post(
            "/sports",
            json=self._sport_payload(),
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 201)
        sport = response.get_json()["sport"]
        self.assertEqual(sport["match_duration"], 60)
        self.assertEqual(
            [method["code"] for method in sport["resolution_methods"]],
            ["penalty", "overtime"],
        )

    def test_create_requires_duration_and_resolution_methods(self):
        for missing_field in ("match_duration", "resolution_methods"):
            payload = self._sport_payload()
            payload.pop(missing_field)
            with self.subTest(field=missing_field):
                response = self.client.post(
                    "/sports",
                    json=payload,
                    headers=self.headers,
                )
                self.assertEqual(response.status_code, 422)

    def test_update_rejects_each_immutable_field(self):
        created = self.client.post(
            "/sports",
            json=self._sport_payload(),
            headers=self.headers,
        ).get_json()["sport"]
        immutable_values = {
            "max_players": 8,
            "match_duration": 70,
            "resolution_methods": [
                {"code": "shootout", "name": "Desempate"}
            ],
        }

        for field, value in immutable_values.items():
            with self.subTest(field=field):
                response = self.client.put(
                    f"/sports/{created['id']}",
                    json={field: value},
                    headers=self.headers,
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.get_json()["error"]["code"],
                    "immutable_field",
                )

    def test_database_constraint_rejects_non_positive_duration(self):
        with self.app.app_context():
            db.session.add(
                Sport(
                    name="Invalid",
                    normalized_name="invalid",
                    max_players=5,
                    match_duration=0,
                    resolution_methods=[{"code": "x", "name": "X"}],
                )
            )
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_model_exposes_postgresql_json_array_constraint(self):
        constraint_names = {
            constraint.name for constraint in Sport.__table__.constraints
        }
        self.assertIn(
            "ck_sports_resolution_methods_non_empty_array",
            constraint_names,
        )


if __name__ == "__main__":
    unittest.main()
