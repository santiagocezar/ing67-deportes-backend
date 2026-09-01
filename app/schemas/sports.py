from typing import Annotated

from pydantic import (
    Field,
    StrictInt,
    StrictStr,
    ValidationInfo,
    field_validator,
)
from pydantic_core import PydanticCustomError

from .common import ApiRequest, ApiResponse


PositiveCapacity = Annotated[int, Field(strict=True, gt=0)]


class SportPath(ApiRequest):
    sport_id: StrictInt


class SportCreateRequest(ApiRequest):
    name: StrictStr = Field(examples=["Fútbol"])
    max_players: PositiveCapacity = Field(examples=[22])
    max_players_in_game: PositiveCapacity = Field(examples=[11])

    @field_validator("max_players_in_game")
    @classmethod
    def validate_capacity_order(
        cls,
        max_players_in_game: int,
        info: ValidationInfo,
    ) -> int:
        max_players = info.data.get("max_players")
        if max_players is not None and max_players_in_game > max_players:
            raise ValueError(
                "max_players_in_game cannot exceed max_players."
            )
        return max_players_in_game


class SportUpdateRequest(ApiRequest):
    name: StrictStr = Field(examples=["Fútbol sala"])
    max_players: PositiveCapacity | None = Field(
        default=None,
        json_schema_extra={"readOnly": True},
    )
    max_players_in_game: PositiveCapacity | None = Field(
        default=None,
        json_schema_extra={"readOnly": True},
    )

    @field_validator(
        "max_players",
        "max_players_in_game",
        mode="before",
    )
    @classmethod
    def reject_capacity_update(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        raise PydanticCustomError(
            "immutable_field",
            f"{info.field_name} cannot be modified after sport creation.",
        )


class SportResponse(ApiResponse):
    id: int
    name: str
    max_players: int
    max_players_in_game: int


class SportEnvelope(ApiResponse):
    sport: SportResponse


class SportListResponse(ApiResponse):
    sports: list[SportResponse]
