from flask import Response, jsonify


def error_response(code: str, message: str, status: int) -> tuple[Response, int]:
    """Build the common API error response."""
    return jsonify(error={"code": code, "message": message}), status
