from flask import current_app, jsonify
from flask_openapi3 import APIBlueprint, Tag, validate_request
from sqlalchemy.exc import SQLAlchemyError

from ..errors import error_response, json_object_required
from ..extensions import db
from ..schemas.common import ErrorResponse
from ..schemas.competitions import (
    CompetitionTeamResponse,
    RefereeSummaryResponse,
    RosterPlayerResponse,
)
from ..schemas.matches import (
    CompetitionMatchesPath,
    MatchCompetitionResponse,
    MatchListResponse,
    MatchPath,
    MatchResponse,
    MatchUpdateRequest,
)
from ..services.competition_state import CompetitionServiceError
from ..services.matches import (
    get_match,
    list_matches,
    randomize_matches,
    update_match,
)
from .authorization import (
    ACCESS_SECURITY,
    administrator_required,
    authenticated_user_required,
)


MATCHES_TAG = Tag(
    name="Matches",
    description="Random Competition draws and Match scheduling.",
)
matches_bp = APIBlueprint(
    "matches",
    __name__,
    abp_tags=[MATCHES_TAG],
)


def _team_response(entry) -> CompetitionTeamResponse:
    return CompetitionTeamResponse(
        id=entry.team.id,
        name=entry.team.name,
        gender_category=entry.team.gender_category,
        is_enabled=entry.team.is_enabled,
        roster=[
            RosterPlayerResponse(
                id=item.player.id,
                name=item.player.name,
                gender=item.player.gender,
                is_enabled=item.player.is_enabled,
            )
            for item in entry.roster_entries
        ],
    )


def _match_response(match) -> MatchResponse:
    entries = {
        entry.team_id: entry for entry in match.competition.team_entries
    }
    return MatchResponse(
        id=match.id,
        competition=MatchCompetitionResponse(
            id=match.competition.id,
            name=match.competition.name,
        ),
        round_number=match.round_number,
        starts_at=match.starts_at,
        ends_at=match.ends_at,
        status=match.status,
        referee=RefereeSummaryResponse(
            id=match.referee.id,
            name=match.referee.name,
        ),
        team_1=_team_response(entries[match.team_1_id]),
        team_2=_team_response(entries[match.team_2_id]),
    )


def _service_error(error: CompetitionServiceError):
    return error_response(error.code, str(error), error.status_code)


def _database_unavailable(operation: str):
    db.session.rollback()
    current_app.logger.error("Could not %s Match data", operation)
    return error_response(
        "service_unavailable",
        "The database is temporarily unavailable.",
        503,
    )


@matches_bp.post(
    "/competitions/<int:competition_id>/matches/randomize",
    summary="Generate or replace a random Match draw",
    operation_id="matchesRandomize",
    security=ACCESS_SECURITY,
    responses={
        200: MatchListResponse,
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
def post_random_draw(path: CompetitionMatchesPath):
    try:
        matches = randomize_matches(path.competition_id)
    except CompetitionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("randomize")
    payload = MatchListResponse(matches=[_match_response(item) for item in matches])
    return jsonify(payload.model_dump(mode="json")), 200


@matches_bp.get(
    "/competitions/<int:competition_id>/matches",
    summary="List Competition Matches",
    operation_id="matchesList",
    security=ACCESS_SECURITY,
    responses={
        200: MatchListResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@validate_request()
def get_competition_matches(path: CompetitionMatchesPath):
    try:
        matches = list_matches(path.competition_id)
    except CompetitionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("list")
    payload = MatchListResponse(matches=[_match_response(item) for item in matches])
    return jsonify(payload.model_dump(mode="json")), 200


@matches_bp.get(
    "/matches/<int:match_id>",
    summary="Get a Match",
    operation_id="matchesGet",
    security=ACCESS_SECURITY,
    responses={
        200: MatchResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@authenticated_user_required
@validate_request()
def get_match_by_id(path: MatchPath):
    try:
        match = get_match(path.match_id)
    except CompetitionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("get")
    payload = _match_response(match)
    return jsonify(payload.model_dump(mode="json")), 200


@matches_bp.put(
    "/matches/<int:match_id>",
    summary="Schedule or reschedule a Match",
    operation_id="matchesUpdate",
    security=ACCESS_SECURITY,
    responses={
        200: MatchResponse,
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
def put_match(path: MatchPath, body: MatchUpdateRequest):
    try:
        match = update_match(path.match_id, body)
    except CompetitionServiceError as error:
        return _service_error(error)
    except SQLAlchemyError:
        return _database_unavailable("update")
    payload = _match_response(match)
    return jsonify(payload.model_dump(mode="json")), 200
