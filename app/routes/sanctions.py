from flask import current_app, jsonify
from flask_jwt_extended import get_jwt, get_jwt_identity
from flask_openapi3 import APIBlueprint, Tag, validate_request
from sqlalchemy.exc import SQLAlchemyError

from ..errors import error_response, json_object_required
from ..extensions import db
from ..schemas.common import ErrorResponse
from ..schemas.sanctions import (
    MatchPlayerSanctionPath,
    PlayerSanctionListPath,
    PlayerSanctionListQuery,
    PlayerSanctionListResponse,
    PlayerSanctionPaginationResponse,
    PlayerSanctionPath,
    PlayerSanctionCreateRequest,
    PlayerSanctionResponse,
    PlayerSanctionUpdateRequest,
    SanctionCompetitionSummary,
    SanctionMatchSummary,
    SanctionPlayerSummary,
    SanctionTeamSummary,
    SanctionUserSummary,
)
from ..services.sanctions import (
    SanctionServiceError,
    annul_sanction,
    create_match_origin_sanction,
    get_reconciled_sanction,
    list_player_sanctions,
    update_sanction,
)
from .authorization import (
    ACCESS_SECURITY,
    administrator_required,
    authenticated_user_required,
)


SANCTIONS_TAG = Tag(
    name="Player Sanctions",
    description="Create, query, and manage global Player suspensions.",
)
sanctions_bp = APIBlueprint(
    "sanctions", __name__, abp_tags=[SANCTIONS_TAG]
)


def sanction_response(sanction) -> PlayerSanctionResponse:
    source_match = sanction.source_match
    return PlayerSanctionResponse(
        id=sanction.id,
        player=SanctionPlayerSummary(
            id=sanction.player.id, name=sanction.player.name
        ),
        title=sanction.title,
        reason=sanction.reason,
        suspension_matches=sanction.suspension_matches,
        remaining_matches=sanction.remaining_matches,
        status=sanction.status,
        source_match=SanctionMatchSummary(
            id=source_match.id,
            starts_at=source_match.starts_at,
            ends_at=source_match.ends_at,
        ),
        source_competition=SanctionCompetitionSummary(
            id=source_match.competition.id,
            name=source_match.competition.name,
        ),
        frozen_teams=[
            SanctionTeamSummary(id=entry.team.id, name=entry.team.name)
            for entry in sorted(
                sanction.frozen_team_entries, key=lambda entry: entry.team_id
            )
        ],
        creator=SanctionUserSummary(
            id=sanction.created_by.id, name=sanction.created_by.name
        ),
        updater=SanctionUserSummary(
            id=sanction.updated_by.id, name=sanction.updated_by.name
        ),
        annuller=(
            SanctionUserSummary(
                id=sanction.annulled_by.id, name=sanction.annulled_by.name
            )
            if sanction.annulled_by is not None
            else None
        ),
        effective_at=sanction.effective_at,
        created_at=sanction.created_at,
        updated_at=sanction.updated_at,
        annulled_at=sanction.annulled_at,
        served_at=sanction.served_at,
    )


def _service_error(error: SanctionServiceError):
    return error_response(error.code, str(error), error.status_code)


def _database_unavailable(operation: str):
    db.session.rollback()
    current_app.logger.error("Could not %s Player sanction", operation)
    return error_response(
        "service_unavailable",
        "The database is temporarily unavailable.",
        503,
    )


@sanctions_bp.post(
    "/matches/<int:match_id>/players/<int:player_id>/sanctions",
    summary="Create a global Player sanction from a Match",
    operation_id="playerSanctionsCreateFromMatch",
    security=ACCESS_SECURITY,
    responses={
        201: PlayerSanctionResponse,
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
def post_match_player_sanction(
    path: MatchPlayerSanctionPath,
    body: PlayerSanctionCreateRequest,
):
    try:
        sanction = create_match_origin_sanction(
            path.match_id,
            path.player_id,
            body,
            actor_user_id=int(get_jwt_identity()),
            actor_role=str(get_jwt().get("role", "")),
        )
    except SanctionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("create")
    payload = sanction_response(sanction)
    return jsonify(payload.model_dump(mode="json")), 201


@sanctions_bp.get(
    "/players/<int:player_id>/sanctions",
    summary="List a Player's sanctions",
    description=(
        "Returns a reconciled, filtered, paginated sanction history. Pages "
        "contain at most 25 records."
    ),
    operation_id="playerSanctionsList",
    security=ACCESS_SECURITY,
    responses={
        200: PlayerSanctionListResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@validate_request()
def get_player_sanctions(
    path: PlayerSanctionListPath,
    query: PlayerSanctionListQuery,
):
    try:
        result = list_player_sanctions(path.player_id, query)
    except SanctionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("list")
    payload = PlayerSanctionListResponse(
        sanctions=[sanction_response(sanction) for sanction in result.sanctions],
        pagination=PlayerSanctionPaginationResponse(
            page=result.page,
            per_page=result.per_page,
            total_items=result.total_items,
            total_pages=result.total_pages,
        ),
    )
    return jsonify(payload.model_dump(mode="json")), 200


@sanctions_bp.get(
    "/sanctions/<int:sanction_id>",
    summary="Get a Player sanction",
    description="Returns one reconciled Player sanction.",
    operation_id="playerSanctionsGet",
    security=ACCESS_SECURITY,
    responses={
        200: PlayerSanctionResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@validate_request()
def get_player_sanction(path: PlayerSanctionPath):
    try:
        sanction = get_reconciled_sanction(path.sanction_id)
    except SanctionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("get")
    payload = sanction_response(sanction)
    return jsonify(payload.model_dump(mode="json")), 200


@sanctions_bp.put(
    "/sanctions/<int:sanction_id>",
    summary="Update an active Player sanction",
    description=(
        "Administrators may update any active sanction. Its creating referee "
        "may update it only while the source Match is in progress."
    ),
    operation_id="playerSanctionsUpdate",
    security=ACCESS_SECURITY,
    responses={
        200: PlayerSanctionResponse,
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
def put_player_sanction(
    path: PlayerSanctionPath,
    body: PlayerSanctionUpdateRequest,
):
    try:
        sanction = update_sanction(
            path.sanction_id,
            body,
            actor_user_id=int(get_jwt_identity()),
            actor_role=str(get_jwt().get("role", "")),
        )
    except SanctionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("update")
    payload = sanction_response(sanction)
    return jsonify(payload.model_dump(mode="json")), 200


@sanctions_bp.patch(
    "/sanctions/<int:sanction_id>/annul",
    summary="Annul an active Player sanction",
    description="Administrators may annul an active sanction without deleting history.",
    operation_id="playerSanctionsAnnul",
    security=ACCESS_SECURITY,
    responses={
        200: PlayerSanctionResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def patch_player_sanction_annul(path: PlayerSanctionPath):
    try:
        sanction = annul_sanction(
            path.sanction_id,
            actor_user_id=int(get_jwt_identity()),
        )
    except SanctionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("annul")
    payload = sanction_response(sanction)
    return jsonify(payload.model_dump(mode="json")), 200
