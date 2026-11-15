"""Backtest results.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-15

Three tables hold one walk-forward backtest: the run header, one metric row per
run, model, season and subset, and the calibration bins. They reference
``seasons`` only, never ``matches``.

CHECK constraints are named without a ``ck_`` prefix: the metadata naming
convention of ``lmi_core.db.base`` renders them as ``ck_<table>_<name>``, so the
bare name here produces the same constraint name that the models generate.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the revision."""
    op.create_table(
        "backtest_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("from_year", sa.Integer(), nullable=False),
        sa.Column("step_days", sa.Integer(), nullable=False),
        sa.Column("code_version", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("data_fingerprint", sa.Text(), nullable=True),
        sa.Column("n_matches", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_runs"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="finished_matches_status",
        ),
        sa.CheckConstraint("step_days >= 1", name="step_days_at_least_one"),
    )
    op.create_index(
        "ix_backtest_runs_status_started_at",
        "backtest_runs",
        ["status", sa.text("started_at DESC")],
        unique=False,
    )

    op.create_table(
        "backtest_metrics",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=True),
        sa.Column("subset", sa.Text(), nullable=False),
        sa.Column("n_matches", sa.Integer(), nullable=False),
        sa.Column("n_fixtures", sa.Integer(), nullable=False),
        sa.Column("log_loss", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("brier", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("rps", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("accuracy", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_metrics"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["backtest_runs.id"],
            name="fk_backtest_metrics_run_id_backtest_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["seasons.id"],
            name="fk_backtest_metrics_season_id_seasons",
        ),
        sa.CheckConstraint(
            "model IN ('home_win', 'base_rate', 'market', 'elo', 'dixon_coles')",
            name="model_valid",
        ),
        sa.CheckConstraint("subset IN ('all', 'common')", name="subset_valid"),
        sa.CheckConstraint("n_matches > 0", name="n_matches_positive"),
        sa.CheckConstraint("n_fixtures >= n_matches", name="n_fixtures_at_least_n_matches"),
        sa.CheckConstraint("log_loss >= 0", name="log_loss_non_negative"),
        sa.CheckConstraint("brier BETWEEN 0 AND 2", name="brier_in_range"),
        sa.CheckConstraint("rps BETWEEN 0 AND 1", name="rps_in_range"),
        sa.CheckConstraint("accuracy BETWEEN 0 AND 1", name="accuracy_in_range"),
    )
    op.create_index(
        "ux_backtest_metrics_season",
        "backtest_metrics",
        ["run_id", "model", "subset", "season_id"],
        unique=True,
        postgresql_where=sa.text("season_id IS NOT NULL"),
    )
    op.create_index(
        "ux_backtest_metrics_overall",
        "backtest_metrics",
        ["run_id", "model", "subset"],
        unique=True,
        postgresql_where=sa.text("season_id IS NULL"),
    )

    op.create_table(
        "backtest_calibration",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("outcome", sa.CHAR(1), nullable=False),
        sa.Column("bin_index", sa.Integer(), nullable=False),
        sa.Column("bin_lower", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("bin_upper", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("mean_predicted", postgresql.DOUBLE_PRECISION(), nullable=True),
        sa.Column("observed_frequency", postgresql.DOUBLE_PRECISION(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_calibration"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["backtest_runs.id"],
            name="fk_backtest_calibration_run_id_backtest_runs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "run_id",
            "model",
            "outcome",
            "bin_index",
            name="uq_backtest_calibration_run_id_model_outcome_bin_index",
        ),
        sa.CheckConstraint(
            "model IN ('home_win', 'base_rate', 'market', 'elo', 'dixon_coles')",
            name="model_valid",
        ),
        sa.CheckConstraint("outcome IN ('H', 'D', 'A')", name="outcome_valid"),
        sa.CheckConstraint("bin_index BETWEEN 0 AND 99", name="bin_index_in_range"),
        sa.CheckConstraint("n >= 0", name="n_non_negative"),
        sa.CheckConstraint(
            "(n = 0) = (mean_predicted IS NULL)",
            name="empty_bin_has_no_mean",
        ),
        sa.CheckConstraint(
            "(n = 0) = (observed_frequency IS NULL)",
            name="empty_bin_has_no_frequency",
        ),
    )


def downgrade() -> None:
    """Revert the revision, leaving the 0001 schema intact."""
    op.drop_table("backtest_calibration")
    op.drop_index("ux_backtest_metrics_overall", table_name="backtest_metrics")
    op.drop_index("ux_backtest_metrics_season", table_name="backtest_metrics")
    op.drop_table("backtest_metrics")
    op.drop_index("ix_backtest_runs_status_started_at", table_name="backtest_runs")
    op.drop_table("backtest_runs")
