"""Database write primitives for the ingestion pipeline.

The module reads one season and applies one :class:`~lmi_worker.ingest.merge.MergePlan`.
It is deliberately not transactional: it never commits, never rolls back and never
opens a session, because the caller owns the transaction. A loader therefore
wraps one plan in one transaction, and a failure inside :func:`apply_plan` leaves
nothing behind.

Every statement is checked against the plan. An insert must return its new id, an
update or an upsert must touch exactly one row, and a delete must remove exactly
the row the plan named. The delete of a match additionally carries ``status =
'scheduled'``, so a guard that somehow addresses a played match fails loudly
instead of erasing a result with its statistics and its odds.

Advisory lock keys are split between two key spaces that share
:data:`LOCK_CLASS`: :func:`lock_season_writes` locks a bare season start year,
while :func:`lmi_worker.ingest.runs.run_lock` locks ``source_id * 10000 +
start_year``. A run lock and a season lock therefore never collide, and two
sources loading the same season still serialise on the season lock.
"""

from collections.abc import Mapping, Sequence
from dataclasses import fields

from lmi_core.db.models import Match, MatchOdd, MatchStat, Season
from lmi_core.db.seed import SEASON_START_YEARS, season_code
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from lmi_worker.ingest.merge import (
    FdRecord,
    MatchKey,
    MatchState,
    MergePlan,
    OddsSlot,
    OfRecord,
    Prices,
    StatsState,
    StoredMatch,
)
from lmi_worker.seasons import SeasonCode

LOCK_CLASS = 0x4C4D49
"""The ``classid`` half of every ingestion advisory lock: ``"LMI"`` in ASCII."""

MARKET = "1x2"
"""The only market the loaders write; stored odds of other markets are untouched."""

_COVERED_FIRST = season_code(min(SEASON_START_YEARS))
_COVERED_LAST = season_code(max(SEASON_START_YEARS))


class MissingSeason(LookupError):
    """Raised when a season code has no seeded row.

    The message names the range the seed covers, so an operator who asks for a
    season outside it learns which seasons exist instead of guessing.
    """

    def __init__(self, code: str) -> None:
        super().__init__(
            f"season {code!r} has no row in the seasons table; the seed covers "
            f"{_COVERED_FIRST}..{_COVERED_LAST}"
        )
        self.code = code


def get_season(session: Session, code: str) -> Season:
    """Return the stored season of ``code``.

    Raises:
        MissingSeason: the seasons table has no row with that code.
        ValueError: the stored football-data code contradicts the code, so the
            row describes a different season than its label claims.
    """
    season = session.scalars(select(Season).where(Season.code == code)).one_or_none()
    if season is None:
        raise MissingSeason(code)
    expected = SeasonCode.parse(code).fd_code
    if season.fd_code != expected:
        raise ValueError(
            f"season {code!r} is stored with football-data code {season.fd_code!r}, "
            f"but the code implies {expected!r}"
        )
    return season


def lock_season_writes(session: Session, start_year: int) -> None:
    """Block every other writer of one season until the transaction ends.

    The lock is a transaction-level advisory lock, so PostgreSQL releases it on
    commit and on rollback without any cleanup code. The key is the bare season
    start year; the run lock of :mod:`lmi_worker.ingest.runs` uses a different key
    space, so the two never wait on each other.
    """
    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_class, :key)"),
        {"lock_class": LOCK_CLASS, "key": start_year},
    )


def load_season(session: Session, season_id: int) -> dict[MatchKey, StoredMatch]:
    """Return the stored state of one season, keyed by the natural key of a match.

    Three queries read the matches, their statistics and their 1X2 odds; nothing
    is read per match, so a whole season costs the same three round trips however
    many fixtures it holds.
    """
    states: dict[MatchKey, tuple[int, MatchState]] = {}
    for match in session.scalars(select(Match).where(Match.season_id == season_id)):
        key = MatchKey(home_team_id=match.home_team_id, away_team_id=match.away_team_id)
        states[key] = (match.id, _match_state(match))
    match_ids = {match_id for match_id, _ in states.values()}
    stored_stats = _stats_by_match(session, match_ids)
    stored_odds = _odds_by_match(session, match_ids)
    return {
        key: StoredMatch(
            id=match_id,
            state=state,
            stats=stored_stats.get(match_id),
            odds=stored_odds.get(match_id, {}),
        )
        for key, (match_id, state) in states.items()
    }


