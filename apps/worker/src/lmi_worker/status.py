"""Read-only status report of the ingested data.

The report answers three questions without touching the network and without
writing anything: how much of each season is stored, which tables hold rows, and
how the latest run of every source and season ended. It works on an empty but
migrated database, so the worker can report before the first ingestion.

The digest proves that a second ingestion changed nothing. It is an MD5 over the
ordered match columns, including ``updated_at`` but never an id, plus the
statistics and the odds keyed by season, home team, away team and slot. The
timestamp belongs to the digest on purpose: a load that writes nothing must not
move it, and a load that touches a row must.

Every line about a run starts with ``run ``. The acceptance check compares two
reports of two consecutive ingestions after filtering the lines that start with
``run``: run ids and start times differ between runs by design, while everything
else must be identical. The prefix is the contract that makes that filter safe.
"""

import hashlib
from dataclasses import dataclass

from lmi_core.db.models import (
    IngestionRun,
    Match,
    MatchOdd,
    MatchStat,
    Season,
    Team,
    TeamAlias,
)
from lmi_core.db.session import session_factory
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.selectable import FromClause

_MISSING = "-"
"""The text of a NULL value inside an aggregation."""


@dataclass(frozen=True)
class SeasonRow:
    """The match counts of one season."""

    code: str
    total: int
    played: int
    scheduled: int
    with_matchday: int


@dataclass(frozen=True)
class TableRow:
    """The row count of one domain table."""

    table: str
    rows: int


@dataclass(frozen=True)
class RunRow:
    """The latest run of one ``(source, season)`` pair."""

    source: str
    season: str
    status: str
    rows_read: int
    rows_upserted: int
    rows_removed: int
    rows_rejected: int
    started_at: str


@dataclass(frozen=True)
class StatusReport:
    """Everything :func:`format_status` prints, already ordered."""

    seasons: tuple[SeasonRow, ...]
    tables: tuple[TableRow, ...]
    runs: tuple[RunRow, ...]
    digest_matches: str
    digest_stats: str
    digest_odds: str
    digest: str


def collect_status(engine: Engine) -> StatusReport:
    """Read the status report from the database.

    The function opens one session for the whole report, so every counter and
    every digest describes the same snapshot of the data.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: the database cannot be reached or queried.
    """
    with session_factory(engine)() as session:
        seasons = tuple(_seasons(session))
        tables = tuple(_tables(session))
        runs = tuple(_latest_runs(session))
        matches_text, stats_text, odds_text = _digest_texts(session)

    matches_digest = _digest(matches_text)
    stats_digest = _digest(stats_text)
    odds_digest = _digest(odds_text)
    return StatusReport(
        seasons=seasons,
        tables=tables,
        runs=runs,
        digest_matches=matches_digest,
        digest_stats=stats_digest,
        digest_odds=odds_digest,
        digest=_digest(f"matches\n{matches_text}\nstats\n{stats_text}\nodds\n{odds_text}"),
    )


def format_status(report: StatusReport) -> str:
    """Render one report as deterministic text, one line per fact."""
    lines: list[str] = []
    for season in report.seasons:
        lines.append(
            f"season {season.code} total={season.total} played={season.played} "
            f"scheduled={season.scheduled} matchday={season.with_matchday}"
        )
    for table in report.tables:
        lines.append(f"table {table.table} rows={table.rows}")
    for run in report.runs:
        lines.append(
            f"run {run.source} {run.season} {run.status} read={run.rows_read} "
            f"upserted={run.rows_upserted} removed={run.rows_removed} "
            f"rejected={run.rows_rejected} started={run.started_at}"
        )
    lines.append(f"digest matches {report.digest_matches}")
    lines.append(f"digest stats {report.digest_stats}")
    lines.append(f"digest odds {report.digest_odds}")
    lines.append(f"digest overall {report.digest}")
    return "\n".join(lines)


def _seasons(session: Session) -> list[SeasonRow]:
    """Return one row per seeded season, including seasons without matches."""
    statement = (
        select(
            Season.code,
            func.count(Match.id).label("total"),
            func.count(Match.id).filter(Match.status == "played").label("played"),
            func.count(Match.id).filter(Match.status == "scheduled").label("scheduled"),
            func.count(Match.matchday).label("with_matchday"),
        )
        .select_from(Season)
        .outerjoin(Match, Match.season_id == Season.id)
        .group_by(Season.id, Season.code, Season.start_year)
        .order_by(Season.start_year)
    )
    return [
        SeasonRow(
            code=str(row.code),
            total=int(row.total or 0),
            played=int(row.played or 0),
            scheduled=int(row.scheduled or 0),
            with_matchday=int(row.with_matchday or 0),
        )
        for row in session.execute(statement)
    ]


def _tables(session: Session) -> list[TableRow]:
    """Return the row count of every domain table, in a fixed order.

    The audit tables ``ingestion_runs`` and ``ingestion_rejections`` are left
    out on purpose: every run appends a row, so their counts would change between
    two identical ingestions and would break the acceptance comparison that
    filters only the lines starting with ``run``.
    """
    return [
        TableRow(table="teams", rows=_count(session, Team.__table__)),
        TableRow(table="team_aliases", rows=_count(session, TeamAlias.__table__)),
        TableRow(table="seasons", rows=_count(session, Season.__table__)),
        TableRow(table="matches", rows=_count(session, Match.__table__)),
        TableRow(table="match_stats", rows=_count(session, MatchStat.__table__)),
        TableRow(table="match_odds", rows=_count(session, MatchOdd.__table__)),
    ]


