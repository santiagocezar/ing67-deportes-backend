"""HTTP endpoints for protected Match roster scans and private evidence."""

from functools import wraps

from flask import current_app, jsonify, request, send_file
from flask_jwt_extended import get_jwt, get_jwt_identity
from flask_openapi3 import APIBlueprint, Tag, validate_request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge

from ..errors import error_response, json_object_required, multipart_form_required
from ..extensions import db
from ..schemas.common import ErrorResponse
from ..schemas.match_roster_scans import (
    ManualReviewCompetitionResponse,
    ManualReviewMatchResponse,
    ManualReviewScanResponse,
    ManualReviewTeamResponse,
    MatchManualReviewStatusPath,
    MatchManualReviewStatusResponse,
    MatchRosterScanFaceResponse,
    MatchRosterScanListQuery,
    MatchRosterScanListResponse,
    MatchRosterScanManualReviewDecisionResponse,
    MatchRosterScanManualReviewFinalizeRequest,
    MatchRosterScanManualReviewListItemResponse,
    MatchRosterScanManualReviewListQuery,
    MatchRosterScanManualReviewListResponse,
    MatchRosterScanManualReviewResponse,
    MatchRosterScanPaginationResponse,
    MatchRosterScanPath,
    MatchRosterScanPreviewForm,
    MatchRosterScanPreviewResponse,
    MatchRosterScanResponse,
    MatchRosterScanSanctionResponse,
    MatchRosterScanUploadForm,
    MatchTeamRosterScanPath,
)
from ..services.match_roster_scan_processing import (
    ScanImageValidationError,
    ScanProcessingUnavailableError,
    preview_match_roster_scan,
    process_match_roster_scan,
)
from ..services.match_roster_scans import (
    MatchRosterScanServiceError,
    get_match_roster_scan,
    get_match_roster_scan_group_image,
    list_match_roster_scans,
)
from ..services.match_roster_scan_manual_reviews import (
    ManualReviewDecisionInput,
    ManualReviewServiceError,
    finalize_match_roster_scan_manual_review,
    get_match_manual_review_status,
    list_match_roster_scan_manual_reviews,
)
from ..services.match_scan_photo_files import ScanImageStorageError
from ..services.player_photo_files import PhotoStorageError
from ..services.player_photos import PhotoProcessingUnavailableError
from .authorization import ACCESS_SECURITY, authenticated_user_required


ROSTER_SCANS_TAG = Tag(
    name="Roster Scans",
    description="Protected Match roster recognition and immutable results.",
)
match_roster_scans_bp = APIBlueprint(
    "match_roster_scans",
    __name__,
    abp_tags=[ROSTER_SCANS_TAG],
)

BINARY_SCHEMA = {"schema": {"type": "string", "format": "binary"}}
GROUP_IMAGE_RESPONSE = {
    "description": "The retained private PNG or JPEG group image.",
    "content": {"image/png": BINARY_SCHEMA, "image/jpeg": BINARY_SCHEMA},
}


def _form_error(field: str, message: str, error_type: str):
    return error_response(
        "validation_error",
        "Request validation failed.",
        422,
        details=[{"field": field, "message": message, "type": error_type}],
    )


