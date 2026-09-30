from datetime import datetime
from typing import Annotated, Literal

from flask_openapi3 import FileStorage
from pydantic import AfterValidator, Field
from pydantic_core import PydanticCustomError
from werkzeug.datastructures import FileStorage as UploadedFile

from .common import ApiRequest, ApiResponse
from .players import PositiveBodyId


PhotoContentType = Literal["image/jpeg", "image/png"]


def _require_uploaded_file(value: object) -> UploadedFile:
    # flask-openapi3 accepts any value for FileStorage fields, including a
    # plain text part with the same name or an empty file input.
    if not isinstance(value, UploadedFile) or not value.filename:
        raise PydanticCustomError(
            "photo_file_required",
            "photo must be an uploaded file.",
        )
    return value


class PlayerPhotoPath(ApiRequest):
    player_id: PositiveBodyId
    photo_id: PositiveBodyId


class PlayerPhotoUploadForm(ApiRequest):
    photo: Annotated[FileStorage, AfterValidator(_require_uploaded_file)] = Field(
        description=(
            "Exactly one PNG or JPG file of at most 5 MB, between 320 and "
            "4096 pixels per side, with no more than 12000000 pixels and "
            "exactly one detected face of at least 160 by 160 pixels."
        ),
    )


class PlayerPhotoResponse(ApiResponse):
    id: int
    player_id: int
    content_type: PhotoContentType
    created_at: datetime


class PlayerPhotoListResponse(ApiResponse):
    photos: list[PlayerPhotoResponse]
