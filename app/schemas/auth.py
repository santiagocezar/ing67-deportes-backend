from datetime import date, datetime
from typing import Annotated

from pydantic import (
    ConfigDict,
    Field,
    StrictStr,
    StringConstraints,
    field_validator,
)

from .common import ApiRequest, ApiResponse


TrimmedString = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1),
]
PasswordString = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=8),
]


class SignupRequest(ApiRequest):
    name: TrimmedString = Field(max_length=100, examples=["Ana Example"])
    birthdate: date = Field(examples=["1995-04-20"])
    email: TrimmedString = Field(max_length=255, examples=["ana@example.com"])
    password: PasswordString = Field(
        json_schema_extra={"writeOnly": True},
        examples=["example-password"],
    )

    @field_validator("birthdate")
    @classmethod
    def validate_birthdate(cls, birthdate: date) -> date:
        reference_date = date.today()
        age = reference_date.year - birthdate.year - (
            (reference_date.month, reference_date.day)
            < (birthdate.month, birthdate.day)
        )
        if age < 18:
            raise ValueError("La persona usuaria debe tener al menos 18 años.")
        if age > 100:
            raise ValueError("La persona usuaria no puede tener más de 100 años.")
        return birthdate

    @field_validator("email")
    @classmethod
    def validate_email(cls, email: str) -> str:
        if "@" not in email:
            raise ValueError("El correo electrónico no es válido.")
        return email.lower()


class LoginRequest(ApiRequest):
    email: StrictStr = Field(examples=["ana@example.com"])
    password: StrictStr = Field(
        json_schema_extra={"writeOnly": True},
        examples=["example-password"],
    )


class UserResponse(ApiResponse):
    id: int
    name: str
    birthdate: date
    role: str
    email: str
    creation_date: datetime | None


class SignupResponse(ApiResponse):
    message: str
    user: UserResponse


class TokenResponse(ApiResponse):
    access_token: str
    refresh_token: str
    token_type: str
    access_expires_in: int

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "access_token": "<access-token>",
                    "refresh_token": "<refresh-token>",
                    "token_type": "Bearer",
                    "access_expires_in": 900,
                }
            ]
        },
    )


class UserEnvelope(ApiResponse):
    user: UserResponse