def apply_plan(session: Session, season_id: int, plan: MergePlan) -> None:
    """Apply exactly the writes of ``plan``, in the order the plan depends on.

    Inserts run first, one match at a time, so a new match gets its id before its
    statistics and its odds are written. Updates follow, then the statistics and
    odds upserts, then the odds deletes and finally the match deletes; a delete
    cannot orphan a row that the same plan touches because the plan addresses
    stored matches by id.

    Raises:
        RuntimeError: one statement affected a different number of rows than the
            plan announced. The message names the statement, and the caller's
            transaction rollback undoes every earlier write of the same plan.
    """
    for record in plan.inserts:
        match_id = _insert_match(session, season_id, record)
        if isinstance(record, FdRecord):
            _insert_stats(session, match_id, record.stats)
            _insert_odds(session, match_id, record.odds)
    for match_id, changes in plan.updates:
        _update_match(session, match_id, changes)
    for match_id, stats in plan.stats:
        _upsert_stats(session, match_id, stats)
    for match_id, slot, prices in plan.odds_upserts:
        _upsert_odds(session, match_id, slot, prices)
    for match_id, slot in plan.odds_deletes:
        _delete_odds(session, match_id, slot)
    for match_id in plan.deletes:
        _delete_scheduled_match(session, match_id)


def _match_state(match: Match) -> MatchState:
    """Return the merge model of one stored match row."""
    return MatchState(
        kickoff_date=match.kickoff_date,
        kickoff_time=match.kickoff_time,
        matchday=match.matchday,
        status=match.status,
        home_goals=match.home_goals,
        away_goals=match.away_goals,
        ht_home_goals=match.ht_home_goals,
        ht_away_goals=match.ht_away_goals,
        result=match.result,
    )


def _stats_by_match(session: Session, match_ids: set[int]) -> dict[int, StatsState]:
    """Return the stored statistics of ``match_ids``, keyed by match id."""
    if not match_ids:
        return {}
    rows = session.scalars(select(MatchStat).where(MatchStat.match_id.in_(match_ids)))
    return {
        row.match_id: StatsState(
            home_shots=row.home_shots,
            away_shots=row.away_shots,
            home_shots_on_target=row.home_shots_on_target,
            away_shots_on_target=row.away_shots_on_target,
            home_fouls=row.home_fouls,
            away_fouls=row.away_fouls,
            home_corners=row.home_corners,
            away_corners=row.away_corners,
            home_yellow=row.home_yellow,
            away_yellow=row.away_yellow,
            home_red=row.home_red,
            away_red=row.away_red,
            home_xg=row.home_xg,
            away_xg=row.away_xg,
        )
        for row in rows
    }


def _odds_by_match(session: Session, match_ids: set[int]) -> dict[int, dict[OddsSlot, Prices]]:
    """Return the stored 1X2 odds of ``match_ids``, keyed by match id and slot."""
    if not match_ids:
        return {}
    odds: dict[int, dict[OddsSlot, Prices]] = {}
    rows = session.scalars(
        select(MatchOdd).where(MatchOdd.match_id.in_(match_ids), MatchOdd.market == MARKET)
    )
    for row in rows:
        slot = OddsSlot(bookmaker=row.bookmaker, is_closing=row.is_closing)
        odds.setdefault(row.match_id, {})[slot] = Prices(
            home=row.home, draw=row.draw, away=row.away
        )
    return odds


def _insert_match(session: Session, season_id: int, record: FdRecord | OfRecord) -> int:
    """Insert one match and return its new id."""
    values = _insert_values(season_id, record)
    match_id = session.execute(
        insert(Match).values(**values).returning(Match.id)
    ).scalar_one_or_none()
    if match_id is None:
        raise RuntimeError("insert into matches returned no id")
    return int(match_id)


def _insert_values(season_id: int, record: FdRecord | OfRecord) -> dict[str, object]:
    """Return the column values of a new match.

    ``updated_at`` is left out on purpose: the column has a server default, and
    the loader only writes the timestamp of a real change.
    """
    values: dict[str, object] = {
        "season_id": season_id,
        "home_team_id": record.key.home_team_id,
        "away_team_id": record.key.away_team_id,
    }
    if isinstance(record, FdRecord):
        values.update(
            {
                "kickoff_date": record.state.kickoff_date,
                "kickoff_time": record.state.kickoff_time,
                "matchday": record.state.matchday,
                "status": record.state.status,
                "home_goals": record.state.home_goals,
                "away_goals": record.state.away_goals,
                "ht_home_goals": record.state.ht_home_goals,
                "ht_away_goals": record.state.ht_away_goals,
                "result": record.state.result,
            }
        )
    else:
        values.update(
            {
                "kickoff_date": record.kickoff_date,
                "kickoff_time": None,
                "matchday": record.matchday,
                "status": "scheduled",
            }
        )
    return values


