"""The shared read layer of the four T5 detail routes.

``GET /v1/predictions``, ``GET /v1/model-metrics/live``,
``GET /v1/teams/{team_id}/focus`` and ``GET /v1/matches/{match_id}/preview``
answer from the same stored rows, so the readers live here once instead of once
per route module. No route and no reader redefines a derived rule: the outcome
of a prediction and its scores come from :func:`lmi_core.live.scoring.evaluate`,
the ranking of a season comes from :func:`lmi_core.standings.compute_standings`,
and this module only decides which rows a reader selects and which row of a
group stands for it.

The storage identity of a prediction (``id``, ``run_id``, ``model_config``,
``odds_source``) is read here as well, because
:func:`lmi_core.live.data.load_prediction_rows` deliberately selects the
evaluated columns only; every derived rule still stays in the core scorer.

The team focus reads the season rather than one ledger, so its readers live
here too: the team set and the counted results of a season, the played matches
of one team in one order, the form and the venue split over a caller-chosen
list of matches, and the team's row in the latest succeeded simulation. The
standings endpoint stays the owner of the table it serves; this module only
selects the same rows for the focus view, so the two cannot rank a season
differently.

The match preview reads the same stored results from another angle, so its two
readers live here too: the prior meetings of a pair, strictly before the
fixture's own date, and the record those meetings add up to.
"""

import datetime
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, cast
from uuid import UUID

from fastapi import HTTPException
from lmi_core.db.models import Match as MatchModel
from lmi_core.db.models import Prediction as PredictionModel
from lmi_core.db.models import Season as SeasonModel
from lmi_core.db.models import SimulationRun as SimulationRunModel
from lmi_core.db.models import SimulationTeamResult as SimulationTeamResultModel
from lmi_core.db.models import Team as TeamModel
from lmi_core.live.schemas import EvaluatedPrediction, PredictionRow
from lmi_core.modeling.registry import MODEL_NAMES
from lmi_core.standings import MatchResult
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.orm import Session, aliased

from lmi_api.schemas.matches import MatchOutcome, MatchStatus, MatchSummary
from lmi_api.schemas.predictions import (
    ModelName,
    PredictionBrief,
    Probabilities,
)
from lmi_api.schemas.standings import StandingForm
from lmi_api.schemas.team_focus import FormSummary, SimulationOutlook, SplitRecord
from lmi_api.schemas.teams import Team

_MatchStatus = Literal["scheduled", "played", "withdrawn"]
_Result = Literal["H", "D", "A"]
_Identity = tuple[int, str, datetime.date]

_MODEL_ORDER: Final[Mapping[str, int]] = {name: index for index, name in enumerate(MODEL_NAMES)}
"""The registry position of each model name, so a report order is not invented here."""

_RETIRED: Final[frozenset[str]] = frozenset({"superseded", "late"})
"""The outcomes that can never stand for their group: a newer row or the date replaced them."""

_POINTS: Final[Mapping[StandingForm, int]] = {"W": 3, "D": 1, "L": 0}
"""The points a result earns: three for a win, one for a draw, none for a loss."""

_Side = Literal["home", "away"]
"""The two venues a split record is kept for."""


@dataclass(frozen=True)
class StoredPrediction:
    """One stored prediction and the identity columns the core rows drop.

    ``row`` is the evaluated view of the row, everything a scorer needs.
    ``id``, ``run_id``, ``model_config`` and ``odds_source`` are the storage
    columns the API serves but a score never reads, which is exactly why the
    core loader does not select them.
    """

    id: int
    run_id: UUID
    model_config: dict[str, Any]
    odds_source: str | None
    row: PredictionRow

    @property
    def identity(self) -> _Identity:
        """Return the natural key ``(match_id, model, as_of_date)`` of the row.

        The database enforces that triple as unique, so it is the only key a
        reader needs to match an evaluated row back to the stored one.
        """
        return (self.row.match_id, self.row.model, self.row.as_of_date)


