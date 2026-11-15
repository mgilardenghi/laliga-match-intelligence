"""Live predictions and season simulations.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-16

Four tables hold the live prediction pass and the Monte Carlo simulation:
``prediction_runs`` headers, immutable ``predictions``, ``simulation_runs``
headers and one ``simulation_team_results`` row per team and run. The revision
also adds ``matches.withdrawn_at`` and ``ingestion_runs.rows_withdrawn``, widens
the ``matches`` status CHECK to ``withdrawn`` and narrows ``scheduled_no_goals``
so that it only constrains a match that is not played.

Triggers enforce what a CHECK constraint cannot: a stored prediction never
changes, and a prediction run is append-only except for its transition out of
``running``.

CHECK constraints are named without a ``ck_`` prefix: the metadata naming
convention of ``lmi_core.db.base`` renders them as ``ck_<table>_<name>``, so the
bare name here produces the same constraint name that the models generate.

The downgrade narrows ``ck_matches_status_valid`` back to ``scheduled`` and
``played``, so it fails while any match row is ``withdrawn``. That is intended:
the downgraded schema cannot represent a withdrawn match.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MATCHES_STATUS_VALID_0001 = "status IN ('scheduled', 'played')"
"""The ``ck_matches_status_valid`` text that revision 0001 installs."""

_MATCHES_SCHEDULED_NO_GOALS_0001 = (
    "status <> 'scheduled' OR (home_goals IS NULL AND away_goals IS NULL "
    "AND ht_home_goals IS NULL AND ht_away_goals IS NULL AND result IS NULL)"
)
"""The ``ck_matches_scheduled_no_goals`` text that revision 0001 installs."""

_GUARD_REJECT_MUTATION = """
CREATE FUNCTION lmi_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'lmi: % on % is not allowed, rows are immutable', TG_OP, TG_TABLE_NAME
    USING ERRCODE = 'P0001';
END $$;
"""
"""Reject every write a table must never receive."""

_GUARD_PREDICTIONS_INSERT = """
CREATE FUNCTION lmi_predictions_guard_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE kd date; st text;
BEGIN
  NEW.created_at := now();
  SELECT kickoff_date, status INTO kd, st FROM matches WHERE id = NEW.match_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'lmi: unknown match %', NEW.match_id USING ERRCODE = 'P0001';
  END IF;
  IF st <> 'scheduled' THEN
    RAISE EXCEPTION 'lmi: match % is %, not scheduled', NEW.match_id, st
      USING ERRCODE = 'P0001';
  END IF;
  IF NEW.as_of_date <> (now() AT TIME ZONE 'Europe/Madrid')::date THEN
    RAISE EXCEPTION 'lmi: as_of_date must be the Madrid date of creation'
      USING ERRCODE = 'P0001';
  END IF;
  IF NEW.created_at >= (kd::timestamp AT TIME ZONE 'Europe/Madrid') THEN
    RAISE EXCEPTION 'lmi: prediction for match % created at or after its kickoff date boundary',
      NEW.match_id USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END $$;
"""
"""Stamp ``created_at`` and refuse a prediction that could see the future."""

_GUARD_PREDICTION_RUNS = """
CREATE FUNCTION lmi_prediction_runs_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' OR OLD.status <> 'running'
     OR NEW.id <> OLD.id OR NEW.as_of_date <> OLD.as_of_date OR NEW.created_at <> OLD.created_at
     OR NEW.code_version <> OLD.code_version OR NEW.trigger <> OLD.trigger THEN
    RAISE EXCEPTION 'lmi: prediction run % is immutable', OLD.id USING ERRCODE = 'P0001';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
"""
"""Allow only the status transition of a run that is still ``running``."""

_TRIGGER_PREDICTIONS_GUARD_INSERT = """
CREATE TRIGGER predictions_guard_insert BEFORE INSERT ON predictions
  FOR EACH ROW EXECUTE FUNCTION lmi_predictions_guard_insert();
