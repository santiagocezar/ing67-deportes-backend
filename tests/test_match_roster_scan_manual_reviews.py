import importlib
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask_jwt_extended import create_access_token
from sqlalchemy.dialects import postgresql

from app.app import create_app
from app.extensions import db
from app.models import (
    MatchRosterScan,
    MatchRosterScanFace,
    MatchRosterScanManualReview,
)
from app.services.match_roster_scan_manual_reviews import (
    MANUAL_REVIEWS_PER_PAGE,
    ManualReviewDecisionInput,
    ManualReviewServiceError,
    _require_assigned_referee,
    _require_review_reader,
    _validate_complete_unknown_coverage,
    _validate_decision_inputs,
    _validated_membership_players,
    finalize_match_roster_scan_manual_review,
    get_match_manual_review_status,
    list_match_roster_scan_manual_reviews,
)


TEST_CONFIG = {
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": "postgresql://test:test@localhost/test",
    "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
    "API_DOCS_ENABLED": True,
}
UTC = timezone.utc


def _resolved_review():
    resolved_at = datetime(2026, 11, 8, 18, tzinfo=UTC)
    face = SimpleNamespace(
        face_index=0,
        manual_review_decision=SimpleNamespace(
            outcome="belongs_to_team",
            player_id=21,
            player_name_snapshot="Historical Player",
            created_at=resolved_at,
        ),
    )
    return SimpleNamespace(
        status="resolved",
        expires_at=resolved_at + timedelta(hours=2),
        resolved_by_user_id=2,
        resolved_at=resolved_at,
        scan=SimpleNamespace(faces=[face]),
    )


def _manual_review_list_item():
    created_at = datetime(2026, 11, 8, 16, tzinfo=UTC)
    return SimpleNamespace(
        competition_id=4,
        competition_name="Spring Cup",
        match_id=7,
        match_starts_at=datetime(2026, 11, 8, 14, tzinfo=UTC),
        match_ends_at=datetime(2026, 11, 8, 16, tzinfo=UTC),
        match_status="finished",
        team_id=9,
        team_name="Team Nine",
        scan_id=12,
        scan_created_at=created_at,
        unknown_face_count=2,
        review=SimpleNamespace(
            status="pending",
            expires_at=created_at + timedelta(hours=2),
        ),
    )


