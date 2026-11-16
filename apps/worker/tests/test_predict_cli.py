"""Tests for the ``predict`` subcommand of the worker CLI.

The unit tests need no database and no network. They pin the command line, the
refusals ``argparse`` makes before any work, the configuration exit code and the
rule that decides how much of an error may leave the process.

The integration tests store one batch against the migrated disposable database
and read the result back: one run row, one prediction row per model and fixture,
and a second identical call that inserts nothing because a stored prediction is
immutable. They also print the report twice, on a database without predictions
and after a batch, so both the empty document and the pending batch are observed.

Every date a scenario stores comes from one PostgreSQL statement, so a run that
crosses Madrid midnight cannot split a fixture date from the as-of date the
command reads from the clock.
"""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, cast

import pytest
from lmi_core.db.models import Match, Prediction, PredictionRun
from lmi_core.live.predict import DEFAULT_HORIZON_DAYS
from lmi_core.modeling.registry import MODEL_NAMES
from lmi_worker import predict
from lmi_worker.cli import build_parser, main
from lmi_worker.predict import run_predict_command
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

if TYPE_CHECKING:
    from conftest import MigratedDatabase

_SELECT_MADRID_DATES = """
SELECT today, today + 1 AS tomorrow, today + 2 AS day_after,
       today - 21 AS played_1, today - 20 AS played_2,
       today - 14 AS played_3, today - 13 AS played_4
FROM (SELECT (now() AT TIME ZONE 'Europe/Madrid')::date AS today) AS dates
"""
"""Six dates around Madrid today from one statement.

One statement is the point: two calls could straddle Madrid midnight and give a
fixture a date relative to a today that is not the today of the as-of date the
command reads.
"""

_SELECT_ONE_SEASON = "SELECT id FROM seasons ORDER BY id LIMIT 1"
"""One seeded season, so a match row has a valid ``season_id``."""

_SELECT_TEAMS = "SELECT id FROM teams ORDER BY id LIMIT 4"
"""The four seeded teams this module builds its fixtures from."""

_INSERT_MATCH = """
INSERT INTO matches (
    season_id, kickoff_date, home_team_id, away_team_id, status,
    home_goals, away_goals, result
) VALUES (
    :season_id, :kickoff_date, :home_team_id, :away_team_id, :status,
    CAST(:home_goals AS integer), CAST(:away_goals AS integer), CAST(:result AS text)
)
"""
"""One match row; the played rows carry a score, the scheduled ones carry none."""

_PLAYED_PAIRS = ((0, 1), (2, 3), (1, 2), (3, 0))
"""Home and away team slots of the played history, all pairs distinct."""

_SCHEDULED_PAIRS = ((0, 2), (1, 3))
"""Home and away team slots of the two fixtures, distinct from the played pairs."""


def _store_matches(database: MigratedDatabase) -> date:
    """Store four played matches and two scheduled fixtures around Madrid today.

    Returns:
        The Madrid today the scenario was built on, read from the same statement
        as every fixture date.
    """
    with database.engine.begin() as connection:
        dates = connection.execute(text(_SELECT_MADRID_DATES)).mappings().one()
        season_id = int(connection.execute(text(_SELECT_ONE_SEASON)).scalar_one())
        teams = [int(team) for team in connection.execute(text(_SELECT_TEAMS)).scalars()]
        played_dates = [dates[f"played_{index}"] for index in range(1, len(_PLAYED_PAIRS) + 1)]
        for kickoff, (home, away) in zip(played_dates, _PLAYED_PAIRS, strict=True):
            connection.execute(
                text(_INSERT_MATCH),
                {
                    "season_id": season_id,
                    "kickoff_date": kickoff,
                    "home_team_id": teams[home],
                    "away_team_id": teams[away],
                    "status": "played",
                    "home_goals": 2,
                    "away_goals": 0,
                    "result": "H",
                },
            )
        for kickoff, (home, away) in zip(
            (dates["tomorrow"], dates["day_after"]), _SCHEDULED_PAIRS, strict=True
        ):
            connection.execute(
                text(_INSERT_MATCH),
                {
                    "season_id": season_id,
                    "kickoff_date": kickoff,
                    "home_team_id": teams[home],
                    "away_team_id": teams[away],
                    "status": "scheduled",
                    "home_goals": None,
                    "away_goals": None,
                    "result": None,
                },
            )
    return cast(date, dates["today"])