def _insert_stats(session: Session, match_id: int, stats: StatsState) -> None:
    """Insert the statistics row of a newly inserted match."""
    rows = session.execute(
        insert(MatchStat)
        .values(match_id=match_id, **_stats_values(stats))
        .returning(MatchStat.match_id)
    ).all()
    _expect_rows(rows, 1, f"insert into match_stats for match {match_id}")


def _insert_odds(session: Session, match_id: int, odds: Mapping[OddsSlot, Prices]) -> None:
    """Insert every odds row of a newly inserted match."""
    for slot, prices in odds.items():
        _upsert_odds(session, match_id, slot, prices)


def _update_match(session: Session, match_id: int, changes: dict[str, object]) -> None:
    """Apply the planned column changes to one stored match and stamp ``updated_at``."""
    rows = session.execute(
        update(Match)
        .where(Match.id == match_id)
        .values(**changes, updated_at=func.now())
        .returning(Match.id)
    ).all()
    _expect_rows(rows, 1, f"update matches for match {match_id} with {sorted(changes)}")


def _upsert_stats(session: Session, match_id: int, stats: StatsState) -> None:
    """Write the statistics row of one stored match, replacing the whole row."""
    statement = postgresql.insert(MatchStat).values(match_id=match_id, **_stats_values(stats))
    rows = session.execute(
        statement.on_conflict_do_update(
            index_elements=["match_id"],
            set_=_stats_values(stats),
        ).returning(MatchStat.match_id)
    ).all()
    _expect_rows(rows, 1, f"upsert match_stats for match {match_id}")


def _upsert_odds(session: Session, match_id: int, slot: OddsSlot, prices: Prices) -> None:
    """Write one 1X2 odds row of one stored match, replacing the price."""
    statement = postgresql.insert(MatchOdd).values(
        match_id=match_id,
        bookmaker=slot.bookmaker,
        market=MARKET,
        is_closing=slot.is_closing,
        home=prices.home,
        draw=prices.draw,
        away=prices.away,
    )
    rows = session.execute(
        statement.on_conflict_do_update(
            index_elements=["match_id", "bookmaker", "market", "is_closing"],
            set_={"home": prices.home, "draw": prices.draw, "away": prices.away},
        ).returning(MatchOdd.id)
    ).all()
    _expect_rows(
        rows,
        1,
        f"upsert match_odds for match {match_id} slot {slot.bookmaker}/closing={slot.is_closing}",
    )


def _delete_odds(session: Session, match_id: int, slot: OddsSlot) -> None:
    """Delete the odds row of one dropped 1X2 slot."""
    rows = session.execute(
        delete(MatchOdd)
        .where(
            MatchOdd.match_id == match_id,
            MatchOdd.bookmaker == slot.bookmaker,
            MatchOdd.market == MARKET,
            MatchOdd.is_closing == slot.is_closing,
        )
        .returning(MatchOdd.id)
    ).all()
    _expect_rows(
        rows,
        1,
        f"delete from match_odds match {match_id} slot {slot.bookmaker}/closing={slot.is_closing}",
    )


def _delete_scheduled_match(session: Session, match_id: int) -> None:
    """Delete one stored match, but only while it is still scheduled.

    A played match that the fixture list omits is a contradiction, not a cleanup:
    the statement affects no row and the caller fails instead of dropping a
    result, its statistics and its odds.
    """
    rows = session.execute(
        delete(Match)
        .where(
            Match.id == match_id,
            Match.status == "scheduled",
        )
        .returning(Match.id)
    ).all()
    if len(rows) != 1:
        raise RuntimeError(
            f"delete from matches where status = 'scheduled' for match {match_id} "
            f"affected {len(rows)} rows, expected 1: the row is missing or has been played"
        )


def _stats_values(stats: StatsState) -> dict[str, object]:
    """Return the ``match_stats`` columns of ``stats``, keyed like the columns."""
    return {info.name: getattr(stats, info.name) for info in fields(StatsState)}


def _expect_rows(rows: Sequence[object], expected: int, statement: str) -> None:
    """Raise unless ``statement`` affected exactly ``expected`` rows."""
    if len(rows) != expected:
        raise RuntimeError(f"{statement} affected {len(rows)} rows, expected {expected}")
