from pydantic import BaseModel, ConfigDict, Field


class ApiRequest(BaseModel):
    """Base contract for JSON supplied by API clients."""

    model_config = ConfigDict(extra="forbid")


class ApiResponse(BaseModel):
    """Base contract for JSON returned by the API."""

    model_config = ConfigDict(from_attributes=True)


class ValidationDetail(ApiResponse):
    field: str
    message: str
    type: str


class ErrorBody(ApiResponse):
    code: str
    message: str
    details: list[ValidationDetail] | None = None


class ErrorResponse(ApiResponse):
    error: ErrorBody

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "error": {
                        "code": "validation_error",
                        "message": "La solicitud contiene datos no válidos.",
                        "details": [
                            {
                                "field": "body.name",
                                "message": "Este campo es obligatorio.",
                                "type": "missing",
                            }
                        ],
                    }
                }
            ]
        },
    )

