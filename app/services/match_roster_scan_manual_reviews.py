"""Historical, one-time manual conclusions for pending roster scan reviews."""

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from flask import current_app
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import (
    ADMIN_USER_ROLE,
    DEFAULT_USER_ROLE,
    Competition,
    CompetitionRosterPlayer,
    Match,
    MatchRosterScan,
    MatchRosterScanFace,
    MatchRosterScanManualReview,
    MatchRosterScanManualReviewDecision,
    MANUAL_REVIEW_DECISION_OUTCOMES,
    Player,
    Team,
)
from .competition_state import as_utc, utc_now
from .match_roster_scans import reconcile_expired_match_roster_scan_reviews
from .match_scan_photo_files import (
    StagedScanImageDeletion,
    finalize_scan_image_deletions,
    restore_scan_image_deletions,
    stage_scan_image_file,
)
from .matches import reconcile_matches


MANUAL_REVIEWS_PER_PAGE = 25
MANUAL_REVIEW_OUTCOMES = frozenset(MANUAL_REVIEW_DECISION_OUTCOMES)
MANUAL_REVIEW_LIST_SORTS = frozenset(
    {"scan_created_at_desc", "scan_created_at_asc"}
)


class ManualReviewServiceError(Exception):
    """Safe business-rule error for manual review routes."""

    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ManualReviewDecisionInput:
    face_index: int
    outcome: str
    player_id: int | None = None


@dataclass(frozen=True)
class ManualReviewListItem:
    review: MatchRosterScanManualReview
    competition_id: int
    competition_name: str
    match_id: int
    match_starts_at: datetime | None
    match_ends_at: datetime | None
    match_status: str
    team_id: int
    team_name: str
    scan_id: int
    scan_created_at: datetime
    unknown_face_count: int


@dataclass(frozen=True)
class ManualReviewPage:
    reviews: list[ManualReviewListItem]
    page: int
    per_page: int
    total_items: int
    total_pages: int


@dataclass(frozen=True)
class MatchManualReviewStatus:
    match_id: int
    requires_biometric_audit: bool
    pending_review_count: int
    resolved_review_count: int
    expired_review_count: int


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_review_reader(
    match: Match,
    *,
    actor_user_id: int,
    actor_role: str,
) -> None:
    if actor_role == ADMIN_USER_ROLE or (
        actor_role == DEFAULT_USER_ROLE and match.referee_id == actor_user_id
    ):
        return
    raise ManualReviewServiceError(
        "manual_review_forbidden",
        "Only an administrator or the assigned referee may read this review.",
        403,
    )


def _require_assigned_referee(
    match: Match,
    *,
    actor_user_id: int,
    actor_role: str,
) -> None:
    if actor_role == DEFAULT_USER_ROLE and match.referee_id == actor_user_id:
        return
    raise ManualReviewServiceError(
        "manual_review_referee_required",
        "Only the referee assigned to this Match may finalize the review.",
        403,
    )


def _find_review_match_id(scan_id: int) -> int | None:
    """Locate a review before acquiring Match-first lifecycle locks."""
    return db.session.execute(
        db.select(MatchRosterScan.match_id)
        .join(
            MatchRosterScanManualReview,
            MatchRosterScanManualReview.scan_id == MatchRosterScan.id,
        )
        .where(MatchRosterScanManualReview.scan_id == scan_id)
    ).scalar_one_or_none()


def _load_review_for_finalization(
    scan_id: int,
) -> MatchRosterScanManualReview | None:
    """Lock the review only after its Match lock is held by the caller."""
    return db.session.execute(
        db.select(MatchRosterScanManualReview)
        .join(MatchRosterScanManualReview.scan)
        .options(
            joinedload(MatchRosterScanManualReview.scan).joinedload(
                MatchRosterScan.match
            ),
            joinedload(MatchRosterScanManualReview.scan)
            .selectinload(MatchRosterScan.faces)
            .selectinload(MatchRosterScanFace.manual_review_decision),
        )
        .where(MatchRosterScanManualReview.scan_id == scan_id)
        .with_for_update(of=MatchRosterScanManualReview)
    ).scalar_one_or_none()


def _lock_match(match_id: int) -> Match:
    match = db.session.execute(
        db.select(Match)
        .where(Match.id == match_id)
        .with_for_update(of=Match)
    ).scalar_one_or_none()
    if match is None:
        raise ManualReviewServiceError(
            "match_not_found", "The Match does not exist.", 404
        )
    return match


