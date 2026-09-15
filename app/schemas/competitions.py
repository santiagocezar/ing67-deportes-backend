from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from .common import ApiRequest, ApiResponse
from .sports import SportResponse


CompetitionGender = Literal["male", "female"]
CompetitionStatus = Literal[
    "scheduled",
    "in_progress",
    "finished",
    "discarded",
]
CompetitionAvailability = Literal["enabled", "disabled", "all"]
CompetitionSort = Literal["starts_at_asc", "starts_at_desc"]
PositiveId = Annotated[int, Field(strict=True, gt=0)]
PositiveQueryInteger = Annotated[int, Field(gt=0)]


def _validate_date_range(
    ends_at: datetime,
    starts_at: datetime | None,
) -> datetime:
    if starts_at is not None and ends_at <= starts_at:
        raise PydanticCustomError(
            "competition_date_range_invalid",
            "Competition end date must be later than its start date.",
        )
    return ends_at


class CompetitionTeamRequest(ApiRequest):
    team_id: PositiveId
    player_ids: list[PositiveId] = Field(min_length=1)

    @field_validator("player_ids")
    @classmethod
    def reject_duplicate_players(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("player_ids must not contain duplicate values.")
        return value


class ParticipantsRequest(ApiRequest):
    teams: list[CompetitionTeamRequest] = Field(min_length=4, max_length=16)
    referee_ids: list[PositiveId]

    @model_validator(mode="after")
    def validate_participant_counts(self):
        team_ids = [entry.team_id for entry in self.teams]
        if len(team_ids) % 2:
            raise ValueError("teams must contain an even number of entries.")
        if len(team_ids) != len(set(team_ids)):
            raise ValueError("team_id values must be unique.")
        if len(self.referee_ids) != len(set(self.referee_ids)):
            raise ValueError("referee_ids must not contain duplicate values.")
        if len(self.referee_ids) != len(team_ids) // 2:
            raise ValueError(
                "referee_ids must contain exactly one referee per two Teams."
            )
        return self


class CompetitionCreateRequest(ParticipantsRequest):
    name: StrictStr = Field(examples=["Tournament 2026"])
    sport_id: PositiveId
    gender: CompetitionGender
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @field_validator("ends_at")
    @classmethod
    def validate_dates(cls, value: datetime, info) -> datetime:
        return _validate_date_range(value, info.data.get("starts_at"))


class CompetitionUpdateRequest(ApiRequest):
    name: StrictStr = Field(examples=["Updated tournament name"])
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @field_validator("ends_at")
    @classmethod
    def validate_dates(cls, value: datetime, info) -> datetime:
        return _validate_date_range(value, info.data.get("starts_at"))


class CompetitionPath(ApiRequest):
    competition_id: StrictInt


class CompetitionListQuery(ApiRequest):
    search: str | None = None
    sport_id: PositiveQueryInteger | None = None
    gender: CompetitionGender | None = None
    lifecycle_status: CompetitionStatus | None = None
    availability: CompetitionAvailability = "enabled"
    starts_from: AwareDatetime | None = None
    starts_to: AwareDatetime | None = None
    sort: CompetitionSort = "starts_at_desc"
    page: PositiveQueryInteger = 1

    @field_validator("starts_to")
    @classmethod
    def validate_start_filter(cls, value: datetime | None, info):
        starts_from = info.data.get("starts_from")
        if value is not None and starts_from is not None and value < starts_from:
            raise ValueError("starts_to must not be earlier than starts_from.")
        return value


class RosterPlayerResponse(ApiResponse):
    id: int
    name: str
    gender: CompetitionGender
    is_enabled: bool


class CompetitionTeamResponse(ApiResponse):
    id: int
    name: str
    gender_category: CompetitionGender
    is_enabled: bool
    roster: list[RosterPlayerResponse]


class RefereeSummaryResponse(ApiResponse):
    id: int
    name: str


class CompetitionSummaryResponse(ApiResponse):
    id: int
    name: str
    sport: SportResponse
    gender: CompetitionGender
    team_count: int
    starts_at: datetime
    ends_at: datetime
    status: CompetitionStatus
    is_enabled: bool
    created_at: datetime
    disabled_at: datetime | None


class CompetitionDetailResponse(CompetitionSummaryResponse):
    teams: list[CompetitionTeamResponse]
    referees: list[RefereeSummaryResponse]


class CompetitionPaginationResponse(ApiResponse):
    page: int
    per_page: int
    total_items: int
    total_pages: int


class CompetitionListResponse(ApiResponse):
    competitions: list[CompetitionSummaryResponse]
    pagination: CompetitionPaginationResponse
