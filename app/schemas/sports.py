from typing import Annotated

from pydantic import Field, StrictInt, StrictStr, field_validator
from pydantic_core import PydanticCustomError

from .common import ApiRequest, ApiResponse


MaxPlayers = Annotated[int, Field(strict=True, ge=1, le=20)]


class SportPath(ApiRequest):
    sport_id: StrictInt


class SportCreateRequest(ApiRequest):
    name: StrictStr = Field(examples=["Fútbol"])
    max_players: MaxPlayers = Field(examples=[11])


class SportUpdateRequest(ApiRequest):
    name: StrictStr = Field(examples=["Fútbol sala"])
    max_players: MaxPlayers | None = Field(
        default=None,
        json_schema_extra={"readOnly": True},
    )

    @field_validator("max_players", mode="before")
    @classmethod
    def reject_max_players_update(cls, value: object) -> object:
        raise PydanticCustomError(
            "immutable_field",
            "max_players cannot be modified after sport creation.",
        )


class SportResponse(ApiResponse):
    id: int
    name: str
    max_players: int


class SportEnvelope(ApiResponse):
    sport: SportResponse


class SportListResponse(ApiResponse):
    sports: list[SportResponse]