"""
"""Guard every insert into ``predictions``."""

_TRIGGER_PREDICTIONS_IMMUTABLE = """
CREATE TRIGGER predictions_immutable BEFORE UPDATE OR DELETE ON predictions
  FOR EACH ROW EXECUTE FUNCTION lmi_reject_mutation();
"""
"""Refuse an update or a delete of a stored prediction."""

_TRIGGER_PREDICTIONS_NO_TRUNCATE = """
CREATE TRIGGER predictions_no_truncate BEFORE TRUNCATE ON predictions
  FOR EACH STATEMENT EXECUTE FUNCTION lmi_reject_mutation();
"""
"""Refuse a truncation of ``predictions``."""

_TRIGGER_PREDICTION_RUNS_GUARD = """
CREATE TRIGGER prediction_runs_guard BEFORE UPDATE OR DELETE ON prediction_runs
  FOR EACH ROW EXECUTE FUNCTION lmi_prediction_runs_guard();
"""
"""Guard every update and delete of a prediction run."""

_TRIGGER_PREDICTION_RUNS_NO_TRUNCATE = """
CREATE TRIGGER prediction_runs_no_truncate BEFORE TRUNCATE ON prediction_runs
  FOR EACH STATEMENT EXECUTE FUNCTION lmi_reject_mutation();
