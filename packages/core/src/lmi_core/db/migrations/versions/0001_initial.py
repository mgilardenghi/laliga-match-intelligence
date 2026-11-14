"""Initial schema.

Revision ID: 0001
Revises: None
Create Date: 2026-11-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the revision.

    CHECK constraints are named without a ``ck_`` prefix: the metadata naming
    convention of ``lmi_core.db.base`` renders them as
    ``ck_<table>_<name>``, so the bare name here produces the same constraint
    name that the models generate.
    """
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_teams"),
        sa.UniqueConstraint("name", name="uq_teams_name"),
    )

    op.create_table(
        "team_aliases",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_team_aliases"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_team_aliases_team_id_teams"),
        sa.UniqueConstraint("source", "alias", name="uq_team_aliases_source_alias"),
        sa.CheckConstraint(
            "source IN ('football_data', 'openfootball')",
            name="source_valid",
        ),
    )

    op.create_table(
        "seasons",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("start_year", sa.Integer(), nullable=False),
        sa.Column("fd_code", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_seasons"),
        sa.UniqueConstraint("code", name="uq_seasons_code"),
        sa.UniqueConstraint("start_year", name="uq_seasons_start_year"),
        sa.UniqueConstraint("fd_code", name="uq_seasons_fd_code"),
    )

    op.create_table(
        "matches",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("kickoff_date", sa.Date(), nullable=False),
        sa.Column("kickoff_time", sa.Time(), nullable=True),
        sa.Column("home_team_id", sa.Integer(), nullable=False),
        sa.Column("away_team_id", sa.Integer(), nullable=False),
        sa.Column("matchday", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("home_goals", sa.Integer(), nullable=True),
        sa.Column("away_goals", sa.Integer(), nullable=True),
        sa.Column("ht_home_goals", sa.Integer(), nullable=True),
        sa.Column("ht_away_goals", sa.Integer(), nullable=True),
        sa.Column("result", sa.CHAR(1), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="The loader sets updated_at = now() on every UPDATE that changes the row.",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_matches"),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], name="fk_matches_season_id_seasons"),
        sa.ForeignKeyConstraint(
            ["home_team_id"], ["teams.id"], name="fk_matches_home_team_id_teams"
        ),
        sa.ForeignKeyConstraint(
            ["away_team_id"], ["teams.id"], name="fk_matches_away_team_id_teams"
        ),
        sa.UniqueConstraint(
            "season_id",
            "home_team_id",
            "away_team_id",
            name="uq_matches_season_id_home_team_id_away_team_id",
        ),
        sa.CheckConstraint("status IN ('scheduled', 'played')", name="status_valid"),
        sa.CheckConstraint(
            "matchday IS NULL OR (matchday >= 1 AND matchday <= 46)",
            name="matchday_range",
        ),
        sa.CheckConstraint("home_team_id <> away_team_id", name="distinct_teams"),
        sa.CheckConstraint(
            "status <> 'scheduled' OR (home_goals IS NULL AND away_goals IS NULL "
            "AND ht_home_goals IS NULL AND ht_away_goals IS NULL AND result IS NULL)",
            name="scheduled_no_goals",
        ),
        sa.CheckConstraint(
            "status <> 'played' OR (home_goals IS NOT NULL AND away_goals IS NOT NULL "
            "AND result IS NOT NULL)",
            name="played_complete",
        ),
        sa.CheckConstraint(
            "result IS NULL OR result = CASE WHEN home_goals > away_goals THEN 'H' "
            "WHEN home_goals = away_goals THEN 'D' ELSE 'A' END",
            name="result_matches_score",
        ),
        sa.CheckConstraint(
            "home_goals IS NULL OR home_goals >= 0",
            name="home_goals_non_negative",
        ),
        sa.CheckConstraint(
            "away_goals IS NULL OR away_goals >= 0",
            name="away_goals_non_negative",
        ),
        sa.CheckConstraint(
            "ht_home_goals IS NULL OR ht_home_goals >= 0",
            name="ht_home_goals_non_negative",
        ),
        sa.CheckConstraint(
            "ht_away_goals IS NULL OR ht_away_goals >= 0",
            name="ht_away_goals_non_negative",
        ),
        sa.CheckConstraint(
            "(ht_home_goals IS NULL) = (ht_away_goals IS NULL)",
            name="ht_goals_both_or_neither",
        ),
        sa.CheckConstraint(
            "ht_home_goals IS NULL OR home_goals IS NULL "
            "OR (ht_home_goals <= home_goals AND ht_away_goals <= away_goals)",
            name="ht_goals_not_above_ft",
        ),
    )

    op.create_table(
        "match_stats",
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("home_shots", sa.Integer(), nullable=True),
        sa.Column("away_shots", sa.Integer(), nullable=True),
        sa.Column("home_shots_on_target", sa.Integer(), nullable=True),
        sa.Column("away_shots_on_target", sa.Integer(), nullable=True),
        sa.Column("home_fouls", sa.Integer(), nullable=True),
        sa.Column("away_fouls", sa.Integer(), nullable=True),
        sa.Column("home_corners", sa.Integer(), nullable=True),
        sa.Column("away_corners", sa.Integer(), nullable=True),
        sa.Column("home_yellow", sa.Integer(), nullable=True),
        sa.Column("away_yellow", sa.Integer(), nullable=True),
        sa.Column("home_red", sa.Integer(), nullable=True),
        sa.Column("away_red", sa.Integer(), nullable=True),
        sa.Column("home_xg", sa.Numeric(5, 2), nullable=True),
        sa.Column("away_xg", sa.Numeric(5, 2), nullable=True),
        sa.PrimaryKeyConstraint("match_id", name="pk_match_stats"),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name="fk_match_stats_match_id_matches",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "home_shots IS NULL OR home_shots >= 0",
            name="home_shots_non_negative",
        ),
        sa.CheckConstraint(
            "away_shots IS NULL OR away_shots >= 0",
            name="away_shots_non_negative",
        ),
        sa.CheckConstraint(
            "home_shots_on_target IS NULL OR home_shots_on_target >= 0",
            name="home_shots_on_target_non_negative",
        ),
        sa.CheckConstraint(
            "away_shots_on_target IS NULL OR away_shots_on_target >= 0",
            name="away_shots_on_target_non_negative",
        ),
        sa.CheckConstraint(
            "home_fouls IS NULL OR home_fouls >= 0",
            name="home_fouls_non_negative",
        ),
        sa.CheckConstraint(
            "away_fouls IS NULL OR away_fouls >= 0",
            name="away_fouls_non_negative",
        ),
        sa.CheckConstraint(
            "home_corners IS NULL OR home_corners >= 0",
            name="home_corners_non_negative",
        ),
        sa.CheckConstraint(
            "away_corners IS NULL OR away_corners >= 0",
            name="away_corners_non_negative",
        ),
        sa.CheckConstraint(
            "home_yellow IS NULL OR home_yellow >= 0",
            name="home_yellow_non_negative",
        ),
        sa.CheckConstraint(
            "away_yellow IS NULL OR away_yellow >= 0",
            name="away_yellow_non_negative",
        ),
        sa.CheckConstraint(
            "home_red IS NULL OR home_red >= 0",
            name="home_red_non_negative",
        ),
        sa.CheckConstraint(
            "away_red IS NULL OR away_red >= 0",
            name="away_red_non_negative",
        ),
        sa.CheckConstraint(
            "home_xg IS NULL OR home_xg >= 0",
            name="home_xg_non_negative",
        ),
        sa.CheckConstraint(
            "away_xg IS NULL OR away_xg >= 0",
            name="away_xg_non_negative",
        ),
    )

    op.create_table(
        "match_odds",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("bookmaker", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), server_default=sa.text("'1x2'"), nullable=False),
        sa.Column("is_closing", sa.Boolean(), nullable=False),
        sa.Column("home", sa.Numeric(7, 3), nullable=False),
        sa.Column("draw", sa.Numeric(7, 3), nullable=False),
        sa.Column("away", sa.Numeric(7, 3), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_match_odds"),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name="fk_match_odds_match_id_matches",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "match_id",
            "bookmaker",
            "market",
            "is_closing",
            name="uq_match_odds_match_id_bookmaker_market_is_closing",
        ),
        sa.CheckConstraint(
            "bookmaker IN ('bet365', 'pinnacle', 'market_avg', 'market_max')",
            name="bookmaker_valid",
        ),
        sa.CheckConstraint("home > 1", name="home_price_above_one"),
        sa.CheckConstraint("draw > 1", name="draw_price_above_one"),
        sa.CheckConstraint("away > 1", name="away_price_above_one"),
    )

    op.create_table(
        "ingestion_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("season_code", sa.Text(), nullable=False),
        sa.Column("loader_version", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rows_read", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_upserted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_removed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_rejected", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_runs"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="finished_matches_status",
        ),
        sa.CheckConstraint(
            "source IN ('football_data', 'openfootball')",
            name="source_valid",
        ),
        sa.CheckConstraint("rows_read >= 0", name="rows_read_non_negative"),
        sa.CheckConstraint("rows_upserted >= 0", name="rows_upserted_non_negative"),
        sa.CheckConstraint("rows_removed >= 0", name="rows_removed_non_negative"),
        sa.CheckConstraint("rows_rejected >= 0", name="rows_rejected_non_negative"),
    )
    op.create_index(
        "ix_ingestion_runs_source_season_code_started_at",
        "ingestion_runs",
        ["source", "season_code", sa.text("started_at DESC")],
        unique=False,
    )

    op.create_table(
        "ingestion_rejections",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_rejections"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ingestion_runs.id"],
            name="fk_ingestion_rejections_run_id_ingestion_runs",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_ingestion_rejections_run_id",
        "ingestion_rejections",
        ["run_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the revision."""
    op.drop_index(
        "ix_ingestion_runs_source_season_code_started_at",
        table_name="ingestion_runs",
    )
    op.drop_index(
        "ix_ingestion_rejections_run_id",
        table_name="ingestion_rejections",
    )
    op.drop_table("ingestion_rejections")
    op.drop_table("ingestion_runs")
    op.drop_table("match_odds")
    op.drop_table("match_stats")
    op.drop_table("matches")
    op.drop_table("team_aliases")
    op.drop_table("seasons")
    op.drop_table("teams")