def resolve_season(session: Session, *, code: str | None) -> tuple[int, str]:
    """Return the identifier and the code of the season a request selects.

    A given code selects that season. Without one, the selection is the greatest
    ``start_year`` among the seasons with at least one match: the season that
    starts latest is the one a dashboard opens on, and requiring a match keeps
    the answer on a season ingestion has loaded instead of a season that is only
    registered. The rules mirror the standings endpoint, so the two resources
    open on the same season.

    Raises:
        HTTPException: 404 when the code is unknown, or when no season has a
            match while no code was given. The detail is a fixed sentence and
            never repeats the value the client sent: the value is untrusted
            input, and the sentence already says which kind of thing is
            unknown, unlike the season and team endpoints, which quote their own
            path parameter because that is the resource being addressed.
    """
    if code is not None:
        stored = session.scalars(select(SeasonModel).where(SeasonModel.code == code)).first()
        if stored is None:
            raise HTTPException(status_code=404, detail="Season not found")
        return stored.id, stored.code

    latest = session.scalars(
        select(SeasonModel)
        .where(select(MatchModel.id).where(MatchModel.season_id == SeasonModel.id).exists())
        .order_by(SeasonModel.start_year.desc())
        .limit(1)
    ).first()
    if latest is None:
        raise HTTPException(status_code=404, detail="No season with matches")
    return latest.id, latest.code


def model_position(model: str) -> int:
    """Return the report position of a model name, unknown names last.

    Both the page order of the predictions endpoint and the brief order of the
    team and match readers use it, so the registry order is read from
    :data:`lmi_core.modeling.registry.MODEL_NAMES` rather than restated.
    """
    return _MODEL_ORDER.get(model, len(MODEL_NAMES))


def load_match_summaries(session: Session, match_ids: Sequence[int]) -> dict[int, MatchSummary]:
    """Return the summary payload of every requested match, keyed by identifier.

    One statement serves the whole page: both teams come from explicit joins on
    aliases of the team table and the season code from a join on the season
    table, because the models declare no relationship the code could lazy-load
    per row. The rows are ordered by identifier so the mapping is built in one
    deterministic order.

    Args:
        session: a session bound to the application database.
        match_ids: the fixtures to describe. An empty sequence returns an empty
            mapping without issuing a statement.

    Returns:
        One :class:`~lmi_api.schemas.matches.MatchSummary` per stored match, by
        identifier. An identifier that names no match is simply absent.
    """
    if not match_ids:
        return {}

    home_team = aliased(TeamModel)
    away_team = aliased(TeamModel)
    statement = (
        select(MatchModel, SeasonModel.code, home_team, away_team)
        .join(SeasonModel, MatchModel.season_id == SeasonModel.id)
        .join(home_team, MatchModel.home_team_id == home_team.id)
        .join(away_team, MatchModel.away_team_id == away_team.id)
        .where(MatchModel.id.in_(match_ids))
        .order_by(MatchModel.id)
    )
    return {
        match.id: MatchSummary(
            id=match.id,
            season=season_code,
            kickoff_date=match.kickoff_date,
            kickoff_time=match.kickoff_time,
            matchday=match.matchday,
            # The ORM types ``status`` and ``result`` as plain text: a check
            # constraint restricts them to the values of their literal, so the
            # casts state a rule the database already enforces.
            status=cast(MatchStatus, match.status),
            home_team=Team(id=home.id, name=home.name),
            away_team=Team(id=away.id, name=away.name),
            home_goals=match.home_goals,
            away_goals=match.away_goals,
            ht_home_goals=match.ht_home_goals,
            ht_away_goals=match.ht_away_goals,
            result=cast(MatchOutcome | None, match.result),
        )
        for match, season_code, home, away in session.execute(statement).all()
    }


