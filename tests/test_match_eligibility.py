from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.services.match_eligibility import (
    _resolve_applicable_sanctions,
    get_player_match_eligibility,
)


UTC = timezone.utc


def _match(
    match_id: int,
    starts_at: datetime,
    ends_at: datetime,
    *,
    team_1_id: int = 1,
    team_2_id: int = 2,
):
    return SimpleNamespace(
        id=match_id,
        starts_at=starts_at,
        ends_at=ends_at,
        team_1_id=team_1_id,
        team_2_id=team_2_id,
    )


def _sanction(
    sanction_id: int,
    effective_at: datetime,
    *,
    source_match_id: int = 99,
    duration: int = 1,
    frozen_team_ids: tuple[int, ...] = (1,),
    status: str = "active",
    annulled_at: datetime | None = None,
):
    return SimpleNamespace(
        id=sanction_id,
        title=f"Sanction {sanction_id}",
        reason=f"Reason {sanction_id}",
        source_match_id=source_match_id,
        suspension_matches=duration,
        effective_at=effective_at,
        status=status,
        annulled_at=annulled_at,
        frozen_team_entries=[
            SimpleNamespace(team_id=team_id) for team_id in frozen_team_ids
        ],
    )


class TestHistoricalMatchEligibility(unittest.TestCase):
    def test_sanction_effective_after_target_is_not_applicable(self):
        target = _match(
            7,
            datetime(2026, 10, 15, 10, tzinfo=UTC),
            datetime(2026, 10, 15, 11, tzinfo=UTC),
        )
        sanction = _sanction(
            3, datetime(2026, 10, 15, 11, tzinfo=UTC)
        )

        assert _resolve_applicable_sanctions(target, [sanction], [target]) == ()

    def test_source_match_never_blocks_its_own_sanction(self):
        source_match = _match(
            7,
            datetime(2026, 10, 15, 10, tzinfo=UTC),
            datetime(2026, 10, 15, 11, tzinfo=UTC),
        )
        sanction = _sanction(
            3,
            datetime(2026, 10, 15, 9, tzinfo=UTC),
            source_match_id=source_match.id,
        )

        assert (
            _resolve_applicable_sanctions(
                source_match, [sanction], [source_match]
            )
            == ()
        )

    def test_served_current_sanction_is_reported_for_its_target_match(self):
        target = _match(
            7,
            datetime(2026, 10, 15, 10, tzinfo=UTC),
            datetime(2026, 10, 15, 11, tzinfo=UTC),
        )
        sanction = _sanction(
            3,
            datetime(2026, 10, 15, 9, tzinfo=UTC),
            status="served",
        )

        result = _resolve_applicable_sanctions(target, [sanction], [target])

        assert [item.sanction_id for item in result] == [3]
        assert result[0].remaining_matches == 1

    def test_strict_boundaries_and_queued_activation_are_preserved(self):
        effective_at = datetime(2026, 10, 15, 9, tzinfo=UTC)
        adjacent = _match(
            2,
            effective_at,
            datetime(2026, 10, 15, 10, tzinfo=UTC),
        )
        first_target = _match(
            3,
            datetime(2026, 10, 15, 10, tzinfo=UTC),
            datetime(2026, 10, 15, 11, tzinfo=UTC),
        )
        first = _sanction(1, effective_at, duration=1)

        # A Match starting exactly at effective_at cannot consume the sanction.
        result = _resolve_applicable_sanctions(
            first_target, [first], [adjacent, first_target]
        )
        assert [item.remaining_matches for item in result] == [1]

        previous = _match(
            4,
            datetime(2026, 10, 15, 9, minute=1, tzinfo=UTC),
            datetime(2026, 10, 15, 10, tzinfo=UTC),
        )
        second = _sanction(2, effective_at, duration=1)
        at_served_boundary = _match(
            5,
            datetime(2026, 10, 15, 10, tzinfo=UTC),
            datetime(2026, 10, 15, 11, tzinfo=UTC),
        )
        queued = _sanction(3, effective_at, duration=1)
        assert _resolve_applicable_sanctions(
            at_served_boundary,
            [second, queued],
            [previous, at_served_boundary],
        ) == ()

        after_served_boundary = _match(
            6,
            datetime(2026, 10, 15, 10, minute=1, tzinfo=UTC),
            datetime(2026, 10, 15, 11, minute=1, tzinfo=UTC),
        )
        result = _resolve_applicable_sanctions(
            after_served_boundary,
            [second, queued],
            [previous, after_served_boundary],
        )
        assert [item.sanction_id for item in result] == [3]

    def test_overlap_groups_frozen_teams_and_disabled_player_state(self):
        effective_at = datetime(2026, 10, 15, 8, tzinfo=UTC)
        first = _match(
            1,
            datetime(2026, 10, 15, 8, minute=1, tzinfo=UTC),
            datetime(2026, 10, 15, 9, tzinfo=UTC),
        )
        overlapping = _match(
            2,
            datetime(2026, 10, 15, 8, minute=30, tzinfo=UTC),
            datetime(2026, 10, 15, 9, minute=30, tzinfo=UTC),
        )
        target = _match(
            3,
            datetime(2026, 10, 15, 10, tzinfo=UTC),
            datetime(2026, 10, 15, 11, tzinfo=UTC),
        )
        sanction = _sanction(5, effective_at, duration=2, frozen_team_ids=(1, 4))

        result = _resolve_applicable_sanctions(
            target, [sanction], [first, overlapping, target]
        )

        # The overlapping Matches consume one queue position, leaving one.
        assert [item.remaining_matches for item in result] == [1]

    def test_annulment_is_evaluated_at_the_target_boundary(self):
        target = _match(
            7,
            datetime(2026, 10, 15, 10, tzinfo=UTC),
            datetime(2026, 10, 15, 11, tzinfo=UTC),
        )
        before = _sanction(
            1,
            datetime(2026, 10, 15, 9, tzinfo=UTC),
            status="annulled",
            annulled_at=datetime(2026, 10, 15, 9, minute=30, tzinfo=UTC),
        )
        after = _sanction(
            2,
            datetime(2026, 10, 15, 9, tzinfo=UTC),
            status="annulled",
            annulled_at=datetime(2026, 10, 15, 10, minute=30, tzinfo=UTC),
        )

        assert _resolve_applicable_sanctions(target, [before], [target]) == ()
        assert [item.sanction_id for item in _resolve_applicable_sanctions(
            target, [after], [target]
        )] == [2]

    def test_annulled_sanction_keeps_its_already_consumed_queue_group(self):
        effective_at = datetime(2026, 10, 15, 8, tzinfo=UTC)
        consumed = _match(
            1,
            datetime(2026, 10, 15, 8, minute=1, tzinfo=UTC),
            datetime(2026, 10, 15, 9, tzinfo=UTC),
        )
        target = _match(
            2,
            datetime(2026, 10, 15, 11, tzinfo=UTC),
            datetime(2026, 10, 15, 12, tzinfo=UTC),
        )
        annulled = _sanction(
            1,
            effective_at,
            status="annulled",
            annulled_at=datetime(2026, 10, 15, 10, tzinfo=UTC),
        )
        queued = _sanction(2, effective_at)

        result = _resolve_applicable_sanctions(
            target, [annulled, queued], [consumed, target]
        )

        assert [item.sanction_id for item in result] == [2]
        assert result[0].remaining_matches == 1


