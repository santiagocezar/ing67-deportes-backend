import io
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from flask_jwt_extended import create_access_token
from werkzeug.datastructures import FileStorage, MultiDict
from werkzeug.test import encode_multipart

from app.app import create_app
from app.services.match_roster_scan_processing import (
    AMBIGUITY_DISTANCE_MARGIN,
    MAX_SCAN_IMAGE_BYTES,
    RECOGNITION_DISTANCE_THRESHOLD,
    ScanImageValidationError,
    _Candidate,
    _best_candidate,
    _match_percentage,
    _processed_faces,
    _validate_group_image,
    process_match_roster_scan,
)
from app.services.match_roster_scans import (
    MatchRosterScanServiceError,
    ROSTER_SCANS_PER_PAGE,
    ScanFaceResult,
    _authorize_scan,
    _scan_read_options,
    list_match_roster_scans,
)


TEST_CONFIG = {
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": "postgresql://test:test@localhost/test",
    "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
    "API_DOCS_ENABLED": True,
}


def _synthetic_png() -> bytes:
    """Create a generated test image; no test contains a real face."""
    pixels = np.zeros((400, 400, 3), dtype=np.uint8)
    encoded, buffer = cv2.imencode(".png", pixels)
    if not encoded:
        raise RuntimeError("Could not create the synthetic scan image.")
    return buffer.tobytes()


PNG_BYTES = _synthetic_png()


def _scan(*, faces=None, expected_players=2, unique_players=1):
    if faces is None:
        faces = [
            SimpleNamespace(
                face_index=0,
                top=10,
                right=210,
                bottom=210,
                left=10,
                status="declared_eligible",
                player_id=4,
                player_name_snapshot="Player Four",
                match_percentage=80,
                sanction_snapshots=[],
            )
        ]
    return SimpleNamespace(
        id=7,
        match_id=3,
        team_id=2,
        created_by_user_id=1,
        expected_players=expected_players,
        detected_faces=len(faces),
        unique_recognized_players=unique_players,
        created_at=datetime(2026, 10, 15, 12, tzinfo=timezone.utc),
        faces=faces,
        group_image_file_name="must-not-leak",
    )


