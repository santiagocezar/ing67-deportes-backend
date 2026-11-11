from datetime import datetime
from typing import Annotated, Literal

from flask_openapi3 import FileStorage
from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    StrictInt,
    model_validator,
)
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
ManualReviewStatus = Literal["pending", "resolved", "expired"]
ManualReviewListStatus = Literal["pending", "resolved", "expired", "all"]
ManualReviewListSort = Literal[
    "scan_created_at_desc",
    "scan_created_at_asc",
]
ManualReviewDecisionOutcome = Literal[
    "belongs_to_team",
    "does_not_belong_to_team",
    "inconclusive",
]
PositivePathId = Annotated[StrictInt, Field(gt=0)]
PositiveQueryInteger = Annotated[int, Field(gt=0)]
NonNegativeInteger = Annotated[StrictInt, Field(ge=0)]


def _strict_query_integer(value: object) -> int:
    """Accept only canonical integer query text before strict validation."""

    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise PydanticCustomError(
        "int_type",
        "Debe ser un número entero positivo.",
    )


PositiveStrictQueryInteger = Annotated[
    StrictInt,
    BeforeValidator(_strict_query_integer),
    Field(gt=0),
]


def _require_uploaded_file(value: object) -> UploadedFile:
    if not isinstance(value, UploadedFile) or not value.filename:
        raise PydanticCustomError(
            "photo_file_required",
            "photo debe ser un archivo adjunto.",
        )
    return value


def _require_retention_confirmation(value: object) -> bool:
    if value is True or value == "true":
        return True
    raise PydanticCustomError(
        "manual_review_confirmation_required",
        "retain_for_manual_review debe ser true.",
    )


RetentionConfirmation = Annotated[
    bool,
    BeforeValidator(_require_retention_confirmation),
]


class MatchTeamRosterScanPath(ApiRequest):
    match_id: PositivePathId
    team_id: PositivePathId


class MatchRosterScanPath(ApiRequest):
    scan_id: PositivePathId


class MatchManualReviewStatusPath(ApiRequest):
    match_id: PositivePathId


class MatchRosterScanListQuery(ApiRequest):
    page: PositiveQueryInteger = 1


class MatchRosterScanManualReviewListQuery(ApiRequest):
    match_id: PositiveStrictQueryInteger | None = None
    sport_id: PositiveStrictQueryInteger | None = None
    status: ManualReviewListStatus = "pending"
    sort: ManualReviewListSort = "scan_created_at_desc"
    page: PositiveQueryInteger = 1


class MatchRosterScanUploadForm(ApiRequest):
    photo: Annotated[FileStorage, AfterValidator(_require_uploaded_file)] = Field(
        description=(
            "One PNG or JPEG group image of at most 10 MiB, between 320 and "
            "4096 pixels per side, with no more than 12000000 total pixels."
        )
    )
    retain_for_manual_review: RetentionConfirmation = Field(
        description=(
            "Must be the literal form value true. It confirms retention only "
            "when the recomputed result contains an unknown face."
        )
    )


class MatchRosterScanPreviewForm(ApiRequest):
    photo: Annotated[FileStorage, AfterValidator(_require_uploaded_file)] = Field(
        description=(
            "One PNG or JPEG group image of at most 10 MiB, between 320 and "
            "4096 pixels per side, with no more than 12000000 total pixels."
        )
    )


class MatchRosterScanManualReviewDecisionRequest(ApiRequest):
    face_index: NonNegativeInteger
    outcome: ManualReviewDecisionOutcome
    player_id: PositivePathId | None = None

    @model_validator(mode="after")
    def validate_outcome_player(self):
        if self.outcome == "belongs_to_team" and self.player_id is None:
            raise PydanticCustomError(
                "manual_review_player_required",
                "belongs_to_team requiere player_id.",
            )
        if self.outcome != "belongs_to_team" and self.player_id is not None:
            raise PydanticCustomError(
                "manual_review_player_forbidden",
                "Solo belongs_to_team puede incluir player_id.",
            )
        return self


class MatchRosterScanManualReviewFinalizeRequest(ApiRequest):
    decisions: Annotated[
        list[MatchRosterScanManualReviewDecisionRequest],
        Field(min_length=1),
    ]


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


class MatchRosterScanManualReviewDecisionResponse(ApiResponse):
    face_index: int
    outcome: ManualReviewDecisionOutcome
    player_id: int | None
    player_name: str | None
    created_at: datetime


class MatchRosterScanManualReviewResponse(ApiResponse):
    status: ManualReviewStatus
    expires_at: datetime
    resolved_by_user_id: int | None
    resolved_at: datetime | None
    decisions: list[MatchRosterScanManualReviewDecisionResponse]


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
    manual_review: MatchRosterScanManualReviewResponse | None


class MatchRosterScanPreviewResponse(ApiResponse):
    match_id: int
    team_id: int
    expected_players: int
    detected_faces: int
    unique_recognized_players: int
    count_warning: CountWarning | None
    faces: list[MatchRosterScanFaceResponse]


class ManualReviewCompetitionResponse(ApiResponse):
    id: int
    name: str


class ManualReviewMatchResponse(ApiResponse):
    id: int
    starts_at: datetime | None
    ends_at: datetime | None
    status: str


class ManualReviewTeamResponse(ApiResponse):
    id: int
    name: str


class ManualReviewScanResponse(ApiResponse):
    id: int
    created_at: datetime


class MatchRosterScanManualReviewListItemResponse(ApiResponse):
    competition: ManualReviewCompetitionResponse
    match: ManualReviewMatchResponse
    team: ManualReviewTeamResponse
    scan: ManualReviewScanResponse
    unknown_face_count: int
    status: ManualReviewStatus
    expires_at: datetime


class MatchRosterScanPaginationResponse(ApiResponse):
    page: int
    per_page: int
    total_items: int
    total_pages: int


class MatchRosterScanManualReviewListResponse(ApiResponse):
    manual_reviews: list[MatchRosterScanManualReviewListItemResponse]
    pagination: MatchRosterScanPaginationResponse


class MatchManualReviewStatusResponse(ApiResponse):
    match_id: int
    requires_biometric_audit: bool
    pending_review_count: int
    resolved_review_count: int
    expired_review_count: int


class MatchRosterScanListResponse(ApiResponse):
    roster_scans: list[MatchRosterScanResponse]
    pagination: MatchRosterScanPaginationResponse
