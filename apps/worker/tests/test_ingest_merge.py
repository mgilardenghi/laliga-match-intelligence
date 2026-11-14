"""Behavior tests for the pure merge planning, without a database or network."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, time
from decimal import Decimal

import pytest
from lmi_worker.ingest.merge import (
    DuplicateKey,
    FdRecord,
    MatchKey,
    MatchState,
    OddsSlot,
    OfRecord,
    Prices,
    SeasonShapeError,
    SourceConflict,
    StatsState,
    StoredMatch,
    check_full_season,
    check_unique_keys,
    fd_record,
    of_record,
    plan_football_data,
    plan_openfootball,
)
from lmi_worker.sources.football_data import FdMatch, FdStats, OddsQuote
from lmi_worker.sources.openfootball import OfFixture

HOME_ID = 1
AWAY_ID = 2
MATCH_ID = 7
BET365 = OddsSlot(bookmaker="bet365", is_closing=False)
PINNACLE = OddsSlot(bookmaker="pinnacle", is_closing=False)


def _fd_match(**overrides: object) -> FdMatch:
    values: dict[str, object] = {
        "date": date(2026, 8, 15),
        "time": time(21, 0),
        "home": "Atlético Madrid",
        "away": "Real Betis",
        "fthg": 2,
        "ftag": 1,
        "ftr": "H",
        "hthg": 1,
        "htag": 0,
        "stats": FdStats(home_shots=12, away_shots=8),
        "home_xg": Decimal("1.225"),
        "away_xg": Decimal("0.8"),
        "odds": [
            OddsQuote(
                bookmaker="bet365",
                is_closing=False,
                home=Decimal("2.0625"),
                draw=Decimal("3.4"),
                away=Decimal("3.75"),
            )
        ],
    }
    values.update(overrides)
    return FdMatch.model_validate(values)


def _fd_record(match: FdMatch | None = None) -> FdRecord:
    return fd_record(match if match is not None else _fd_match(), HOME_ID, AWAY_ID)


def _fixture(*, kickoff_date: date = date(2026, 8, 15), matchday: int = 1) -> OfFixture:
    return OfFixture(date=kickoff_date, matchday=matchday, team1="A", team2="B")


def _of_record(*, kickoff_date: date = date(2026, 8, 15), matchday: int = 1) -> OfRecord:
    return of_record(_fixture(kickoff_date=kickoff_date, matchday=matchday), HOME_ID, AWAY_ID)


def _state(
    *,
    kickoff_date: date = date(2026, 8, 15),
    kickoff_time: time | None = time(21, 0),
    matchday: int | None = None,
    status: str = "played",
    home_goals: int | None = 2,
    away_goals: int | None = 1,
    ht_home_goals: int | None = 1,
    ht_away_goals: int | None = 0,
    result: str | None = "H",
) -> MatchState:
    return MatchState(
        kickoff_date=kickoff_date,
        kickoff_time=kickoff_time,
        matchday=matchday,
        status=status,
        home_goals=home_goals,
        away_goals=away_goals,
        ht_home_goals=ht_home_goals,
        ht_away_goals=ht_away_goals,
        result=result,
    )


def _scheduled_state(
    *,
    kickoff_date: date = date(2026, 8, 15),
    kickoff_time: time | None = None,
    matchday: int | None = 1,
) -> MatchState:
    return _state(
        kickoff_date=kickoff_date,
        kickoff_time=kickoff_time,
        matchday=matchday,
        status="scheduled",
        home_goals=None,
        away_goals=None,
        ht_home_goals=None,
        ht_away_goals=None,
        result=None,
    )


def _prices(home: str = "2.063", draw: str = "3.400", away: str = "3.750") -> Prices:
    return Prices(home=Decimal(home), draw=Decimal(draw), away=Decimal(away))


def _stored(
    *,
    match_id: int = MATCH_ID,
    state: MatchState | None = None,
    stats: StatsState | None = None,
    odds: Mapping[OddsSlot, Prices] | None = None,
) -> StoredMatch:
    return StoredMatch(
        id=match_id,
        state=state if state is not None else _state(),
        stats=stats,
        odds=odds if odds is not None else {},
    )


def _full_season(teams: int = 20) -> list[OfRecord]:
    ids = list(range(1, teams + 1))
    rotation = ids[1:]
    records: list[OfRecord] = []
    for round_index in range(teams - 1):
        pairs = [(ids[0], rotation[0])]
        pairs.extend((rotation[offset], rotation[-offset]) for offset in range(1, teams // 2))
        for home, away in pairs:
            matchday = round_index + 1
            records.append(OfRecord(MatchKey(home, away), date(2026, 8, 1), matchday))
            records.append(OfRecord(MatchKey(away, home), date(2026, 8, 2), matchday + teams - 1))
        rotation = rotation[1:] + rotation[:1]
    return records


def test_football_data_record_inserts_a_played_match_without_matchday() -> None:
    record = _fd_record()

    plan = plan_football_data({}, [record])

    assert plan.inserts == [record]
    assert record.state.status == "played"
    assert record.state.matchday is None
    assert record.state.result == "H"
    assert plan.updates == []
    assert plan.stats == []
    assert plan.odds_upserts == []
    assert plan.odds_deletes == []
    assert plan.deletes == []
    assert plan.rows_upserted == 1
    assert plan.rows_removed == 0


def test_football_data_record_quantizes_prices_and_expected_goals_half_up() -> None:
    record = _fd_record()

    assert record.odds[BET365].home == Decimal("2.063")
    assert record.odds[BET365].home != Decimal("2.062")
    assert record.stats.home_xg == Decimal("1.23")
    assert record.stats.home_xg != Decimal("1.22")


def test_a_half_up_value_does_not_look_like_a_change_against_stored_state() -> None:
    record = _fd_record()
    stored_stats = replace(record.stats, home_xg=Decimal("1.23"))
    stored = _stored(state=record.state, stats=stored_stats, odds={BET365: _prices()})

    plan = plan_football_data({record.key: stored}, [record])

    assert plan.updates == []
    assert plan.stats == []
    assert plan.odds_upserts == []
    assert plan.rows_upserted == 0


def test_an_identical_football_data_match_produces_an_empty_plan() -> None:
    record = _fd_record()
    stored = _stored(state=record.state, stats=record.stats, odds=dict(record.odds))

    plan = plan_football_data({record.key: stored}, [record])

    assert plan.inserts == []
    assert plan.updates == []
    assert plan.stats == []
    assert plan.odds_upserts == []
    assert plan.odds_deletes == []
    assert plan.deletes == []
    assert plan.notes == []
    assert plan.rows_upserted == 0


def test_football_data_turns_a_scheduled_match_into_played_and_keeps_matchday() -> None:
    record = _fd_record()
    scheduled = _scheduled_state(kickoff_date=date(2026, 8, 14), matchday=10)

    plan = plan_football_data({record.key: _stored(state=scheduled)}, [record])

    assert len(plan.updates) == 1
    match_id, changes = plan.updates[0]
    assert match_id == MATCH_ID
    assert set(changes) == {
        "kickoff_date",
        "kickoff_time",
        "status",
        "home_goals",
        "away_goals",
        "ht_home_goals",
        "ht_away_goals",
        "result",
    }
    assert changes["kickoff_date"] == date(2026, 8, 15)
    assert changes["kickoff_time"] == time(21, 0)
    assert plan.stats == [(MATCH_ID, record.stats)]
    assert plan.odds_upserts == [(MATCH_ID, BET365, record.odds[BET365])]


def test_football_data_overwrites_a_stored_time_with_null() -> None:
    record = _fd_record(_fd_match(time=None))
    stored = _stored(
        state=replace(_state(), kickoff_time=time(20, 0)),
        stats=record.stats,
        odds=dict(record.odds),
    )

    plan = plan_football_data({record.key: stored}, [record])

    assert plan.updates == [(MATCH_ID, {"kickoff_time": None})]
    assert plan.rows_upserted == 1


def test_stats_are_replaced_only_when_a_field_differs() -> None:
    record = _fd_record()
    stored = _stored(
        state=record.state,
        stats=replace(record.stats, home_shots=5),
        odds=dict(record.odds),
    )

    plan = plan_football_data({record.key: stored}, [record])

    assert plan.stats == [(MATCH_ID, record.stats)]
    assert plan.updates == []
    assert plan.rows_upserted == 1


def test_odds_mirror_the_file_with_upserts_and_deletions() -> None:
    record = _fd_record()
    stored = _stored(
        state=record.state,
        stats=record.stats,
        odds={BET365: _prices(home="2.100"), PINNACLE: _prices()},
    )

    plan = plan_football_data({record.key: stored}, [record])

    assert plan.odds_upserts == [(MATCH_ID, BET365, record.odds[BET365])]
    assert plan.odds_deletes == [(MATCH_ID, PINNACLE)]
    assert plan.updates == []
    assert plan.stats == []
    assert plan.rows_upserted == 1


def test_a_played_match_missing_from_the_file_is_noted_and_kept() -> None:
    record = _fd_record()
    stored = {
        record.key: _stored(state=record.state, stats=record.stats, odds=dict(record.odds)),
        MatchKey(home_team_id=3, away_team_id=4): _stored(
            match_id=9, state=_state(), stats=record.stats, odds={}
        ),
    }

    plan = plan_football_data(stored, [record])

    assert plan.deletes == []
    assert plan.rows_removed == 0
    assert len(plan.notes) == 1
    assert plan.updates == []


def test_openfootball_record_inserts_a_scheduled_match() -> None:
    record = _of_record(kickoff_date=date(2026, 8, 15), matchday=1)

    plan = plan_openfootball({}, [record])

    assert plan.inserts == [record]
    assert record.kickoff_date == date(2026, 8, 15)
    assert record.matchday == 1
    assert plan.rows_upserted == 1


def test_openfootball_updates_the_date_and_matchday_of_a_scheduled_match() -> None:
    record = _of_record(kickoff_date=date(2026, 9, 1), matchday=3)
    stored = _stored(state=_scheduled_state(kickoff_date=date(2026, 8, 20), matchday=1))

    plan = plan_openfootball({record.key: stored}, [record])

    assert plan.updates == [(MATCH_ID, {"kickoff_date": date(2026, 9, 1), "matchday": 3})]
    assert plan.rows_upserted == 1


def test_an_identical_scheduled_match_produces_no_changes() -> None:
    record = _of_record()
    stored = _stored(state=_scheduled_state(kickoff_date=record.kickoff_date, matchday=1))

    plan = plan_openfootball({record.key: stored}, [record])

    assert plan.updates == []
    assert plan.rows_upserted == 0


def test_openfootball_clears_a_stored_kickoff_time_on_a_scheduled_match() -> None:
    record = _of_record()
    stored = _stored(state=_scheduled_state(kickoff_time=time(20, 0), matchday=1))

    plan = plan_openfootball({record.key: stored}, [record])

    assert plan.updates == [(MATCH_ID, {"kickoff_time": None})]


def test_openfootball_fills_a_null_matchday_on_a_played_match() -> None:
    record = _of_record(kickoff_date=date(2026, 9, 20), matchday=5)
    stored = _stored(state=_state(), stats=None)

    plan = plan_openfootball({record.key: stored}, [record])

    assert plan.updates == [(MATCH_ID, {"matchday": 5})]
    assert plan.rows_upserted == 1


def test_openfootball_notes_but_keeps_a_non_null_played_matchday() -> None:
    record = _of_record(matchday=5)
    stored = _stored(state=_state(matchday=4))

    plan = plan_openfootball({record.key: stored}, [record])

    assert plan.updates == []
    assert len(plan.notes) == 1
    assert plan.rows_upserted == 0


def test_openfootball_deletes_scheduled_matches_absent_from_the_snapshot() -> None:
    record = _of_record()
    present = _stored(state=_scheduled_state(kickoff_date=record.kickoff_date, matchday=1))
    stale = _stored(match_id=8, state=_scheduled_state(kickoff_date=date(2026, 12, 1), matchday=2))
    stored = {record.key: present, MatchKey(home_team_id=3, away_team_id=4): stale}

    plan = plan_openfootball(stored, [record])

    assert plan.deletes == [8]
    assert plan.rows_removed == 1
    assert plan.updates == []


def test_openfootball_conflicts_when_a_played_match_is_absent() -> None:
    record = _of_record()
    played = _stored(match_id=9, state=_state())
    stored = {MatchKey(home_team_id=3, away_team_id=4): played}

    with pytest.raises(SourceConflict):
        plan_openfootball(stored, [record])


def test_check_full_season_accepts_a_complete_double_round_robin() -> None:
    check_full_season(_full_season())


def test_check_full_season_rejects_the_wrong_team_count() -> None:
    with pytest.raises(SeasonShapeError):
        check_full_season(_full_season(teams=19))


def test_check_full_season_rejects_a_missing_fixture() -> None:
    records = _full_season()

    with pytest.raises(SeasonShapeError):
        check_full_season(records[:-1])


def test_check_full_season_rejects_an_uneven_matchday() -> None:
    records = _full_season()
    records[0] = replace(records[0], matchday=2)

    with pytest.raises(SeasonShapeError):
        check_full_season(records)


def test_check_unique_keys_accepts_distinct_keys() -> None:
    check_unique_keys([_fd_record(), of_record(_fixture(), 3, 4)])


def test_check_unique_keys_rejects_a_repeated_key() -> None:
    record = _fd_record()

    with pytest.raises(DuplicateKey):
        check_unique_keys([record, record])


def test_check_unique_keys_rejects_a_team_playing_itself() -> None:
    with pytest.raises(DuplicateKey):
        check_unique_keys([fd_record(_fd_match(), 1, 1)])
