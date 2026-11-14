"""Behavior tests for planning ingestion jobs without a database or network."""

from datetime import date

import pytest
from lmi_worker.ingest.plan import Job, PlanError, plan_jobs, source_url
from lmi_worker.seasons import SeasonCode

TODAY = date(2026, 11, 14)


def _season(code: str) -> SeasonCode:
    return SeasonCode.parse(code)


def test_no_flags_plan_the_current_season_openfootball_then_football_data() -> None:
    jobs = plan_jobs(today=TODAY)

    assert jobs == [
        Job(source="openfootball", season=_season("2026-27")),
        Job(source="football_data", season=_season("2026-27")),
    ]


def test_backfill_plans_every_past_season_as_football_data_only() -> None:
    jobs = plan_jobs(backfill=True, today=TODAY)

    assert len(jobs) == 23
    assert jobs[0] == Job(source="football_data", season=_season("2005-06"))
    assert jobs[-2:] == [
        Job(source="openfootball", season=_season("2026-27")),
        Job(source="football_data", season=_season("2026-27")),
    ]
    past = jobs[:-2]
    assert all(job.source == "football_data" for job in past)
    assert [job.season for job in past] == [
        SeasonCode(start_year=year, end_year=year + 1) for year in range(2005, 2026)
    ]


def test_backfill_through_ends_at_the_requested_season() -> None:
    jobs = plan_jobs(backfill_through="2010-11", today=TODAY)

    assert [job.season.code for job in jobs] == [
        "2005-06",
        "2006-07",
        "2007-08",
        "2008-09",
        "2009-10",
        "2010-11",
    ]
    assert all(job.source == "football_data" for job in jobs)


def test_an_explicit_past_season_is_football_data_only() -> None:
    assert plan_jobs(season="2025-26", today=TODAY) == [
        Job(source="football_data", season=_season("2025-26"))
    ]


def test_an_explicit_current_season_plans_both_sources() -> None:
    assert plan_jobs(season="2026-27", today=TODAY) == [
        Job(source="openfootball", season=_season("2026-27")),
        Job(source="football_data", season=_season("2026-27")),
    ]


def test_openfootball_alone_plans_only_the_current_season() -> None:
    assert plan_jobs(season="2026-27", sources="openfootball", today=TODAY) == [
        Job(source="openfootball", season=_season("2026-27"))
    ]


def test_openfootball_alone_cannot_plan_a_past_season() -> None:
    with pytest.raises(PlanError):
        plan_jobs(season="2025-26", sources="openfootball", today=TODAY)


def test_openfootball_alone_cannot_plan_a_backfill() -> None:
    with pytest.raises(PlanError):
        plan_jobs(backfill=True, sources="openfootball", today=TODAY)


def test_football_data_alone_never_plans_openfootball() -> None:
    jobs = plan_jobs(backfill=True, sources="football_data", today=TODAY)

    assert len(jobs) == 22
    assert all(job.source == "football_data" for job in jobs)


@pytest.mark.parametrize("season", ["2004-05", "2027-28"])
def test_a_season_outside_the_covered_range_is_rejected(season: str) -> None:
    with pytest.raises(PlanError):
        plan_jobs(season=season, today=TODAY)


@pytest.mark.parametrize("season", ["2004-05", "2027-28"])
def test_a_backfill_through_outside_the_covered_range_is_rejected(season: str) -> None:
    with pytest.raises(PlanError):
        plan_jobs(backfill_through=season, today=TODAY)


def test_a_malformed_season_is_rejected() -> None:
    with pytest.raises(PlanError):
        plan_jobs(season="2026/27", today=TODAY)


def test_an_unknown_source_selection_is_rejected() -> None:
    with pytest.raises(PlanError):
        plan_jobs(sources="elsewhere", today=TODAY)  # type: ignore[arg-type]


def test_the_season_turns_on_the_first_of_july() -> None:
    on_30_june = plan_jobs(today=date(2026, 6, 30))
    on_1_july = plan_jobs(today=date(2026, 7, 1))

    assert on_30_june == [
        Job(source="openfootball", season=_season("2025-26")),
        Job(source="football_data", season=_season("2025-26")),
    ]
    assert on_1_july == [
        Job(source="openfootball", season=_season("2026-27")),
        Job(source="football_data", season=_season("2026-27")),
    ]


def test_a_season_after_the_seeded_range_is_rejected() -> None:
    on_30_june_2027 = plan_jobs(today=date(2027, 6, 30))

    assert on_30_june_2027 == [
        Job(source="openfootball", season=_season("2026-27")),
        Job(source="football_data", season=_season("2026-27")),
    ]
    with pytest.raises(PlanError):
        plan_jobs(today=date(2027, 7, 1))


def test_source_urls_follow_the_source_layouts() -> None:
    fd_url = source_url(Job(source="football_data", season=_season("2026-27")))
    of_url = source_url(Job(source="openfootball", season=_season("2026-27")))

    assert fd_url == "https://www.football-data.co.uk/mmz4281/2627/SP1.csv"
    assert of_url == (
        "https://raw.githubusercontent.com/openfootball/football.json/master/2026-27/es.1.json"
    )


def test_a_past_season_uses_its_football_data_code() -> None:
    url = source_url(Job(source="football_data", season=_season("2005-06")))

    assert url == "https://www.football-data.co.uk/mmz4281/0506/SP1.csv"
