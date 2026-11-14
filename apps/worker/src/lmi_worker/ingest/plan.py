"""Plan the ingestion jobs of one command.

The plan is pure: it turns the command flags and the calendar into an ordered
list of source and season pairs. It performs no I/O and touches no database, so
a caller can log the plan before the first download.

The order is fixed. Past seasons are football-data rows only, oldest first,
because the historical files are the only complete record of those seasons. For
the current season the openfootball fixture list comes first, so the played
matches that football-data then writes already carry their matchday.

A season is covered only when its first year appears in
:data:`lmi_core.db.seed.SEASON_START_YEARS`: a season without a seeded row
cannot be loaded, so asking for it is a planning error rather than an empty plan.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from lmi_core.db.seed import SEASON_START_YEARS

from lmi_worker.seasons import SeasonCode, current_season

Source = Literal["football_data", "openfootball"]
"""A data source the loader knows how to fetch."""

SourceSelection = Literal["football_data", "openfootball", "all"]
"""The value of the source flag: one source or both."""

FD_URL = "https://www.football-data.co.uk/mmz4281/{fd_code}/SP1.csv"
"""football-data.co.uk season file; format with :attr:`SeasonCode.fd_code`."""

OF_URL = "https://raw.githubusercontent.com/openfootball/football.json/master/{season}/es.1.json"
"""openfootball fixture list; format with :attr:`SeasonCode.openfootball_path`."""

_SOURCE_SELECTIONS: tuple[SourceSelection, ...] = ("football_data", "openfootball", "all")


@dataclass(frozen=True)
class Job:
    """One unit of work: fetch and load one source for one season."""

    source: Source
    season: SeasonCode


class PlanError(ValueError):
    """Raised when the requested seasons or sources cannot form a plan."""


def source_url(job: Job) -> str:
    """Return the URL of the file that ``job`` loads."""
    if job.source == "football_data":
        return FD_URL.format(fd_code=job.season.fd_code)
    return OF_URL.format(season=job.season.openfootball_path)


def plan_jobs(
    *,
    season: str | None = None,
    backfill: bool = False,
    backfill_through: str | None = None,
    sources: SourceSelection = "all",
    today: date,
) -> list[Job]:
    """Return the ordered jobs of one ingestion command.

    An explicit ``season`` wins over the other flags, then an explicit
    ``backfill_through``, then ``backfill``, and with no flag the plan covers the
    season in progress on ``today``. ``backfill`` always starts at the oldest
    covered season, 2005-06.

    Args:
        season: one season code, for example ``"2025-26"``.
        backfill: plan every season from 2005-06 through the current season.
        backfill_through: plan every season from 2005-06 through this code.
        sources: which sources to plan; ``"all"`` means both.
        today: the calendar date that decides the current season.

    Raises:
        PlanError: a season is malformed or outside the covered range, the
            source selection is unknown, or openfootball is requested for a plan
            that contains a past season, because openfootball has no past
            fixture list.
    """
    if sources not in _SOURCE_SELECTIONS:
        raise PlanError(
            f"unknown source selection {sources!r}: expected one of {', '.join(_SOURCE_SELECTIONS)}"
        )
    in_progress = current_season(today)
    if season is not None:
        planned = [_covered(season)]
    elif backfill_through is not None:
        planned = _seasons_through(_covered(backfill_through))
    elif backfill:
        planned = _seasons_through(_covered(in_progress.code))
    else:
        planned = [_covered(in_progress.code)]

    past = [item for item in planned if item.start_year != in_progress.start_year]
    in_progress_seasons = [item for item in planned if item.start_year == in_progress.start_year]
    if sources == "openfootball" and past:
        raise PlanError(
            f"source openfootball covers only the current season {in_progress.code}; "
            "it has no fixture list for past seasons"
        )

    jobs = [Job(source="football_data", season=item) for item in past]
    for item in in_progress_seasons:
        if sources in ("openfootball", "all"):
            jobs.append(Job(source="openfootball", season=item))
        if sources in ("football_data", "all"):
            jobs.append(Job(source="football_data", season=item))
    return jobs


def _covered(code: str) -> SeasonCode:
    """Parse ``code`` and refuse a season outside the covered range."""
    try:
        season = SeasonCode.parse(code)
    except ValueError as error:
        raise PlanError(str(error)) from error
    if season.start_year not in SEASON_START_YEARS:
        first = min(SEASON_START_YEARS)
        last = max(SEASON_START_YEARS)
        oldest = SeasonCode(start_year=first, end_year=first + 1)
        newest = SeasonCode(start_year=last, end_year=last + 1)
        raise PlanError(
            f"season {season.code} is outside the covered range {oldest.code}..{newest.code}"
        )
    return season


def _seasons_through(last: SeasonCode) -> list[SeasonCode]:
    """Return the covered seasons from 2005-06 through ``last``, ascending."""
    first = min(SEASON_START_YEARS)
    return [
        SeasonCode(start_year=year, end_year=year + 1) for year in range(first, last.start_year + 1)
    ]
