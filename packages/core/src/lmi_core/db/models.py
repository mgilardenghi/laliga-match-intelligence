"""SQLAlchemy ORM models for the LMI database schema.

The schema is the contract shared by the API, the worker and the migration
job. Enum-like columns are TEXT guarded by a CHECK constraint, never a native
PostgreSQL enum: adding a value only changes a constraint, not the column type.

NULL means "not known yet". A ``scheduled`` match therefore carries no goals
and no result, while a ``played`` match must carry both goals and a result
that agrees with the score.
"""

import datetime
import decimal
import uuid

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from lmi_core.db.base import Base


class Team(Base):
    """A club that plays in the league."""

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TeamAlias(Base):
    """A source-specific name spelling for a team."""

    __tablename__ = "team_aliases"
    __table_args__ = (
        CheckConstraint(
            "source IN ('football_data', 'openfootball')",
            name="source_valid",
        ),
        UniqueConstraint("source", "alias"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)


class Season(Base):
    """One league season, identified by its code and its data-source code."""

    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    start_year: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    fd_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)


class Match(Base):
    """A fixture, with its result once the match has been played."""

    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("season_id", "home_team_id", "away_team_id"),
        CheckConstraint("status IN ('scheduled', 'played')", name="status_valid"),
        CheckConstraint(
            "matchday IS NULL OR (matchday >= 1 AND matchday <= 46)",
            name="matchday_range",
        ),
        CheckConstraint("home_team_id <> away_team_id", name="distinct_teams"),
        CheckConstraint(
            "status <> 'scheduled' OR (home_goals IS NULL AND away_goals IS NULL "
            "AND ht_home_goals IS NULL AND ht_away_goals IS NULL AND result IS NULL)",
            name="scheduled_no_goals",
        ),
        CheckConstraint(
            "status <> 'played' OR (home_goals IS NOT NULL AND away_goals IS NOT NULL "
            "AND result IS NOT NULL)",
            name="played_complete",
        ),
        CheckConstraint(
            "result IS NULL OR result = CASE WHEN home_goals > away_goals THEN 'H' "
            "WHEN home_goals = away_goals THEN 'D' ELSE 'A' END",
            name="result_matches_score",
        ),
        CheckConstraint(
            "home_goals IS NULL OR home_goals >= 0",
            name="home_goals_non_negative",
        ),
        CheckConstraint(
            "away_goals IS NULL OR away_goals >= 0",
            name="away_goals_non_negative",
        ),
        CheckConstraint(
            "ht_home_goals IS NULL OR ht_home_goals >= 0",
            name="ht_home_goals_non_negative",
        ),
        CheckConstraint(
            "ht_away_goals IS NULL OR ht_away_goals >= 0",
            name="ht_away_goals_non_negative",
        ),
        CheckConstraint(
            "(ht_home_goals IS NULL) = (ht_away_goals IS NULL)",
            name="ht_goals_both_or_neither",
        ),
        CheckConstraint(
            "ht_home_goals IS NULL OR home_goals IS NULL "
            "OR (ht_home_goals <= home_goals AND ht_away_goals <= away_goals)",
            name="ht_goals_not_above_ft",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    kickoff_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    kickoff_time: Mapped[datetime.time | None] = mapped_column(Time, nullable=True)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    matchday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    home_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ht_home_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ht_away_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str | None] = mapped_column(CHAR(1), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="The loader sets updated_at = now() on every UPDATE that changes the row.",
    )


