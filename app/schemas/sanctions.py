from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .common import ApiRequest, ApiResponse


SanctionStatus = Literal["active", "served", "annulled"]
PositivePathId = Annotated[StrictInt, Field(gt=0)]
PositiveQueryInteger = Annotated[int, Field(gt=0)]


def _normalized_required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise PydanticCustomError(
            "string_type", "Input should be a valid string."
        )
    normalized = value.strip()
    if not normalized:
        raise PydanticCustomError(
            "sanction_text_blank", f"{field_name} is required."
        )
    return normalized


class MatchPlayerSanctionPath(ApiRequest):
    match_id: PositivePathId
    player_id: PositivePathId


class PlayerSanctionCreateRequest(ApiRequest):
    title: StrictStr = Field(max_length=30, examples=["Serious misconduct"])
    reason: StrictStr = Field(
        max_length=250,
        examples=["The player committed a disciplinary offense."],
    )
    suspension_matches: StrictInt = Field(ge=1, le=10, examples=[3])

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> str:
        return _normalized_required_text(value, "title")

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: object) -> str:
        return _normalized_required_text(value, "reason")


class PlayerSanctionPath(ApiRequest):
    sanction_id: PositivePathId


class PlayerSanctionListPath(ApiRequest):
    player_id: PositivePathId


class PlayerSanctionListQuery(ApiRequest):
    status: SanctionStatus | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    page: PositiveQueryInteger = 1

    @field_validator("created_from", "created_to")
    @classmethod
    def require_aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise PydanticCustomError(
                "timezone_aware_datetime_required",
                "A timezone-aware datetime is required.",
            )
        return value

    @model_validator(mode="after")
    def validate_date_range(self):
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_to < self.created_from
        ):
            raise PydanticCustomError(
                "sanction_created_date_range_invalid",
                "created_to must be later than or equal to created_from.",
            )
        return self


class PlayerSanctionUpdateRequest(ApiRequest):
    title: StrictStr = Field(max_length=30, examples=["Updated title"])
    reason: StrictStr = Field(
        max_length=250,
        examples=["Corrected disciplinary description."],
    )
    suspension_matches: StrictInt = Field(ge=1, le=10, examples=[4])

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> str:
        return _normalized_required_text(value, "title")

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: object) -> str:
        return _normalized_required_text(value, "reason")


class SanctionPlayerSummary(ApiResponse):
    id: int
    name: str


class SanctionTeamSummary(ApiResponse):
    id: int
    name: str


class SanctionUserSummary(ApiResponse):
    id: int
    name: str


class SanctionMatchSummary(ApiResponse):
    id: int
    starts_at: datetime
    ends_at: datetime


class SanctionCompetitionSummary(ApiResponse):
    id: int
    name: str


class PlayerSanctionResponse(ApiResponse):
    id: int
    player: SanctionPlayerSummary
    title: str
    reason: str
    suspension_matches: int
    remaining_matches: int
    status: SanctionStatus
    source_match: SanctionMatchSummary
    source_competition: SanctionCompetitionSummary
    frozen_teams: list[SanctionTeamSummary]
    creator: SanctionUserSummary
    updater: SanctionUserSummary
    annuller: SanctionUserSummary | None
    effective_at: datetime
    created_at: datetime
    updated_at: datetime
    annulled_at: datetime | None
    served_at: datetime | None


class PlayerSanctionPaginationResponse(ApiResponse):
    page: int
    per_page: int
    total_items: int
    total_pages: int


class PlayerSanctionListResponse(ApiResponse):
    sanctions: list[PlayerSanctionResponse]
    pagination: PlayerSanctionPaginationResponse