@pytest.mark.parametrize("value", ["0", "61"])
def test_a_horizon_outside_the_column_range_exits_two(
    value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["predict", "--horizon-days", value])

    assert excinfo.value.code == 2
    error = capsys.readouterr().err
    assert "--horizon-days" in error
    assert "1" in error
    assert "60" in error


def test_the_subcommand_defaults_follow_the_documented_values() -> None:
    args = build_parser().parse_args(["predict"])

    assert args.command == "predict"
    assert args.horizon_days == DEFAULT_HORIZON_DAYS
    assert args.models == MODEL_NAMES
    assert args.report is False


def test_the_parser_accepts_the_documented_flags() -> None:
    args = build_parser().parse_args(
        ["predict", "--horizon-days", "7", "--models", "home_win,base_rate", "--report"]
    )

    assert args.horizon_days == 7
    assert args.models == ("home_win", "base_rate")
    assert args.report is True


def test_an_unknown_model_exits_two_and_names_the_known_ones(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["predict", "--models", "home_win,bogus"])

    assert excinfo.value.code == 2
    error = capsys.readouterr().err
    assert "bogus" in error
    for name in MODEL_NAMES:
        assert name in error


def test_a_missing_database_configuration_exits_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse(settings: object) -> object:
        raise ValueError("POSTGRES_HOST is not set")

    monkeypatch.setattr(predict, "make_engine", refuse)

    code = run_predict_command(build_parser().parse_args(["predict"]))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "POSTGRES_HOST is not set" in captured.err


def test_the_error_text_keeps_a_guard_message_and_hides_a_driver_message() -> None:
    guard = IntegrityError(
        "INSERT INTO predictions ...",
        {"match_id": 1},
        RuntimeError("lmi: the match is no longer scheduled"),
    )
    driver = OperationalError(
        "SELECT 1",
        {},
        RuntimeError('connection to server at "db", port 5432 failed'),
    )

    guard_text = predict._error_text(guard)
    assert guard_text.startswith("IntegrityError: ")
    assert "lmi: the match is no longer scheduled" in guard_text

    # A driver message can carry the connection URL, so only the kind survives.
    assert predict._error_text(driver) == "OperationalError"

    other = predict._error_text(ValueError("x" * 2000))
    assert other.startswith("ValueError: ")
    assert len(other) == predict.MAX_ERROR_LENGTH


@pytest.mark.integration
def test_the_store_path_writes_one_run_and_the_second_call_inserts_nothing(
    migrated_engine: MigratedDatabase,
    capsys: pytest.CaptureFixture[str],
) -> None:
    today = _store_matches(migrated_engine)

    code = main(["predict", "--models", "home_win,base_rate,market"])

    first = capsys.readouterr()
    assert code == 0
    assert first.err == ""
    assert "predict: home_win rows=2 inserted=2 existing=0 no_forecast=0" in first.out
    assert "predict: base_rate rows=2 inserted=2 existing=0 no_forecast=0" in first.out
    # A scheduled match carries no odds in this scenario, so the market model
    # reports no forecast for both fixtures instead of a made-up probability.
    assert "predict: market rows=0 inserted=0 existing=0 no_forecast=2" in first.out
    assert "horizon=14" in first.out
    assert "history=4" in first.out
    assert "fixtures=2" in first.out

    with migrated_engine.factories() as session:
        runs = list(session.scalars(select(PredictionRun)))
        assert [run.status for run in runs] == ["succeeded"]
        run = runs[0]
        assert run.as_of_date == today
        assert run.trigger == "cli"
        assert run.n_history == 4
        assert run.n_fixtures == 2
        assert run.n_inserted == 4
        assert run.n_existing == 0
        assert run.n_no_forecast == 2
        assert run.error is None
        per_model = dict(
            session.execute(select(Prediction.model, func.count()).group_by(Prediction.model)).all()
        )
        assert per_model == {"home_win": 2, "base_rate": 2}
        assert session.scalar(select(func.count()).select_from(Match)) == 6

    code = main(["predict", "--models", "home_win,base_rate,market"])

    second = capsys.readouterr()
    assert code == 0
    assert "predict: home_win rows=2 inserted=0 existing=2 no_forecast=0" in second.out
    assert "predict: base_rate rows=2 inserted=0 existing=2 no_forecast=0" in second.out
    assert "predict: market rows=0 inserted=0 existing=0 no_forecast=2" in second.out

    with migrated_engine.factories() as session:
        runs = list(session.scalars(select(PredictionRun)))
        assert [run.status for run in runs] == ["succeeded", "succeeded"]
        assert any(run.n_inserted == 4 and run.n_existing == 0 for run in runs)
        assert any(run.n_inserted == 0 and run.n_existing == 4 for run in runs)
        assert session.scalar(select(func.count()).select_from(Prediction)) == 4


