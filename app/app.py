import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

import click
from dotenv import load_dotenv
from flask import Flask
from flask_migrate import stamp, upgrade
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from .errors import error_response
from .extensions import cors, db, jwt, migrate


def _cors_origins() -> list[str]:
    configured = os.getenv("CORS_ORIGINS", "http://localhost:5173")
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


def create_app(test_config: Mapping[str, Any] | None = None) -> Flask:
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path)

    database_uri = (
        test_config.get("SQLALCHEMY_DATABASE_URI")
        if test_config is not None
        else None
    ) or os.getenv("SQLALCHEMY_DATABASE_URI")
    if not database_uri:
        raise RuntimeError(
            "SQLALCHEMY_DATABASE_URI is not configured. "
            "Add it to app/.env or to the process environment."
        )

    flask_app = Flask(__name__)
    flask_app.config.update(
        SQLALCHEMY_DATABASE_URI=database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=os.getenv("JWT_SECRET_KEY"),
        JWT_ACCESS_TOKEN_EXPIRES=timedelta(minutes=15),
        JWT_REFRESH_TOKEN_EXPIRES=timedelta(days=30),
        JWT_TOKEN_LOCATION=["headers"],
    )
    if test_config:
        flask_app.config.update(test_config)

    db.init_app(flask_app)
    jwt.init_app(flask_app)
    migrate.init_app(flask_app, db)
    allowed_origins = _cors_origins()
    cors.init_app(
        flask_app,
        resources={
            r"/auth(?:/.*)?": {"origins": allowed_origins},
            r"/sports(?:/.*)?": {"origins": allowed_origins},
            r"/users(?:/.*)?": {"origins": allowed_origins},
        },
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        supports_credentials=False,
        max_age=600,
        vary_header=True,
    )

    from . import models as _models
    from .models import ADMIN_USER_ROLE
    from .routes.admin_users import users_bp
    from .routes.sports import sports_bp
    from .routes.users import auth_bp
    from .services.auth import is_token_revoked
    from .services.users import (
        DuplicateEmailError,
        UserValidationError,
        create_user,
    )

    flask_app.register_blueprint(auth_bp)
    flask_app.register_blueprint(sports_bp)
    flask_app.register_blueprint(users_bp)

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
        return error_response("session_revoked", "The session is no longer active.", 401)

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
            create_user(
                {
                    "name": name,
                    "birthdate": birthdate,
                    "email": email,
                    "password": password,
                },
                role=ADMIN_USER_ROLE,
            )
        except (UserValidationError, DuplicateEmailError) as error:
            raise click.ClickException(str(error)) from error
        except SQLAlchemyError as error:
            raise click.ClickException(
                "The administrator could not be created."
            ) from error
        click.echo("Administrator created successfully.")

    return flask_app