class TestMatchEligibilityService(unittest.TestCase):
    @patch("app.services.match_eligibility.db")
    def test_repeated_reads_are_write_free_and_use_bounded_queries(self, mocked_db):
        target = _match(
            7,
            datetime(2026, 10, 15, 10, tzinfo=UTC),
            datetime(2026, 10, 15, 11, tzinfo=UTC),
        )
        player = SimpleNamespace(id=4, is_enabled=False)
        sanctions = [
            _sanction(
                sanction_id,
                datetime(2026, 10, 15, 9, tzinfo=UTC),
            )
            for sanction_id in range(1, 6)
        ]
        match_result = MagicMock()
        match_result.scalar_one_or_none.return_value = target
        player_result = MagicMock()
        player_result.scalar_one_or_none.return_value = player
        sanctions_result = MagicMock()
        sanctions_result.scalars.return_value = sanctions
        candidates_result = MagicMock()
        candidates_result.scalars.return_value = [target]
        mocked_db.session.no_autoflush = nullcontext()
        mocked_db.session.execute.side_effect = [
            match_result,
            player_result,
            sanctions_result,
            candidates_result,
            match_result,
            player_result,
            sanctions_result,
            candidates_result,
        ]

        first = get_player_match_eligibility(4, 7)
        second = get_player_match_eligibility(4, 7)

        assert first == second
        assert first.applicable_sanctions[0].remaining_matches == 1
        assert mocked_db.session.execute.call_count == 8
        mocked_db.session.add.assert_not_called()
        mocked_db.session.commit.assert_not_called()
        mocked_db.session.flush.assert_not_called()
