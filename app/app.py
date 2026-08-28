import os
from datetime import timedelta
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask
from flask_migrate import stamp, upgrade
from sqlalchemy import inspect

from .errors import error_response
from .extensions import cors, db, jwt, migrate


def _cors_origins() -> list[str]:
    configured = os.getenv("CORS_ORIGINS", "http://localhost:5173")
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


def create_app() -> Flask:
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path)

    database_uri = os.getenv("SQLALCHEMY_DATABASE_URI")
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

    db.init_app(flask_app)
    jwt.init_app(flask_app)
    migrate.init_app(flask_app, db)
    cors.init_app(
        flask_app,
        resources={r"/auth/*": {"origins": _cors_origins()}},
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "DELETE", "OPTIONS"],
        supports_credentials=False,
        max_age=600,
        vary_header=True,
    )

    from . import models as _models
    from .routes.users import auth_bp
    from .services.auth import is_token_revoked

    flask_app.register_blueprint(auth_bp)

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

    return flask_app
