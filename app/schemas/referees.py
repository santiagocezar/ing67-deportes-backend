from typing import Annotated

from pydantic import Field

from .common import ApiRequest, ApiResponse
PositiveQueryInteger = Annotated[int, Field(gt=0)]


class RefereeListQuery(ApiRequest):
    search: str | None = None
    page: PositiveQueryInteger = 1


class RefereePaginationResponse(ApiResponse):
    page: int
    per_page: int
    total_items: int
    total_pages: int


class RefereeLookupResponse(ApiResponse):
    id: int
    name: str
    email: str


class RefereeListResponse(ApiResponse):
    referees: list[RefereeLookupResponse]
    pagination: RefereePaginationResponse
