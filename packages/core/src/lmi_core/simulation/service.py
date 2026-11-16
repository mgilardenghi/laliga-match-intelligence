"""The season-simulation service: request, claim, data assembly, execution, storage.

The service enforces three leakage rules on every run:

- Fixed results are the season's played matches dated strictly before the run's
  as-of date. A played match dated on or after it is never counted.
- The model history is exactly :func:`lmi_core.modeling.data.load_match_data`
  restricted to kickoff dates strictly before the as-of date, so a match dated
  on the as-of day is never visible to the fit.
- Every other match of the season, except a withdrawn one, is re-simulated with
  ``sim_date = max(kickoff_date, as_of_date)``. A played match dated on or after
  the as-of date is therefore re-simulated and its stored result influences
  neither the fixed table nor the model.

The claim rule makes execution idempotent. :func:`execute_run` moves a row to
``running`` only when it is queued or when an earlier claim looks abandoned
(``running`` with no ``started_at`` or one older than :data:`STALE_AFTER`), in
one committed ``UPDATE ... RETURNING``. When the update matches no row, another
executor owns the run or it already finished, and the call returns without doing
any work. Staleness timestamps come from the database clock, never the process
clock, so a stale decision cannot disagree with the stored row.

A request that repeats an active run is answered with that row, and its message
is sent again only when the row looks abandoned. The two states have different
windows: a ``queued`` row waits :data:`QUEUED_STALE_AFTER` (one minute), because
a lost message leaves work nobody is doing, while a ``running`` row waits
:data:`STALE_AFTER` (30 minutes), because a claimed run may still be executing.
The claim's stale branch keeps the 30-minute window for the same reason.
"""

import datetime
import time
import uuid
from typing import Final, cast

import numpy as np
from sqlalchemy import Select, func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from lmi_core.db.models import (
    Match,
    Season,
    SimulationFixture,
    SimulationNextMatch,
    SimulationRun,
    SimulationTeamResult,
    Team,
)
from lmi_core.evaluation.backtest import data_fingerprint
from lmi_core.modeling.data import load_match_data
from lmi_core.modeling.dixon_coles import DixonColesModel
from lmi_core.modeling.types import Fixtures, History
from lmi_core.results.store import installed_code_version
from lmi_core.simulation.engine import (
    FixtureOutcomeProbs,
    Qualification,
    SeasonSimulation,
    SimFixture,
    fixture_outcome_probs,
    simulate_season,
)
from lmi_core.standings import MatchResult, StandingRow, compute_standings

__all__ = [
    "MODEL_NAME",
    "QUEUED_STALE_AFTER",
    "STALE_AFTER",
    "backfill_dates",
    "create_run",
    "execute_run",
]

MODEL_NAME: Final = "dixon_coles"
"""The only model the simulation schema accepts."""

QUEUED_STALE_AFTER: Final = datetime.timedelta(minutes=1)
"""How long a queued run may wait before a second request re-sends its message."""

STALE_AFTER: Final = datetime.timedelta(minutes=30)
"""How old a claim may be before a second executor may take the run over."""

_CLAIM_RUN = text(
    """
    UPDATE simulation_runs
       SET status = 'running',
           started_at = now()
     WHERE id = :run_id
       AND (
            status = 'queued'
            OR (
                 status = 'running'
                 AND (started_at IS NULL OR started_at < now() - interval '30 minutes')
            )
       )
    RETURNING id
    """
)
"""Claim one run and stamp its start, or match no row when another owns it.

The ``interval '30 minutes'`` is :data:`STALE_AFTER` spelled as SQL. It is
written here because one statement must compare against the database clock.
"""

_ACTIVE_STATUSES: Final = ("queued", "running")
"""The states the partial unique index ``ux_simulation_runs_active`` covers."""

_INSERT_ATTEMPTS: Final = 3
"""How many times ``create_run`` retries an insert whose key was taken."""


class _SeasonInputs:
    """The stored match rows of one season, split for the engine.

    ``next_match_by_team`` maps a team id to the match id of the first simulated
    fixture it plays in ``(kickoff_date, id)`` order, the same order the API
    lists remaining fixtures in. A withdrawn match is never chosen, and neither
    is a fixed result: only a fixture the run simulates can answer what a club's
    next match is.
    """

    def __init__(
        self,
        *,
        teams: dict[int, str],
        fixed: list[MatchResult],
        fixtures: list[SimFixture],
        next_match_by_team: dict[int, int],
        n_withdrawn: int,
    ) -> None:
        self.teams = teams
        self.fixed = fixed
        self.fixtures = fixtures
        self.next_match_by_team = next_match_by_team
        self.n_withdrawn = n_withdrawn


