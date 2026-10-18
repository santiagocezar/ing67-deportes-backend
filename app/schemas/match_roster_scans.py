from datetime import datetime
from typing import Annotated, Literal

from flask_openapi3 import FileStorage
from pydantic import AfterValidator, Field, StrictInt
from pydantic_core import PydanticCustomError
from werkzeug.datastructures import FileStorage as UploadedFile

from .common import ApiRequest, ApiResponse


ScanFaceStatus = Literal[
    "declared_eligible",
    "declared_sanctioned",
    "team_member_undeclared",
    "team_member_undeclared_sanctioned",
    "unknown",
]
CountWarning = Literal[
    "recognized_player_count_below_expected",
    "recognized_player_count_above_expected",
]
PositivePathId = Annotated[StrictInt, Field(gt=0)]
PositiveQueryInteger = Annotated[int, Field(gt=0)]


def _require_uploaded_file(value: object) -> UploadedFile:
    if not isinstance(value, UploadedFile) or not value.filename:
        raise PydanticCustomError(
            "photo_file_required",
            "photo must be an uploaded file.",
        )
    return value


class MatchTeamRosterScanPath(ApiRequest):
    match_id: PositivePathId
    team_id: PositivePathId


class MatchRosterScanPath(ApiRequest):
    scan_id: PositivePathId


class MatchRosterScanListQuery(ApiRequest):
    page: PositiveQueryInteger = 1


class MatchRosterScanUploadForm(ApiRequest):
    photo: Annotated[FileStorage, AfterValidator(_require_uploaded_file)] = Field(
        description=(
            "One PNG or JPEG group image of at most 10 MiB, between 320 and "
            "4096 pixels per side, with no more than 12000000 total pixels."
        )
    )


class MatchRosterScanSanctionResponse(ApiResponse):
    sanction_id: int
    title: str
    reason: str
    remaining_matches: int


class MatchRosterScanFaceResponse(ApiResponse):
    face_index: int
    top: int
    right: int
    bottom: int
    left: int
    status: ScanFaceStatus
    player_id: int | None
    player_name: str | None
    match_percentage: int | None
    sanctions: list[MatchRosterScanSanctionResponse]


class MatchRosterScanResponse(ApiResponse):
    id: int
    match_id: int
    team_id: int
    created_by_user_id: int
    expected_players: int
    detected_faces: int
    unique_recognized_players: int
    count_warning: CountWarning | None
    created_at: datetime
    faces: list[MatchRosterScanFaceResponse]


class MatchRosterScanPaginationResponse(ApiResponse):
    page: int
    per_page: int
    total_items: int
    total_pages: int


class MatchRosterScanListResponse(ApiResponse):
    roster_scans: list[MatchRosterScanResponse]
    pagination: MatchRosterScanPaginationResponse