def load_stored_predictions(
    session: Session,
    *,
    match_ids: Sequence[int] | None = None,
    model: str | None = None,
    match_status: str | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
) -> list[StoredPrediction]:
    """Return the stored predictions that match every given filter.

    The filters are applied in SQL, never in Python, so a request over one
    season does not read the predictions of the others: ``match_ids`` restricts
    the page to a fixture set, ``model`` to one registered name,
    ``match_status`` to the current status of the match and the two dates to an
    inclusive range of ``kickoff_date``. The rows are ordered by the natural key
    and then by ``created_at``, so a ledger reads oldest first.

    The statement is one statement whatever the filters, and it selects the
    storage identity (``id``, ``run_id``, ``model_config``, ``odds_source``)
    that :func:`lmi_core.live.data.load_prediction_rows` deliberately does not
    select; every derived rule stays in the core scorer, which this function
    feeds and never replaces.

    Args:
        session: a session bound to the application database.
        match_ids: the fixtures to read. ``None`` reads every fixture.
        model: restrict the result to one model name; ``None`` reads every model.
        match_status: restrict the result to matches in one status.
        date_from: the inclusive lower bound of the kickoff date.
        date_to: the inclusive upper bound of the kickoff date.

    Returns:
        One :class:`StoredPrediction` per stored row, or an empty list when
        nothing matches.
    """
    conditions: list[ColumnElement[bool]] = []
    if match_ids is not None:
        conditions.append(PredictionModel.match_id.in_(match_ids))
    if model is not None:
        conditions.append(PredictionModel.model == model)
    if match_status is not None:
        conditions.append(MatchModel.status == match_status)
    if date_from is not None:
        conditions.append(MatchModel.kickoff_date >= date_from)
    if date_to is not None:
        conditions.append(MatchModel.kickoff_date <= date_to)

    statement = (
        select(
            PredictionModel.id,
            PredictionModel.run_id,
            PredictionModel.model_config,
            PredictionModel.match_id,
            PredictionModel.model,
            PredictionModel.model_version,
            PredictionModel.as_of_date,
            PredictionModel.created_at,
            PredictionModel.p_home,
            PredictionModel.p_draw,
            PredictionModel.p_away,
            PredictionModel.odds_source,
            MatchModel.kickoff_date,
            MatchModel.status,
            MatchModel.result,
        )
        .join(MatchModel, MatchModel.id == PredictionModel.match_id)
        .where(*conditions)
        .order_by(
            PredictionModel.match_id,
            PredictionModel.model,
            PredictionModel.as_of_date,
            PredictionModel.created_at,
        )
    )

    stored: list[StoredPrediction] = []
    for row in session.execute(statement):
        status = cast(_MatchStatus, str(row.status))
        # ``result`` is null unless the match is played. The stored letter is
        # passed through unchanged: the core scorer raises for a played match
        # without one, and a second guard here would hide that broken read.
        result: _Result | None = cast(_Result, row.result) if status == "played" else None
        stored.append(
            StoredPrediction(
                id=int(row.id),
                run_id=cast(UUID, row.run_id),
                model_config=cast(dict[str, Any], row.model_config),
                odds_source=cast(str | None, row.odds_source),
                row=PredictionRow(
                    match_id=int(row.match_id),
                    model=str(row.model),
                    model_version=str(row.model_version),
                    as_of_date=row.as_of_date,
                    created_at=row.created_at,
                    p_home=float(row.p_home),
                    p_draw=float(row.p_draw),
                    p_away=float(row.p_away),
                    kickoff_date=row.kickoff_date,
                    match_status=status,
                    result=result,
                ),
            )
        )
    return stored


def standing_predictions(
    evaluated: Sequence[EvaluatedPrediction],
) -> list[EvaluatedPrediction]:
    """Return the one row per ``(match, model)`` that stands for its group.

    A group is the ledger of one model over one match. The standing row is the
    greatest ``(as_of_date, created_at)`` among the rows that could still be
    served, which is the row :func:`lmi_core.live.scoring.evaluate` called
    ``scored`` or ``pending``. Two outcomes can leave a group without such a
    row, and neither is an error:

    * a withdrawn group keeps one row, its newest, because the fixture still has
      a forecast worth showing and the group is not empty;
    * a group whose every row is late keeps its newest row too, so a fixture
      whose date moved earlier still reports what was stored for it.

    The result is sorted by ``(match_id, model in registry order, as_of_date
    descending)``, so a caller that renders a match sees its models in report
    order regardless of the input order.
    """
    groups: dict[tuple[int, str], list[EvaluatedPrediction]] = defaultdict(list)
    for row in evaluated:
        groups[(row.match_id, row.model)].append(row)

    standing: list[EvaluatedPrediction] = []
    for group in groups.values():
        newest_first = sorted(group, key=_position, reverse=True)
        standing.append(
            next((row for row in newest_first if row.outcome not in _RETIRED), newest_first[0])
        )
    standing.sort(key=_standing_order)
    return standing


