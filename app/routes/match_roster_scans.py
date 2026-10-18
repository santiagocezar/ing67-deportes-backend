"""HTTP endpoint for protected, immutable Match roster scans."""

from functools import wraps

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity
from flask_openapi3 import APIBlueprint, Tag, validate_request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge

from ..errors import error_response, multipart_form_required
from ..extensions import db
from ..schemas.common import ErrorResponse
from ..schemas.match_roster_scans import (
    MatchRosterScanFaceResponse,
    MatchRosterScanListQuery,
    MatchRosterScanListResponse,
    MatchRosterScanPaginationResponse,
    MatchRosterScanPath,
    MatchRosterScanResponse,
    MatchRosterScanSanctionResponse,
    MatchRosterScanUploadForm,
    MatchTeamRosterScanPath,
)
from ..services.match_roster_scan_processing import (
    ScanImageValidationError,
    ScanProcessingUnavailableError,
    process_match_roster_scan,
)
from ..services.match_roster_scans import (
    MatchRosterScanServiceError,
    get_match_roster_scan,
    list_match_roster_scans,
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


def _form_error(field: str, message: str, error_type: str):
    return error_response(
        "validation_error",
        "Request validation failed.",
        422,
        details=[{"field": field, "message": message, "type": error_type}],
    )


def _require_exactly_one_photo_part(function):
    """Reject repeated and unknown multipart parts before Pydantic selects one."""

    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            fields = set(request.form.keys()) | set(request.files.keys())
            unexpected = sorted(fields - {"photo"})
            if unexpected:
                return _form_error(
                    f"form.{unexpected[0]}",
                    "Extra inputs are not permitted.",
                    "extra_forbidden",
                )
            photo_parts = len(request.files.getlist("photo")) + len(
                request.form.getlist("photo")
            )
        except RequestEntityTooLarge:
            return _scan_image_too_large()
        if photo_parts > 1:
            return _form_error(
                "form.photo",
                "Exactly one photo file is required.",
                "multiple_files",
            )
        return function(*args, **kwargs)

    return wrapper


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


def _service_unavailable(operation: str):
    db.session.rollback()
    current_app.logger.error("Could not %s Match roster scan", operation)
    return error_response(
        "scan_processing_unavailable",
        "Roster scan processing is temporarily unavailable.",
        503,
    )


def _scan_response(scan) -> MatchRosterScanResponse:
    if scan.unique_recognized_players < scan.expected_players:
        count_warning = "recognized_player_count_below_expected"
    elif scan.unique_recognized_players > scan.expected_players:
        count_warning = "recognized_player_count_above_expected"
    else:
        count_warning = None
    return MatchRosterScanResponse(
        id=scan.id,
        match_id=scan.match_id,
        team_id=scan.team_id,
        created_by_user_id=scan.created_by_user_id,
        expected_players=scan.expected_players,
        detected_faces=scan.detected_faces,
        unique_recognized_players=scan.unique_recognized_players,
        count_warning=count_warning,
        created_at=scan.created_at,
        faces=[
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
                sanctions=[
                    MatchRosterScanSanctionResponse(
                        sanction_id=sanction.sanction_id,
                        title=sanction.title_snapshot,
                        reason=sanction.reason_snapshot,
                        remaining_matches=(
                            sanction.remaining_matches_snapshot
                        ),
                    )
                    for sanction in face.sanction_snapshots
                ],
            )
            for face in scan.faces
        ],
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
    except SQLAlchemyError:
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
        "Returns one immutable scan snapshot. Group images, file references, "
        "raw image bytes, and embeddings are never returned."
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
    except SQLAlchemyError:
        return _service_unavailable("get")
    return jsonify(_scan_response(scan).model_dump(mode="json")), 200


@match_roster_scans_bp.post(
    "/matches/<int:match_id>/teams/<int:team_id>/roster-scans",
    summary="Process a protected Match roster scan",
    description=(
        "Detects and compares faces only against enabled current members of "
        "the selected Match Team. Embeddings are never stored or returned. "
        "A group image is retained privately only if a result is unknown."
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
@_require_exactly_one_photo_part
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
        )
    except ScanImageValidationError as error:
        return _processing_error(error)
    except MatchRosterScanServiceError as error:
        return _service_error(error)
    except (ScanProcessingUnavailableError, PhotoProcessingUnavailableError):
        return _service_unavailable("process")
    except (PhotoStorageError, ScanImageStorageError, OSError):
        return _service_unavailable("store")
    except SQLAlchemyError:
        return _service_unavailable("store")
    return jsonify(_scan_response(scan).model_dump(mode="json")), 201
