"""Pure merge planning for the football-data and openfootball loaders.

The module holds no database session, performs no I/O and never reads the clock:
it receives the stored state and the parsed snapshot and returns a
:class:`MergePlan` that the caller applies inside one transaction. Keeping the
rules pure makes them testable without a database and makes a second identical
run provably a no-op.

football-data wins the date, the time and the result of a played match, and it
owns the statistics and the odds. openfootball supplies the fixture list of the
current season: the date and the matchday of a scheduled match, and the matchday
of a played match when the stored value is still ``NULL``.

Prices and expected goals are quantized with ``ROUND_HALF_UP``, which is the
rounding PostgreSQL applies to a ``numeric`` column. ``ROUND_HALF_EVEN`` would
turn a stored ``2.063`` into ``2.062`` and report a change on every run.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import date, time
from decimal import ROUND_HALF_UP, Decimal

from lmi_worker.sources.football_data import FdMatch
from lmi_worker.sources.openfootball import OfFixture

_ODDS_QUANTUM = Decimal("0.001")
_XG_QUANTUM = Decimal("0.01")


@dataclass(frozen=True)
class MatchKey:
    """The natural key of a match inside one season."""

    home_team_id: int
    away_team_id: int


@dataclass(frozen=True)
class MatchState:
    """The mutable columns of ``matches``, without the season and the key."""

    kickoff_date: date
    kickoff_time: time | None
    matchday: int | None
    status: str
    home_goals: int | None
    away_goals: int | None
    ht_home_goals: int | None
    ht_away_goals: int | None
    result: str | None


@dataclass(frozen=True)
class StatsState:
    """The counters of one ``match_stats`` row, named like the columns."""

    home_shots: int | None = None
    away_shots: int | None = None
    home_shots_on_target: int | None = None
    away_shots_on_target: int | None = None
    home_fouls: int | None = None
    away_fouls: int | None = None
    home_corners: int | None = None
    away_corners: int | None = None
    home_yellow: int | None = None
    away_yellow: int | None = None
    home_red: int | None = None
    away_red: int | None = None
    home_xg: Decimal | None = None
    away_xg: Decimal | None = None


@dataclass(frozen=True)
class OddsSlot:
    """One 1X2 snapshot: a bookmaker family and whether the price is closing."""

    bookmaker: str
    is_closing: bool


@dataclass(frozen=True)
class Prices:
    """The three 1X2 prices of one slot."""

    home: Decimal
    draw: Decimal
    away: Decimal


@dataclass(frozen=True)
class StoredMatch:
    """A match as the database holds it, with its statistics and its odds."""

    id: int
    state: MatchState
    stats: StatsState | None
    odds: Mapping[OddsSlot, Prices]


@dataclass(frozen=True)
class FdRecord:
    """One football-data match mapped onto team ids."""

    key: MatchKey
    state: MatchState
    stats: StatsState
    odds: Mapping[OddsSlot, Prices]


@dataclass(frozen=True)
class OfRecord:
    """One openfootball fixture mapped onto team ids."""

    key: MatchKey
    kickoff_date: date
    matchday: int


class DuplicateKey(ValueError):
    """Raised when one season would hold the same natural key twice."""


class SeasonShapeError(ValueError):
    """Raised when a fixture list is not a complete league season."""


class SourceConflict(ValueError):
    """Raised when the fixture list contradicts a played match already stored."""


@dataclass
class MergePlan:
    """The writes that turn the stored season into the parsed snapshot.

    ``inserts`` carry everything a new match needs, including its statistics and
    odds, because a new match has no database id yet. The ``stats``,
    ``odds_upserts`` and ``odds_deletes`` lists address stored matches by id.

    ``updates`` holds only the columns that differ from the stored row, so a
    second identical run writes nothing. ``notes`` records differences that the
    merge deliberately does not apply.
    """

    inserts: list[FdRecord | OfRecord] = field(default_factory=list)
    updates: list[tuple[int, dict[str, object]]] = field(default_factory=list)
    stats: list[tuple[int, StatsState]] = field(default_factory=list)
    odds_upserts: list[tuple[int, OddsSlot, Prices]] = field(default_factory=list)
    odds_deletes: list[tuple[int, OddsSlot]] = field(default_factory=list)
    deletes: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> list[MatchKey | int]:
        """One entry per distinct match with a change: inserted keys and stored ids."""
        entries: list[MatchKey | int] = [record.key for record in self.inserts]
        stored_ids: list[int] = [match_id for match_id, _ in self.updates]
        stored_ids.extend(match_id for match_id, _ in self.stats)
        stored_ids.extend(match_id for match_id, _, _ in self.odds_upserts)
        stored_ids.extend(match_id for match_id, _ in self.odds_deletes)
        entries.extend(dict.fromkeys(stored_ids))
        return entries

    @property
    def rows_upserted(self) -> int:
        """The number of distinct matches the plan inserts or changes."""
        return len(self.changed)

    @property
    def rows_removed(self) -> int:
        """The number of stored matches the plan deletes."""
        return len(self.deletes)


def fd_record(match: FdMatch, home_team_id: int, away_team_id: int) -> FdRecord:
    """Map one parsed football-data match onto the merge model.

    The record is always a played match without a matchday, because the
    football-data files carry no matchday. Prices are quantized to three decimals
    and expected goals to two, both with ``ROUND_HALF_UP``.
    """
    return FdRecord(
        key=MatchKey(home_team_id=home_team_id, away_team_id=away_team_id),
        state=MatchState(
            kickoff_date=match.date,
            kickoff_time=match.time,
            matchday=None,
            status="played",
            home_goals=match.fthg,
            away_goals=match.ftag,
            ht_home_goals=match.hthg,
            ht_away_goals=match.htag,
            result=match.ftr,
        ),
        stats=StatsState(
            home_shots=match.stats.home_shots,
            away_shots=match.stats.away_shots,
            home_shots_on_target=match.stats.home_shots_on_target,
            away_shots_on_target=match.stats.away_shots_on_target,
            home_fouls=match.stats.home_fouls,
            away_fouls=match.stats.away_fouls,
            home_corners=match.stats.home_corners,
            away_corners=match.stats.away_corners,
            home_yellow=match.stats.home_yellow,
            away_yellow=match.stats.away_yellow,
            home_red=match.stats.home_red,
            away_red=match.stats.away_red,
            home_xg=_quantize(match.home_xg, _XG_QUANTUM),
            away_xg=_quantize(match.away_xg, _XG_QUANTUM),
        ),
        odds=_fd_odds(match),
    )


def of_record(fixture: OfFixture, home_team_id: int, away_team_id: int) -> OfRecord:
    """Map one parsed openfootball fixture onto the merge model.

    The kickoff time is deliberately dropped: openfootball times are about an
    hour off the football-data wall clock and are never stored.
    """
    return OfRecord(
        key=MatchKey(home_team_id=home_team_id, away_team_id=away_team_id),
        kickoff_date=fixture.date,
        matchday=fixture.matchday,
    )


def check_unique_keys(records: Sequence[FdRecord | OfRecord]) -> None:
    """Raise :class:`DuplicateKey` if two records share a key or a team plays itself.

    Two different source spellings can resolve to the same pair of team ids; that
    is a duplicate match, not a second fixture. A team drawn against itself can
    never be a valid match either.
    """
    seen: set[MatchKey] = set()
    for record in records:
        if record.key.home_team_id == record.key.away_team_id:
            raise DuplicateKey(f"team id {record.key.home_team_id} cannot play itself")
        if record.key in seen:
            raise DuplicateKey(
                f"duplicate match key: home_team_id={record.key.home_team_id} "
                f"away_team_id={record.key.away_team_id}"
            )
        seen.add(record.key)


def check_full_season(records: Sequence[OfRecord], *, teams: int = 20) -> None:
    """Raise :class:`SeasonShapeError` unless the list is a complete league season.

    A complete season has exactly ``teams`` distinct team ids, exactly
    ``teams * (teams - 1)`` fixtures, and every matchday from ``1`` to
    ``2 * (teams - 1)`` with exactly ``teams // 2`` fixtures.
    """
    team_ids = {record.key.home_team_id for record in records}
    team_ids.update(record.key.away_team_id for record in records)
    if len(team_ids) != teams:
        raise SeasonShapeError(
            f"the fixture list has {len(team_ids)} teams, expected exactly {teams}"
        )
    expected_fixtures = teams * (teams - 1)
    if len(records) != expected_fixtures:
        raise SeasonShapeError(
            f"the fixture list has {len(records)} fixtures, expected exactly {expected_fixtures}"
        )
    last_matchday = 2 * (teams - 1)
    fixtures_per_matchday = teams // 2
    counts = Counter(record.matchday for record in records)
    outside = sorted(matchday for matchday in counts if not 1 <= matchday <= last_matchday)
    if outside:
        raise SeasonShapeError(
            f"matchday {outside[0]} is outside 1..{last_matchday}, the range of a double "
            f"round robin of {teams} teams"
        )
    absent = [str(matchday) for matchday in range(1, last_matchday + 1) if matchday not in counts]
    if absent:
        raise SeasonShapeError(
            f"matchday(s) {', '.join(absent)} have no fixtures; every matchday from 1 to "
            f"{last_matchday} must appear"
        )
    uneven = [
        f"matchday {matchday} has {counts[matchday]}"
        for matchday in range(1, last_matchday + 1)
        if counts[matchday] != fixtures_per_matchday
    ]
    if uneven:
        raise SeasonShapeError(
            f"every matchday needs exactly {fixtures_per_matchday} fixtures, but "
            f"{', '.join(uneven)}"
        )


def plan_football_data(
    stored: Mapping[MatchKey, StoredMatch], records: Sequence[FdRecord]
) -> MergePlan:
    """Return the plan that applies one complete football-data season file.

    A played stored match that the file omits is left untouched and noted: the
    file may lag behind the database, and deleting a played match would also
    delete its statistics and odds.
    """
    plan = MergePlan()
    seen: set[MatchKey] = set()
    for record in records:
        seen.add(record.key)
        existing = stored.get(record.key)
        if existing is None:
            plan.inserts.append(record)
            continue
        changes = _football_data_changes(existing.state, record.state)
        if changes:
            plan.updates.append((existing.id, changes))
        if existing.stats != record.stats:
            plan.stats.append((existing.id, record.stats))
        _plan_odds(plan, existing.id, existing.odds, record.odds)
    for key, existing in stored.items():
        if key not in seen and existing.state.status == "played":
            plan.notes.append(
                f"played match {existing.id} is missing from the football-data file; "
                "it was left untouched"
            )
    return plan


def plan_openfootball(
    stored: Mapping[MatchKey, StoredMatch], records: Sequence[OfRecord]
) -> MergePlan:
    """Return the plan that applies one complete openfootball fixture list.

    A stored scheduled match is updated and a missing one is inserted. A played
    match keeps its date, time, status and goals; only a ``NULL`` matchday is
    filled, and a different non-NULL matchday is noted instead of changed.

    Raises:
        SourceConflict: a stored played match is absent from the snapshot. A full
            fixture list must contain every played match, so its absence means
            the file is stale or belongs to another season; nothing is written.
    """
    plan = MergePlan()
    seen: set[MatchKey] = set()
    missing: list[int] = []
    for record in records:
        seen.add(record.key)
        existing = stored.get(record.key)
        if existing is None:
            plan.inserts.append(record)
            continue
        if existing.state.status == "played":
            if existing.state.matchday is None:
                plan.updates.append((existing.id, {"matchday": record.matchday}))
            elif existing.state.matchday != record.matchday:
                plan.notes.append(
                    f"played match {existing.id} keeps its matchday "
                    f"{existing.state.matchday}; the fixture list says {record.matchday}"
                )
            continue
        changes = _openfootball_changes(existing.state, record)
        if changes:
            plan.updates.append((existing.id, changes))
    for key, existing in stored.items():
        if key in seen:
            continue
        if existing.state.status == "played":
            missing.append(existing.id)
        else:
            plan.deletes.append(existing.id)
    if missing:
        listed = ", ".join(str(match_id) for match_id in sorted(missing))
        raise SourceConflict(f"the openfootball fixture list omits played match id(s): {listed}")
    return plan


def _fd_odds(match: FdMatch) -> dict[OddsSlot, Prices]:
    """Return the quantized prices of one football-data match, keyed by slot."""
    odds: dict[OddsSlot, Prices] = {}
    for quote in match.odds:
        slot = OddsSlot(bookmaker=quote.bookmaker, is_closing=quote.is_closing)
        odds[slot] = Prices(
            home=_quantize_required(quote.home, _ODDS_QUANTUM),
            draw=_quantize_required(quote.draw, _ODDS_QUANTUM),
            away=_quantize_required(quote.away, _ODDS_QUANTUM),
        )
    return odds


def _quantize(value: Decimal | None, quantum: Decimal) -> Decimal | None:
    """Round a value to ``quantum`` with ``ROUND_HALF_UP``; ``None`` stays ``None``."""
    if value is None:
        return None
    return _quantize_required(value, quantum)


def _quantize_required(value: Decimal, quantum: Decimal) -> Decimal:
    """Round a required value to ``quantum`` with ``ROUND_HALF_UP``."""
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _football_data_changes(before: MatchState, after: MatchState) -> dict[str, object]:
    """Return the football-data columns that differ, never including ``matchday``."""
    changes: dict[str, object] = {}
    for info in fields(MatchState):
        if info.name == "matchday":
            continue
        new_value: object = getattr(after, info.name)
        if getattr(before, info.name) != new_value:
            changes[info.name] = new_value
    return changes


def _openfootball_changes(before: MatchState, record: OfRecord) -> dict[str, object]:
    """Return the scheduled-row changes: date, matchday, and no kickoff time."""
    changes: dict[str, object] = {}
    if before.kickoff_date != record.kickoff_date:
        changes["kickoff_date"] = record.kickoff_date
    if before.matchday != record.matchday:
        changes["matchday"] = record.matchday
    if before.kickoff_time is not None:
        changes["kickoff_time"] = None
    return changes


def _plan_odds(
    plan: MergePlan,
    match_id: int,
    stored_odds: Mapping[OddsSlot, Prices],
    new_odds: Mapping[OddsSlot, Prices],
) -> None:
    """Mirror the file odds: upsert a different or new slot, delete a dropped one."""
    for slot, prices in new_odds.items():
        if stored_odds.get(slot) != prices:
            plan.odds_upserts.append((match_id, slot, prices))
    for slot in stored_odds:
        if slot not in new_odds:
            plan.odds_deletes.append((match_id, slot))