def prediction_briefs(
    evaluated: Sequence[EvaluatedPrediction],
    *,
    prediction_ids: Mapping[_Identity, int],
) -> list[PredictionBrief]:
    """Return the standing prediction of each group as a nested brief.

    ``prediction_ids`` maps the natural key of a stored row to its identifier,
    because the evaluated rows carry no identifier and the brief has to be
    followable to the detail endpoint. A caller that read its rows through
    :func:`load_stored_predictions` already holds that mapping.

    The result is ordered by the registry position of the model and then by its
    version, so a match lists its models in report order and two versions of one
    model stay distinguishable. Filtering is the caller's job: this function
    returns the standing row of every group it is given, and a caller that wants
    one fixture passes the rows of that fixture only.

    Raises:
        KeyError: a standing row is absent from ``prediction_ids``, which means
            the mapping was built from a different read than the one the rows
            came from.
    """
    briefs = [
        PredictionBrief(
            prediction_id=prediction_ids[(row.match_id, row.model, row.as_of_date)],
            model=cast(ModelName, row.model),
            model_version=row.model_version,
            created_at=row.created_at,
            as_of_date=row.as_of_date,
            probabilities=Probabilities(home=row.p_home, draw=row.p_draw, away=row.p_away),
            evaluation=row.outcome,
        )
        for row in standing_predictions(evaluated)
    ]
    briefs.sort(key=lambda brief: (model_position(brief.model), brief.model_version))
    return briefs


def load_season_standings(
    session: Session, *, season_id: int
) -> tuple[dict[int, str], list[MatchResult]]:
    """Return the team set and the counted results of one season.

    This mirrors :func:`lmi_api.routes.standings.get_standings`, which stays the
    owner of the table endpoint: the team set is every team that appears as the
    home or the away side in any match of the season, whatever the status of
    that match, so a club whose only fixture is still scheduled keeps a row of
    zeros. The counted results are the ``played`` matches with both goal columns
    set, which the ``played_complete`` check constraint already guarantees.

    Nothing here ranks anything. The only ranking is
    :func:`lmi_core.standings.compute_standings`, which a caller invokes with
    these two values, so the table a client reads from the standings endpoint
    and the row a team focus reports are the same computation.

    The request issues two statements whatever the number of teams and
    matches: one for the team set, read through a union of the two team columns
    because the models declare no relationship, and one for the results. The
    results are ordered by kickoff date and identifier so a form string is
    stable when a team played two matches on the same date.

    Args:
        session: a session bound to the application database.
        season_id: the stored identifier of the season to read.

    Returns:
        The ``team id -> name`` mapping of the season and its played matches.
        A season without a match returns an empty mapping and an empty list.
    """
    team_ids = (
        select(MatchModel.home_team_id.label("team_id"))
        .where(MatchModel.season_id == season_id)
        .union(
            select(MatchModel.away_team_id.label("team_id")).where(
                MatchModel.season_id == season_id
            )
        )
        .subquery()
    )
    team_statement = select(TeamModel.id, TeamModel.name).join(
        team_ids, TeamModel.id == team_ids.c.team_id
    )
    teams: dict[int, str] = {
        team_id: name for team_id, name in session.execute(team_statement).all()
    }

    result_statement = (
        select(
            MatchModel.home_team_id,
            MatchModel.away_team_id,
            MatchModel.home_goals,
            MatchModel.away_goals,
            MatchModel.kickoff_date,
        )
        .where(
            MatchModel.season_id == season_id,
            MatchModel.status == "played",
            # The ``played_complete`` check constraint makes both goal columns
            # non-null for a played row, so the filter states the rule the
            # database already enforces and the casts below narrow what it
            # guarantees.
            MatchModel.home_goals.is_not(None),
            MatchModel.away_goals.is_not(None),
        )
        .order_by(MatchModel.kickoff_date, MatchModel.id)
    )
    results = [
        MatchResult(
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_goals=cast(int, home_goals),
            away_goals=cast(int, away_goals),
            kickoff_date=kickoff_date,
        )
        for home_team_id, away_team_id, home_goals, away_goals, kickoff_date in session.execute(
            result_statement
        ).all()
    ]
    return teams, results