def create_run(
    session: Session,
    *,
    season_code: str,
    as_of_date: datetime.date,
    n_runs: int,
    seed: int,
    trigger: str,
    qualification: Qualification,
    kind: str = "live",
) -> tuple[SimulationRun, bool]:
    """Insert a queued run, or return the active run of the same natural key.

    The natural key is the one ``ux_simulation_runs_active`` enforces: season,
    as-of date, run count, seed and model, restricted to ``queued`` and
    ``running`` rows. When such a row exists nothing is inserted and the second
    element says whether the caller must enqueue it again: a queued row whose
    ``requested_at`` is older than :data:`QUEUED_STALE_AFTER` (one minute), or a
    running row with no ``started_at`` or one older than :data:`STALE_AFTER`
    (30 minutes), looks abandoned and is ``True``; every other active row is
    ``False``. The queued window is the short one because a lost message leaves
    work nobody is doing, while a running row may still be executing.

    A concurrent insert that wins the natural key is not an error: the
    ``IntegrityError`` is rolled back and the row that won is returned. When that
    winner finished between the rejected insert and the read that follows it,
    nothing holds the key any more, so the insert is retried up to
    :data:`_INSERT_ATTEMPTS` times instead of failing a request the database can
    serve.

    Args:
        session: the session that inserts or reads the run.
        season_code: the code of the season to simulate.
        as_of_date: the last date whose played matches may count as fixed.
        n_runs: how many seasons to simulate.
        seed: the seed of the model and of the sampler.
        trigger: what asked for the run: ``api``, ``cli`` or ``schedule``.
        qualification: the continental and relegation thresholds of the run.
        kind: ``live`` for a current season, ``backfill`` for a past one.

    Returns:
        The new run and ``True``, or the row that already holds the natural key
        and whether the caller must enqueue it again.

    Raises:
        ValueError: no season carries ``season_code``.
    """
    season = session.scalars(select(Season).where(Season.code == season_code)).one_or_none()
    if season is None:
        raise ValueError(f"unknown season code {season_code!r}")

    now = _database_now(session)
    last_conflict: IntegrityError | None = None
    for _ in range(_INSERT_ATTEMPTS):
        active = session.scalars(
            _active_run_statement(season.id, as_of_date, n_runs, seed)
        ).one_or_none()
        if active is not None:
            return active, _is_stale(active, now)

        run = SimulationRun(
            season_id=season.id,
            as_of_date=as_of_date,
            n_runs=n_runs,
            seed=seed,
            model=MODEL_NAME,
            ucl_positions=qualification.ucl_positions,
            europe_positions=qualification.europe_positions,
            relegation_positions=qualification.relegation_positions,
            kind=kind,
            trigger=trigger,
            status="queued",
            code_version=installed_code_version(),
        )
        session.add(run)
        try:
            session.commit()
        except IntegrityError as conflict:
            # Another writer won the natural key between the read above and this
            # insert. The next turn of the loop returns that winner; when it
            # finished in the same window nothing holds the key any more and the
            # insert is retried, so a served request never becomes an error.
            session.rollback()
            last_conflict = conflict
            continue
        return run, True

    winner = session.scalars(
        _active_run_statement(season.id, as_of_date, n_runs, seed)
    ).one_or_none()
    if winner is not None:
        return winner, _is_stale(winner, _database_now(session))
    if last_conflict is None:  # pragma: no cover - the loop only continues on a conflict
        raise RuntimeError("no run was inserted and no conflict was recorded")
    raise last_conflict


def execute_run(session_factory: sessionmaker[Session], run_id: uuid.UUID) -> None:
    """Claim ``run_id`` and, when the claim succeeds, execute and store it.

    The claim is one committed ``UPDATE ... RETURNING`` in its own session. When
    it updates no row, another executor owns the run or it has already finished,
    and this function returns without doing anything and without raising.

    A claimed run is executed in a fresh session: the fixed results, the
    history and the simulated fixtures are read, the model is fitted once, the
    engine runs, and one transaction writes the team rows and the terminal run
    state. Any exception rolls that work back, marks the row ``failed`` in a
    best-effort separate session, and returns without re-raising; there are no
    retries.

    Args:
        session_factory: the factory of the sessions the claim and the run use.
        run_id: the run to claim and execute.
    """
    if not _claim(session_factory, run_id):
        return
    started = time.monotonic()
    try:
        _execute(session_factory, run_id, started)
    except Exception as error:
        _mark_failed(session_factory, run_id, error)


