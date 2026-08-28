from .app import create_app
from .extensions import db


app = create_app()

__all__ = ["app", "create_app", "db"]
