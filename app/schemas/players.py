from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictStr, field_validator
from pydantic_core import PydanticCustomError

from .common import ApiRequest, ApiResponse
from .sports import SportResponse


Gender = Literal["male", "female"]
PlayerStatus = Literal["enabled", "disabled", "all"]
PlayerSort = Literal["name_asc", "created_at_desc"]
PositiveBodyId = Annotated[int, Field(strict=True, gt=0)]
PositiveQueryInteger = Annotated[int, Field(gt=0)]


def _reject_duplicate_team_ids(team_ids: list[int]) -> list[int]:
    if len(team_ids) != len(set(team_ids)):
        raise PydanticCustomError(
            "duplicate_team_ids",
            "team_ids must not contain duplicate values.",
        )
    return team_ids


class PlayerPath(ApiRequest):
    player_id: PositiveBodyId


class PlayerCreateRequest(ApiRequest):
    name: StrictStr = Field(examples=["Lionel Messi"])
    sport_id: PositiveBodyId = Field(examples=[1])
    gender: Gender = Field(examples=["male"])
    team_ids: list[PositiveBodyId] = Field(
        default_factory=list,
        max_length=3,
        examples=[[1]],
        json_schema_extra={"default": [], "uniqueItems": True},
    )

    _validate_team_ids = field_validator("team_ids")(
        _reject_duplicate_team_ids
    )


class PlayerUpdateRequest(ApiRequest):
    name: StrictStr = Field(examples=["Updated name"])
    team_ids: list[PositiveBodyId] = Field(
        max_length=3,
        examples=[[1, 2]],
        json_schema_extra={"uniqueItems": True},
    )

    _validate_team_ids = field_validator("team_ids")(
        _reject_duplicate_team_ids
    )


class PlayerListQuery(ApiRequest):
    search: str | None = Field(
        default=None,
        description="Partial accent-insensitive Player-name search.",
    )
    sport_id: PositiveQueryInteger | None = None
    gender: Gender | None = None
    team_id: PositiveQueryInteger | None = None
    status: PlayerStatus = "enabled"
    sort: PlayerSort = "name_asc"
    page: PositiveQueryInteger = 1


class PlayerTeamResponse(ApiResponse):
    id: int
    name: str


class PlayerResponse(ApiResponse):
    id: int
    name: str
    sport: SportResponse
    gender: Gender
    teams: list[PlayerTeamResponse]
    is_enabled: bool
    created_at: datetime
    disabled_at: datetime | None


class PlayerPaginationResponse(ApiResponse):
    page: int
    per_page: int
    total_items: int
    total_pages: int


class PlayerListResponse(ApiResponse):
    players: list[PlayerResponse]
    pagination: PlayerPaginationResponse
