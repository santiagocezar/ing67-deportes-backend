from flask import current_app, jsonify
from flask_openapi3 import APIBlueprint, Tag, validate_request
from sqlalchemy.exc import SQLAlchemyError

from ..errors import error_response, json_object_required
from ..extensions import db
from ..schemas.common import ErrorResponse
from ..schemas.competitions import (
    CompetitionCreateRequest,
    CompetitionDetailResponse,
    CompetitionListQuery,
    CompetitionListResponse,
    CompetitionPaginationResponse,
    CompetitionPath,
    CompetitionSummaryResponse,
    CompetitionTeamResponse,
    ParticipantsRequest,
    RefereeSummaryResponse,
    RosterPlayerResponse,
    CompetitionUpdateRequest,
)
from ..services.competition_state import CompetitionServiceError
from ..services.competitions import (
    create_competition,
    get_competition,
    list_competitions,
    replace_participants,
    set_competition_enabled,
    update_competition,
)
from .authorization import (
    ACCESS_SECURITY,
    administrator_required,
    authenticated_user_required,
)


COMPETITIONS_TAG = Tag(
    name="Competitions",
    description="Competition registration and lifecycle management.",
)
competitions_bp = APIBlueprint(
    "competitions",
    __name__,
    url_prefix="/competitions",
    abp_tags=[COMPETITIONS_TAG],
)


def _team_response(entry) -> CompetitionTeamResponse:
    return CompetitionTeamResponse(
        id=entry.team.id,
        name=entry.team.name,
        gender_category=entry.team.gender_category,
        is_enabled=entry.team.is_enabled,
        roster=[
            RosterPlayerResponse(
                id=roster.player.id,
                name=roster.player.name,
                gender=roster.player.gender,
                is_enabled=roster.player.is_enabled,
            )
            for roster in entry.roster_entries
        ],
    )


def _summary_response(competition) -> CompetitionSummaryResponse:
    return CompetitionSummaryResponse(
        id=competition.id,
        name=competition.name,
        sport=competition.sport,
        gender=competition.gender,
        team_count=competition.team_count,
        starts_at=competition.starts_at,
        ends_at=competition.ends_at,
        status=competition.status,
        is_enabled=competition.is_enabled,
        created_at=competition.created_at,
        disabled_at=competition.disabled_at,
    )


def competition_detail_response(competition) -> CompetitionDetailResponse:
    summary = _summary_response(competition).model_dump()
    return CompetitionDetailResponse(
        **summary,
        teams=[_team_response(entry) for entry in competition.team_entries],
        referees=[
            RefereeSummaryResponse(
                id=entry.referee.id,
                name=entry.referee.name,
            )
            for entry in competition.referee_entries
        ],
    )


def _service_error(error: CompetitionServiceError):
    return error_response(error.code, str(error), error.status_code)


def _database_unavailable(operation: str):
    db.session.rollback()
    current_app.logger.error("Could not %s Competition data", operation)
    return error_response(
        "service_unavailable",
        "The database is temporarily unavailable.",
        503,
    )


@competitions_bp.post(
    "",
    summary="Create a Competition",
    operation_id="competitionsCreate",
    security=ACCESS_SECURITY,
    responses={
        201: CompetitionDetailResponse,
        400: ErrorResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@json_object_required
@validate_request()
def post_competition(body: CompetitionCreateRequest):
    try:
        competition = create_competition(body)
    except CompetitionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("create")
    payload = competition_detail_response(competition)
    return jsonify(payload.model_dump(mode="json")), 201


@competitions_bp.get(
    "",
    summary="List Competitions",
    operation_id="competitionsList",
    security=ACCESS_SECURITY,
    responses={
        200: CompetitionListResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@validate_request()
def get_competitions(query: CompetitionListQuery):
    try:
        result = list_competitions(query)
    except CompetitionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("list")
    payload = CompetitionListResponse(
        competitions=[
            _summary_response(competition)
            for competition in result.competitions
        ],
        pagination=CompetitionPaginationResponse(
            page=result.page,
            per_page=result.per_page,
            total_items=result.total_items,
            total_pages=result.total_pages,
        ),
    )
    return jsonify(payload.model_dump(mode="json")), 200


@competitions_bp.get(
    "/<int:competition_id>",
    summary="Get a Competition",
    operation_id="competitionsGet",
    security=ACCESS_SECURITY,
    responses={
        200: CompetitionDetailResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@validate_request()
def get_competition_by_id(path: CompetitionPath):
    try:
        competition = get_competition(path.competition_id)
    except CompetitionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("get")
    payload = competition_detail_response(competition)
    return jsonify(payload.model_dump(mode="json")), 200


@competitions_bp.put(
    "/<int:competition_id>",
    summary="Update a Competition",
    operation_id="competitionsUpdate",
    security=ACCESS_SECURITY,
    responses={
        200: CompetitionDetailResponse,
        400: ErrorResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@json_object_required
@validate_request()
def put_competition(
    path: CompetitionPath,
    body: CompetitionUpdateRequest,
):
    try:
        competition = update_competition(path.competition_id, body)
    except CompetitionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("update")
    payload = competition_detail_response(competition)
    return jsonify(payload.model_dump(mode="json")), 200


@competitions_bp.put(
    "/<int:competition_id>/participants",
    summary="Correct Competition participants",
    operation_id="competitionsReplaceParticipants",
    security=ACCESS_SECURITY,
    responses={
        200: CompetitionDetailResponse,
        400: ErrorResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@json_object_required
@validate_request()
def put_competition_participants(
    path: CompetitionPath,
    body: ParticipantsRequest,
):
    try:
        competition = replace_participants(path.competition_id, body)
    except CompetitionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("replace participants")
    payload = competition_detail_response(competition)
    return jsonify(payload.model_dump(mode="json")), 200


def _set_competition_state(competition_id: int, *, enabled: bool):
    try:
        competition = set_competition_enabled(
            competition_id, enabled=enabled
        )
    except CompetitionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        operation = "enable" if enabled else "disable"
        return _database_unavailable(operation)
    payload = competition_detail_response(competition)
    return jsonify(payload.model_dump(mode="json")), 200


@competitions_bp.patch(
    "/<int:competition_id>/disable",
    summary="Disable a Competition",
    operation_id="competitionsDisable",
    security=ACCESS_SECURITY,
    responses={
        200: CompetitionDetailResponse,
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
def disable_competition(path: CompetitionPath):
    return _set_competition_state(path.competition_id, enabled=False)


@competitions_bp.patch(
    "/<int:competition_id>/enable",
    summary="Enable a Competition",
    operation_id="competitionsEnable",
    security=ACCESS_SECURITY,
    responses={
        200: CompetitionDetailResponse,
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
def enable_competition(path: CompetitionPath):
    return _set_competition_state(path.competition_id, enabled=True)
