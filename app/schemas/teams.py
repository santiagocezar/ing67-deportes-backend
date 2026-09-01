from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictStr

from .common import ApiRequest, ApiResponse
from .sports import SportResponse


GenderCategory = Literal["male", "female"]
TeamStatus = Literal["enabled", "disabled", "all"]
TeamSort = Literal["name_asc", "created_at_desc"]
PositiveQueryInteger = Annotated[int, Field(gt=0)]


class TeamPath(ApiRequest):
    team_id: StrictInt


class TeamCreateRequest(ApiRequest):
    name: StrictStr = Field(examples=["Boca Juniors"])
    sport_id: Annotated[int, Field(strict=True, gt=0)] = Field(examples=[1])
    gender_category: GenderCategory = Field(examples=["male"])


class TeamUpdateRequest(ApiRequest):
    name: StrictStr = Field(examples=["Nuevo nombre"])


class TeamListQuery(ApiRequest):
    search: str | None = Field(
        default=None,
        description="Partial accent-insensitive Team-name search.",
    )
    sport_id: PositiveQueryInteger | None = None
    gender_category: GenderCategory | None = None
    status: TeamStatus = "enabled"
    sort: TeamSort = "name_asc"
    page: PositiveQueryInteger = 1


class TeamResponse(ApiResponse):
    id: int
    name: str
    sport: SportResponse
    gender_category: GenderCategory
    is_enabled: bool
    created_at: datetime
    disabled_at: datetime | None


class TeamEnvelope(ApiResponse):
    team: TeamResponse


class PaginationResponse(ApiResponse):
    page: int
    per_page: int
    total_items: int
    total_pages: int


class TeamListResponse(ApiResponse):
    teams: list[TeamResponse]
    pagination: PaginationResponse