class MatchStat(Base):
    """Team statistics for one match, one row per match."""

    __tablename__ = "match_stats"
    __table_args__ = (
        CheckConstraint("home_shots IS NULL OR home_shots >= 0", name="home_shots_non_negative"),
        CheckConstraint("away_shots IS NULL OR away_shots >= 0", name="away_shots_non_negative"),
        CheckConstraint(
            "home_shots_on_target IS NULL OR home_shots_on_target >= 0",
            name="home_shots_on_target_non_negative",
        ),
        CheckConstraint(
            "away_shots_on_target IS NULL OR away_shots_on_target >= 0",
            name="away_shots_on_target_non_negative",
        ),
        CheckConstraint("home_fouls IS NULL OR home_fouls >= 0", name="home_fouls_non_negative"),
        CheckConstraint("away_fouls IS NULL OR away_fouls >= 0", name="away_fouls_non_negative"),
        CheckConstraint(
            "home_corners IS NULL OR home_corners >= 0", name="home_corners_non_negative"
        ),
        CheckConstraint(
            "away_corners IS NULL OR away_corners >= 0", name="away_corners_non_negative"
        ),
        CheckConstraint("home_yellow IS NULL OR home_yellow >= 0", name="home_yellow_non_negative"),
        CheckConstraint("away_yellow IS NULL OR away_yellow >= 0", name="away_yellow_non_negative"),
        CheckConstraint("home_red IS NULL OR home_red >= 0", name="home_red_non_negative"),
        CheckConstraint("away_red IS NULL OR away_red >= 0", name="away_red_non_negative"),
        CheckConstraint("home_xg IS NULL OR home_xg >= 0", name="home_xg_non_negative"),
        CheckConstraint("away_xg IS NULL OR away_xg >= 0", name="away_xg_non_negative"),
    )

    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), primary_key=True
    )
    home_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_fouls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_fouls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_corners: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_corners: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_yellow: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_yellow: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_red: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_red: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_xg: Mapped[decimal.Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    away_xg: Mapped[decimal.Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)


class MatchOdd(Base):
    """One 1X2 price for one match, bookmaker and market snapshot."""

    __tablename__ = "match_odds"
    __table_args__ = (
        UniqueConstraint("match_id", "bookmaker", "market", "is_closing"),
        CheckConstraint(
            "bookmaker IN ('bet365', 'pinnacle', 'market_avg', 'market_max')",
            name="bookmaker_valid",
        ),
        CheckConstraint("home > 1", name="home_price_above_one"),
        CheckConstraint("draw > 1", name="draw_price_above_one"),
        CheckConstraint("away > 1", name="away_price_above_one"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    bookmaker: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'1x2'"))
    is_closing: Mapped[bool] = mapped_column(nullable=False)
    home: Mapped[decimal.Decimal] = mapped_column(Numeric(7, 3), nullable=False)
    draw: Mapped[decimal.Decimal] = mapped_column(Numeric(7, 3), nullable=False)
    away: Mapped[decimal.Decimal] = mapped_column(Numeric(7, 3), nullable=False)


class IngestionRun(Base):
    """One execution of a source loader for one season."""

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="status_valid"),
        CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="finished_matches_status",
        ),
        CheckConstraint(
            "source IN ('football_data', 'openfootball')",
            name="source_valid",
        ),
        CheckConstraint(
            "rows_read >= 0",
            name="rows_read_non_negative",
        ),
        CheckConstraint(
            "rows_upserted >= 0",
            name="rows_upserted_non_negative",
        ),
        CheckConstraint(
            "rows_removed >= 0",
            name="rows_removed_non_negative",
        ),
        CheckConstraint(
            "rows_rejected >= 0",
            name="rows_rejected_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    season_code: Mapped[str] = mapped_column(Text, nullable=False)
    loader_version: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    rows_read: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_upserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_removed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    content_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


Index(
    "ix_ingestion_runs_source_season_code_started_at",
    IngestionRun.source,
    IngestionRun.season_code,
    text("started_at DESC"),
)


class IngestionRejection(Base):
    """A source row that a loader refused, kept for later inspection."""

    __tablename__ = "ingestion_rejections"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    raw: Mapped[dict[str, object]] = mapped_column(postgresql.JSONB, nullable=False)


Index(
    "ix_ingestion_rejections_run_id",
    IngestionRejection.run_id,
)

BACKTEST_MODEL_NAMES = ("home_win", "base_rate", "market", "elo", "dixon_coles")
"""The models a backtest scores, in report order."""

_BACKTEST_MODEL_NAMES_SQL = ", ".join(f"'{name}'" for name in BACKTEST_MODEL_NAMES)
"""The model names as one SQL list literal, so both CHECK constraints agree."""


class BacktestRun(Base):
    """One execution of the walk-forward backtest.

    A run is inserted as ``running`` before any metric row exists and is then
    moved to ``succeeded`` or ``failed``. Every other column is written by the
    worker and never updated afterwards.
    """

    __tablename__ = "backtest_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="status_valid"),
        CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="finished_matches_status",
        ),
        CheckConstraint("step_days >= 1", name="step_days_at_least_one"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    from_year: Mapped[int] = mapped_column(Integer, nullable=False)
    step_days: Mapped[int] = mapped_column(Integer, nullable=False)
    code_version: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict[str, object]] = mapped_column(postgresql.JSONB, nullable=False)
    data_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    n_matches: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


Index(
    "ix_backtest_runs_status_started_at",
    BacktestRun.status,
    text("started_at DESC"),
)


class BacktestMetric(Base):
    """One metric row per run, model, season and subset.

    ``season_id`` is NULL for the row that covers every scored season. The
    tables of M3 reference ``seasons`` only: per-match backtest predictions are
    not stored here.
    """

    __tablename__ = "backtest_metrics"
    __table_args__ = (
        CheckConstraint(f"model IN ({_BACKTEST_MODEL_NAMES_SQL})", name="model_valid"),
        CheckConstraint("subset IN ('all', 'common')", name="subset_valid"),
        CheckConstraint("n_matches > 0", name="n_matches_positive"),
        CheckConstraint("n_fixtures >= n_matches", name="n_fixtures_at_least_n_matches"),
        CheckConstraint("log_loss >= 0", name="log_loss_non_negative"),
        CheckConstraint("brier BETWEEN 0 AND 2", name="brier_in_range"),
        CheckConstraint("rps BETWEEN 0 AND 1", name="rps_in_range"),
        CheckConstraint("accuracy BETWEEN 0 AND 1", name="accuracy_in_range"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    season_id: Mapped[int | None] = mapped_column(ForeignKey("seasons.id"), nullable=True)
    subset: Mapped[str] = mapped_column(Text, nullable=False)
    n_matches: Mapped[int] = mapped_column(Integer, nullable=False)
    n_fixtures: Mapped[int] = mapped_column(Integer, nullable=False)
    log_loss: Mapped[float] = mapped_column(postgresql.DOUBLE_PRECISION, nullable=False)
    brier: Mapped[float] = mapped_column(postgresql.DOUBLE_PRECISION, nullable=False)
    rps: Mapped[float] = mapped_column(postgresql.DOUBLE_PRECISION, nullable=False)
    accuracy: Mapped[float] = mapped_column(postgresql.DOUBLE_PRECISION, nullable=False)


# Two partial unique indexes instead of one unique constraint that includes
# season_id: a unique constraint does not treat two NULLs as equal, so it would
# accept a second overall row. ``NULLS NOT DISTINCT`` needs PostgreSQL 15+.
Index(
    "ux_backtest_metrics_season",
    BacktestMetric.run_id,
    BacktestMetric.model,
    BacktestMetric.subset,
    BacktestMetric.season_id,
    unique=True,
    postgresql_where=text("season_id IS NOT NULL"),
)
Index(
    "ux_backtest_metrics_overall",
    BacktestMetric.run_id,
    BacktestMetric.model,
    BacktestMetric.subset,
    unique=True,
    postgresql_where=text("season_id IS NULL"),
)


class BacktestCalibration(Base):
    """One calibration bin per run, model, outcome and bin index."""

    __tablename__ = "backtest_calibration"
    __table_args__ = (
        UniqueConstraint("run_id", "model", "outcome", "bin_index"),
        CheckConstraint(f"model IN ({_BACKTEST_MODEL_NAMES_SQL})", name="model_valid"),
        CheckConstraint("outcome IN ('H', 'D', 'A')", name="outcome_valid"),
        CheckConstraint("bin_index BETWEEN 0 AND 99", name="bin_index_in_range"),
        CheckConstraint("n >= 0", name="n_non_negative"),
        CheckConstraint(
            "(n = 0) = (mean_predicted IS NULL)",
            name="empty_bin_has_no_mean",
        ),
        CheckConstraint(
            "(n = 0) = (observed_frequency IS NULL)",
            name="empty_bin_has_no_frequency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(CHAR(1), nullable=False)
    bin_index: Mapped[int] = mapped_column(Integer, nullable=False)
    bin_lower: Mapped[float] = mapped_column(postgresql.DOUBLE_PRECISION, nullable=False)
    bin_upper: Mapped[float] = mapped_column(postgresql.DOUBLE_PRECISION, nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    mean_predicted: Mapped[float | None] = mapped_column(postgresql.DOUBLE_PRECISION, nullable=True)
    observed_frequency: Mapped[float | None] = mapped_column(
        postgresql.DOUBLE_PRECISION, nullable=True
    )
