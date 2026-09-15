import random
from datetime import datetime

from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..models import (
    DEFAULT_USER_ROLE,
    Competition,
    CompetitionRosterPlayer,
    CompetitionTeam,
    Match,
    Team,
    User,
)
from ..schemas.matches import MatchUpdateRequest
from .competition_participants import lock_entities
from .competition_state import (
    CompetitionServiceError,
    as_utc,
    lock_competition,
    reconcile_competitions,
    utc_now,
)


def _match_options():
    competition = selectinload(Match.competition)
    return (
        joinedload(Match.team_1),
        joinedload(Match.team_2),
        joinedload(Match.referee),
        competition.joinedload(Competition.sport),
        competition.selectinload(Competition.team_entries).joinedload(
            CompetitionTeam.team
        ),
        competition.selectinload(Competition.team_entries)
        .selectinload(CompetitionTeam.roster_entries)
        .joinedload(CompetitionRosterPlayer.player),
    )


def _load_match(match_id: int) -> Match:
    match = db.session.execute(
        db.select(Match)
        .options(*_match_options())
        .where(Match.id == match_id)
    ).scalar_one_or_none()
    if match is None:
        raise CompetitionServiceError(
            "match_not_found", "The Match does not exist.", 404
        )
    return match


def _lock_match(match_id: int, competition_id: int) -> Match:
    match = db.session.execute(
        db.select(Match)
        .where(
            Match.id == match_id,
            Match.competition_id == competition_id,
        )
        .with_for_update(of=Match)
    ).scalar_one_or_none()
    if match is None:
        raise CompetitionServiceError(
            "match_not_found", "The Match does not exist.", 404
        )
    return match


def _match_status(match: Match, now: datetime) -> str:
    if match.starts_at is None or match.ends_at is None:
        return "incomplete"
    if now < match.starts_at:
        return "scheduled"
    if now < match.ends_at:
        return "in_progress"
    return "finished"


def reconcile_matches(
    now: datetime | None = None,
    *,
    competition_id: int | None = None,
    match_id: int | None = None,
) -> bool:
    current_time = as_utc(now or utc_now())
    stale_state = or_(
        and_(
            Match.starts_at.is_(None),
            Match.status != "incomplete",
        ),
        and_(
            Match.starts_at.is_not(None),
            Match.starts_at > current_time,
            Match.status != "scheduled",
        ),
        and_(
            Match.starts_at <= current_time,
            Match.ends_at > current_time,
            Match.status != "in_progress",
        ),
        and_(
            Match.ends_at <= current_time,
            Match.status != "finished",
        ),
    )
    statement = db.select(Match).where(stale_state).with_for_update(of=Match)
    if competition_id is not None:
        statement = statement.where(Match.competition_id == competition_id)
    if match_id is not None:
        statement = statement.where(Match.id == match_id)
    changed = False
    for match in db.session.execute(statement).scalars():
        status = _match_status(match, current_time)
        if match.status != status:
            match.status = status
            changed = True
    return changed


