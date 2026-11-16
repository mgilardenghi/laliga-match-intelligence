"""Per-fixture and per-team detail of one simulation run.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-23

Two tables hold the detail behind a ``simulation_runs`` header: one
``simulation_fixtures`` row per simulated fixture, with the probability triple
the simulation produced for it, and one ``simulation_next_match`` row per run,
team and outcome of an upcoming fixture, with the count of simulated seasons
that reached that outcome and the qualifiers that came with it.

Both tables are written once, in the transaction that stores a finished run, and
never updated afterwards: a rerun stores a new run id. The
``simulation_fixtures_immutable`` and ``simulation_next_match_immutable``
triggers reject every update with SQLSTATE ``P0001``, reusing
``lmi_reject_mutation()`` from revision 0003. Neither trigger guards DELETE or
TRUNCATE, so the ``ON DELETE CASCADE`` of ``run_id`` still removes the detail of
a deleted run and the destructive test reset still works.

CHECK constraints are named without a ``ck_`` prefix: the metadata naming
convention of ``lmi_core.db.base`` renders them as ``ck_<table>_<name>``, so the
bare name here produces the same constraint name that the models generate.

The downgrade drops both triggers and both tables and leaves
``lmi_reject_mutation()`` in place, because revision 0003 owns that function and
its own triggers still use it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRIGGER_SIMULATION_FIXTURES_IMMUTABLE = """
CREATE TRIGGER simulation_fixtures_immutable BEFORE UPDATE ON simulation_fixtures
  FOR EACH ROW EXECUTE FUNCTION lmi_reject_mutation();
"""
"""Refuse an update of a stored fixture probability row."""

_TRIGGER_SIMULATION_NEXT_MATCH_IMMUTABLE = """
CREATE TRIGGER simulation_next_match_immutable BEFORE UPDATE ON simulation_next_match
  FOR EACH ROW EXECUTE FUNCTION lmi_reject_mutation();
"""
"""Refuse an update of a stored next-match count row."""


def upgrade() -> None:
    """Apply the revision."""
    op.create_table(
        "simulation_fixtures",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("p_home", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("p_draw", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("p_away", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.PrimaryKeyConstraint("run_id", "match_id", name="pk_simulation_fixtures"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["simulation_runs.id"],
            name="fk_simulation_fixtures_run_id_simulation_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name="fk_simulation_fixtures_match_id_matches",
        ),
        sa.CheckConstraint("p_home BETWEEN 0 AND 1", name="p_home_in_range"),
        sa.CheckConstraint("p_draw BETWEEN 0 AND 1", name="p_draw_in_range"),
        sa.CheckConstraint("p_away BETWEEN 0 AND 1", name="p_away_in_range"),
        sa.CheckConstraint(
            "abs(p_home + p_draw + p_away - 1) < 1e-6",
            name="probs_sum_to_one",
        ),
    )

    op.create_table(
        "simulation_next_match",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("n_outcome", sa.Integer(), nullable=False),
        sa.Column("n_relegated", sa.Integer(), nullable=False),
        sa.Column("n_europe", sa.Integer(), nullable=False),
        sa.Column("n_title", sa.Integer(), nullable=False),
        sa.Column("points_sum", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("run_id", "team_id", "outcome", name="pk_simulation_next_match"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["simulation_runs.id"],
            name="fk_simulation_next_match_run_id_simulation_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_simulation_next_match_team_id_teams",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name="fk_simulation_next_match_match_id_matches",
        ),
        sa.CheckConstraint("outcome IN ('win', 'draw', 'loss')", name="outcome_valid"),
        sa.CheckConstraint("n_outcome >= 0", name="n_outcome_non_negative"),
        sa.CheckConstraint(
            "n_relegated BETWEEN 0 AND n_outcome",
            name="n_relegated_in_range",
        ),
        sa.CheckConstraint(
            "n_europe BETWEEN 0 AND n_outcome",
            name="n_europe_in_range",
        ),
        sa.CheckConstraint(
            "n_title BETWEEN 0 AND n_outcome",
            name="n_title_in_range",
        ),
        sa.CheckConstraint("points_sum >= 0", name="points_sum_non_negative"),
    )

    op.execute(_TRIGGER_SIMULATION_FIXTURES_IMMUTABLE)
    op.execute(_TRIGGER_SIMULATION_NEXT_MATCH_IMMUTABLE)


def downgrade() -> None:
    """Revert the revision, keeping the shared trigger function of revision 0003."""
    op.execute("DROP TRIGGER simulation_next_match_immutable ON simulation_next_match")
    op.execute("DROP TRIGGER simulation_fixtures_immutable ON simulation_fixtures")
    op.drop_table("simulation_next_match")
    op.drop_table("simulation_fixtures")