class ManualReviewRouteTests(unittest.TestCase):
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
                additional_claims={"sid": "admin", "role": "administrator"},
            )
            self.referee_token = create_access_token(
                identity="2",
                additional_claims={"sid": "referee", "role": "referee"},
            )

    def tearDown(self):
        self.blocklist_patch.stop()

    @staticmethod
    def _headers(token):
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _valid_decisions():
        return {
            "decisions": [
                {
                    "face_index": 0,
                    "outcome": "belongs_to_team",
                    "player_id": 21,
                }
            ]
        }

    def test_assigned_referee_finalizes_with_safe_historical_response(self):
        with patch(
            "app.routes.match_roster_scans.finalize_match_roster_scan_manual_review",
            return_value=_resolved_review(),
        ) as finalize:
            response = self.client.put(
                "/roster-scans/12/manual-review",
                headers=self._headers(self.referee_token),
                json=self._valid_decisions(),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "resolved")
        self.assertEqual(payload["resolved_by_user_id"], 2)
        self.assertEqual(
            payload["decisions"],
            [
                {
                    "face_index": 0,
                    "outcome": "belongs_to_team",
                    "player_id": 21,
                    "player_name": "Historical Player",
                    "created_at": "2026-11-08T18:00:00Z",
                }
            ],
        )
        self.assertNotIn("group_image", response.get_data(as_text=True))
        self.assertEqual(finalize.call_args.kwargs["actor_user_id"], 2)
        self.assertEqual(finalize.call_args.kwargs["actor_role"], "referee")
        self.assertEqual(finalize.call_args.args[1][0].face_index, 0)

    def test_finalize_requires_authenticated_strict_json_and_valid_outcomes(self):
        url = "/roster-scans/12/manual-review"
        with patch(
            "app.routes.match_roster_scans.finalize_match_roster_scan_manual_review"
        ) as finalize:
            anonymous = self.client.put(url, json=self._valid_decisions())
            missing = self.client.put(
                url,
                headers=self._headers(self.referee_token),
            )
            malformed = self.client.put(
                url,
                headers=self._headers(self.referee_token),
                data="{",
                content_type="application/json",
            )
            non_object = self.client.put(
                url,
                headers=self._headers(self.referee_token),
                json=[],
            )
            missing_player = self.client.put(
                url,
                headers=self._headers(self.referee_token),
                json={
                    "decisions": [
                        {"face_index": 0, "outcome": "belongs_to_team"}
                    ]
                },
            )
            forbidden_player = self.client.put(
                url,
                headers=self._headers(self.referee_token),
                json={
                    "decisions": [
                        {
                            "face_index": 0,
                            "outcome": "inconclusive",
                            "player_id": 21,
                        }
                    ]
                },
            )
            extra_field = self.client.put(
                url,
                headers=self._headers(self.referee_token),
                json={**self._valid_decisions(), "unexpected": True},
            )

        self.assertEqual(anonymous.status_code, 401)
        for response in (missing, malformed, non_object):
            with self.subTest(response=response):
                self.assertEqual(response.status_code, 400)
        for response in (missing_player, forbidden_player, extra_field):
            with self.subTest(response=response):
                self.assertEqual(response.status_code, 422)
        finalize.assert_not_called()

    def test_finalize_returns_the_assigned_referee_authorization_error(self):
        with patch(
            "app.routes.match_roster_scans.finalize_match_roster_scan_manual_review",
            side_effect=ManualReviewServiceError(
                "manual_review_referee_required",
                "Only the referee assigned to this Match may finalize the review.",
                403,
            ),
        ):
            response = self.client.put(
                "/roster-scans/12/manual-review",
                headers=self._headers(self.admin_token),
                json=self._valid_decisions(),
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "manual_review_referee_required",
        )

    def test_lists_safe_role_scoped_metadata_and_derived_match_status(self):
        page = SimpleNamespace(
            reviews=[_manual_review_list_item()],
            page=2,
            per_page=25,
            total_items=26,
            total_pages=2,
        )
        audit_status = SimpleNamespace(
            match_id=7,
            requires_biometric_audit=True,
            pending_review_count=1,
            resolved_review_count=2,
            expired_review_count=3,
        )
        with (
            patch(
                "app.routes.match_roster_scans.list_match_roster_scan_manual_reviews",
                return_value=page,
            ) as list_reviews,
            patch(
                "app.routes.match_roster_scans.get_match_manual_review_status",
                return_value=audit_status,
            ) as get_status,
        ):
            listing = self.client.get(
                "/manual-reviews?match_id=7&status=resolved&"
                "sort=scan_created_at_asc&page=2",
                headers=self._headers(self.admin_token),
            )
            status = self.client.get(
                "/matches/7/manual-review-status",
                headers=self._headers(self.referee_token),
            )

        self.assertEqual(listing.status_code, 200)
        item = listing.get_json()["manual_reviews"][0]
        self.assertEqual(item["competition"], {"id": 4, "name": "Spring Cup"})
        self.assertEqual(
            item["scan"],
            {"id": 12, "created_at": "2026-11-08T16:00:00Z"},
        )
        self.assertEqual(item["unknown_face_count"], 2)
        rendered = listing.get_data(as_text=True)
        self.assertNotIn("group_image", rendered)
        self.assertNotIn("file_name", rendered)
        self.assertEqual(list_reviews.call_args.kwargs["match_id"], 7)
        self.assertEqual(list_reviews.call_args.kwargs["status"], "resolved")
        self.assertEqual(
            list_reviews.call_args.kwargs["sort"],
            "scan_created_at_asc",
        )
        self.assertEqual(list_reviews.call_args.kwargs["page"], 2)
        self.assertEqual(list_reviews.call_args.kwargs["actor_role"], "administrator")

        self.assertEqual(status.status_code, 200)
        self.assertEqual(
            status.get_json(),
            {
                "match_id": 7,
                "requires_biometric_audit": True,
                "pending_review_count": 1,
                "resolved_review_count": 2,
                "expired_review_count": 3,
            },
        )
        self.assertEqual(get_status.call_args.kwargs["actor_user_id"], 2)

    def test_listing_defaults_and_rejects_invalid_query_filters(self):
        page = SimpleNamespace(
            reviews=[],
            page=1,
            per_page=25,
            total_items=0,
            total_pages=0,
        )
        with patch(
            "app.routes.match_roster_scans.list_match_roster_scan_manual_reviews",
            return_value=page,
        ) as list_reviews:
            default = self.client.get(
                "/manual-reviews",
                headers=self._headers(self.admin_token),
            )
            invalid_responses = [
                self.client.get(
                    "/manual-reviews?match_id=0",
                    headers=self._headers(self.admin_token),
                ),
                self.client.get(
                    "/manual-reviews?match_id=not-a-number",
                    headers=self._headers(self.admin_token),
                ),
                self.client.get(
                    "/manual-reviews?match_id=1.0",
                    headers=self._headers(self.admin_token),
                ),
                self.client.get(
                    "/manual-reviews?sort=review_created_at_desc",
                    headers=self._headers(self.admin_token),
                ),
                self.client.get(
                    "/manual-reviews?page=0",
                    headers=self._headers(self.admin_token),
                ),
                self.client.get(
                    "/manual-reviews?unexpected=true",
                    headers=self._headers(self.admin_token),
                ),
            ]

        self.assertEqual(default.status_code, 200)
        self.assertEqual(list_reviews.call_count, 1)
        self.assertEqual(list_reviews.call_args.kwargs["match_id"], None)
        self.assertEqual(list_reviews.call_args.kwargs["status"], "pending")
        self.assertEqual(
            list_reviews.call_args.kwargs["sort"],
            "scan_created_at_desc",
        )
        self.assertEqual(list_reviews.call_args.kwargs["page"], 1)
        for response in invalid_responses:
            with self.subTest(response=response):
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.get_json()["error"]["code"],
                    "validation_error",
                )

    def test_openapi_documents_manual_review_operations_and_safe_schemas(self):
        contract = self.client.get("/openapi.json").get_json()
        finalize = contract["paths"]["/roster-scans/{scan_id}/manual-review"][
            "put"
        ]
        listing = contract["paths"]["/manual-reviews"]["get"]
        status = contract["paths"]["/matches/{match_id}/manual-review-status"][
            "get"
        ]

        self.assertEqual(
            finalize["operationId"], "matchRosterScansFinalizeManualReview"
        )
        self.assertEqual(finalize["security"], [{"AccessTokenAuth": []}])
        self.assertEqual(
            set(finalize["requestBody"]["content"]), {"application/json"}
        )
        self.assertEqual(
            listing["operationId"], "matchRosterScansManualReviewsList"
        )
        listing_query = {
            parameter["name"]: parameter
            for parameter in listing["parameters"]
        }
        self.assertEqual(
            set(listing_query),
            {"match_id", "status", "sort", "page"},
        )
        self.assertEqual(
            listing_query["sort"]["schema"]["default"],
            "scan_created_at_desc",
        )
        self.assertEqual(
            listing_query["sort"]["schema"]["enum"],
            ["scan_created_at_desc", "scan_created_at_asc"],
        )
        self.assertEqual(
            status["operationId"], "matchRosterScansManualReviewStatus"
        )
        review_fields = set(
            contract["components"]["schemas"][
                "MatchRosterScanManualReviewListItemResponse"
            ]["properties"]
        )
        self.assertNotIn("group_image_file_name", review_fields)
        self.assertNotIn("group_image_content_type", review_fields)
        self.assertNotIn("embedding", review_fields)
        self.assertNotIn("scan_created_at", review_fields)
        self.assertEqual(
            set(
                contract["components"]["schemas"]["ManualReviewScanResponse"][
                    "properties"
                ]
            ),
            {"id", "created_at"},
        )


class ManualReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TEST_CONFIG)
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()

    def test_reader_and_finalizer_authorization_are_intentionally_different(self):
        match = SimpleNamespace(referee_id=2)
        _require_review_reader(
            match, actor_user_id=1, actor_role="administrator"
        )
        _require_review_reader(match, actor_user_id=2, actor_role="referee")
        _require_assigned_referee(
            match, actor_user_id=2, actor_role="referee"
        )
        with self.assertRaises(ManualReviewServiceError) as raised:
            _require_assigned_referee(
                match, actor_user_id=1, actor_role="administrator"
            )
        self.assertEqual(raised.exception.status_code, 403)

    def test_decision_validation_rejects_partial_duplicate_and_non_member_data(self):
        valid = _validate_decision_inputs(
            [
                ManualReviewDecisionInput(0, "belongs_to_team", 21),
                ManualReviewDecisionInput(2, "inconclusive"),
            ]
        )
        unknown_faces = {
            0: SimpleNamespace(manual_review_decision=None),
            2: SimpleNamespace(manual_review_decision=None),
        }
        _validate_complete_unknown_coverage(valid, unknown_faces)

        with self.assertRaises(ManualReviewServiceError) as duplicate:
            _validate_decision_inputs(
                [
                    ManualReviewDecisionInput(0, "inconclusive"),
                    ManualReviewDecisionInput(0, "does_not_belong_to_team"),
                ]
            )
        self.assertEqual(duplicate.exception.code, "duplicate_manual_review_face")

        with self.assertRaises(ManualReviewServiceError) as partial:
            _validate_complete_unknown_coverage(valid[:1], unknown_faces)
        self.assertEqual(
            partial.exception.code, "manual_review_decisions_incomplete"
        )

        with self.assertRaises(ManualReviewServiceError) as non_member_player:
            _validate_decision_inputs(
                [ManualReviewDecisionInput(0, "inconclusive", 21)]
            )
        self.assertEqual(
            non_member_player.exception.code,
            "manual_review_player_forbidden",
        )

    def test_membership_uses_frozen_roster_not_current_player_state(self):
        decisions = [ManualReviewDecisionInput(0, "belongs_to_team", 21)]
        disabled_historical_player = SimpleNamespace(
            id=21,
            name="Historical Name",
            is_enabled=False,
        )
        with patch(
            "app.services.match_roster_scan_manual_reviews._frozen_roster_players",
            return_value={21: disabled_historical_player},
        ):
            players = _validated_membership_players(
                decisions,
                SimpleNamespace(team_id=9),
                SimpleNamespace(competition_id=4),
            )
        self.assertIs(players[21], disabled_historical_player)

        with self.assertRaises(ManualReviewServiceError) as duplicate:
            _validated_membership_players(
                [
                    ManualReviewDecisionInput(0, "belongs_to_team", 21),
                    ManualReviewDecisionInput(2, "belongs_to_team", 21),
                ],
                SimpleNamespace(team_id=9),
                SimpleNamespace(competition_id=4),
            )
        self.assertEqual(duplicate.exception.code, "duplicate_manual_review_player")

    def test_finalization_is_atomic_and_snapshots_the_historical_player_name(self):
        current_time = datetime(2026, 11, 8, 18, tzinfo=UTC)
        face = MatchRosterScanFace(
            scan_id=12,
            face_index=0,
            top=10,
            right=210,
            bottom=210,
            left=10,
            status="unknown",
        )
        scan = MatchRosterScan(
            id=12,
            match_id=7,
            team_id=9,
            created_by_user_id=2,
            expected_players=5,
            detected_faces=1,
            unique_recognized_players=0,
            group_image_file_name="0123456789abcdef0123456789abcdef",
            group_image_content_type="image/png",
        )
        scan.faces.append(face)
        review = MatchRosterScanManualReview(
            scan_id=12,
            status="pending",
            expires_at=current_time + timedelta(hours=1),
        )
        review.scan = scan
        match = SimpleNamespace(
            id=7,
            competition_id=4,
            status="in_progress",
            referee_id=2,
        )
        player = SimpleNamespace(id=21, name="Historical Name")
        staged_evidence = SimpleNamespace()

        with (
            patch(
                "app.services.match_roster_scan_manual_reviews._find_review_match_id",
                return_value=7,
            ),
            patch(
                "app.services.match_roster_scan_manual_reviews._lock_match",
                return_value=match,
            ),
            patch(
                "app.services.match_roster_scan_manual_reviews._load_review_for_finalization",
                return_value=review,
            ),
            patch(
                "app.services.match_roster_scan_manual_reviews.utc_now",
                return_value=current_time,
            ),
            patch(
                "app.services.match_roster_scan_manual_reviews.reconcile_matches"
            ),
            patch(
                "app.services.match_roster_scan_manual_reviews._validated_membership_players",
                return_value={21: player},
            ),
            patch(
                "app.services.match_roster_scan_manual_reviews.stage_scan_image_file",
                return_value=staged_evidence,
            ),
            patch(
                "app.services.match_roster_scan_manual_reviews.finalize_scan_image_deletions"
            ) as finalize_files,
            patch(
                "app.services.match_roster_scan_manual_reviews.restore_scan_image_deletions"
            ) as restore_files,
            patch.object(db.session, "add") as add,
            patch.object(db.session, "commit") as commit,
            patch.object(db.session, "rollback") as rollback,
        ):
            finalized = finalize_match_roster_scan_manual_review(
                12,
                [ManualReviewDecisionInput(0, "belongs_to_team", 21)],
                actor_user_id=2,
                actor_role="referee",
            )

        self.assertIs(finalized, review)
        decision = add.call_args.args[0]
        self.assertEqual(decision.face_index, 0)
        self.assertEqual(decision.outcome, "belongs_to_team")
        self.assertEqual(decision.player_id, 21)
        self.assertEqual(decision.player_name_snapshot, "Historical Name")
        self.assertEqual(review.status, "resolved")
        self.assertEqual(review.resolved_by_user_id, 2)
        self.assertEqual(review.resolved_at, current_time)
        self.assertIsNone(scan.group_image_file_name)
        self.assertIsNone(scan.group_image_content_type)
        commit.assert_called_once()
        rollback.assert_not_called()
        restore_files.assert_not_called()
        finalize_files.assert_called_once_with([staged_evidence])

    def test_audit_flag_is_derived_from_unexpired_pending_reviews(self):
        now = datetime(2026, 11, 8, 18, tzinfo=UTC)
        match = SimpleNamespace(id=7, referee_id=2)
        match_result = MagicMock()
        match_result.scalar_one_or_none.return_value = match
        rows_result = MagicMock()
        rows_result.all.return_value = [
            ("pending", now + timedelta(minutes=1)),
            ("resolved", now - timedelta(minutes=1)),
            ("expired", now - timedelta(minutes=1)),
        ]
        with (
            patch.object(
                db.session,
                "execute",
                side_effect=[match_result, rows_result],
            ),
            patch(
                "app.services.match_roster_scan_manual_reviews.reconcile_expired_match_roster_scan_reviews"
            ),
            patch(
                "app.services.match_roster_scan_manual_reviews.utc_now",
                return_value=now,
            ),
        ):
            status = get_match_manual_review_status(
                7,
                actor_user_id=2,
                actor_role="referee",
            )

        self.assertTrue(status.requires_biometric_audit)
        self.assertEqual(status.pending_review_count, 1)
        self.assertEqual(status.resolved_review_count, 1)
        self.assertEqual(status.expired_review_count, 1)

        past_pending_rows = MagicMock()
        past_pending_rows.all.return_value = [("pending", now)]
        with (
            patch.object(
                db.session,
                "execute",
                side_effect=[match_result, past_pending_rows],
            ),
            patch(
                "app.services.match_roster_scan_manual_reviews.reconcile_expired_match_roster_scan_reviews"
            ),
            patch(
                "app.services.match_roster_scan_manual_reviews.utc_now",
                return_value=now,
            ),
        ):
            expired_status = get_match_manual_review_status(
                7,
                actor_user_id=2,
                actor_role="referee",
            )
        self.assertFalse(expired_status.requires_biometric_audit)
        self.assertEqual(expired_status.pending_review_count, 0)

    def test_list_reconciles_match_status_before_returning_safe_metadata(self):
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        rows_result = MagicMock()
        rows_result.all.return_value = []
        with (
            patch(
                "app.services.match_roster_scan_manual_reviews.reconcile_expired_match_roster_scan_reviews"
            ),
            patch(
                "app.services.match_roster_scan_manual_reviews.reconcile_matches",
                return_value=True,
            ) as reconcile_matches,
            patch.object(
                db.session,
                "execute",
                side_effect=[count_result, rows_result],
            ),
            patch.object(db.session, "commit") as commit,
        ):
            page = list_match_roster_scan_manual_reviews(
                status="pending",
                page=3,
                actor_user_id=1,
                actor_role="administrator",
            )

        self.assertEqual(page.reviews, [])
        self.assertEqual(page.per_page, MANUAL_REVIEWS_PER_PAGE)
        self.assertEqual(page.total_pages, 0)
        reconcile_matches.assert_called_once_with()
        commit.assert_called_once()

    def test_list_applies_match_status_and_referee_filters_before_stable_scan_order(self):
        expected_orders = {
            "scan_created_at_desc": (
                "ORDER BY match_roster_scans.created_at DESC, "
                "match_roster_scans.id DESC"
            ),
            "scan_created_at_asc": (
                "ORDER BY match_roster_scans.created_at ASC, "
                "match_roster_scans.id ASC"
            ),
        }
        for sort, expected_order in expected_orders.items():
            with self.subTest(sort=sort):
                count_result = MagicMock()
                count_result.scalar_one.return_value = 26
                rows_result = MagicMock()
                rows_result.all.return_value = []
                with (
                    patch(
                        "app.services.match_roster_scan_manual_reviews.reconcile_expired_match_roster_scan_reviews"
                    ),
                    patch(
                        "app.services.match_roster_scan_manual_reviews.reconcile_matches",
                        return_value=False,
                    ),
                    patch.object(
                        db.session,
                        "execute",
                        side_effect=[count_result, rows_result],
                    ) as execute,
                ):
                    page = list_match_roster_scan_manual_reviews(
                        match_id=7,
                        status="pending",
                        sort=sort,
                        page=2,
                        actor_user_id=99,
                        actor_role="referee",
                    )

                self.assertEqual(page.reviews, [])
                self.assertEqual(page.total_items, 26)
                self.assertEqual(page.total_pages, 2)
                count_sql = str(
                    execute.call_args_list[0].args[0].compile(
                        dialect=postgresql.dialect(),
                        compile_kwargs={"literal_binds": True},
                    )
                )
                data_sql = str(
                    execute.call_args_list[1].args[0].compile(
                        dialect=postgresql.dialect(),
                        compile_kwargs={"literal_binds": True},
                    )
                )
                for sql in (count_sql, data_sql):
                    self.assertIn("match_roster_scans.match_id = 7", sql)
                    self.assertIn(
                        "match_roster_scan_manual_reviews.status = 'pending'",
                        sql,
                    )
                    self.assertIn("matches.referee_id = 99", sql)
                self.assertIn(expected_order, data_sql)

    def test_list_rejects_invalid_match_filter_and_sort_before_querying(self):
        for kwargs, expected_code in (
            ({"match_id": 0}, "invalid_manual_review_match_id"),
            ({"sort": "review_created_at_desc"}, "invalid_manual_review_sort"),
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ManualReviewServiceError) as raised:
                    list_match_roster_scan_manual_reviews(
                        status="pending",
                        page=1,
                        actor_user_id=1,
                        actor_role="administrator",
                        **kwargs,
                    )
                self.assertEqual(raised.exception.code, expected_code)

    def test_list_keeps_exact_metadata_for_an_out_of_range_page(self):
        count_result = MagicMock()
        count_result.scalar_one.return_value = 26
        rows_result = MagicMock()
        rows_result.all.return_value = []
        with (
            patch(
                "app.services.match_roster_scan_manual_reviews.reconcile_expired_match_roster_scan_reviews"
            ),
            patch(
                "app.services.match_roster_scan_manual_reviews.reconcile_matches",
                return_value=False,
            ),
            patch.object(
                db.session,
                "execute",
                side_effect=[count_result, rows_result],
            ) as execute,
        ):
            page = list_match_roster_scan_manual_reviews(
                match_id=7,
                status="all",
                page=3,
                actor_user_id=1,
                actor_role="administrator",
            )

        self.assertEqual(page.reviews, [])
        self.assertEqual(page.page, 3)
        self.assertEqual(page.per_page, MANUAL_REVIEWS_PER_PAGE)
        self.assertEqual(page.total_items, 26)
        self.assertEqual(page.total_pages, 2)
        data_sql = str(
            execute.call_args_list[1].args[0].compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertIn("LIMIT 25 OFFSET 50", data_sql)


class ManualReviewMigrationTests(unittest.TestCase):
    def test_migration_adds_audited_review_columns_and_decision_table(self):
        migration = importlib.import_module(
            "migrations.versions."
            "c9d0e1f2a3b4_add_manual_roster_review_decisions"
        )
        self.assertEqual(
            migration.down_revision,
            ("b6e7f8a9b0c1", "d4e5f6a7b8c9"),
        )

        with patch.object(migration, "op") as operation:
            migration.upgrade()
        self.assertEqual(operation.add_column.call_count, 2)
        self.assertEqual(
            operation.create_table.call_args.args[0],
            "match_roster_scan_manual_review_decisions",
        )
        columns = {
            item.name
            for item in operation.create_table.call_args.args[1:]
            if hasattr(item, "name") and hasattr(item, "type")
        }
        self.assertEqual(
            columns,
            {
                "scan_id",
                "face_index",
                "outcome",
                "player_id",
                "player_name_snapshot",
                "created_at",
            },
        )
        operation.create_foreign_key.assert_called_once_with(
            "fk_match_roster_scan_manual_reviews_resolved_by_user_id_users",
            "match_roster_scan_manual_reviews",
            "users",
            ["resolved_by_user_id"],
            ["id"],
            ondelete="RESTRICT",
        )

        with patch.object(migration, "op") as operation:
            migration.downgrade()
        operation.drop_table.assert_called_once_with(
            "match_roster_scan_manual_review_decisions"
        )
        self.assertEqual(operation.drop_column.call_count, 2)


if __name__ == "__main__":
    unittest.main()
