"""The browser test of the matchday page.

It covers what the page promises whatever data the stack holds: one heading,
one card per fixture with a link to the match page, a bar or a stated reason per
card, a chart that carries an accessible name, the previous and next matchday
links bounded to the season, and — once a matchday has been played — the score and
the evaluated forecasts of that round. Every assertion is about structure, a link
target or an element the server rendered, and never about a probability, a score
or a metric a particular dataset happens to produce. The empty tier holds on a
stack with no data; the rest needs the ingested fixtures and a stored
prediction, so they are loaded-tier tests.
"""

import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

# The design fixes both bounds of a La Liga season.
FIRST_MATCHDAY = 1
LAST_MATCHDAY = 46

MATCHDAY_HEADING = re.compile(r"^Matchday (\d+)$")
MATCH_LINK = re.compile(r"/matches/\d+")
PROBABILITY_CAPTION = re.compile(r"probabilities$")
# The score joins both goal totals with an en dash, which is written as an
# escape so the source stays plain ASCII.
SCORE = re.compile(r"\d+\u2013\d+")
# Every honest reason a card can give for drawing no bar: no stored forecast at
# all, a stored Dixon-Coles row that is not a pre-kickoff forecast, or stored rows
# that do not include the model the bar is built on.
NO_BAR = re.compile(
    r"No pre-kickoff forecast is stored|No Dixon-Coles forecast is stored|"
    r"was stamped at or after matchday midnight"
)


@pytest.mark.empty
def test_empty_stack_shows_the_ingest_commands(page: Page, dashboard_url: str) -> None:
    response = page.goto(f"{dashboard_url}/matchday")

    assert response is not None, "/matchday returned no navigation response"
    assert response.status == 200, f"/matchday answered HTTP {response.status}"

    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    expect(page.locator("body")).not_to_contain_text("API unreachable")
    expect(page.locator("main code").first).to_have_text("make ingest")


@pytest.mark.loaded
def test_cards_link_to_matches_and_show_a_probability_bar(page: Page, dashboard_url: str) -> None:
    page.goto(f"{dashboard_url}/matchday")

    cards = page.locator("article")
    count = cards.count()
    assert 1 <= count <= 10, f"/matchday rendered {count} fixture cards"

    drew_a_bar = False
    for index in range(count):
        card = cards.nth(index)
        link = card.get_by_role("link").first
        assert MATCH_LINK.search(link.get_attribute("href") or ""), (
            f"card {index} links to {link.get_attribute('href')!r} instead of a match page"
        )

        # A card either draws the Dixon-Coles forecast or says why it draws
        # none, so a regression that empties one card cannot hide behind the
        # nine others that still have a bar.
        figures = card.locator("figure").filter(
            has=page.locator("figcaption", has_text=PROBABILITY_CAPTION)
        )
        if figures.count() == 0:
            # The reason sits below the card's heading and metadata, so the card
            # text is searched rather than matched from its first character.
            assert card.get_by_text(NO_BAR).count() >= 1, (
                f"card {index} neither draws a probability bar nor states why: "
                f"{card.inner_text()!r}"
            )
        else:
            drew_a_bar = True
            expect(figures.first.locator('svg[role="img"]')).to_have_attribute(
                "aria-labelledby", re.compile(r"\S+")
            )

    assert drew_a_bar, "no card on the loaded stack drew a probability bar"


@pytest.mark.loaded
def test_pager_walks_the_season_inside_its_bounds(page: Page, dashboard_url: str) -> None:
    page.goto(f"{dashboard_url}/matchday")

    heading = page.get_by_role("heading", level=1)
    text = heading.inner_text().strip()
    match = MATCHDAY_HEADING.match(text)
    if match is None:
        pytest.skip(
            "the loaded dataset resolved no matchday: /matchday fell back to "
            f"{text!r}, so there is no pager to walk"
        )
    matchday = int(match.group(1))

    nav = page.get_by_role("navigation", name="Matchday")
    expect(nav).to_have_count(1)
    previous = nav.get_by_role("link", name="Previous matchday")
    following = nav.get_by_role("link", name="Next matchday")

    if matchday > FIRST_MATCHDAY:
        expect(previous).to_have_attribute("href", f"/matchday?matchday={matchday - 1}")
    else:
        expect(previous).to_have_count(0)

    if matchday < LAST_MATCHDAY:
        expect(following).to_have_attribute("href", f"/matchday?matchday={matchday + 1}")
        following.click()
        expect(page.get_by_role("heading", level=1)).to_have_text(f"Matchday {matchday + 1}")
    else:
        expect(following).to_have_count(0)

    first = page.goto(f"{dashboard_url}/matchday?matchday={FIRST_MATCHDAY}")
    assert first is not None and first.status == 200
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    expect(
        page.get_by_role("navigation", name="Matchday").get_by_role(
            "link", name="Previous matchday"
        )
    ).to_have_count(0)

    last = page.goto(f"{dashboard_url}/matchday?matchday={LAST_MATCHDAY}")
    assert last is not None and last.status == 200
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    expect(
        page.get_by_role("navigation", name="Matchday").get_by_role("link", name="Next matchday")
    ).to_have_count(0)