def load_played_summaries(
    session: Session,
    *,
    team_id: int,
    season_id: int | None = None,
    before: datetime.date | None = None,
    limit: int | None = None,
) -> list[MatchSummary]:
    """Return the played matches of one team, newest first.

    ``season_id`` bounds the read to one season, ``before`` to the matches whose
    kickoff date is strictly earlier than the given date, and ``limit`` to the
    first rows of the order. Every bound is applied in SQL, never in Python, so
    a bounded read does not load the rows it then discards.

    The ``before`` bound is what keeps a view leakage-free: a caller that shows
    the form of a fixture passes that fixture's own kickoff date and therefore
    never sees the match itself or a later one, which is the whole rule the
    match preview relies on. The order is ``kickoff_date`` descending and then
    identifier descending, so the newest match comes first and two matches on
    one date keep a stable order.

    A team that appears in a season but played nothing returns an empty list,
    not an error: that is the normal state of a club whose fixtures are still to
    come.

    The request issues one statement for the match identifiers and then the one
    of :func:`load_match_summaries`, so at most two whatever the limit.

    Args:
        session: a session bound to the application database.
        team_id: the team whose matches are read, home or away.
        season_id: restrict the read to one season; ``None`` reads every season.
        before: restrict the read to matches before this kickoff date, strictly.
        limit: keep at most this many matches; ``None`` keeps every match.

    Returns:
        The summaries in the order described above.
    """
    statement = (
        select(MatchModel.id)
        .where(
            MatchModel.status == "played",
            or_(MatchModel.home_team_id == team_id, MatchModel.away_team_id == team_id),
        )
        .order_by(MatchModel.kickoff_date.desc(), MatchModel.id.desc())
    )
    if season_id is not None:
        statement = statement.where(MatchModel.season_id == season_id)
    if before is not None:
        statement = statement.where(MatchModel.kickoff_date < before)
    if limit is not None:
        statement = statement.limit(limit)

    match_ids = list(session.scalars(statement))
    summaries = load_match_summaries(session, match_ids)
    return [summaries[match_id] for match_id in match_ids]


def load_head_to_head(
    session: Session,
    *,
    home_team_id: int,
    away_team_id: int,
    before: datetime.date,
    limit: int | None = None,
) -> list[MatchSummary]:
    """Return the prior meetings of two clubs, newest first.

    A meeting qualifies when it is ``played``, dated strictly before ``before``
    and played by the two clubs at either venue: the pair met as
    ``home against away`` or as ``away against home``, and both count, because a
    home-and-away season is symmetric from the record's point of view. ``limit``
    keeps the first rows of the order, so a caller that renders ten recent
    meetings still reads only ten.

    The strict ``before`` bound is what keeps a preview leakage-free. A caller
    passes the kickoff date of the fixture it describes, so the match itself is
    excluded, and so is a fixture of the same day and every later one: a date is
    compared, never a time, because no source provides a verified kickoff
    instant. A played match's preview therefore shows the pre-match view of it
    and never its own result.

    The order is ``kickoff_date`` descending and then identifier descending, so
    the newest meeting comes first and two meetings on one date keep a stable
    order. Two clubs that never met return an empty list, not an error: that is
    the normal state of a newly promoted pair.

    The request issues one statement for the match identifiers and then the one
    of :func:`load_match_summaries`, so at most two whatever the limit.

    Args:
        session: a session bound to the application database.
        home_team_id: the home side of the fixture being previewed.
        away_team_id: the away side of the fixture being previewed.
        before: the exclusive upper bound of the kickoff date, normally the
            fixture's own kickoff date.
        limit: keep at most this many meetings; ``None`` keeps every meeting.

    Returns:
        The summaries in the order described above.
    """
    statement = (
        select(MatchModel.id)
        .where(
            MatchModel.status == "played",
            MatchModel.kickoff_date < before,
            or_(
                and_(
                    MatchModel.home_team_id == home_team_id,
                    MatchModel.away_team_id == away_team_id,
                ),
                and_(
                    MatchModel.home_team_id == away_team_id,
                    MatchModel.away_team_id == home_team_id,
                ),
            ),
        )
        .order_by(MatchModel.kickoff_date.desc(), MatchModel.id.desc())
    )
    if limit is not None:
        statement = statement.limit(limit)

    match_ids = list(session.scalars(statement))
    summaries = load_match_summaries(session, match_ids)
    return [summaries[match_id] for match_id in match_ids]