def _validate_decision_inputs(
    decisions: Sequence[ManualReviewDecisionInput],
) -> tuple[ManualReviewDecisionInput, ...]:
    immutable_decisions = tuple(decisions)
    if not immutable_decisions:
        raise ManualReviewServiceError(
            "manual_review_decisions_required",
            "Every unknown face requires one manual decision.",
            422,
        )
    seen_indexes: set[int] = set()
    for decision in immutable_decisions:
        if not _is_integer(decision.face_index) or decision.face_index < 0:
            raise ManualReviewServiceError(
                "invalid_manual_review_face_index",
                "A manual review face index is invalid.",
                422,
            )
        if decision.face_index in seen_indexes:
            raise ManualReviewServiceError(
                "duplicate_manual_review_face",
                "A manual review cannot repeat a face.",
            )
        seen_indexes.add(decision.face_index)
        if decision.outcome not in MANUAL_REVIEW_OUTCOMES:
            raise ManualReviewServiceError(
                "invalid_manual_review_outcome",
                "A manual review outcome is invalid.",
                422,
            )
        if decision.outcome == "belongs_to_team":
            if not _is_integer(decision.player_id) or decision.player_id <= 0:
                raise ManualReviewServiceError(
                    "manual_review_player_required",
                    "A membership conclusion requires a Player.",
                    422,
                )
        elif decision.player_id is not None:
            raise ManualReviewServiceError(
                "manual_review_player_forbidden",
                "Only a membership conclusion may include a Player.",
                422,
            )
    return immutable_decisions


def _unknown_faces_by_index(
    scan: MatchRosterScan,
) -> dict[int, MatchRosterScanFace]:
    return {
        face.face_index: face
        for face in scan.faces
        if face.status == "unknown"
    }


def _validate_complete_unknown_coverage(
    decisions: Sequence[ManualReviewDecisionInput],
    unknown_faces: dict[int, MatchRosterScanFace],
) -> None:
    provided_indexes = {decision.face_index for decision in decisions}
    if not unknown_faces or provided_indexes != set(unknown_faces):
        raise ManualReviewServiceError(
            "manual_review_decisions_incomplete",
            "Submit exactly one decision for every unknown scan face.",
        )
    if any(
        face.manual_review_decision is not None
        for face in unknown_faces.values()
    ):
        raise ManualReviewServiceError(
            "manual_review_already_finalized",
            "The manual review already has immutable decisions.",
        )


def _frozen_roster_players(
    *,
    competition_id: int,
    team_id: int,
    player_ids: set[int],
) -> dict[int, Player]:
    if not player_ids:
        return {}
    players = db.session.execute(
        db.select(Player)
        .join(
            CompetitionRosterPlayer,
            CompetitionRosterPlayer.player_id == Player.id,
        )
        .where(
            CompetitionRosterPlayer.competition_id == competition_id,
            CompetitionRosterPlayer.team_id == team_id,
            Player.id.in_(player_ids),
        )
    ).scalars()
    return {player.id: player for player in players}


def _validated_membership_players(
    decisions: Sequence[ManualReviewDecisionInput],
    scan: MatchRosterScan,
    match: Match,
) -> dict[int, Player]:
    player_ids = {
        decision.player_id
        for decision in decisions
        if decision.outcome == "belongs_to_team"
        and decision.player_id is not None
    }
    if len(player_ids) != sum(
        decision.outcome == "belongs_to_team" for decision in decisions
    ):
        raise ManualReviewServiceError(
            "duplicate_manual_review_player",
            "The same Player cannot be assigned to more than one scan face.",
        )
    players = _frozen_roster_players(
        competition_id=match.competition_id,
        team_id=scan.team_id,
        player_ids=player_ids,
    )
    if set(players) != player_ids:
        raise ManualReviewServiceError(
            "manual_review_player_not_in_roster",
            "Each selected Player must belong to the frozen Match Team roster.",
        )
    return players


def _review_is_open_for_resolution(
    review: MatchRosterScanManualReview,
    match: Match,
    current_time: datetime,
) -> bool:
    if review.status != "pending" or current_time >= as_utc(review.expires_at):
        return False
    reconcile_matches(current_time, match_id=match.id)
    return match.status in {"in_progress", "finished"}


