from flask import current_app, jsonify
from flask_openapi3 import APIBlueprint, Tag, validate_request
from sqlalchemy.exc import SQLAlchemyError

from ..errors import error_response
from ..extensions import db
from ..schemas.common import ErrorResponse
from ..schemas.referees import (
    RefereeListQuery,
    RefereeListResponse,
    RefereeLookupResponse,
    RefereePaginationResponse,
)
from ..services.referees import list_referees
from .authorization import ACCESS_SECURITY, administrator_required


REFEREES_TAG = Tag(
    name="Referees",
    description="Administrator-only referee lookup.",
)
referees_bp = APIBlueprint(
    "referees",
    __name__,
    url_prefix="/referees",
    abp_tags=[REFEREES_TAG],
)


@referees_bp.get(
    "",
    summary="List referees",
    operation_id="refereesList",
    security=ACCESS_SECURITY,
    responses={
        200: RefereeListResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def get_referees(query: RefereeListQuery):
    try:
        result = list_referees(query)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.error("Could not list referees")
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )
    payload = RefereeListResponse(
        referees=[
            RefereeLookupResponse(
                id=referee.id,
                name=referee.name,
                email=referee.email,
            )
            for referee in result.referees
        ],
        pagination=RefereePaginationResponse(
            page=result.page,
            per_page=result.per_page,
            total_items=result.total_items,
            total_pages=result.total_pages,
        ),
    )
    return jsonify(payload.model_dump(mode="json")), 200
