import unicodedata
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..models import (
    Competition,
    CompetitionReferee,
    CompetitionRosterPlayer,
    CompetitionTeam,
    Sport,
    team_players,
)
from ..schemas.competitions import (
    CompetitionCreateRequest,
    CompetitionListQuery,
    CompetitionUpdateRequest,
    ParticipantsRequest,
)
from .competition_participants import build_participant_entries
from .competition_state import (
    CompetitionServiceError,
    as_utc,
    has_matches,
    lock_competition,
    reconcile_competitions,
    utc_now,
)


COMPETITION_PAGE_SIZE = 25


@dataclass(frozen=True)
class CompetitionPage:
    competitions: list[Competition]
    page: int
    per_page: int
    total_items: int
    total_pages: int


def _comparison_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )


def normalize_competition_name(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise CompetitionServiceError(
            "validation_error",
            "name must be a string.",
            422,
        )
    display_name = " ".join(value.split())
    if not display_name:
        raise CompetitionServiceError(
            "validation_error",
            "name is required.",
            422,
        )
    if len(display_name) > 100:
        raise CompetitionServiceError(
            "validation_error",
            "name must contain at most 100 characters.",
            422,
        )
    return display_name, _comparison_name(display_name)


def _competition_options():
    return (
        joinedload(Competition.sport),
        selectinload(Competition.team_entries).joinedload(
            CompetitionTeam.team
        ),
        selectinload(Competition.team_entries)
        .selectinload(CompetitionTeam.roster_entries)
        .joinedload(CompetitionRosterPlayer.player),
        selectinload(Competition.referee_entries).joinedload(
            CompetitionReferee.referee
        ),
    )


def _load_competition(competition_id: int) -> Competition:
    competition = db.session.execute(
        db.select(Competition)
        .options(*_competition_options())
        .where(Competition.id == competition_id)
    ).scalar_one_or_none()
    if competition is None:
        raise CompetitionServiceError(
            "competition_not_found",
            "The Competition does not exist.",
            404,
        )
    return competition


def _validate_future_end(ends_at: datetime, now: datetime) -> None:
    if as_utc(ends_at) <= now:
        raise CompetitionServiceError(
            "competition_end_not_future",
            "Competition end date must be in the future.",
            422,
        )


def roster_import_options(competition_id: int, source_id: int) -> dict:
    """Return complete historical rosters that still satisfy enrollment rules."""
    from .sanctions import reconcile_player_sanctions_batch

    try:
        now = utc_now()
        for identifier in sorted({competition_id, source_id}):
            lock_competition(identifier)
            reconcile_competitions(now, competition_id=identifier)
        destination = _load_competition(competition_id)
        source = _load_competition(source_id)
        if not destination.is_enabled:
            raise CompetitionServiceError(
                "competition_disabled", "The destination is disabled."
            )
        if has_matches(competition_id):
            raise CompetitionServiceError(
                "competition_has_matches", "The destination already has Matches."
            )
        if source_id == competition_id:
            raise CompetitionServiceError(
                "import_same_competition", "Source and destination must differ.", 422
            )
        if source.status != "finished":
            raise CompetitionServiceError(
                "import_source_not_finished", "The source Competition must be finished."
            )
        if (source.sport_id, source.gender) != (destination.sport_id, destination.gender):
            raise CompetitionServiceError(
                "import_competition_mismatch",
                "Competitions must have the same Sport and gender.",
                422,
            )

        destination_teams = {entry.team_id for entry in destination.team_entries}
        entries = [
            entry for entry in source.team_entries if entry.team_id in destination_teams
        ]
        player_ids = sorted(
            {item.player.id for entry in entries for item in entry.roster_entries}
        )
        sanctions = reconcile_player_sanctions_batch(player_ids)
        memberships = set(
            db.session.execute(
                db.select(team_players.c.team_id, team_players.c.player_id).where(
                    team_players.c.team_id.in_(destination_teams),
                    team_players.c.player_id.in_(player_ids),
                )
            ).all()
        )
        teams = []
        for entry in entries:
            players = [item.player for item in entry.roster_entries]
            if (
                not entry.team.is_enabled
                or entry.team.sport_id != destination.sport_id
                or entry.team.gender_category != destination.gender
                or not (
                    destination.sport.max_players_in_game
                    <= len(players)
                    <= destination.sport.max_players
                )
            ):
                continue
            if any(
                not player.is_enabled
                or player.sport_id != destination.sport_id
                or player.gender != destination.gender
                or (entry.team_id, player.id) not in memberships
                or sanctions.get(player.id, False)
                for player in players
            ):
                continue
            teams.append({
                "team_id": entry.team_id,
                "name": entry.team.name,
                "players": [{"player_id": p.id, "name": p.name} for p in players],
            })
        result = {
            "source": {"id": source.id, "name": source.name},
            "destination": {"id": destination.id, "name": destination.name},
            "teams": teams,
        }
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def create_competition(data: CompetitionCreateRequest) -> Competition:
    now = utc_now()
    _validate_future_end(data.ends_at, now)
    display_name, normalized_name = normalize_competition_name(data.name)

    try:
        sport = db.session.get(Sport, data.sport_id)
        if sport is None:
            raise CompetitionServiceError(
                "sport_not_found", "The Sport does not exist.", 404
            )
        team_entries, referee_entries = build_participant_entries(
            data, sport, data.gender
        )
        competition = Competition(
            name=display_name,
            normalized_name=normalized_name,
            sport=sport,
            gender=data.gender,
            starts_at=as_utc(data.starts_at),
            ends_at=as_utc(data.ends_at),
            status=(
                "scheduled" if as_utc(data.starts_at) > now else "in_progress"
            ),
            is_enabled=True,
            disabled_at=None,
            team_entries=team_entries,
            referee_entries=referee_entries,
        )
        db.session.add(competition)
        db.session.commit()
        competition_id = competition.id
    except Exception:
        db.session.rollback()
        raise
    return _load_competition(competition_id)


def get_competition(competition_id: int) -> Competition:
    try:
        if reconcile_competitions(competition_id=competition_id):
            db.session.commit()
        return _load_competition(competition_id)
    except Exception:
        db.session.rollback()
        raise


def update_competition(
    competition_id: int,
    data: CompetitionUpdateRequest,
) -> Competition:
    now = utc_now()
    _validate_future_end(data.ends_at, now)
    display_name, normalized_name = normalize_competition_name(data.name)
    try:
        competition = lock_competition(competition_id)
        reconcile_competitions(now, competition_id=competition.id)
        if not competition.is_enabled:
            raise CompetitionServiceError(
                "competition_disabled",
                "A disabled Competition cannot be updated.",
            )
        if has_matches(competition.id):
            raise CompetitionServiceError(
                "competition_has_matches",
                "A Competition with Matches cannot be updated.",
            )
        competition.name = display_name
        competition.normalized_name = normalized_name
        competition.starts_at = as_utc(data.starts_at)
        competition.ends_at = as_utc(data.ends_at)
        competition.status = (
            "scheduled" if competition.starts_at > now else "in_progress"
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return _load_competition(competition_id)


def replace_participants(
    competition_id: int,
    data: ParticipantsRequest,
) -> Competition:
    now = utc_now()
    try:
        competition = lock_competition(competition_id)
        reconcile_competitions(now, competition_id=competition.id)
        if not competition.is_enabled:
            raise CompetitionServiceError(
                "competition_disabled",
                "A disabled Competition cannot change participants.",
            )
        if has_matches(competition.id):
            raise CompetitionServiceError(
                "competition_has_matches",
                "Participants are immutable after Matches are generated.",
            )
        original_count = db.session.execute(
            db.select(func.count(CompetitionTeam.team_id)).where(
                CompetitionTeam.competition_id == competition.id
            )
        ).scalar_one()
        if len(data.teams) != original_count:
            raise CompetitionServiceError(
                "competition_team_count_immutable",
                "The Competition Team count is immutable.",
            )
        team_entries, referee_entries = build_participant_entries(
            data, competition.sport, competition.gender
        )
        competition.team_entries.clear()
        competition.referee_entries.clear()
        db.session.flush()
        competition.team_entries.extend(team_entries)
        competition.referee_entries.extend(referee_entries)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return _load_competition(competition_id)


def set_competition_enabled(
    competition_id: int,
    *,
    enabled: bool,
) -> Competition:
    now = utc_now()
    try:
        competition = lock_competition(competition_id)
        reconcile_competitions(now, competition_id=competition.id)
        if competition.is_enabled == enabled:
            db.session.commit()
            return _load_competition(competition_id)
        if has_matches(competition.id):
            raise CompetitionServiceError(
                "competition_has_matches",
                "A Competition with Matches cannot change availability.",
            )
        if enabled and competition.ends_at <= now:
            raise CompetitionServiceError(
                "competition_ended",
                "An ended Competition cannot be enabled.",
            )
        competition.is_enabled = enabled
        competition.disabled_at = None if enabled else now
        competition.status = (
            "scheduled"
            if enabled and competition.starts_at > now
            else "in_progress"
            if enabled
            else "discarded"
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return _load_competition(competition_id)


def list_competitions(query: CompetitionListQuery) -> CompetitionPage:
    try:
        if query.sport_id is not None and db.session.get(
            Sport, query.sport_id
        ) is None:
            raise CompetitionServiceError(
                "sport_not_found", "The Sport does not exist.", 404
            )
        if reconcile_competitions():
            db.session.commit()

        filters = []
        compact_search = " ".join((query.search or "").split())
        search = _comparison_name(compact_search) if compact_search else None
        if search is not None:
            filters.append(
                Competition.normalized_name.contains(search, autoescape=True)
            )
        if query.sport_id is not None:
            filters.append(Competition.sport_id == query.sport_id)
        if query.gender is not None:
            filters.append(Competition.gender == query.gender)
        if query.lifecycle_status is not None:
            filters.append(Competition.status == query.lifecycle_status)
        if query.availability == "enabled":
            filters.append(Competition.is_enabled.is_(True))
        elif query.availability == "disabled":
            filters.append(Competition.is_enabled.is_(False))
        if query.starts_from is not None:
            filters.append(Competition.starts_at >= as_utc(query.starts_from))
        if query.starts_to is not None:
            filters.append(Competition.starts_at <= as_utc(query.starts_to))

        count_statement = db.select(func.count()).select_from(Competition)
        statement = db.select(Competition).options(
            joinedload(Competition.sport),
            selectinload(Competition.team_entries),
        )
        if filters:
            count_statement = count_statement.where(*filters)
            statement = statement.where(*filters)
        if query.sort == "starts_at_asc":
            statement = statement.order_by(
                Competition.starts_at.asc(), Competition.id.asc()
            )
        else:
            statement = statement.order_by(
                Competition.starts_at.desc(), Competition.id.desc()
            )

        total_items = db.session.execute(count_statement).scalar_one()
        competitions = list(
            db.session.execute(
                statement.offset(
                    (query.page - 1) * COMPETITION_PAGE_SIZE
                ).limit(COMPETITION_PAGE_SIZE)
            ).scalars()
        )
        total_pages = (
            (total_items + COMPETITION_PAGE_SIZE - 1)
            // COMPETITION_PAGE_SIZE
            if total_items
            else 0
        )
        return CompetitionPage(
            competitions,
            query.page,
            COMPETITION_PAGE_SIZE,
            total_items,
            total_pages,
        )
    except Exception:
        db.session.rollback()
        raise