def form_summary(team: Team, played: Sequence[MatchSummary]) -> FormSummary:
    """Return the form of ``team`` over the matches it is given, in that order.

    The caller decides the scope and the length: the team focus passes the last
    five played matches of the season and a match preview passes everything a
    side played before a kickoff. ``results`` is the ``W``/``D``/``L`` letter of
    each match from this team's point of view, ``points`` counts three, one and
    none of them, and ``goals_for`` and ``goals_against`` are the same matches
    seen from the same side. ``matches`` repeats the input as it was given, so
    the letters and the fixtures they came from stay aligned.

    Only a played match may reach this function: a scheduled fixture carries no
    goals and a substituted zero would invent a 0-0 result.

    Raises:
        ValueError: a match is not ``played``, or ``team`` is not one of its two
            sides. Both mean the caller selected the wrong rows.
    """
    lines = [_goal_line(match, team.id) for match in played]
    results: list[StandingForm] = [
        _outcome(goals_for, goals_against) for goals_for, goals_against in lines
    ]
    return FormSummary(
        team=team,
        results=results,
        points=sum(_POINTS[result] for result in results),
        goals_for=sum(scored for scored, _ in lines),
        goals_against=sum(conceded for _, conceded in lines),
        matches=list(played),
    )


def head_to_head_records(
    all_matches: Sequence[MatchSummary], *, home_team_id: int
) -> tuple[int, int, int]:
    """Return ``(home_side_wins, draws, away_side_wins)`` of a list of meetings.

    ``home_team_id`` is **this fixture's** home team, not the venue of each
    earlier meeting. For every prior match the goals of ``home_team_id`` are
    therefore the home goals when it was the home side of that match and the
    away goals otherwise, and the counters follow from those goals: a win when
    it scored more than its opponent, a draw when the two scored the same and a
    win for the other club otherwise. A venue-based count would describe the
    home teams of the earlier meetings instead, which is the other club in half
    of them.

    Nothing is read and nothing is ordered: the caller decides which meetings
    the record covers, so the same function serves every prior meeting of a pair,
    whatever number of them the payload then lists.

    Raises:
        ValueError: a match is not ``played``, so it carries no score, or
            ``home_team_id`` is neither of its two sides. Both mean the caller
            selected the wrong rows.
    """
    home_side_wins = 0
    draws = 0
    away_side_wins = 0
    for match in all_matches:
        goals_for, goals_against = _goal_line(match, home_team_id)
        if goals_for > goals_against:
            home_side_wins += 1
        elif goals_for < goals_against:
            away_side_wins += 1
        else:
            draws += 1
    return (home_side_wins, draws, away_side_wins)


def split_record(team_id: int, played: Sequence[MatchSummary], *, side: _Side) -> SplitRecord:
    """Return the record of the matches ``team_id`` played on one side.

    ``side`` selects the venue: ``home`` keeps the matches ``team_id`` played as
    the home side and ``away`` the ones it played as the visitor. The counters
    cover exactly that group, so the two records of a season partition its
    played matches and their points add up to the table's total for the team.

    Only a played match may reach this function, for the reason
    :func:`form_summary` gives: a missing score must not become 0-0.

    Raises:
        ValueError: a match is not ``played``, or ``team_id`` is not on the
            requesting side of a match that was passed in.
    """
    selected = [match for match in played if _plays(match, side, team_id)]
    lines = [_goal_line(match, team_id) for match in selected]
    points = sum(_POINTS[_outcome(scored, conceded)] for scored, conceded in lines)
    return SplitRecord(
        played=len(selected),
        won=sum(1 for goals_for, goals_against in lines if goals_for > goals_against),
        drawn=sum(1 for goals_for, goals_against in lines if goals_for == goals_against),
        lost=sum(1 for goals_for, goals_against in lines if goals_for < goals_against),
        goals_for=sum(scored for scored, _ in lines),
        goals_against=sum(conceded for _, conceded in lines),
        points=points,
        points_per_game=None if not selected else points / len(selected),
    )