def _count(session: Session, table: FromClause) -> int:
    """Return the number of rows of one table."""
    return int(session.scalar(select(func.count()).select_from(table)) or 0)


def _latest_runs(session: Session) -> list[RunRow]:
    """Return the latest run of each ``(source, season)`` pair, ordered by pair."""
    statement = select(IngestionRun).order_by(
        IngestionRun.source,
        IngestionRun.season_code,
        IngestionRun.started_at.desc(),
        IngestionRun.id.desc(),
    )
    latest: dict[tuple[str, str], IngestionRun] = {}
    for run in session.scalars(statement):
        latest.setdefault((run.source, run.season_code), run)
    return [
        RunRow(
            source=run.source,
            season=run.season_code,
            status=run.status,
            rows_read=run.rows_read,
            rows_upserted=run.rows_upserted,
            rows_removed=run.rows_removed,
            rows_rejected=run.rows_rejected,
            started_at=run.started_at.isoformat(),
        )
        for run in latest.values()
    ]


def _digest_texts(session: Session) -> tuple[str, str, str]:
    """Return the ordered texts of the matches, statistics and odds digest."""
    home = aliased(Team, name="home_team")
    away = aliased(Team, name="away_team")

    match_statement = (
        select(
            Season.code.label("season"),
            home.name.label("home"),
            away.name.label("away"),
            Match.kickoff_date,
            Match.kickoff_time,
            Match.matchday,
            Match.status,
            Match.home_goals,
            Match.away_goals,
            Match.ht_home_goals,
            Match.ht_away_goals,
            Match.result,
            Match.updated_at,
        )
        .join(Season, Season.id == Match.season_id)
        .join(home, home.id == Match.home_team_id)
        .join(away, away.id == Match.away_team_id)
        .order_by(Season.start_year, home.name, away.name)
    )
    matches = [
        _join(
            row.season,
            row.home,
            row.away,
            row.kickoff_date,
            row.kickoff_time,
            row.matchday,
            row.status,
            row.home_goals,
            row.away_goals,
            row.ht_home_goals,
            row.ht_away_goals,
            row.result,
            row.updated_at,
        )
        for row in session.execute(match_statement)
    ]

    stat_statement = (
        select(
            Season.code.label("season"),
            home.name.label("home"),
            away.name.label("away"),
            MatchStat.home_shots,
            MatchStat.away_shots,
            MatchStat.home_shots_on_target,
            MatchStat.away_shots_on_target,
            MatchStat.home_fouls,
            MatchStat.away_fouls,
            MatchStat.home_corners,
            MatchStat.away_corners,
            MatchStat.home_yellow,
            MatchStat.away_yellow,
            MatchStat.home_red,
            MatchStat.away_red,
            MatchStat.home_xg,
            MatchStat.away_xg,
        )
        .join(Match, Match.id == MatchStat.match_id)
        .join(Season, Season.id == Match.season_id)
        .join(home, home.id == Match.home_team_id)
        .join(away, away.id == Match.away_team_id)
        .order_by(Season.start_year, home.name, away.name)
    )
    stats = [
        _join(
            row.season,
            row.home,
            row.away,
            row.home_shots,
            row.away_shots,
            row.home_shots_on_target,
            row.away_shots_on_target,
            row.home_fouls,
            row.away_fouls,
            row.home_corners,
            row.away_corners,
            row.home_yellow,
            row.away_yellow,
            row.home_red,
            row.away_red,
            row.home_xg,
            row.away_xg,
        )
        for row in session.execute(stat_statement)
    ]

    odds_statement = (
        select(
            Season.code.label("season"),
            home.name.label("home"),
            away.name.label("away"),
            MatchOdd.bookmaker,
            MatchOdd.market,
            MatchOdd.is_closing,
            MatchOdd.home.label("price_home"),
            MatchOdd.draw.label("price_draw"),
            MatchOdd.away.label("price_away"),
        )
        .join(Match, Match.id == MatchOdd.match_id)
        .join(Season, Season.id == Match.season_id)
        .join(home, home.id == Match.home_team_id)
        .join(away, away.id == Match.away_team_id)
        .order_by(
            Season.start_year,
            home.name,
            away.name,
            MatchOdd.bookmaker,
            MatchOdd.market,
            MatchOdd.is_closing,
        )
    )
    odds = [
        _join(
            row.season,
            row.home,
            row.away,
            row.bookmaker,
            row.market,
            row.is_closing,
            row.price_home,
            row.price_draw,
            row.price_away,
        )
        for row in session.execute(odds_statement)
    ]
    return "\n".join(matches), "\n".join(stats), "\n".join(odds)


def _join(*values: object) -> str:
    """Join the values of one row with a separator no column value can contain."""
    return "|".join(_text(value) for value in values)


def _text(value: object) -> str:
    """Render one column value deterministically; ``None`` becomes a marker."""
    if value is None:
        return _MISSING
    return str(value)


def _digest(text: str) -> str:
    """Return the hexadecimal MD5 of ``text`` as UTF-8."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()