def backfill_dates(session: Session, season_code: str) -> list[datetime.date]:
    """Return leakage-free retroactive as-of dates for one season, ascending.

    The first date is always the day before the season's first kickoff. When any
    match carries a matchday, every fully played matchday contributes the day
    after its last kickoff. When no match carries a matchday, the fallback is
    every 7, 14, 21 ... days after the first kickoff that is not later than the
    last kickoff of a played match, and no fallback date exists without a played
    match. Duplicate dates are returned once.

    Args:
        session: the session that reads the season and its matches.
        season_code: the season to build the dates for.

    Returns:
        The dates ascending, or an empty list when the season has no matches.

    Raises:
        ValueError: no season carries ``season_code``.
    """
    season = session.scalars(select(Season).where(Season.code == season_code)).one_or_none()
    if season is None:
        raise ValueError(f"unknown season code {season_code!r}")

    matches = list(session.scalars(select(Match).where(Match.season_id == season.id)))
    if not matches:
        return []

    first_kickoff = min(match.kickoff_date for match in matches)
    dates = {first_kickoff - datetime.timedelta(days=1)}
    matchdays = _matchdays(matches)
    if matchdays:
        for matchday in sorted(matchdays):
            day_matches = matchdays[matchday]
            if all(match.status == "played" for match in day_matches):
                last_kickoff = max(match.kickoff_date for match in day_matches)
                dates.add(last_kickoff + datetime.timedelta(days=1))
    else:
        played = [match.kickoff_date for match in matches if match.status == "played"]
        if played:
            last_played = max(played)
            days = 7
            candidate = first_kickoff + datetime.timedelta(days=days)
            while candidate <= last_played:
                dates.add(candidate)
                days += 7
                candidate = first_kickoff + datetime.timedelta(days=days)
    return sorted(dates)


def _active_run_statement(
    season_id: int, as_of_date: datetime.date, n_runs: int, seed: int
) -> Select[SimulationRun]:
    """Return the query of the one active run that holds the natural key."""
    return select(SimulationRun).where(
        SimulationRun.season_id == season_id,
        SimulationRun.as_of_date == as_of_date,
        SimulationRun.n_runs == n_runs,
        SimulationRun.seed == seed,
        SimulationRun.model == MODEL_NAME,
        SimulationRun.status.in_(_ACTIVE_STATUSES),
    )


def _database_now(session: Session) -> datetime.datetime:
    """Return the database clock, which staleness decisions must use."""
    now = session.scalar(select(func.now()))
    if now is None:  # pragma: no cover - ``now()`` always returns one row
        raise RuntimeError("the database returned no clock value")
    return cast(datetime.datetime, now)


def _is_stale(run: SimulationRun, now: datetime.datetime) -> bool:
    """Return whether an active row looks abandoned at database time ``now``."""
    if run.status == "queued":
        return now - run.requested_at > QUEUED_STALE_AFTER
    if run.status == "running":
        started_at = run.started_at
        return started_at is None or now - started_at > STALE_AFTER
    return False


def _claim(session_factory: sessionmaker[Session], run_id: uuid.UUID) -> bool:
    """Claim the run in its own committed transaction and report whether it worked."""
    with session_factory.begin() as session:
        claimed = session.execute(_CLAIM_RUN, {"run_id": run_id}).scalar_one_or_none()
    return claimed is not None


