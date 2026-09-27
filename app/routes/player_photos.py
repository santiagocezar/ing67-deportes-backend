from functools import wraps

from flask import current_app, jsonify, request, send_file
from flask_openapi3 import APIBlueprint, validate_request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge

from ..errors import error_response, multipart_form_required
from ..schemas.common import ErrorResponse
from ..schemas.player_photos import (
    PlayerPhotoListResponse,
    PlayerPhotoPath,
    PlayerPhotoResponse,
    PlayerPhotoUploadForm,
)
from ..schemas.players import PlayerPath
from ..services.player_photos import (
    MAX_PLAYER_PHOTO_BYTES,
    PhotoTooLargeError,
    PhotoValidationError,
    PlayerPhotoNotFoundError,
    UnsupportedPhotoFormatError,
    add_player_photo,
    get_player_photo_file,
    list_player_photos,
)
from ..services.players import PlayerDisabledError, PlayerNotFoundError
from .authorization import ACCESS_SECURITY, administrator_required
from .players import PLAYERS_TAG


# Room for the multipart boundary and part headers around a single photo.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
BINARY_SCHEMA = {"schema": {"type": "string", "format": "binary"}}
PHOTO_IMAGE_RESPONSE = {
    "description": "The stored PNG or JPG image.",
    "content": {"image/png": BINARY_SCHEMA, "image/jpeg": BINARY_SCHEMA},
}

player_photos_bp = APIBlueprint(
    "player_photos",
    __name__,
    url_prefix="/players",
    abp_tags=[PLAYERS_TAG],
)


def _photo_response(photo) -> PlayerPhotoResponse:
    return PlayerPhotoResponse.model_validate(photo)


def _database_unavailable(operation: str):
    current_app.logger.error("Could not %s Player photo", operation)
    return error_response(
        "service_unavailable",
        "The database is temporarily unavailable.",
        503,
    )


def _photo_error(error: PhotoValidationError):
    if isinstance(error, PhotoTooLargeError):
        return error_response("photo_too_large", str(error), 413)
    if isinstance(error, UnsupportedPhotoFormatError):
        return error_response("unsupported_photo_format", str(error), 415)
    return error_response("invalid_photo", str(error), 422)


def _photo_upload_size_limited(function):
    """Parse the multipart body under a limit sized for one photo."""

    @wraps(function)
    def wrapper(*args, **kwargs):
        request.max_content_length = (
            MAX_PLAYER_PHOTO_BYTES + MULTIPART_OVERHEAD_BYTES
        )
        try:
            request.files  # Parse now so an oversized body gets a JSON error.
        except RequestEntityTooLarge:
            return _photo_error(PhotoTooLargeError())
        return function(*args, **kwargs)

    return wrapper


@player_photos_bp.post(
    "/<int:player_id>/photos",
    summary="Upload a Player photo",
    description=(
        "Stores one PNG or JPG image of at most 5 MB as a base photo for "
        "the Player's facial recognition. Rejected files are never persisted."
    ),
    operation_id="playerPhotosUpload",
    security=ACCESS_SECURITY,
    responses={
        201: PlayerPhotoResponse,
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
@administrator_required
@multipart_form_required
@_photo_upload_size_limited
@validate_request()
def post_player_photo(path: PlayerPath, form: PlayerPhotoUploadForm):
    try:
        photo = add_player_photo(
            path.player_id,
            file_name=form.photo.filename,
            content=form.photo.read(),
        )
    except PhotoValidationError as error:
        return _photo_error(error)
    except PlayerNotFoundError as error:
        return error_response("player_not_found", str(error), 404)
    except PlayerDisabledError as error:
        return error_response("player_disabled", str(error), 409)
    except SQLAlchemyError:
        return _database_unavailable("store")
    except OSError:
        current_app.logger.error("Could not write a Player photo file")
        return error_response(
            "photo_storage_unavailable",
            "The photo storage is temporarily unavailable.",
            503,
        )

    payload = _photo_response(photo)
    return jsonify(payload.model_dump(mode="json")), 201


@player_photos_bp.get(
    "/<int:player_id>/photos",
    summary="List Player photos",
    description=(
        "Returns the base photos of an enabled or disabled Player in upload "
        "order. Each image is downloaded from its own endpoint."
    ),
    operation_id="playerPhotosList",
    security=ACCESS_SECURITY,
    responses={
        200: PlayerPhotoListResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def get_player_photos(path: PlayerPath):
    try:
        photos = list_player_photos(path.player_id)
    except PlayerNotFoundError as error:
        return error_response("player_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("list")

    payload = PlayerPhotoListResponse(
        photos=[_photo_response(photo) for photo in photos]
    )
    return jsonify(payload.model_dump(mode="json")), 200


@player_photos_bp.get(
    "/<int:player_id>/photos/<int:photo_id>",
    summary="Get a Player photo image",
    description=(
        "Returns the stored image with its PNG or JPG media type. The "
        "response is marked as non-storable because it is biometric data."
    ),
    operation_id="playerPhotosGetImage",
    security=ACCESS_SECURITY,
    responses={
        200: PHOTO_IMAGE_RESPONSE,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def get_player_photo_image(path: PlayerPhotoPath):
    try:
        photo_file = get_player_photo_file(path.player_id, path.photo_id)
    except PlayerPhotoNotFoundError as error:
        return error_response("photo_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("get")

    response = send_file(photo_file.path, mimetype=photo_file.content_type)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