class MatchRosterScanRouteTests(unittest.TestCase):
    def setUp(self):
        self.blocklist_patch = patch(
            "app.services.auth.is_token_revoked", return_value=False
        )
        self.blocklist_patch.start()
        self.app = create_app(TEST_CONFIG)
        self.client = self.app.test_client()
        with self.app.app_context():
            self.admin_token = create_access_token(
                identity="1",
                additional_claims={"sid": "admin-session", "role": "administrator"},
            )
            self.referee_token = create_access_token(
                identity="2",
                additional_claims={"sid": "referee-session", "role": "referee"},
            )

    def tearDown(self):
        self.blocklist_patch.stop()

    @staticmethod
    def _bearer(token):
        return {"Authorization": f"Bearer {token}"}

    def _post(self, fields=None, *, token=None):
        values = fields or {"photo": (PNG_BYTES, "scan.png")}
        encoded_fields = MultiDict()
        entries = (
            values.items(multi=True)
            if isinstance(values, MultiDict)
            else values.items()
        )
        for name, value in entries:
            encoded_fields.add(
                name,
                (
                    FileStorage(io.BytesIO(value[0]), filename=value[1])
                    if isinstance(value, tuple)
                    else value
                ),
            )
        boundary, body = encode_multipart(encoded_fields)
        return self.client.post(
            "/matches/3/teams/2/roster-scans",
            headers=self._bearer(token or self.admin_token),
            data=body,
            content_type=f'multipart/form-data; boundary="{boundary}"',
        )

    def test_returns_immutable_scan_without_embeddings_or_file_references(self):
        with patch(
            "app.routes.match_roster_scans.process_match_roster_scan",
            return_value=_scan(),
        ) as process:
            response = self._post()

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["count_warning"], "recognized_player_count_below_expected")
        self.assertEqual(payload["faces"][0]["status"], "declared_eligible")
        rendered = response.get_data(as_text=True)
        self.assertNotIn("embedding", rendered)
        self.assertNotIn("must-not-leak", rendered)
        process.assert_called_once()

    def test_referee_is_allowed_to_reach_the_service(self):
        with patch(
            "app.routes.match_roster_scans.process_match_roster_scan",
            return_value=_scan(),
        ) as process:
            response = self._post(token=self.referee_token)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(process.call_args.kwargs["actor_role"], "referee")

    def test_authentication_and_form_failures_are_rejected_before_processing(self):
        with patch(
            "app.routes.match_roster_scans.process_match_roster_scan"
        ) as process:
            anonymous = self.client.post("/matches/3/teams/2/roster-scans")
            non_multipart = self.client.post(
                "/matches/3/teams/2/roster-scans",
                headers=self._bearer(self.admin_token),
                json={"photo": "scan.png"},
            )
            missing = self._post({"note": "not a file"})
            extra = self._post(
                {"photo": (PNG_BYTES, "scan.png"), "note": "unexpected"}
            )

        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(non_multipart.status_code, 400)
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(extra.status_code, 422)
        process.assert_not_called()

    def test_repeated_photo_and_oversized_transport_bodies_are_rejected(self):
        repeated = MultiDict(
            [
                ("photo", (PNG_BYTES, "one.png")),
                ("photo", (PNG_BYTES, "two.png")),
            ]
        )
        with patch(
            "app.routes.match_roster_scans.process_match_roster_scan"
        ) as process:
            response = self._post(repeated)
            oversized = self._post(
                {
                    "photo": (
                        b"x" * (MAX_SCAN_IMAGE_BYTES + 65 * 1024),
                        "scan.png",
                    )
                }
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.get_json()["error"]["details"][0]["type"], "multiple_files"
        )
        self.assertEqual(oversized.status_code, 413)
        process.assert_not_called()

    def test_documents_the_multipart_contract_and_safe_responses(self):
        contract = self.client.get("/openapi.json").get_json()
        operation = contract["paths"][
            "/matches/{match_id}/teams/{team_id}/roster-scans"
        ]["post"]
        self.assertEqual(operation["operationId"], "matchRosterScansProcess")
        self.assertEqual(
            set(operation["requestBody"]["content"]), {"multipart/form-data"}
        )
        self.assertEqual(operation["security"], [{"AccessTokenAuth": []}])
        response_fields = set(
            contract["components"]["schemas"]["MatchRosterScanResponse"][
                "properties"
            ]
        )
        self.assertNotIn("group_image_file_name", response_fields)
        self.assertNotIn("embedding", response_fields)

    def test_lists_a_paginated_immutable_history_without_private_data(self):
        page = SimpleNamespace(
            scans=[_scan()],
            page=2,
            per_page=25,
            total_items=26,
            total_pages=2,
        )
        with patch(
            "app.routes.match_roster_scans.list_match_roster_scans",
            return_value=page,
        ) as list_scans:
            response = self.client.get(
                "/matches/3/teams/2/roster-scans?page=2",
                headers=self._bearer(self.admin_token),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            payload["pagination"],
            {
                "page": 2,
                "per_page": 25,
                "total_items": 26,
                "total_pages": 2,
            },
        )
        self.assertEqual(payload["roster_scans"][0]["id"], 7)
        rendered = response.get_data(as_text=True)
        self.assertNotIn("must-not-leak", rendered)
        self.assertNotIn("embedding", rendered)
        self.assertEqual(list_scans.call_args.kwargs["page"], 2)

    def test_history_accepts_empty_or_out_of_range_pages_and_rejects_invalid_page(
        self,
    ):
        empty_page = SimpleNamespace(
            scans=[], page=3, per_page=25, total_items=26, total_pages=2
        )
        with patch(
            "app.routes.match_roster_scans.list_match_roster_scans",
            return_value=empty_page,
        ) as list_scans:
            empty = self.client.get(
                "/matches/3/teams/2/roster-scans?page=3",
                headers=self._bearer(self.admin_token),
            )
            invalid = self.client.get(
                "/matches/3/teams/2/roster-scans?page=0",
                headers=self._bearer(self.admin_token),
            )

        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.get_json()["roster_scans"], [])
        self.assertEqual(empty.get_json()["pagination"]["total_pages"], 2)
        self.assertEqual(invalid.status_code, 422)
        list_scans.assert_called_once()

    def test_history_detail_and_authorization_errors_use_safe_responses(self):
        with patch(
            "app.routes.match_roster_scans.get_match_roster_scan",
            return_value=_scan(),
        ) as get_scan:
            detail = self.client.get(
                "/roster-scans/7", headers=self._bearer(self.referee_token)
            )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["id"], 7)
        self.assertEqual(get_scan.call_args.kwargs["actor_role"], "referee")

        cases = (
            (
                "/matches/3/teams/9/roster-scans",
                "list_match_roster_scans",
                MatchRosterScanServiceError(
                    "scan_team_not_in_match",
                    "The selected Team is not part of this Match.",
                    404,
                ),
                404,
            ),
            (
                "/roster-scans/8",
                "get_match_roster_scan",
                MatchRosterScanServiceError(
                    "scan_forbidden",
                    "The Match is not assigned to this referee.",
                    403,
                ),
                403,
            ),
        )
        for path, service_name, service_error, status in cases:
            with self.subTest(path=path), patch(
                f"app.routes.match_roster_scans.{service_name}",
                side_effect=service_error,
            ):
                response = self.client.get(
                    path, headers=self._bearer(self.referee_token)
                )
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.get_json()["error"]["code"], service_error.code)

    def test_documents_history_operations_security_and_safe_response_fields(self):
        contract = self.client.get("/openapi.json").get_json()
        list_operation = contract["paths"][
            "/matches/{match_id}/teams/{team_id}/roster-scans"
        ]["get"]
        detail_operation = contract["paths"]["/roster-scans/{scan_id}"]["get"]
        self.assertEqual(list_operation["operationId"], "matchRosterScansList")
        self.assertEqual(detail_operation["operationId"], "matchRosterScansGet")
        self.assertEqual(list_operation["security"], [{"AccessTokenAuth": []}])
        self.assertEqual(detail_operation["security"], [{"AccessTokenAuth": []}])
        list_fields = set(
            contract["components"]["schemas"]["MatchRosterScanListResponse"][
                "properties"
            ]
        )
        scan_fields = set(
            contract["components"]["schemas"]["MatchRosterScanResponse"][
                "properties"
            ]
        )
        self.assertEqual(list_fields, {"roster_scans", "pagination"})
        self.assertNotIn("group_image_file_name", scan_fields)
        self.assertNotIn("group_image_content_type", scan_fields)
        self.assertNotIn("embedding", scan_fields)


