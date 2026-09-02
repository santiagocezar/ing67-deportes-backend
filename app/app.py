import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

import click
from dotenv import load_dotenv
from flask_migrate import stamp, upgrade
from flask_openapi3 import Info, OpenAPI, SecurityScheme
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from .errors import (
    error_response,
    validation_error_response,
)
from .extensions import cors, db, jwt, migrate

API_VERSION = "1.0.0"


def _cors_origins() -> list[str]:
    configured = os.getenv("CORS_ORIGINS", "http://localhost:5173")
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


def _environment_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false.")


def _validation_summary(error: ValidationError) -> str:
    messages: list[str] = []
    for item in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        field = ".".join(str(part) for part in item.get("loc", ()))
        message = str(item.get("msg", "Invalid value"))
        messages.append(f"{field}: {message}" if field else message)
    return "; ".join(messages)


def create_app(
    config_overrides: Mapping[str, Any] | None = None,
) -> OpenAPI:
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path)

    overrides = dict(config_overrides or {})
    database_uri = overrides.get(
        "SQLALCHEMY_DATABASE_URI",
        os.getenv("SQLALCHEMY_DATABASE_URI"),
    )
    if not database_uri:
        raise RuntimeError(
            "SQLALCHEMY_DATABASE_URI is not configured. "
            "Add it to app/.env or to the process environment."
        )

    docs_enabled = bool(
        overrides.get(
            "API_DOCS_ENABLED",
            _environment_flag("API_DOCS_ENABLED", True),
        )
    )
    flask_app = OpenAPI(
        __name__,
        info=Info(
            title="Sports App API",
            version=API_VERSION,
            summary="Authentication, Sports, and Teams management API.",
        ),
        security_schemes={
            "AccessTokenAuth": SecurityScheme(
                type="http",
                scheme="bearer",
                bearerFormat="JWT",
                description="JWT access token. It expires after 15 minutes.",
            ),
            "RefreshTokenAuth": SecurityScheme(
                type="http",
                scheme="bearer",
                bearerFormat="JWT",
                description=(
                    "Rotating JWT refresh token. Use it only for refresh or logout."
                ),
            ),
        },
        validation_error_status=422,
        validation_error_callback=validation_error_response,
        doc_ui=False,
        validate_response=False,
    )
    flask_app.config.update(
        SQLALCHEMY_DATABASE_URI=database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=os.getenv("JWT_SECRET_KEY"),
        JWT_ACCESS_TOKEN_EXPIRES=timedelta(minutes=15),
        JWT_REFRESH_TOKEN_EXPIRES=timedelta(days=30),
        JWT_TOKEN_LOCATION=["headers"],
        API_DOCS_ENABLED=docs_enabled,
    )
    flask_app.config.update(overrides)

    db.init_app(flask_app)
    jwt.init_app(flask_app)
    migrate.init_app(flask_app, db)
    allowed_origins = _cors_origins()
    cors.init_app(
        flask_app,
        resources={
            r"/auth(?:/.*)?": {"origins": allowed_origins},
            r"/sports(?:/.*)?": {"origins": allowed_origins},
            r"/teams(?:/.*)?": {"origins": allowed_origins},
        },
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        supports_credentials=False,
        max_age=600,
        vary_header=True,
    )

    from . import models as _models
    from .models import ADMIN_USER_ROLE
    from .routes.sports import sports_bp
    from .routes.teams import teams_bp
    from .routes.users import auth_bp
    from .schemas.auth import SignupRequest
    from .services.auth import is_token_revoked
    from .services.users import (
        DuplicateEmailError,
        UserValidationError,
        create_user,
    )

    flask_app.register_api(auth_bp)
    flask_app.register_api(sports_bp)
    flask_app.register_api(teams_bp)

    if docs_enabled:
        from flask_openapi3_swagger.plugins import RegisterPlugin

        swagger_blueprint = RegisterPlugin.register(doc_url="/openapi.json")
        flask_app.register_blueprint(swagger_blueprint)
        flask_app.add_url_rule(
            "/openapi.json",
            endpoint="openapi_document",
            view_func=lambda: flask_app.api_doc,
            methods=["GET"],
        )

    @jwt.token_in_blocklist_loader
    def token_in_blocklist(_jwt_header: dict, jwt_payload: dict) -> bool:
        return is_token_revoked(jwt_payload)

    @jwt.unauthorized_loader
    def missing_token(reason: str):
        return error_response("authentication_required", reason, 401)

    @jwt.invalid_token_loader
    def invalid_token(reason: str):
        return error_response("invalid_token", reason, 401)

    @jwt.expired_token_loader
    def expired_token(_jwt_header: dict, _jwt_payload: dict):
        return error_response("token_expired", "The token has expired.", 401)

    @jwt.revoked_token_loader
    def revoked_token(_jwt_header: dict, _jwt_payload: dict):
        return error_response(
            "session_revoked",
            "The session is no longer active.",
            401,
        )

    @flask_app.errorhandler(SQLAlchemyError)
    def unhandled_database_error(_error: SQLAlchemyError):
        flask_app.logger.error("Unhandled database operation failure")
        db.session.rollback()
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )

    @flask_app.cli.command("init-db")
    def init_db_command() -> None:
        """Create all tables in a new, empty database."""
        if inspect(db.engine).has_table("users"):
            raise click.ClickException(
                "The database already has application tables. Use upgrade-db."
            )
        db.create_all()
        stamp(revision="head")
        click.echo("Database tables created and stamped successfully.")

    @flask_app.cli.command("upgrade-db")
    def upgrade_db_command() -> None:
        """Apply pending migrations to an existing database."""
        upgrade()
        click.echo("Database migrations applied successfully.")

    @flask_app.cli.command("create-admin")
    @click.option("--name", prompt="Name")
    @click.option("--birthdate", prompt="Birthdate (YYYY-MM-DD)")
    @click.option("--email", prompt="Email")
    @click.password_option(confirmation_prompt=True)
    def create_admin_command(
        name: str,
        birthdate: str,
        email: str,
        password: str,
    ) -> None:
        """Create an administrator without exposing a public admin signup."""
        try:
            admin_data = SignupRequest(
                name=name,
                birthdate=birthdate,
                email=email,
                password=password,
            )
            create_user(admin_data, role=ADMIN_USER_ROLE)
        except ValidationError as error:
            raise click.ClickException(_validation_summary(error)) from error
        except (UserValidationError, DuplicateEmailError) as error:
            raise click.ClickException(str(error)) from error
        except SQLAlchemyError as error:
            raise click.ClickException(
                "The administrator could not be created."
            ) from error
        click.echo("Administrator created successfully.")

    @flask_app.cli.command("export-openapi")
    def export_openapi_command() -> None:
        """Export the generated OpenAPI contract for external API clients."""
        output_path = (
            Path(flask_app.root_path).resolve().parent / "docs" / "openapi.json"
        )
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(
                flask_app.api_doc,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            output_path.write_text(
                serialized + chr(10),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as error:
            raise click.ClickException(
                "The OpenAPI contract could not be exported."
            ) from error
        click.echo(str(output_path))

    return flask_app
