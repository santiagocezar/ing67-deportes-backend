from flask import current_app, jsonify
from flask_jwt_extended import get_jwt, get_jwt_identity
from flask_openapi3 import APIBlueprint, Tag, validate_request
from sqlalchemy.exc import SQLAlchemyError

from ..errors import error_response, json_object_required
from ..extensions import db
from ..schemas.common import ErrorResponse
from ..schemas.sanctions import (
    MatchPlayerSanctionPath,
    PlayerSanctionCreateRequest,
    PlayerSanctionResponse,
    SanctionCompetitionSummary,
    SanctionMatchSummary,
    SanctionPlayerSummary,
    SanctionTeamSummary,
    SanctionUserSummary,
)
from ..services.sanctions import (
    SanctionServiceError,
    create_match_origin_sanction,
)
from .authorization import ACCESS_SECURITY, authenticated_user_required


SANCTIONS_TAG = Tag(
    name="Player Sanctions",
    description="Create global Player suspensions from in-progress Matches.",
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
        db.session.rollback()
        current_app.logger.error("Could not create Player sanction")
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )
    payload = sanction_response(sanction)
    return jsonify(payload.model_dump(mode="json")), 201