def finalize_match_roster_scan_manual_review(
    scan_id: int,
    decisions: Sequence[ManualReviewDecisionInput],
    *,
    actor_user_id: int,
    actor_role: str,
) -> MatchRosterScanManualReview:
    """Atomically resolve all unknown faces and delete now-unneeded evidence."""
    immutable_decisions = _validate_decision_inputs(decisions)
    staged_evidence: list[StagedScanImageDeletion] = []
    try:
        review_match_id = _find_review_match_id(scan_id)
        if review_match_id is None:
            raise ManualReviewServiceError(
                "manual_review_not_found", "The manual review does not exist.", 404
            )
        # Expiry and scan persistence also lock Match before review rows. Keeping
        # that order avoids a finalization/deadline-reconciliation deadlock.
        match = _lock_match(review_match_id)
        review = _load_review_for_finalization(scan_id)
        if review is None:
            raise ManualReviewServiceError(
                "manual_review_not_found", "The manual review does not exist.", 404
            )
        scan = review.scan
        _require_assigned_referee(
            match,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
        )
        current_time = as_utc(utc_now())
        if review.status == "pending" and current_time >= as_utc(
            review.expires_at
        ):
            db.session.rollback()
            reconcile_expired_match_roster_scan_reviews(
                match.id,
                now=current_time,
            )
            raise ManualReviewServiceError(
                "manual_review_closed",
                "The manual review is no longer pending.",
            )
        if not _review_is_open_for_resolution(review, match, current_time):
            raise ManualReviewServiceError(
                "manual_review_closed",
                "The manual review is no longer pending.",
            )
        if (
            scan.group_image_file_name is None
            or scan.group_image_content_type is None
        ):
            raise ManualReviewServiceError(
                "manual_review_evidence_unavailable",
                "The pending manual review has no private evidence.",
            )

        unknown_faces = _unknown_faces_by_index(scan)
        _validate_complete_unknown_coverage(immutable_decisions, unknown_faces)
        membership_players = _validated_membership_players(
            immutable_decisions,
            scan,
            match,
        )

        staged_file = stage_scan_image_file(scan)
        if staged_file is not None:
            staged_evidence.append(staged_file)
        else:
            current_app.logger.warning(
                "No private evidence file exists for resolved Match scan %s",
                scan.id,
            )

        for decision in immutable_decisions:
            player = membership_players.get(decision.player_id)
            db.session.add(
                MatchRosterScanManualReviewDecision(
                    scan_id=scan.id,
                    face_index=decision.face_index,
                    face=unknown_faces[decision.face_index],
                    outcome=decision.outcome,
                    player_id=(player.id if player is not None else None),
                    player_name_snapshot=(
                        player.name if player is not None else None
                    ),
                )
            )
        review.status = "resolved"
        review.resolved_by_user_id = actor_user_id
        review.resolved_at = current_time
        scan.group_image_file_name = None
        scan.group_image_content_type = None
        db.session.commit()
    except Exception:
        db.session.rollback()
        restore_scan_image_deletions(staged_evidence)
        raise

    finalize_scan_image_deletions(staged_evidence)
    return review


