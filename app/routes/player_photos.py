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
    FaceTooSmallError,
    InvalidFaceCountError,
    InvalidPhotoDimensionsError,
    PhotoProcessingUnavailableError,
    PhotoStorageError,
    PhotoTooLargeError,
    PhotoValidationError,
    PlayerPhotoLimitReachedError,
    PlayerPhotoNotFoundError,
    UnsupportedPhotoFormatError,
    add_player_photo,
    delete_player_photo,
    get_player_photo_file,
    list_player_photos,
)
from ..services.players import PlayerDisabledError, PlayerNotFoundError
from .authorization import ACCESS_SECURITY, administrator_required
from .players import PLAYERS_TAG


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
    if isinstance(error, InvalidPhotoDimensionsError):
        return error_response("invalid_photo_dimensions", str(error), 422)
    if isinstance(error, InvalidFaceCountError):
        return error_response("invalid_face_count", str(error), 422)
    if isinstance(error, FaceTooSmallError):
        return error_response("face_too_small", str(error), 422)
    return error_response("invalid_photo", str(error), 422)


def _multiple_photo_parts_error():
    return error_response(
        "validation_error",
        "Request validation failed.",
        422,
        details=[
            {
                "field": "form.photo",
                "message": "Exactly one photo file is required.",
                "type": "multiple_files",
            }
        ],
    )


def _require_single_photo_part(function):
    """Reject repeated photo parts before Pydantic selects one value."""
    from functools import wraps

    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            photo_parts = len(request.files.getlist("photo")) + len(
                request.form.getlist("photo")
            )
        except RequestEntityTooLarge:
            return _photo_error(PhotoTooLargeError())
        if photo_parts > 1:
            return _multiple_photo_parts_error()
        return function(*args, **kwargs)

    return wrapper


def _photo_storage_unavailable():
    current_app.logger.error("Could not complete Player photo file operation")
    return error_response(
        "photo_storage_unavailable",
        "The photo storage is temporarily unavailable.",
        503,
    )


def _photo_processing_unavailable():
    current_app.logger.error("Player photo processing dependencies unavailable")
    return error_response(
        "photo_processing_unavailable",
        "Photo validation is temporarily unavailable.",
        503,
    )


@player_photos_bp.post(
    "/<int:player_id>/photos",
    summary="Upload a Player photo",
    description=(
        "Stores one validated PNG or JPG reference image of at most 5 MB. "
        "An enabled Player may have at most three; each image must contain "
        "exactly one sufficiently large detected face."
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
@_require_single_photo_part
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
    except PlayerPhotoLimitReachedError as error:
        return error_response("player_photo_limit_reached", str(error), 409)
    except PhotoProcessingUnavailableError:
        return _photo_processing_unavailable()
    except SQLAlchemyError:
        return _database_unavailable("store")
    except (PhotoStorageError, OSError):
        return _photo_storage_unavailable()

    payload = _photo_response(photo)
    return jsonify(payload.model_dump(mode="json")), 201


@player_photos_bp.get(
    "/<int:player_id>/photos",
    summary="List Player photos",
    description=(
        "Returns at most three base photos of an enabled Player in upload "
        "order. Disabled Players have an empty gallery."
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
        response = send_file(
            photo_file.path,
            mimetype=photo_file.content_type,
            conditional=False,
            etag=False,
        )
    except PlayerPhotoNotFoundError as error:
        return error_response("photo_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("get")
    except (PhotoStorageError, OSError):
        return _photo_storage_unavailable()
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


@player_photos_bp.delete(
    "/<int:player_id>/photos/<int:photo_id>",
    summary="Delete a Player photo",
    description=(
        "Permanently deletes one reference photo. The last photo may be "
        "deleted; automatic recognition then requires manual resolution."
    ),
    operation_id="playerPhotosDelete",
    security=ACCESS_SECURITY,
    responses={
        204: None,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def delete_player_photo_image(path: PlayerPhotoPath):
    try:
        delete_player_photo(path.player_id, path.photo_id)
    except PlayerNotFoundError as error:
        return error_response("player_not_found", str(error), 404)
    except PlayerPhotoNotFoundError as error:
        return error_response("photo_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("delete")
    except (PhotoStorageError, OSError):
        return _photo_storage_unavailable()
    return "", 204