def _execute(session_factory: sessionmaker[Session], run_id: uuid.UUID, started: float) -> None:
    """Read, fit, simulate and store one claimed run."""
    with session_factory() as session:
        run = session.get(SimulationRun, run_id)
        if run is None:
            raise ValueError(f"simulation run {run_id} disappeared after it was claimed")

        inputs = _season_inputs(session, run.season_id, run.as_of_date)
        history = _history_before(session, run.as_of_date)
        model = DixonColesModel(seed=run.seed)
        model.fit(history, run.as_of_date)

        fixtures = Fixtures(
            match_id=np.array([fixture.match_id for fixture in inputs.fixtures], dtype=np.int64),
            kickoff_date=np.array(
                [fixture.sim_date for fixture in inputs.fixtures], dtype="datetime64[D]"
            ),
            season_id=np.full(len(inputs.fixtures), run.season_id, dtype=np.int64),
            home_team_id=np.array(
                [fixture.home_team_id for fixture in inputs.fixtures], dtype=np.int64
            ),
            away_team_id=np.array(
                [fixture.away_team_id for fixture in inputs.fixtures], dtype=np.int64
            ),
            odds=np.full((len(inputs.fixtures), 3), np.nan, dtype=np.float64),
            odds_source=tuple(None for _ in inputs.fixtures),
        )
        score_probs = model.score_matrix(fixtures)
        fixture_probs = fixture_outcome_probs(inputs.fixtures, score_probs)
        qualification = Qualification(
            ucl_positions=run.ucl_positions,
            europe_positions=run.europe_positions,
            relegation_positions=run.relegation_positions,
        )
        simulation = simulate_season(
            inputs.teams,
            inputs.fixed,
            inputs.fixtures,
            score_probs,
            n_runs=run.n_runs,
            seed=run.seed,
            qualification=qualification,
            condition_on=inputs.next_match_by_team,
        )
        current = {row.team_id: row for row in compute_standings(inputs.teams, inputs.fixed)}
        fingerprint = data_fingerprint(history)
        last_complete_matchday = _last_complete_matchday(session, run.season_id, run.as_of_date)
        description = model.describe()

        _store_success(
            session,
            run,
            simulation=simulation,
            fixture_probs=fixture_probs,
            current=current,
            fingerprint=fingerprint,
            description=description,
            n_fixed=len(inputs.fixed),
            n_simulated=len(inputs.fixtures),
            n_withdrawn_excluded=inputs.n_withdrawn,
            last_complete_matchday=last_complete_matchday,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )


def _season_inputs(session: Session, season_id: int, as_of_date: datetime.date) -> _SeasonInputs:
    """Split one season's matches into fixed results and simulated fixtures."""
    matches = list(
        session.scalars(
            select(Match).where(Match.season_id == season_id).order_by(Match.kickoff_date, Match.id)
        )
    )
    team_ids = sorted(
        {match.home_team_id for match in matches} | {match.away_team_id for match in matches}
    )
    teams = {
        int(team.id): str(team.name)
        for team in session.scalars(select(Team).where(Team.id.in_(team_ids)))
    }
    if len(teams) != len(team_ids):
        missing = sorted(set(team_ids) - set(teams))
        raise ValueError(f"matches name teams without a row: {missing}")

    fixed: list[MatchResult] = []
    fixtures: list[SimFixture] = []
    next_match_by_team: dict[int, int] = {}
    n_withdrawn = 0
    for match in matches:
        if match.status == "withdrawn":
            n_withdrawn += 1
            continue
        if match.status == "played" and match.kickoff_date < as_of_date:
            fixed.append(_match_result(match))
        else:
            fixtures.append(
                SimFixture(
                    match_id=match.id,
                    home_team_id=match.home_team_id,
                    away_team_id=match.away_team_id,
                    sim_date=max(match.kickoff_date, as_of_date),
                )
            )
            # The first simulated fixture a club meets in kickoff order is its
            # next match. ``sim_date`` is never consulted: a postponed fixture
            # collapses to the as-of date and would reorder the choice, and a
            # status check beyond the withdrawn rule could let a backfill run
            # read knowledge of later matches.
            next_match_by_team.setdefault(match.home_team_id, match.id)
            next_match_by_team.setdefault(match.away_team_id, match.id)
    fixtures.sort(key=lambda fixture: (fixture.sim_date, fixture.match_id))
    return _SeasonInputs(
        teams=teams,
        fixed=fixed,
        fixtures=fixtures,
        next_match_by_team=next_match_by_team,
        n_withdrawn=n_withdrawn,
    )


def _match_result(match: Match) -> MatchResult:
    """Return the fixed result of a played match, which must carry both goals."""
    if match.home_goals is None or match.away_goals is None:
        raise ValueError(f"played match {match.id} has no goals; a played match must carry both")
    return MatchResult(
        home_team_id=match.home_team_id,
        away_team_id=match.away_team_id,
        home_goals=match.home_goals,
        away_goals=match.away_goals,
        kickoff_date=match.kickoff_date,
    )


def _history_before(session: Session, as_of_date: datetime.date) -> History:
    """Return the loader's played matches, restricted to strictly before the date."""
    data = load_match_data(session)
    cutoff = np.datetime64(as_of_date, "D")
    return data.select(data.kickoff_date < cutoff)


