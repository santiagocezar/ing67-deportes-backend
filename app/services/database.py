from sqlalchemy.exc import IntegrityError


def constraint_name(error: IntegrityError) -> str | None:
    """Return a PostgreSQL constraint name without exposing database details."""
    original = getattr(error, "orig", None)
    diagnostic = getattr(original, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