def _require_scan_form(*, retention_confirmation: bool):
    """Reject repeated, file-backed, and unknown multipart inputs early."""

    def decorator(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            allowed_fields = {"photo"}
            if retention_confirmation:
                allowed_fields.add("retain_for_manual_review")
            try:
                fields = set(request.form.keys()) | set(request.files.keys())
                unexpected = sorted(fields - allowed_fields)
                if unexpected:
                    return _form_error(
                        f"form.{unexpected[0]}",
                        "Extra inputs are not permitted.",
                        "extra_forbidden",
                    )
                photo_parts = len(request.files.getlist("photo")) + len(
                    request.form.getlist("photo")
                )
                if retention_confirmation:
                    confirmation_file_parts = len(
                        request.files.getlist("retain_for_manual_review")
                    )
                    confirmation_parts = confirmation_file_parts + len(
                        request.form.getlist("retain_for_manual_review")
                    )
                else:
                    confirmation_file_parts = 0
                    confirmation_parts = 0
            except RequestEntityTooLarge:
                return _scan_image_too_large()
            if photo_parts > 1:
                return _form_error(
                    "form.photo",
                    "Exactly one photo file is required.",
                    "multiple_files",
                )
            if confirmation_parts > 1:
                return _form_error(
                    "form.retain_for_manual_review",
                    "Exactly one retention confirmation is required.",
                    "multiple_values",
                )
            if confirmation_file_parts:
                return _form_error(
                    "form.retain_for_manual_review",
                    "The retention confirmation must be a form field.",
                    "bool_type",
                )
            return function(*args, **kwargs)

        return wrapper

    return decorator


def _scan_image_too_large():
    return error_response(
        "scan_image_too_large",
        "The group image must be at most 10 MiB.",
        413,
    )


def _processing_error(error: ScanImageValidationError):
    if error.reason == "image_too_large":
        return _scan_image_too_large()
    if error.reason == "unsupported_image_format":
        return error_response(
            "unsupported_image_format",
            "Only PNG and JPEG group images are allowed.",
            415,
        )
    return error_response(
        "image_not_processable",
        "The group image cannot be processed.",
        422,
        details=[
            {
                "field": "form.photo",
                "message": error.reason,
                "type": error.reason,
            }
        ],
    )


def _service_error(error: MatchRosterScanServiceError):
    return error_response(error.code, str(error), error.status_code)


def _manual_review_error(error: ManualReviewServiceError):
    return error_response(error.code, str(error), error.status_code)


def _service_unavailable(operation: str):
    db.session.rollback()
    current_app.logger.error("Could not %s Match roster scan", operation)
    return error_response(
        "scan_processing_unavailable",
        "Roster scan processing is temporarily unavailable.",
        503,
    )


def _manual_review_unavailable(operation: str):
    db.session.rollback()
    current_app.logger.error("Could not %s Match manual review", operation)
    return error_response(
        "manual_review_unavailable",
        "Manual review is temporarily unavailable.",
        503,
    )


def _count_warning(
    *, expected_players: int, unique_recognized_players: int
) -> str | None:
    if unique_recognized_players < expected_players:
        return "recognized_player_count_below_expected"
    if unique_recognized_players > expected_players:
        return "recognized_player_count_above_expected"
    return None


def _sanction_responses(face) -> list[MatchRosterScanSanctionResponse]:
    snapshots = getattr(face, "sanction_snapshots", None)
    if snapshots is None:
        snapshots = getattr(face, "sanctions", ())
    responses = []
    for sanction in snapshots:
        title = getattr(sanction, "title_snapshot", None)
        reason = getattr(sanction, "reason_snapshot", None)
        remaining_matches = getattr(
            sanction,
            "remaining_matches_snapshot",
            None,
        )
        responses.append(
            MatchRosterScanSanctionResponse(
                sanction_id=sanction.sanction_id,
                title=title if title is not None else sanction.title,
                reason=reason if reason is not None else sanction.reason,
                remaining_matches=(
                    remaining_matches
                    if remaining_matches is not None
                    else sanction.remaining_matches
                ),
            )
        )
    return responses


def _face_responses(faces) -> list[MatchRosterScanFaceResponse]:
    return [
        MatchRosterScanFaceResponse(
            face_index=face.face_index,
            top=face.top,
            right=face.right,
            bottom=face.bottom,
            left=face.left,
            status=face.status,
            player_id=face.player_id,
            player_name=face.player_name_snapshot,
            match_percentage=face.match_percentage,
            sanctions=_sanction_responses(face),
        )
        for face in faces
    ]


def _manual_review_response_from_faces(review, faces):
    decisions = []
    if review.status == "resolved":
        decisions = [
            MatchRosterScanManualReviewDecisionResponse(
                face_index=face.face_index,
                outcome=decision.outcome,
                player_id=decision.player_id,
                player_name=decision.player_name_snapshot,
                created_at=decision.created_at,
            )
            for face in sorted(faces, key=lambda item: item.face_index)
            if (
                decision := getattr(face, "manual_review_decision", None)
            ) is not None
        ]
    return MatchRosterScanManualReviewResponse(
        status=review.status,
        expires_at=review.expires_at,
        resolved_by_user_id=getattr(review, "resolved_by_user_id", None),
        resolved_at=getattr(review, "resolved_at", None),
        decisions=decisions,
    )


def _manual_review_response(scan):
    review = getattr(scan, "manual_review", None)
    if review is None:
        return None
    return _manual_review_response_from_faces(review, scan.faces)


def _manual_review_list_item_response(item):
    return MatchRosterScanManualReviewListItemResponse(
        competition=ManualReviewCompetitionResponse(
            id=item.competition_id,
            name=item.competition_name,
        ),
        match=ManualReviewMatchResponse(
            id=item.match_id,
            starts_at=item.match_starts_at,
            ends_at=item.match_ends_at,
            status=item.match_status,
        ),
        team=ManualReviewTeamResponse(id=item.team_id, name=item.team_name),
        scan=ManualReviewScanResponse(
            id=item.scan_id,
            created_at=item.scan_created_at,
        ),
        unknown_face_count=item.unknown_face_count,
        status=item.review.status,
        expires_at=item.review.expires_at,
    )


def _scan_response(scan) -> MatchRosterScanResponse:
    return MatchRosterScanResponse(
        id=scan.id,
        match_id=scan.match_id,
        team_id=scan.team_id,
        created_by_user_id=scan.created_by_user_id,
        expected_players=scan.expected_players,
        detected_faces=scan.detected_faces,
        unique_recognized_players=scan.unique_recognized_players,
        count_warning=_count_warning(
            expected_players=scan.expected_players,
            unique_recognized_players=scan.unique_recognized_players,
        ),
        created_at=scan.created_at,
        faces=_face_responses(scan.faces),
        manual_review=_manual_review_response(scan),
    )


def _preview_response(preview) -> MatchRosterScanPreviewResponse:
    return MatchRosterScanPreviewResponse(
        match_id=preview.match_id,
        team_id=preview.team_id,
        expected_players=preview.expected_players,
        detected_faces=preview.detected_faces,
        unique_recognized_players=preview.unique_recognized_players,
        count_warning=_count_warning(
            expected_players=preview.expected_players,
            unique_recognized_players=preview.unique_recognized_players,
        ),
        faces=_face_responses(preview.faces),
    )


@match_roster_scans_bp.get(
    "/matches/<int:match_id>/teams/<int:team_id>/roster-scans",
    summary="List protected Match roster scan history",
    description=(
        "Returns immutable scan snapshots for one Match Team, newest first. "
        "Group images, file references, raw image bytes, and embeddings are "
        "never returned."
    ),
    operation_id="matchRosterScansList",
    security=ACCESS_SECURITY,
    responses={
        200: MatchRosterScanListResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@validate_request()
def get_match_roster_scan_history(
    path: MatchTeamRosterScanPath,
    query: MatchRosterScanListQuery,
):
    try:
        result = list_match_roster_scans(
            path.match_id,
            path.team_id,
            page=query.page,
            actor_user_id=int(get_jwt_identity()),
            actor_role=str(get_jwt().get("role", "")),
        )
    except MatchRosterScanServiceError as error:
        return _service_error(error)
    except (ScanImageStorageError, OSError, SQLAlchemyError):
        return _service_unavailable("list")
    payload = MatchRosterScanListResponse(
        roster_scans=[_scan_response(scan) for scan in result.scans],
        pagination=MatchRosterScanPaginationResponse(
            page=result.page,
            per_page=result.per_page,
            total_items=result.total_items,
            total_pages=result.total_pages,
        ),
    )
    return jsonify(payload.model_dump(mode="json")), 200


@match_roster_scans_bp.get(
    "/roster-scans/<int:scan_id>",
    summary="Get a protected Match roster scan",
    description=(
        "Returns one immutable scan snapshot and its safe manual-review "
        "lifecycle summary. Group images, file references, raw image bytes, "
        "and embeddings are never returned."
    ),
    operation_id="matchRosterScansGet",
    security=ACCESS_SECURITY,
    responses={
        200: MatchRosterScanResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@validate_request()
def get_roster_scan(path: MatchRosterScanPath):
    try:
        scan = get_match_roster_scan(
            path.scan_id,
            actor_user_id=int(get_jwt_identity()),
            actor_role=str(get_jwt().get("role", "")),
        )
    except MatchRosterScanServiceError as error:
        return _service_error(error)
    except (ScanImageStorageError, OSError, SQLAlchemyError):
        return _service_unavailable("get")
    return jsonify(_scan_response(scan).model_dump(mode="json")), 200


@match_roster_scans_bp.put(
    "/roster-scans/<int:scan_id>/manual-review",
    summary="Finalize one historical manual roster review",
    description=(
        "Allows only the Match's assigned referee to submit one immutable, "
        "complete decision for every automated unknown face. A membership "
        "choice is checked against the frozen Competition roster."
    ),
    operation_id="matchRosterScansFinalizeManualReview",
    security=ACCESS_SECURITY,
    responses={
        200: MatchRosterScanManualReviewResponse,
        400: ErrorResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@json_object_required
@validate_request()
def put_roster_scan_manual_review(
    path: MatchRosterScanPath,
    body: MatchRosterScanManualReviewFinalizeRequest,
):
    try:
        review = finalize_match_roster_scan_manual_review(
            path.scan_id,
            [
                ManualReviewDecisionInput(
                    face_index=decision.face_index,
                    outcome=decision.outcome,
                    player_id=decision.player_id,
                )
                for decision in body.decisions
            ],
            actor_user_id=int(get_jwt_identity()),
            actor_role=str(get_jwt().get("role", "")),
        )
    except ManualReviewServiceError as error:
        return _manual_review_error(error)
    except (ScanImageStorageError, OSError, SQLAlchemyError):
        return _manual_review_unavailable("finalize")
    payload = _manual_review_response_from_faces(review, review.scan.faces)
    return jsonify(payload.model_dump(mode="json")), 200


@match_roster_scans_bp.get(
    "/manual-reviews",
    summary="List historical manual roster reviews",
    description=(
        "Returns safe review metadata only. Administrators can read all "
        "reviews; referees can read reviews for their assigned Matches."
    ),
    operation_id="matchRosterScansManualReviewsList",
    security=ACCESS_SECURITY,
    responses={
        200: MatchRosterScanManualReviewListResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@validate_request()
def get_manual_reviews(query: MatchRosterScanManualReviewListQuery):
    try:
        result = list_match_roster_scan_manual_reviews(
            match_id=query.match_id,
            sport_id=query.sport_id,
            status=query.status,
            sort=query.sort,
            page=query.page,
            actor_user_id=int(get_jwt_identity()),
            actor_role=str(get_jwt().get("role", "")),
        )
    except ManualReviewServiceError as error:
        return _manual_review_error(error)
    except (ScanImageStorageError, OSError, SQLAlchemyError):
        return _manual_review_unavailable("list")
    payload = MatchRosterScanManualReviewListResponse(
        manual_reviews=[
            _manual_review_list_item_response(item) for item in result.reviews
        ],
        pagination=MatchRosterScanPaginationResponse(
            page=result.page,
            per_page=result.per_page,
            total_items=result.total_items,
            total_pages=result.total_pages,
        ),
    )
    return jsonify(payload.model_dump(mode="json")), 200


@match_roster_scans_bp.get(
    "/matches/<int:match_id>/manual-review-status",
    summary="Read the derived Match biometric-audit state",
    description=(
        "Returns whether at least one unexpired pending manual review remains "
        "for the Match, with safe lifecycle counts."
    ),
    operation_id="matchRosterScansManualReviewStatus",
    security=ACCESS_SECURITY,
    responses={
        200: MatchManualReviewStatusResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@validate_request()
def get_match_manual_review_status_by_id(path: MatchManualReviewStatusPath):
    try:
        review_status = get_match_manual_review_status(
            path.match_id,
            actor_user_id=int(get_jwt_identity()),
            actor_role=str(get_jwt().get("role", "")),
        )
    except ManualReviewServiceError as error:
        return _manual_review_error(error)
    except (ScanImageStorageError, OSError, SQLAlchemyError):
        return _manual_review_unavailable("read status")
    payload = MatchManualReviewStatusResponse(
        match_id=review_status.match_id,
        requires_biometric_audit=review_status.requires_biometric_audit,
        pending_review_count=review_status.pending_review_count,
        resolved_review_count=review_status.resolved_review_count,
        expired_review_count=review_status.expired_review_count,
    )
    return jsonify(payload.model_dump(mode="json")), 200


@match_roster_scans_bp.get(
    "/roster-scans/<int:scan_id>/group-image",
    summary="Download retained private group-image evidence",
    description=(
        "Returns the original private PNG or JPEG only to an administrator or "
        "the assigned referee while its manual review remains pending. The "
        "response has no URL, filename, validators, range support, or cache."
    ),
    operation_id="matchRosterScansGetGroupImage",
    security=ACCESS_SECURITY,
    responses={
        200: GROUP_IMAGE_RESPONSE,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@validate_request()
def get_roster_scan_group_image(path: MatchRosterScanPath):
    try:
        group_image = get_match_roster_scan_group_image(
            path.scan_id,
            actor_user_id=int(get_jwt_identity()),
            actor_role=str(get_jwt().get("role", "")),
        )
        response = send_file(
            group_image.path,
            mimetype=group_image.content_type,
            conditional=False,
            etag=False,
        )
    except MatchRosterScanServiceError as error:
        return _service_error(error)
    except (ScanImageStorageError, OSError, SQLAlchemyError):
        return _service_unavailable("get evidence")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    for header in (
        "Accept-Ranges",
        "Content-Disposition",
        "ETag",
        "Last-Modified",
    ):
        response.headers.pop(header, None)
    return response


@match_roster_scans_bp.post(
    "/matches/<int:match_id>/teams/<int:team_id>/roster-scans/preview",
    summary="Preview a non-persistent Match roster scan",
    description=(
        "Runs the approved group-image validation and recognition process "
        "without creating scan history, a manual review, a file reference, or "
        "a stored image."
    ),
    operation_id="matchRosterScansPreview",
    security=ACCESS_SECURITY,
    responses={
        200: MatchRosterScanPreviewResponse,
        400: ErrorResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        413: ErrorResponse,
        415: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@multipart_form_required
@_require_scan_form(retention_confirmation=False)
@validate_request()
def post_match_roster_scan_preview(
    path: MatchTeamRosterScanPath,
    form: MatchRosterScanPreviewForm,
):
    try:
        preview = preview_match_roster_scan(
            path.match_id,
            path.team_id,
            actor_user_id=int(get_jwt_identity()),
            actor_role=str(get_jwt().get("role", "")),
            content=form.photo.read(),
        )
    except ScanImageValidationError as error:
        return _processing_error(error)
    except MatchRosterScanServiceError as error:
        return _service_error(error)
    except (ScanProcessingUnavailableError, PhotoProcessingUnavailableError):
        return _service_unavailable("preview")
    except (PhotoStorageError, ScanImageStorageError, OSError, SQLAlchemyError):
        return _service_unavailable("preview")
    return jsonify(_preview_response(preview).model_dump(mode="json")), 200


@match_roster_scans_bp.post(
    "/matches/<int:match_id>/teams/<int:team_id>/roster-scans",
    summary="Persist a confirmed Match roster scan",
    description=(
        "Reprocesses a group image after explicit retention confirmation. "
        "Embeddings are never stored or returned. A private group image and "
        "pending manual review are created only when the final result has an "
        "unknown face."
    ),
    operation_id="matchRosterScansProcess",
    security=ACCESS_SECURITY,
    responses={
        201: MatchRosterScanResponse,
        400: ErrorResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        413: ErrorResponse,
        415: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@multipart_form_required
@_require_scan_form(retention_confirmation=True)
@validate_request()
def post_match_roster_scan(
    path: MatchTeamRosterScanPath,
    form: MatchRosterScanUploadForm,
):
    try:
        scan = process_match_roster_scan(
            path.match_id,
            path.team_id,
            actor_user_id=int(get_jwt_identity()),
            actor_role=str(get_jwt().get("role", "")),
            content=form.photo.read(),
            retain_for_manual_review=form.retain_for_manual_review,
        )
    except ScanImageValidationError as error:
        return _processing_error(error)
    except MatchRosterScanServiceError as error:
        return _service_error(error)
    except (ScanProcessingUnavailableError, PhotoProcessingUnavailableError):
        return _service_unavailable("process")
    except (PhotoStorageError, ScanImageStorageError, OSError, SQLAlchemyError):
        return _service_unavailable("store")
    return jsonify(_scan_response(scan).model_dump(mode="json")), 201