def _last_complete_matchday(
    session: Session, season_id: int, as_of_date: datetime.date
) -> int | None:
    """Return the largest matchday whose matches and all earlier ones are complete."""
    matches = list(
        session.scalars(
            select(Match).where(
                Match.season_id == season_id,
                Match.matchday.is_not(None),
            )
        )
    )
    matchdays = _matchdays(matches)
    complete: int | None = None
    for matchday in sorted(matchdays):
        if not all(
            match.status == "played" and match.kickoff_date < as_of_date
            for match in matchdays[matchday]
        ):
            break
        complete = matchday
    return complete


def _matchdays(matches: list[Match]) -> dict[int, list[Match]]:
    """Group matches that carry a matchday by that number, ignoring the nulls."""
    grouped: dict[int, list[Match]] = {}
    for match in matches:
        if match.matchday is not None:
            grouped.setdefault(match.matchday, []).append(match)
    return grouped


def _store_success(
    session: Session,
    run: SimulationRun,
    *,
    simulation: SeasonSimulation,
    fixture_probs: tuple[FixtureOutcomeProbs, ...],
    current: dict[int, StandingRow],
    fingerprint: str,
    description: dict[str, object],
    n_fixed: int,
    n_simulated: int,
    n_withdrawn_excluded: int,
    last_complete_matchday: int | None,
    duration_ms: int,
) -> None:
    """Write the team rows and the terminal run state in one transaction."""
    rows: list[SimulationTeamResult] = []
    for team in simulation.teams:
        standing = current[team.team_id]
        rows.append(
            SimulationTeamResult(
                run_id=run.id,
                team_id=team.team_id,
                current_played=standing.played,
                current_points=standing.points,
                current_goal_difference=standing.goal_difference,
                mean_points=team.mean_points,
                points_p05=team.points_p05,
                points_p25=team.points_p25,
                points_p50=team.points_p50,
                points_p75=team.points_p75,
                points_p95=team.points_p95,
                points_min=team.points_min,
                points_pmf=[float(value) for value in team.points_pmf],
                mean_position=team.mean_position,
                position_probs=[float(value) for value in team.position_probs],
                p_title=team.p_title,
                p_ucl=team.p_ucl,
                p_europe=team.p_europe,
                p_relegation=team.p_relegation,
            )
        )
    fixture_rows = [
        SimulationFixture(
            run_id=run.id,
            match_id=fixture.match_id,
            p_home=fixture.p_home,
            p_draw=fixture.p_draw,
            p_away=fixture.p_away,
        )
        for fixture in fixture_probs
    ]
    next_match_rows = [
        SimulationNextMatch(
            run_id=run.id,
            team_id=counts.team_id,
            match_id=counts.match_id,
            outcome=counts.outcome,
            n_outcome=counts.n_outcome,
            n_relegated=counts.n_relegated,
            n_europe=counts.n_europe,
            n_title=counts.n_title,
            points_sum=counts.points_sum,
        )
        for counts in simulation.next_match
    ]
    finished_at = _database_now(session)
    session.add_all(rows)
    session.add_all(fixture_rows)
    session.add_all(next_match_rows)
    run.status = "succeeded"
    run.finished_at = finished_at
    run.duration_ms = duration_ms
    run.data_fingerprint = fingerprint
    run.model_version = str(description["version"])
    run.model_config = description
    run.n_fixed = n_fixed
    run.n_simulated = n_simulated
    run.n_withdrawn_excluded = n_withdrawn_excluded
    run.last_complete_matchday = last_complete_matchday
    session.commit()


def _mark_failed(
    session_factory: sessionmaker[Session], run_id: uuid.UUID, error: Exception
) -> None:
    """Best-effort separate transaction that records ``error`` on the run."""
    message = _error_text(error)
    try:
        with session_factory.begin() as session:
            session.execute(
                update(SimulationRun)
                .where(
                    SimulationRun.id == run_id,
                    SimulationRun.status == "running",
                )
                .values(
                    status="failed",
                    finished_at=func.now(),
                    error=message,
                )
            )
    except Exception:
        return


def _error_text(error: Exception) -> str:
    """Return the short, URL-free text stored in the run's ``error`` column."""
    if isinstance(error, SQLAlchemyError):
        return f"{type(error).__name__}: database error; driver details omitted"
    return str(error)[:2000]
