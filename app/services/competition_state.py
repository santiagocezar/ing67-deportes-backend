from datetime import datetime, timezone

from sqlalchemy import and_, func, or_

from ..extensions import db
from ..models import Competition, CompetitionTeam, Match


class CompetitionServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def lock_competition(competition_id: int) -> Competition:
    competition = db.session.execute(
        db.select(Competition)
        .where(Competition.id == competition_id)
        .with_for_update(of=Competition)
    ).scalar_one_or_none()
    if competition is None:
        raise CompetitionServiceError(
            "competition_not_found",
            "The Competition does not exist.",
            404,
        )
    return competition


def has_matches(competition_id: int) -> bool:
    return db.session.execute(
        db.select(Match.id)
        .where(Match.competition_id == competition_id)
        .limit(1)
    ).scalar_one_or_none() is not None


def _ended_competition_counts(
    competition_ids: list[int],
) -> tuple[dict[int, int], dict[int, tuple[int, int]]]:
    if not competition_ids:
        return {}, {}
    team_counts = dict(
        db.session.execute(
            db.select(
                CompetitionTeam.competition_id,
                func.count(CompetitionTeam.team_id),
            )
            .where(CompetitionTeam.competition_id.in_(competition_ids))
            .group_by(CompetitionTeam.competition_id)
        ).all()
    )
    match_counts = {
        competition_id: (total, scheduled)
        for competition_id, total, scheduled in db.session.execute(
            db.select(
                Match.competition_id,
                func.count(Match.id),
                func.count(Match.starts_at),
            )
            .where(Match.competition_id.in_(competition_ids))
            .group_by(Match.competition_id)
        ).all()
    }
    return team_counts, match_counts


def reconcile_competitions(
    now: datetime | None = None,
    *,
    competition_id: int | None = None,
) -> bool:
    current_time = as_utc(now or utc_now())
    stale_state = or_(
        and_(
            Competition.ends_at <= current_time,
            Competition.status != "finished",
        ),
        and_(
            Competition.starts_at <= current_time,
            Competition.ends_at > current_time,
            Competition.status != "in_progress",
        ),
        and_(
            Competition.starts_at > current_time,
            Competition.status != "scheduled",
        ),
    )
    statement = (
        db.select(Competition)
        .where(Competition.is_enabled.is_(True), stale_state)
        .with_for_update(of=Competition)
    )
    if competition_id is not None:
        statement = statement.where(Competition.id == competition_id)
    competitions = list(
        db.session.execute(statement).scalars()
    )
    ended_ids = [
        competition.id
        for competition in competitions
        if competition.ends_at <= current_time
    ]
    team_counts, match_counts = _ended_competition_counts(ended_ids)
    changed = False

    for competition in competitions:
        if competition.ends_at <= current_time:
            team_count = team_counts.get(competition.id, 0)
            total, scheduled = match_counts.get(competition.id, (0, 0))
            expected = 3 * team_count // 2
            if total == expected and scheduled == expected:
                new_status = "finished"
            else:
                new_status = "discarded"
                competition.is_enabled = False
                competition.disabled_at = competition.ends_at
        elif competition.starts_at <= current_time:
            new_status = "in_progress"
        else:
            new_status = "scheduled"

        if competition.status != new_status:
            competition.status = new_status
            changed = True
        if new_status == "discarded":
            changed = True
    return changed