def list_match_roster_scan_manual_reviews(
    *,
    status: str,
    match_id: int | None = None,
    sort: str = "scan_created_at_desc",
    page: int,
    actor_user_id: int,
    actor_role: str,
) -> ManualReviewPage:
    """Return a safe, role-scoped manual-review section."""
    if actor_role not in {ADMIN_USER_ROLE, DEFAULT_USER_ROLE}:
        raise ManualReviewServiceError(
            "manual_review_forbidden",
            "The actor cannot read manual reviews.",
            403,
        )
    if status not in {"pending", "resolved", "expired", "all"}:
        raise ManualReviewServiceError(
            "invalid_manual_review_status",
            "The requested manual review status is invalid.",
            422,
        )
    if match_id is not None and (
        not _is_integer(match_id) or match_id <= 0
    ):
        raise ManualReviewServiceError(
            "invalid_manual_review_match_id",
            "The requested Match is invalid.",
            422,
        )
    if sort not in MANUAL_REVIEW_LIST_SORTS:
        raise ManualReviewServiceError(
            "invalid_manual_review_sort",
            "The requested manual review order is invalid.",
            422,
        )
    if not _is_integer(page) or page <= 0:
        raise ManualReviewServiceError(
            "invalid_manual_review_page",
            "The requested manual review page is invalid.",
            422,
        )
    reconcile_expired_match_roster_scan_reviews()
    if reconcile_matches():
        db.session.commit()

    filters = []
    if status != "all":
        filters.append(MatchRosterScanManualReview.status == status)
    if match_id is not None:
        filters.append(MatchRosterScan.match_id == match_id)
    if actor_role == DEFAULT_USER_ROLE:
        filters.append(Match.referee_id == actor_user_id)

    if sort == "scan_created_at_asc":
        order_by = (
            MatchRosterScan.created_at.asc(),
            MatchRosterScan.id.asc(),
        )
    else:
        order_by = (
            MatchRosterScan.created_at.desc(),
            MatchRosterScan.id.desc(),
        )

    unknown_face_count = (
        db.select(func.count())
        .select_from(MatchRosterScanFace)
        .where(
            MatchRosterScanFace.scan_id == MatchRosterScanManualReview.scan_id,
            MatchRosterScanFace.status == "unknown",
        )
        .correlate(MatchRosterScanManualReview)
        .scalar_subquery()
    )
    count_statement = (
        db.select(MatchRosterScanManualReview.scan_id)
        .join(
            MatchRosterScan,
            MatchRosterScan.id == MatchRosterScanManualReview.scan_id,
        )
        .join(Match, Match.id == MatchRosterScan.match_id)
        .where(*filters)
    )
    total_items = db.session.execute(
        db.select(func.count())
        .select_from(count_statement.subquery())
    ).scalar_one()
    total_pages = (
        (total_items + MANUAL_REVIEWS_PER_PAGE - 1) // MANUAL_REVIEWS_PER_PAGE
        if total_items
        else 0
    )
    rows = db.session.execute(
        db.select(
            MatchRosterScanManualReview,
            Competition.id,
            Competition.name,
            Match.id,
            Match.starts_at,
            Match.ends_at,
            Match.status,
            Team.id,
            Team.name,
            MatchRosterScan.id,
            MatchRosterScan.created_at,
            unknown_face_count,
        )
        .join(
            MatchRosterScan,
            MatchRosterScan.id == MatchRosterScanManualReview.scan_id,
        )
        .join(Match, Match.id == MatchRosterScan.match_id)
        .join(Competition, Competition.id == Match.competition_id)
        .join(Team, Team.id == MatchRosterScan.team_id)
        .where(*filters)
        .order_by(*order_by)
        .limit(MANUAL_REVIEWS_PER_PAGE)
        .offset((page - 1) * MANUAL_REVIEWS_PER_PAGE)
    ).all()
    return ManualReviewPage(
        reviews=[
            ManualReviewListItem(
                review=row[0],
                competition_id=row[1],
                competition_name=row[2],
                match_id=row[3],
                match_starts_at=row[4],
                match_ends_at=row[5],
                match_status=row[6],
                team_id=row[7],
                team_name=row[8],
                scan_id=row[9],
                scan_created_at=row[10],
                unknown_face_count=row[11],
            )
            for row in rows
        ],
        page=page,
        per_page=MANUAL_REVIEWS_PER_PAGE,
        total_items=total_items,
        total_pages=total_pages,
    )


def get_match_manual_review_status(
    match_id: int,
    *,
    actor_user_id: int,
    actor_role: str,
) -> MatchManualReviewStatus:
    """Derive the Match audit flag from current, unexpired pending reviews."""
    match = db.session.execute(
        db.select(Match).where(Match.id == match_id)
    ).scalar_one_or_none()
    if match is None:
        raise ManualReviewServiceError(
            "match_not_found", "The Match does not exist.", 404
        )
    _require_review_reader(
        match,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
    )
    reconcile_expired_match_roster_scan_reviews(match.id)
    current_time = as_utc(utc_now())
    review_rows = db.session.execute(
        db.select(
            MatchRosterScanManualReview.status,
            MatchRosterScanManualReview.expires_at,
        )
        .join(
            MatchRosterScan,
            MatchRosterScan.id == MatchRosterScanManualReview.scan_id,
        )
        .where(MatchRosterScan.match_id == match.id)
    ).all()
    pending_review_count = sum(
        status == "pending" and expires_at > current_time
        for status, expires_at in review_rows
    )
    return MatchManualReviewStatus(
        match_id=match.id,
        requires_biometric_audit=bool(pending_review_count),
        pending_review_count=pending_review_count,
        resolved_review_count=sum(
            status == "resolved" for status, _ in review_rows
        ),
        expired_review_count=sum(
            status == "expired" for status, _ in review_rows
        ),
    )


__all__ = [
    "MANUAL_REVIEWS_PER_PAGE",
    "ManualReviewDecisionInput",
    "ManualReviewPage",
    "ManualReviewServiceError",
    "MatchManualReviewStatus",
    "finalize_match_roster_scan_manual_review",
    "get_match_manual_review_status",
    "list_match_roster_scan_manual_reviews",
]
