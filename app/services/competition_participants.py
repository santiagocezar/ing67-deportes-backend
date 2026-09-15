from ..extensions import db
from ..models import (
    DEFAULT_USER_ROLE,
    CompetitionReferee,
    CompetitionRosterPlayer,
    CompetitionTeam,
    Player,
    Sport,
    Team,
    User,
    team_players,
)
from ..schemas.competitions import ParticipantsRequest
from .competition_state import CompetitionServiceError


def lock_entities(model, identifiers: list[int]):
    if not identifiers:
        return []
    return list(
        db.session.execute(
            db.select(model)
            .where(model.id.in_(identifiers))
            .order_by(model.id)
            .with_for_update(of=model)
        ).scalars()
    )


def build_participant_entries(
    data: ParticipantsRequest,
    sport: Sport,
    gender: str,
) -> tuple[list[CompetitionTeam], list[CompetitionReferee]]:
    team_ids = sorted(entry.team_id for entry in data.teams)
    player_ids = sorted(
        {player_id for entry in data.teams for player_id in entry.player_ids}
    )
    referee_ids = sorted(data.referee_ids)

    players = lock_entities(Player, player_ids)
    teams = lock_entities(Team, team_ids)
    referees = lock_entities(User, referee_ids)
    if [team.id for team in teams] != team_ids:
        raise CompetitionServiceError(
            "team_not_found", "A selected Team does not exist.", 404
        )
    if [player.id for player in players] != player_ids:
        raise CompetitionServiceError(
            "player_not_found", "A selected Player does not exist.", 404
        )
    if [referee.id for referee in referees] != referee_ids:
        raise CompetitionServiceError(
            "referee_not_found", "A selected referee does not exist.", 404
        )

    teams_by_id = {team.id: team for team in teams}
    players_by_id = {player.id: player for player in players}
    memberships = set(
        db.session.execute(
            db.select(team_players.c.team_id, team_players.c.player_id).where(
                team_players.c.team_id.in_(team_ids),
                team_players.c.player_id.in_(player_ids),
            )
        ).all()
    )
    seen_players: set[int] = set()
    team_entries: list[CompetitionTeam] = []

    for request_entry in data.teams:
        team = teams_by_id[request_entry.team_id]
        if not team.is_enabled:
            raise CompetitionServiceError(
                "team_disabled", "A selected Team is disabled."
            )
        if team.sport_id != sport.id:
            raise CompetitionServiceError(
                "team_sport_mismatch",
                "Every Team must use the Competition Sport.",
            )
        if team.gender_category != gender:
            raise CompetitionServiceError(
                "team_gender_mismatch",
                "Every Team must use the Competition gender.",
            )
        if not (
            sport.max_players_in_game
            <= len(request_entry.player_ids)
            <= sport.max_players
        ):
            raise CompetitionServiceError(
                "competition_roster_size_invalid",
                "Each roster must satisfy the Sport player limits.",
            )

        roster_entries: list[CompetitionRosterPlayer] = []
        for player_id in request_entry.player_ids:
            player = players_by_id[player_id]
            if player_id in seen_players:
                raise CompetitionServiceError(
                    "competition_player_already_registered",
                    "A Player may represent only one Team per Competition.",
                )
            if not player.is_enabled:
                raise CompetitionServiceError(
                    "player_disabled", "A selected Player is disabled."
                )
            if (team.id, player.id) not in memberships:
                raise CompetitionServiceError(
                    "player_not_in_team",
                    "Every roster Player must currently belong to its Team.",
                )
            if player.sport_id != sport.id or player.gender != gender:
                raise CompetitionServiceError(
                    "player_competition_mismatch",
                    "Every roster Player must match the Competition.",
                )
            seen_players.add(player_id)
            roster_entries.append(CompetitionRosterPlayer(player=player))

        team_entries.append(
            CompetitionTeam(team=team, roster_entries=roster_entries)
        )

    if any(referee.role != DEFAULT_USER_ROLE for referee in referees):
        raise CompetitionServiceError(
            "invalid_referee_role",
            "Every selected referee must have the referee role.",
        )
    referee_entries = [
        CompetitionReferee(referee=referee) for referee in referees
    ]
    return team_entries, referee_entries