@pytest.mark.integration
def test_an_interruption_fails_the_run_and_stores_no_prediction(
    migrated_engine: MigratedDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupted batch must not leave the run ``running`` for ever.

    ``KeyboardInterrupt`` is not an ``Exception``, so the ordinary guard does not
    see it; the run opened before the batch was built must still reach ``failed``
    while the interrupt itself leaves the command.
    """
    _store_matches(migrated_engine)

    def interrupt(*args: object, **kwargs: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(predict, "build_predictions", interrupt)

    with pytest.raises(KeyboardInterrupt):
        main(["predict", "--models", "home_win"])

    with migrated_engine.factories() as session:
        runs = list(session.scalars(select(PredictionRun)))
        assert [run.status for run in runs] == ["failed"]
        assert "KeyboardInterrupt" in (runs[0].error or "")
        assert session.scalar(select(func.count()).select_from(Prediction)) == 0


@pytest.mark.integration
def test_a_failed_batch_leaves_the_run_failed_and_stores_no_prediction(
    migrated_engine: MigratedDatabase,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _store_matches(migrated_engine)

    def refuse(session: object, run_id: object, batch: object) -> object:
        raise ValueError("synthetic store failure")

    monkeypatch.setattr(predict, "write_batch", refuse)

    code = main(["predict", "--models", "home_win"])

    captured = capsys.readouterr()
    assert code == 1
    assert "predict: ValueError: synthetic store failure" in captured.err

    with migrated_engine.factories() as session:
        runs = list(session.scalars(select(PredictionRun)))
        assert [run.status for run in runs] == ["failed"]
        assert "synthetic store failure" in (runs[0].error or "")
        assert session.scalar(select(func.count()).select_from(Prediction)) == 0


@pytest.mark.integration
def test_the_report_prints_a_valid_document_before_and_after_a_batch(
    migrated_engine: MigratedDatabase,
    capsys: pytest.CaptureFixture[str],
) -> None:
    today = _store_matches(migrated_engine)

    assert main(["predict", "--report"]) == 0

    empty = json.loads(capsys.readouterr().out)
    assert empty["evaluated_as_of"] == today.isoformat()
    assert empty["models"] == []
    assert empty["common"] is None
    assert empty["counts"] == {
        "n_rows": 0,
        "n_scored": 0,
        "n_pending": 0,
        "n_superseded": 0,
        "n_late": 0,
        "n_withdrawn": 0,
    }

    assert main(["predict", "--models", "home_win,base_rate"]) == 0
    capsys.readouterr()

    assert main(["predict", "--report"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert {"counts", "models", "evaluated_as_of"} <= set(document)
    # Two models forecast two fixtures each, and every fixture is still in the
    # future, so every stored row is pending and nothing is scored yet.
    assert document["counts"]["n_rows"] == 4
    assert document["counts"]["n_pending"] == 4
    assert document["counts"]["n_scored"] == 0
    assert document["models"] == []