"""
"""Refuse a truncation of ``prediction_runs``."""


def upgrade() -> None:
    """Apply the revision."""
    op.drop_constraint(op.f("ck_matches_status_valid"), "matches", type_="check")
    op.create_check_constraint(
        "status_valid",
        "matches",
        "status IN ('scheduled', 'played', 'withdrawn')",
    )
    op.drop_constraint(op.f("ck_matches_scheduled_no_goals"), "matches", type_="check")
    op.create_check_constraint(
        "scheduled_no_goals",
        "matches",
        "status = 'played' OR (home_goals IS NULL AND away_goals IS NULL "
        "AND ht_home_goals IS NULL AND ht_away_goals IS NULL AND result IS NULL)",
    )
    op.add_column(
        "matches",
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "withdrawn_at_matches_status",
        "matches",
        "(status = 'withdrawn') = (withdrawn_at IS NOT NULL)",
    )

    op.add_column(
        "ingestion_runs",
        sa.Column("rows_withdrawn", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_check_constraint(
        "rows_withdrawn_non_negative",
        "ingestion_runs",
        "rows_withdrawn >= 0",
    )

    op.create_table(
        "prediction_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("code_version", sa.Text(), nullable=False),
        sa.Column("data_fingerprint", sa.Text(), nullable=True),
        sa.Column("n_history", sa.Integer(), nullable=True),
        sa.Column("n_fixtures", sa.Integer(), nullable=True),
        sa.Column("n_inserted", sa.Integer(), nullable=True),
        sa.Column("n_existing", sa.Integer(), nullable=True),
        sa.Column("n_no_forecast", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_prediction_runs"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="status_valid",
        ),
        sa.CheckConstraint("trigger IN ('cli', 'schedule')", name="trigger_valid"),
        sa.CheckConstraint("horizon_days BETWEEN 1 AND 60", name="horizon_days_in_range"),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="finished_matches_status",
        ),
    )
    op.create_index(
        "ix_prediction_runs_as_of_date",
        "prediction_runs",
        [sa.text("as_of_date DESC")],
        unique=False,
    )

    op.create_table(
        "predictions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("model_config", postgresql.JSONB(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="The predictions_guard_insert trigger overwrites created_at with now().",
        ),
        sa.Column("p_home", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("p_draw", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("p_away", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("odds_source", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_predictions"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["prediction_runs.id"],
            name="fk_predictions_run_id_prediction_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name="fk_predictions_match_id_matches",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "match_id",
            "model",
            "as_of_date",
            name="uq_predictions_match_id_model_as_of_date",
        ),
        sa.CheckConstraint(
            "model IN ('home_win', 'base_rate', 'market', 'elo', 'dixon_coles')",
            name="model_valid",
        ),
        sa.CheckConstraint("p_home BETWEEN 0 AND 1", name="p_home_in_range"),
        sa.CheckConstraint("p_draw BETWEEN 0 AND 1", name="p_draw_in_range"),
        sa.CheckConstraint("p_away BETWEEN 0 AND 1", name="p_away_in_range"),
        sa.CheckConstraint(
            "abs(p_home + p_draw + p_away - 1) < 1e-6",
            name="probs_sum_to_one",
        ),
    )
    op.create_index(
        "ix_predictions_match_id_model_as_of_date",
        "predictions",
        ["match_id", "model", sa.text("as_of_date DESC")],
        unique=False,
    )
    op.create_index("ix_predictions_run_id", "predictions", ["run_id"], unique=False)

    op.create_table(
        "simulation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("n_runs", sa.Integer(), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("model_config", postgresql.JSONB(), nullable=True),
        sa.Column("ucl_positions", sa.Integer(), nullable=False),
        sa.Column("europe_positions", sa.Integer(), nullable=False),
        sa.Column("relegation_positions", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("code_version", sa.Text(), nullable=False),
        sa.Column("data_fingerprint", sa.Text(), nullable=True),
        sa.Column("n_fixed", sa.Integer(), nullable=True),
        sa.Column("n_simulated", sa.Integer(), nullable=True),
        sa.Column("n_withdrawn_excluded", sa.Integer(), nullable=True),
        sa.Column("last_complete_matchday", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_simulation_runs"),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["seasons.id"],
            name="fk_simulation_runs_season_id_seasons",
        ),
        sa.CheckConstraint("n_runs BETWEEN 100 AND 100000", name="n_runs_in_range"),
        sa.CheckConstraint("seed >= 0", name="seed_non_negative"),
        sa.CheckConstraint("model IN ('dixon_coles')", name="model_valid"),
        sa.CheckConstraint(
            "1 <= ucl_positions AND ucl_positions <= europe_positions",
            name="positions_ordered",
        ),
        sa.CheckConstraint(
            "relegation_positions >= 1",
            name="relegation_positions_at_least_one",
        ),
        sa.CheckConstraint("kind IN ('live', 'backfill')", name="kind_valid"),
        sa.CheckConstraint("trigger IN ('api', 'cli', 'schedule')", name="trigger_valid"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'queued') = (started_at IS NULL)",
            name="queued_has_no_start",
        ),
        sa.CheckConstraint(
            "(status IN ('queued', 'running')) = (finished_at IS NULL)",
            name="finished_matches_status",
        ),
    )
    op.create_index(
        "ux_simulation_runs_active",
        "simulation_runs",
        ["season_id", "as_of_date", "n_runs", "seed", "model"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_simulation_runs_season_id_as_of_date",
        "simulation_runs",
        ["season_id", sa.text("as_of_date DESC")],
        unique=False,
        postgresql_where=sa.text("status = 'succeeded'"),
    )

    op.create_table(
        "simulation_team_results",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("current_played", sa.Integer(), nullable=False),
        sa.Column("current_points", sa.Integer(), nullable=False),
        sa.Column("current_goal_difference", sa.Integer(), nullable=False),
        sa.Column("mean_points", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("points_p05", sa.Integer(), nullable=False),
        sa.Column("points_p25", sa.Integer(), nullable=False),
        sa.Column("points_p50", sa.Integer(), nullable=False),
        sa.Column("points_p75", sa.Integer(), nullable=False),
        sa.Column("points_p95", sa.Integer(), nullable=False),
        sa.Column("points_min", sa.Integer(), nullable=False),
        sa.Column(
            "points_pmf",
            postgresql.ARRAY(postgresql.DOUBLE_PRECISION()),
            nullable=False,
        ),
        sa.Column("mean_position", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column(
            "position_probs",
            postgresql.ARRAY(postgresql.DOUBLE_PRECISION()),
            nullable=False,
        ),
        sa.Column("p_title", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("p_ucl", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("p_europe", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("p_relegation", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_simulation_team_results"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["simulation_runs.id"],
            name="fk_simulation_team_results_run_id_simulation_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_simulation_team_results_team_id_teams",
        ),
        sa.UniqueConstraint(
            "run_id",
            "team_id",
            name="uq_simulation_team_results_run_id_team_id",
        ),
        sa.CheckConstraint("p_title BETWEEN 0 AND 1", name="p_title_in_range"),
        sa.CheckConstraint("p_ucl BETWEEN 0 AND 1", name="p_ucl_in_range"),
        sa.CheckConstraint("p_europe BETWEEN 0 AND 1", name="p_europe_in_range"),
        sa.CheckConstraint("p_relegation BETWEEN 0 AND 1", name="p_relegation_in_range"),
    )

    op.execute(_GUARD_REJECT_MUTATION)
    op.execute(_GUARD_PREDICTIONS_INSERT)
    op.execute(_GUARD_PREDICTION_RUNS)
    op.execute(_TRIGGER_PREDICTIONS_GUARD_INSERT)
    op.execute(_TRIGGER_PREDICTIONS_IMMUTABLE)
    op.execute(_TRIGGER_PREDICTIONS_NO_TRUNCATE)
    op.execute(_TRIGGER_PREDICTION_RUNS_GUARD)
    op.execute(_TRIGGER_PREDICTION_RUNS_NO_TRUNCATE)


def downgrade() -> None:
    """Revert the revision, refusing to lose a withdrawn match.

    The restored ``ck_matches_status_valid`` only accepts ``scheduled`` and
    ``played``, so this function fails while any row is ``withdrawn``.
    """
    op.execute("DROP TRIGGER predictions_guard_insert ON predictions")
    op.execute("DROP TRIGGER predictions_immutable ON predictions")
    op.execute("DROP TRIGGER predictions_no_truncate ON predictions")
    op.execute("DROP TRIGGER prediction_runs_guard ON prediction_runs")
    op.execute("DROP TRIGGER prediction_runs_no_truncate ON prediction_runs")
    op.execute("DROP FUNCTION lmi_predictions_guard_insert()")
    op.execute("DROP FUNCTION lmi_reject_mutation()")
    op.execute("DROP FUNCTION lmi_prediction_runs_guard()")

    op.drop_table("simulation_team_results")
    op.drop_index("ix_simulation_runs_season_id_as_of_date", table_name="simulation_runs")
    op.drop_index("ux_simulation_runs_active", table_name="simulation_runs")
    op.drop_table("simulation_runs")
    op.drop_index("ix_predictions_run_id", table_name="predictions")
    op.drop_index("ix_predictions_match_id_model_as_of_date", table_name="predictions")
    op.drop_table("predictions")
    op.drop_index("ix_prediction_runs_as_of_date", table_name="prediction_runs")
    op.drop_table("prediction_runs")

    op.drop_constraint(
        op.f("ck_ingestion_runs_rows_withdrawn_non_negative"),
        "ingestion_runs",
        type_="check",
    )
    op.drop_column("ingestion_runs", "rows_withdrawn")

    op.drop_constraint(
        op.f("ck_matches_withdrawn_at_matches_status"),
        "matches",
        type_="check",
    )
    op.drop_column("matches", "withdrawn_at")
    op.drop_constraint(op.f("ck_matches_scheduled_no_goals"), "matches", type_="check")
    op.create_check_constraint(
        "scheduled_no_goals",
        "matches",
        _MATCHES_SCHEDULED_NO_GOALS_0001,
    )
    op.drop_constraint(op.f("ck_matches_status_valid"), "matches", type_="check")
    op.create_check_constraint(
        "status_valid",
        "matches",
        _MATCHES_STATUS_VALID_0001,
    )
