import unicodedata
from dataclasses import dataclass

from sqlalchemy import func, or_

from ..extensions import db
from ..models import DEFAULT_USER_ROLE, User
from ..schemas.referees import RefereeListQuery


REFEREE_PAGE_SIZE = 25


@dataclass(frozen=True)
class RefereePage:
    referees: list[User]
    page: int
    per_page: int
    total_items: int
    total_pages: int


def _normalized_search(value: str | None) -> str | None:
    compact = " ".join((value or "").split()).casefold()
    if not compact:
        return None
    decomposed = unicodedata.normalize("NFKD", compact)
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )


def list_referees(query: RefereeListQuery) -> RefereePage:
    filters = [User.role == DEFAULT_USER_ROLE]
    search = _normalized_search(query.search)
    if search is not None:
        normalized_name = func.translate(
            func.lower(User.name),
            "áéíóúüñ",
            "aeiouun",
        )
        filters.append(
            or_(
                normalized_name.contains(search, autoescape=True),
                func.lower(User.email).contains(search, autoescape=True),
            )
        )
    count_statement = db.select(func.count()).select_from(User).where(*filters)
    statement = (
        db.select(User)
        .where(*filters)
        .order_by(func.lower(User.name), User.id)
        .offset((query.page - 1) * REFEREE_PAGE_SIZE)
        .limit(REFEREE_PAGE_SIZE)
    )
    total_items = db.session.execute(count_statement).scalar_one()
    referees = list(db.session.execute(statement).scalars())
    total_pages = (
        (total_items + REFEREE_PAGE_SIZE - 1) // REFEREE_PAGE_SIZE
        if total_items
        else 0
    )
    return RefereePage(
        referees,
        query.page,
        REFEREE_PAGE_SIZE,
        total_items,
        total_pages,
    )