@pytest.mark.loaded
def test_a_played_matchday_card_shows_its_score_and_scores(page: Page, dashboard_url: str) -> None:
    page.goto(f"{dashboard_url}/matchday?matchday={FIRST_MATCHDAY}")

    played = page.locator("article").filter(has_text="Played")
    if played.count() == 0:
        pytest.skip(
            "no card on matchday 1 is marked Played: the loaded dataset must "
            "already have played a matchday of the current season"
        )

    card = played.first
    expect(card).to_contain_text(SCORE)

    # A played card lists every stored forecast with the score it earned. A
    # freshly loaded stack cannot hold a forecast for a matchday that is already
    # played, because the worker stores only the days ahead of it, so a card
    # without one says so instead of drawing an empty table. Both states are
    # honest and both are asserted here, so this test never depends on data that
    # the loaded tier is unable to produce.
    tables = card.locator("table")
    if tables.count() == 0:
        expect(card).to_contain_text("No pre-kickoff forecast is stored for this fixture")
    else:
        expect(tables.first.locator("th", has_text="Correct")).to_have_count(1)
        expect(tables.first.locator("th", has_text="RPS")).to_have_count(1)


@pytest.mark.loaded
@pytest.mark.parametrize("value", ["0", "47", "abc", "-3", "13.5"])
def test_an_out_of_range_matchday_falls_back_to_a_valid_page(
    page: Page, dashboard_url: str, value: str
) -> None:
    """Guard the query validation: a bad value never reaches the API as a 422."""
    response = page.goto(f"{dashboard_url}/matchday?matchday={value}")
    assert response is not None and response.status == 200, (
        f"?matchday={value} answered HTTP {response.status if response else 'none'}"
    )
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    expect(page.locator("body")).not_to_contain_text("API unreachable")

    nav = page.get_by_role("navigation", name="Matchday")
    hrefs = nav.get_by_role("link").evaluate_all("links => links.map(l => l.getAttribute('href'))")
    assert len(hrefs) >= 1, f"?matchday={value} renders no pager link"
    for href in hrefs:
        found = re.search(r"matchday=(-?\d+)", href or "")
        assert found is not None, f"the pager links to {href!r}"
        assert FIRST_MATCHDAY <= int(found.group(1)) <= LAST_MATCHDAY, (
            f"the pager links outside the season: {href!r}"
        )


REPO_ROOT = Path(__file__).resolve().parents[2]
UPCOMING_HEADING = "Upcoming fixtures"


def _psql(sql: str) -> str:
    """Run ``sql`` in the throwaway e2e database and return its unaligned output.

    The project name comes from the same ``E2E_PROJECT`` variable the Makefile
    guard validates, and it is passed as an argument, never through a shell.
    """
    project = os.environ.get("E2E_PROJECT", "lmi-e2e")
    # The developer's own stack is the Compose project `lmi`; this helper writes,
    # so it refuses that name even though the Makefile guard already does.
    if project.lower() == "lmi":
        pytest.fail("E2E_PROJECT names the developer stack `lmi`; refusing to write to it")
    command = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        "docker-compose.yml",
        "-f",
        "e2e/compose.e2e.yml",
        "exec",
        "-T",
        "db",
        "sh",
        "-c",
        'psql -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
    ]
    result = subprocess.run(
        command, input=sql, capture_output=True, text=True, check=True, cwd=REPO_ROOT
    )
    return result.stdout


@pytest.fixture
def upcoming_without_rounds() -> Iterator[None]:
    """Clear the round of every fixture from Madrid today on, then restore it.

    That is the state the fallback exists for: the next scheduled fixture
    carries no matchday. The original rounds are read back from the update
    itself and written again afterwards, whatever the test did.
    """
    rows = _psql(
        """
        WITH old AS (
            SELECT id, matchday FROM matches
            WHERE status = 'scheduled'
              AND kickoff_date >= (now() AT TIME ZONE 'Europe/Madrid')::date
              AND matchday IS NOT NULL
            FOR UPDATE
        )
        UPDATE matches AS m SET matchday = NULL FROM old WHERE m.id = old.id
        RETURNING old.id, old.matchday;
        """
    )
    pairs = [line.split("|") for line in rows.splitlines() if "|" in line]
    assert pairs, "no scheduled fixture from today on carries a round to clear"
    try:
        yield
    finally:
        values = ", ".join(f"({int(match_id)}, {int(matchday)})" for match_id, matchday in pairs)
        _psql(
            "UPDATE matches AS m SET matchday = v.matchday "
            f"FROM (VALUES {values}) AS v(id, matchday) WHERE m.id = v.id;"
        )


@pytest.mark.loaded
@pytest.mark.usefixtures("upcoming_without_rounds")
def test_a_next_fixture_without_a_round_falls_back_to_upcoming_fixtures(
    page: Page, dashboard_url: str
) -> None:
    """Guard the fallback: no round on the next fixture shows the coming week."""
    response = page.goto(f"{dashboard_url}/matchday")
    assert response is not None and response.status == 200

    expect(page.get_by_role("heading", level=1)).to_have_text(UPCOMING_HEADING)
    expect(page.get_by_role("navigation", name="Matchday")).to_have_count(0)
    cards = page.locator("article")
    assert cards.count() >= 1, "the fallback lists no fixture of the coming week"
    hrefs = cards.get_by_role("link").evaluate_all(
        "links => links.map(l => l.getAttribute('href'))"
    )
    assert any(MATCH_LINK.search(href or "") for href in hrefs), (
        f"the fallback cards link to {hrefs} and none is a match page"
    )