def _round_robin_draw(
    team_ids: list[int],
    referee_ids: list[int],
    rng,
) -> list[Match]:
    rotating = team_ids.copy()
    rng.shuffle(rotating)
    matches: list[Match] = []

    for round_number in range(1, 4):
        round_referees = referee_ids.copy()
        rng.shuffle(round_referees)
        for index in range(len(rotating) // 2):
            team_1_id, team_2_id = sorted(
                (rotating[index], rotating[-index - 1])
            )
            matches.append(
                Match(
                    team_1_id=team_1_id,
                    team_2_id=team_2_id,
                    referee_id=round_referees[index],
                    round_number=round_number,
                    starts_at=None,
                    ends_at=None,
                    status="incomplete",
                )
            )
        rotating = [rotating[0], rotating[-1], *rotating[1:-1]]
    return matches


def randomize_matches(
    competition_id: int,
    *,
    rng=None,
) -> list[Match]:
    now = utc_now()
    generator = rng or random.SystemRandom()
    try:
        competition = lock_competition(competition_id)
        reconcile_competitions(now, competition_id=competition.id)
        if competition.status == "finished":
            raise CompetitionServiceError(
                "competition_finished",
                "A finished Competition cannot be randomized.",
            )
        if not competition.is_enabled:
            raise CompetitionServiceError(
                "competition_disabled",
                "A disabled Competition cannot be randomized.",
            )

        team_ids = sorted(
            db.session.execute(
                db.select(CompetitionTeam.team_id).where(
                    CompetitionTeam.competition_id == competition.id
                )
            ).scalars()
        )
        referee_ids = sorted(
            entry.referee_id for entry in competition.referee_entries
        )
        if (
            len(team_ids) < 4
            or len(team_ids) > 16
            or len(team_ids) % 2
            or len(referee_ids) != len(team_ids) // 2
        ):
            raise CompetitionServiceError(
                "competition_participants_invalid",
                "Competition participants are incomplete or inconsistent.",
            )
        teams = lock_entities(Team, team_ids)
        referees = lock_entities(User, referee_ids)
        if any(not team.is_enabled for team in teams):
            raise CompetitionServiceError(
                "team_disabled",
                "Every registered Team must be enabled before randomization.",
            )
        if any(referee.role != DEFAULT_USER_ROLE for referee in referees):
            raise CompetitionServiceError(
                "invalid_referee_role",
                "Every registered referee must still have the referee role.",
            )

        existing_matches = list(
            db.session.execute(
                db.select(Match)
                .where(Match.competition_id == competition.id)
                .with_for_update(of=Match)
            ).scalars()
        )
        if any(match.starts_at is not None for match in existing_matches):
            raise CompetitionServiceError(
                "competition_draw_locked",
                "A draw cannot be replaced after a Match is scheduled.",
            )
        for match in existing_matches:
            db.session.delete(match)
        if existing_matches:
            db.session.flush()

        draw = _round_robin_draw(team_ids, referee_ids, generator)
        for match in draw:
            match.competition = competition
        db.session.add_all(draw)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return list_matches(competition_id)


def _validate_schedule(
    competition: Competition,
    starts_at: datetime,
    ends_at: datetime,
    now: datetime,
) -> tuple[datetime, datetime]:
    start = as_utc(starts_at)
    end = as_utc(ends_at)
    if end <= start:
        raise CompetitionServiceError(
            "match_date_range_invalid",
            "Match end date must be later than its start date.",
            422,
        )
    if start <= now:
        raise CompetitionServiceError(
            "match_retroactive_schedule",
            "Match start date must be in the future.",
            422,
        )
    if start < competition.starts_at or end > competition.ends_at:
        raise CompetitionServiceError(
            "match_outside_competition",
            "The Match schedule must fit inside the Competition.",
        )
    return start, end


def _has_overlap(
    match_id: int,
    team_ids: list[int],
    referee_id: int,
    starts_at: datetime,
    ends_at: datetime,
) -> str | None:
    interval_filters = (
        Match.id != match_id,
        Match.starts_at.is_not(None),
        Match.starts_at < ends_at,
        Match.ends_at > starts_at,
    )
    team_overlap = db.session.execute(
        db.select(Match.id)
        .where(
            *interval_filters,
            or_(
                Match.team_1_id.in_(team_ids),
                Match.team_2_id.in_(team_ids),
            ),
        )
        .limit(1)
    ).scalar_one_or_none()
    if team_overlap is not None:
        return "team"
    referee_overlap = db.session.execute(
        db.select(Match.id)
        .where(
            *interval_filters,
            Match.referee_id == referee_id,
        )
        .limit(1)
    ).scalar_one_or_none()
    return "referee" if referee_overlap is not None else None


def update_match(match_id: int, data: MatchUpdateRequest) -> Match:
    now = utc_now()
    try:
        match_reference = db.session.get(Match, match_id)
        if match_reference is None:
            raise CompetitionServiceError(
                "match_not_found", "The Match does not exist.", 404
            )
        competition = lock_competition(match_reference.competition_id)
        match = _lock_match(match_id, competition.id)
        reconcile_competitions(now, competition_id=competition.id)
        reconcile_matches(now, match_id=match.id)
        if competition.status == "finished":
            raise CompetitionServiceError(
                "competition_finished",
                "A finished Competition cannot schedule Matches.",
            )
        if not competition.is_enabled:
            raise CompetitionServiceError(
                "competition_disabled",
                "A disabled Competition cannot schedule Matches.",
            )
        if match.starts_at is not None and match.starts_at <= now:
            raise CompetitionServiceError(
                "match_already_started",
                "A started Match cannot be rescheduled.",
            )

        start, end = _validate_schedule(
            competition, data.starts_at, data.ends_at, now
        )
        team_ids = sorted((match.team_1_id, match.team_2_id))
        teams = lock_entities(Team, team_ids)
        referees = lock_entities(User, [match.referee_id])
        if any(not team.is_enabled for team in teams):
            raise CompetitionServiceError(
                "team_disabled", "A disabled Team cannot schedule a Match."
            )
        if not referees or referees[0].role != DEFAULT_USER_ROLE:
            raise CompetitionServiceError(
                "invalid_referee_role",
                "The assigned User must still have the referee role.",
            )
        overlapping_resource = _has_overlap(
            match.id,
            team_ids,
            match.referee_id,
            start,
            end,
        )
        if overlapping_resource is not None:
            raise CompetitionServiceError(
                f"match_{overlapping_resource}_overlap",
                f"The {overlapping_resource} has an overlapping Match.",
            )
        match.starts_at = start
        match.ends_at = end
        match.status = "scheduled"
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return _load_match(match_id)


def get_match(match_id: int) -> Match:
    try:
        reference = db.session.get(Match, match_id)
        if reference is None:
            raise CompetitionServiceError(
                "match_not_found", "The Match does not exist.", 404
            )
        changed = reconcile_competitions(
            competition_id=reference.competition_id
        )
        changed = reconcile_matches(match_id=match_id) or changed
        if changed:
            db.session.commit()
        return _load_match(match_id)
    except Exception:
        db.session.rollback()
        raise


def list_matches(competition_id: int) -> list[Match]:
    try:
        competition_exists = db.session.get(Competition, competition_id)
        if competition_exists is None:
            raise CompetitionServiceError(
                "competition_not_found",
                "The Competition does not exist.",
                404,
            )
        changed = reconcile_competitions(competition_id=competition_id)
        changed = reconcile_matches(competition_id=competition_id) or changed
        if changed:
            db.session.commit()
        return list(
            db.session.execute(
                db.select(Match)
                .options(*_match_options())
                .where(Match.competition_id == competition_id)
                .order_by(Match.round_number, Match.id)
            ).scalars()
        )
    except Exception:
        db.session.rollback()
        raise
