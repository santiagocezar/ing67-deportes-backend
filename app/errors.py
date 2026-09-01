from functools import wraps
from typing import Any, Callable

from flask import Response, jsonify, request
from pydantic import ValidationError


def error_response(
    code: str,
    message: str,
    status: int,
    *,
    details: list[dict[str, str]] | None = None,
) -> tuple[Response, int]:
    """Build the common API error response."""
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return jsonify(error=error), status


def _validation_source(location: tuple[Any, ...]) -> str:
    first_part = str(location[0]) if location else ""
    if first_part and first_part in (request.view_args or {}):
        return "path"
    return "body"


def validation_details(error: ValidationError) -> list[dict[str, str]]:
    """Return stable validation details without Pydantic input values."""
    details: list[dict[str, str]] = []
    for item in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = tuple(item.get("loc", ()))
        source = _validation_source(location)
        suffix = ".".join(str(part) for part in location)
        field = f"{source}.{suffix}" if suffix else source
        details.append(
            {
                "field": field,
                "message": str(item.get("msg", "Invalid value")),
                "type": str(item.get("type", "value_error")),
            }
        )
    return details


def validation_error_response(error: ValidationError) -> Response:
    """Translate flask-openapi3/Pydantic failures into the API error envelope."""
    details = validation_details(error)
    immutable_error = next(
        (detail for detail in details if detail["type"] == "immutable_field"),
        None,
    )
    if immutable_error:
        response, status = error_response(
            "immutable_field",
            immutable_error["message"],
            422,
            details=details,
        )
    else:
        response, status = error_response(
            "validation_error",
            "Request validation failed.",
            422,
            details=details,
        )
    response.status_code = status
    return response


def _json_object_error() -> tuple[Response, int] | None:
    if not request.is_json:
        return error_response(
            "invalid_request",
            "A JSON request body is required.",
            400,
        )

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error_response(
            "invalid_request",
            "A JSON request body is required.",
            400,
        )
    return None


def require_json_object() -> tuple[Response, int] | None:
    """Validate public JSON bodies before automatic schema validation."""
    if request.endpoint not in {"auth.signup", "auth.login"}:
        return None
    return _json_object_error()


def json_object_required(function: Callable) -> Callable:
    """Validate a protected JSON body after its authorization decorator."""

    @wraps(function)
    def wrapper(*args, **kwargs):
        invalid_body = _json_object_error()
        if invalid_body is not None:
            return invalid_body
        return function(*args, **kwargs)

    return wrapper
