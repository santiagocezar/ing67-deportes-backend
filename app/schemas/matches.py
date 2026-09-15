from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, StrictInt, field_validator
from pydantic_core import PydanticCustomError

from .common import ApiRequest, ApiResponse
from .competitions import CompetitionTeamResponse, RefereeSummaryResponse


MatchStatus = Literal["incomplete", "scheduled", "in_progress", "finished"]


class MatchPath(ApiRequest):
    match_id: StrictInt


class CompetitionMatchesPath(ApiRequest):
    competition_id: StrictInt


class MatchUpdateRequest(ApiRequest):
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @field_validator("ends_at")
    @classmethod
    def validate_dates(cls, value: datetime, info) -> datetime:
        starts_at = info.data.get("starts_at")
        if starts_at is not None and value <= starts_at:
            raise PydanticCustomError(
                "match_date_range_invalid",
                "Match end date must be later than its start date.",
            )
        return value


class MatchCompetitionResponse(ApiResponse):
    id: int
    name: str


class MatchResponse(ApiResponse):
    id: int
    competition: MatchCompetitionResponse
    round_number: int
    starts_at: datetime | None
    ends_at: datetime | None
    status: MatchStatus
    referee: RefereeSummaryResponse
    team_1: CompetitionTeamResponse
    team_2: CompetitionTeamResponse


class MatchListResponse(ApiResponse):
    matches: list[MatchResponse]