class MatchRosterScanHistoryServiceTests(unittest.TestCase):
    def test_read_authorization_allows_administrator_and_assigned_referee_only(
        self,
    ):
        match = SimpleNamespace(referee_id=2)
        _authorize_scan(match, actor_user_id=1, actor_role="administrator")
        _authorize_scan(match, actor_user_id=2, actor_role="referee")
        with self.assertRaisesRegex(
            MatchRosterScanServiceError, "assigned referee"
        ) as error:
            _authorize_scan(match, actor_user_id=3, actor_role="referee")
        self.assertEqual(error.exception.status_code, 403)

    def test_history_load_options_prevent_per_face_or_per_sanction_queries(self):
        self.assertEqual(len(_scan_read_options()), 2)

    def test_history_uses_stable_bounded_query_and_reports_out_of_range_page(self):
        match = SimpleNamespace(id=3, team_1_id=2, team_2_id=4, referee_id=2)
        query_results = (
            SimpleNamespace(scalar_one_or_none=lambda: match),
            SimpleNamespace(scalar_one=lambda: 26),
            SimpleNamespace(scalars=lambda: iter([])),
        )
        with (
            patch(
                "app.services.match_roster_scans.db.session.execute",
                side_effect=query_results,
            ) as execute,
            patch(
                "app.services.match_roster_scans.db.session.get",
                return_value=SimpleNamespace(id=2),
            ),
        ):
            result = list_match_roster_scans(
                3,
                2,
                page=3,
                actor_user_id=2,
                actor_role="referee",
            )

        statement = execute.call_args_list[2].args[0]
        self.assertEqual(result.scans, [])
        self.assertEqual(result.total_pages, 2)
        self.assertEqual(result.per_page, ROSTER_SCANS_PER_PAGE)
        self.assertEqual(statement._limit_clause.value, ROSTER_SCANS_PER_PAGE)
        self.assertEqual(statement._offset_clause.value, 2 * ROSTER_SCANS_PER_PAGE)
        self.assertIn("created_at DESC", str(statement))
        self.assertIn("id DESC", str(statement))