def load_latest_simulation_outlook(
    session: Session, *, season_id: int, team_id: int
) -> SimulationOutlook | None:
    """Return the team's row in the latest succeeded simulation of a season.

    The run is selected among the ``succeeded`` runs of the season, ordered by
    ``finished_at`` descending with the rows still missing that stamp last, then
    by ``as_of_date`` descending and finally by identifier descending. Only
    succeeded runs count: a queued, running or failed run measures nothing, and
    serving its partial row would report a simulation that never finished.

    The selected run then has to carry a row for ``team_id``: the join is on the
    run and the team together, so the answer is ``None`` when the season has no
    succeeded run and also when the latest succeeded run stored no row for the
    team. An older run's row is deliberately not substituted, because the focus
    view describes one simulation, not the history of them.

    ``expected_points`` is the stored ``mean_points`` and ``expected_position``
    the stored ``mean_position``; the four probabilities and the two points
    quantiles are their stored columns as well, so no number is re-simulated or
    recomputed on read.

    The request issues one statement, whatever the number of stored runs.

    Args:
        session: a session bound to the application database.
        season_id: the stored identifier of the season to read.
        team_id: the team whose row is requested.

    Returns:
        The outlook, or ``None`` when no succeeded run of the season has a row
        for the team.
    """
    statement = (
        select(
            SimulationRunModel.id,
            SimulationRunModel.as_of_date,
            SimulationTeamResultModel.p_title,
            SimulationTeamResultModel.p_ucl,
            SimulationTeamResultModel.p_europe,
            SimulationTeamResultModel.p_relegation,
            SimulationTeamResultModel.mean_points,
            SimulationTeamResultModel.points_p05,
            SimulationTeamResultModel.points_p95,
            SimulationTeamResultModel.mean_position,
        )
        .join(
            SimulationTeamResultModel,
            and_(
                SimulationTeamResultModel.run_id == SimulationRunModel.id,
                SimulationTeamResultModel.team_id == team_id,
            ),
        )
        .where(
            SimulationRunModel.season_id == season_id,
            SimulationRunModel.status == "succeeded",
        )
        .order_by(
            SimulationRunModel.finished_at.desc().nulls_last(),
            SimulationRunModel.as_of_date.desc(),
            SimulationRunModel.id.desc(),
        )
        .limit(1)
    )
    row = session.execute(statement).first()
    if row is None:
        return None
    return SimulationOutlook(
        run_id=cast(UUID, row.id),
        as_of_date=row.as_of_date,
        p_title=float(row.p_title),
        p_ucl=float(row.p_ucl),
        p_europe=float(row.p_europe),
        p_relegation=float(row.p_relegation),
        expected_points=float(row.mean_points),
        points_p05=int(row.points_p05),
        points_p95=int(row.points_p95),
        expected_position=float(row.mean_position),
    )


def _position(row: EvaluatedPrediction) -> tuple[datetime.date, datetime.datetime]:
    """Return the ledger position of one row: its as-of date, then its write instant."""
    return (row.as_of_date, row.created_at)


def _standing_order(row: EvaluatedPrediction) -> tuple[int, int, int]:
    """Return the brief order of one standing row.

    The as-of date is negated through its ordinal because it sorts in the other
    direction from the two identifiers before it.
    """
    return (row.match_id, model_position(row.model), -row.as_of_date.toordinal())


def _plays(match: MatchSummary, side: _Side, team_id: int) -> bool:
    """Return whether ``team_id`` played ``match`` on ``side``."""
    team = match.home_team if side == "home" else match.away_team
    return team.id == team_id


def _goal_line(match: MatchSummary, team_id: int) -> tuple[int, int]:
    """Return ``(goals_for, goals_against)`` of one match as ``team_id`` saw it.

    Raises:
        ValueError: the match is not ``played``, so it carries no score, or
            ``team_id`` is neither of its two sides.
    """
    if match.home_team.id == team_id:
        goals_for, goals_against = match.home_goals, match.away_goals
    elif match.away_team.id == team_id:
        goals_for, goals_against = match.away_goals, match.home_goals
    else:
        raise ValueError(
            f"team {team_id} does not play match {match.id}: "
            f"it is {match.home_team.id} against {match.away_team.id}"
        )
    if goals_for is None or goals_against is None:
        raise ValueError(f"match {match.id} is {match.status}; only a played match has a score")
    return goals_for, goals_against


def _outcome(goals_for: int, goals_against: int) -> StandingForm:
    """Return ``W``, ``D`` or ``L`` from the side that scored ``goals_for``."""
    if goals_for > goals_against:
        return "W"
    if goals_for < goals_against:
        return "L"
    return "D"