class MatchRosterScanRecognitionTests(unittest.TestCase):
    def test_image_validation_rejects_empty_oversized_and_wrong_content(self):
        for content, reason in (
            (b"", "invalid_image"),
            (b"x" * (MAX_SCAN_IMAGE_BYTES + 1), "image_too_large"),
            (b"%PDF-1.7", "unsupported_image_format"),
        ):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ScanImageValidationError, reason):
                    _validate_group_image(content)

    def test_distance_threshold_margin_and_percentage_boundaries(self):
        candidate_one = _Candidate(1, "One", ("one-a", "one-b"))
        candidate_two = _Candidate(2, "Two", ("two",))
        recognizer = MagicMock()
        recognizer.face_distance.side_effect = (
            [0.60, 0.61],
            [0.70],
        )
        with patch(
            "app.services.match_roster_scan_processing._recognition_module",
            return_value=recognizer,
        ):
            found = _best_candidate("scan", [candidate_one, candidate_two])
        self.assertEqual((found[0].player_id, found[1]), (1, 0.60))
        self.assertEqual(
            len(recognizer.face_distance.call_args_list[0].args[0]), 2
        )
        self.assertEqual(_match_percentage(0.0), 100)
        self.assertEqual(_match_percentage(1.0), 0)
        self.assertEqual(_match_percentage(-0.2), 100)
        self.assertEqual(_match_percentage(1.2), 0)

    def test_ambiguity_requires_a_strictly_smaller_distance_difference(self):
        candidates = [_Candidate(1, "One", ("one",)), _Candidate(2, "Two", ("two",))]
        recognizer = MagicMock()
        recognizer.face_distance.side_effect = ([0.40], [0.40 + AMBIGUITY_DISTANCE_MARGIN])
        with patch(
            "app.services.match_roster_scan_processing._recognition_module",
            return_value=recognizer,
        ):
            self.assertIsNotNone(_best_candidate("scan", candidates))

        recognizer.face_distance.side_effect = ([0.40], [0.44])
        with patch(
            "app.services.match_roster_scan_processing._recognition_module",
            return_value=recognizer,
        ):
            self.assertIsNone(_best_candidate("scan", candidates))

    def test_duplicate_recognitions_become_unknown_and_are_not_eligible(self):
        candidate = _Candidate(4, "Duplicate", ("reference",))
        eligibility = SimpleNamespace(applicable_sanctions=())
        with (
            patch(
                "app.services.match_roster_scan_processing._candidates_with_encodings",
                return_value=[candidate],
            ),
            patch(
                "app.services.match_roster_scan_processing._roster_player_ids",
                return_value={4},
            ),
            patch(
                "app.services.match_roster_scan_processing._best_candidate",
                return_value=(candidate, 0.20),
            ),
            patch(
                "app.services.match_roster_scan_processing.get_player_match_eligibility",
                return_value=eligibility,
            ) as eligibility_service,
        ):
            faces = _processed_faces(
                match_id=3,
                competition_id=1,
                team_id=2,
                locations=[(10, 210, 210, 10), (20, 220, 220, 20)],
                encodings=["face-one", "face-two"],
            )
        self.assertEqual([face.status for face in faces], ["unknown", "unknown"])
        eligibility_service.assert_not_called()

    def test_all_recognized_outcome_classes_and_sanction_snapshot(self):
        candidates = [
            _Candidate(1, "Declared eligible", ("one",)),
            _Candidate(2, "Declared sanctioned", ("two",)),
            _Candidate(3, "Undeclared", ("three",)),
            _Candidate(4, "Undeclared sanctioned", ("four",)),
        ]
        sanction = SimpleNamespace(
            sanction_id=9,
            title="Suspension",
            reason="Reason",
            remaining_matches=2,
        )
        with (
            patch(
                "app.services.match_roster_scan_processing._candidates_with_encodings",
                return_value=candidates,
            ),
            patch(
                "app.services.match_roster_scan_processing._roster_player_ids",
                return_value={1, 2},
            ),
            patch(
                "app.services.match_roster_scan_processing._best_candidate",
                side_effect=[
                    (candidates[0], 0.1),
                    (candidates[1], 0.2),
                    (candidates[2], 0.3),
                    (candidates[3], 0.4),
                ],
            ),
            patch(
                "app.services.match_roster_scan_processing.get_player_match_eligibility",
                side_effect=[
                    SimpleNamespace(applicable_sanctions=()),
                    SimpleNamespace(applicable_sanctions=(sanction,)),
                    SimpleNamespace(applicable_sanctions=()),
                    SimpleNamespace(applicable_sanctions=(sanction,)),
                ],
            ),
        ):
            faces = _processed_faces(
                match_id=3,
                competition_id=1,
                team_id=2,
                locations=[
                    (10, 210, 210, 10),
                    (20, 220, 220, 20),
                    (30, 230, 230, 30),
                    (40, 240, 240, 40),
                ],
                encodings=["one", "two", "three", "four"],
            )
        self.assertEqual(
            [face.status for face in faces],
            [
                "declared_eligible",
                "declared_sanctioned",
                "team_member_undeclared",
                "team_member_undeclared_sanctioned",
            ],
        )
        self.assertEqual(faces[3].sanctions[0].sanction_id, 9)

    def test_unknown_outcome_retains_image_but_known_only_scan_does_not(self):
        match = SimpleNamespace(id=3, competition_id=1)
        known_face = ScanFaceResult(0, 10, 210, 210, 10, "declared_eligible", 1, "One", 80)
        unknown_face = ScanFaceResult(0, 10, 210, 210, 10, "unknown")
        with (
            patch(
                "app.services.match_roster_scan_processing._validate_group_image",
                return_value="image/png",
            ),
            patch(
                "app.services.match_roster_scan_processing._load_group_faces",
                return_value=(None, [(10, 210, 210, 10)], ["encoding"]),
            ),
            patch(
                "app.services.match_roster_scan_processing.validate_match_roster_scan_target",
                return_value=match,
            ),
            patch(
                "app.services.match_roster_scan_processing._processed_faces",
                side_effect=[[known_face], [unknown_face]],
            ),
            patch(
                "app.services.match_roster_scan_processing.record_match_roster_scan",
                return_value=_scan(),
            ) as record,
        ):
            process_match_roster_scan(3, 2, actor_user_id=1, actor_role="administrator", content=PNG_BYTES)
            process_match_roster_scan(3, 2, actor_user_id=1, actor_role="administrator", content=PNG_BYTES)
        self.assertIsNone(record.call_args_list[0].kwargs["group_image"])
        self.assertEqual(record.call_args_list[1].kwargs["group_image"].content, PNG_BYTES)


if __name__ == "__main__":
    unittest.main()
