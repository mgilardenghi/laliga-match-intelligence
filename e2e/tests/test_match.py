"""The browser test of the match page.

It covers the two answers the page can give: a fixture that exists renders the
pre-kickoff view of it — the probabilities, both form strips, the head-to-head
section — and a route segment that names no fixture falls through to the
not-found page rather than to the error boundary. The empty tier holds on a
stack with no data; the rest needs the ingested fixtures and their stored
forecasts, so they are loaded-tier tests.

Every assertion is about structure, a link target, a heading or an element the
server rendered, and never about a probability or a score a particular dataset
happens to produce.
"""

import re

import pytest
from playwright.sync_api import Page, expect

MATCH_URL = re.compile(r"/matches/\d+$")
FIXTURE_HEADING = re.compile(r" vs ")
FORM_CAPTION = re.compile(r"^Form: ")
PROBABILITY_CAPTION = re.compile(r"probabilities$")


def _first_card_link(page: Page, dashboard_url: str) -> str:
    """Return the match link of the first card of the next matchday."""
    page.goto(f"{dashboard_url}/matchday")
    card = page.locator("article").first
    expect(card).to_have_count(1)
    href = card.get_by_role("link").first.get_attribute("href")
    assert href is not None and MATCH_URL.search(href), (
        f"the first matchday card links to {href!r} instead of a match page"
    )
    return href


@pytest.mark.empty
def test_a_segment_that_names_no_match_renders_the_not_found_page(
    page: Page, dashboard_url: str
) -> None:
    # The numeric identifier no fixture has is already covered by
    # `test_shell.py`, which owns the route's 404. What only this page can answer
    # is a segment that is not an identifier at all: it must reach the same
    # not-found screen instead of asking the API for a 422 the page cannot show.
    url = f"{dashboard_url}/matches/not-a-number"
    response = page.goto(url)

    assert response is not None, f"{url} returned no navigation response"
    assert response.status == 404, f"{url} answered HTTP {response.status}"
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    expect(page.get_by_role("heading", level=1)).to_have_text("Page not found")
    expect(page.get_by_role("navigation", name="Main")).to_have_count(1)
    expect(page.locator("body")).not_to_contain_text("API unreachable")


@pytest.mark.loaded
def test_a_match_card_opens_the_pre_match_view(page: Page, dashboard_url: str) -> None:
    href = _first_card_link(page, dashboard_url)

    page.click(f'a[href="{href}"]')
    page.wait_for_url(re.compile(re.escape(href) + r"$"))

    heading = page.get_by_role("heading", level=1)
    expect(heading).to_have_count(1)
    assert FIXTURE_HEADING.search(heading.inner_text()), (
        f"the match heading {heading.inner_text()!r} does not name both clubs"
    )

    expect(page.get_by_role("heading", name="Pre-match view")).to_have_count(1)
    expect(page.get_by_role("heading", name="Head-to-head")).to_have_count(1)

    # Both form strips: one per club, each captioned with the letters it shows.
    forms = page.locator("figure").filter(has=page.locator("figcaption", has_text=FORM_CAPTION))
    expect(forms).to_have_count(2)

    # At least one model probability bar, with an accessible name. A bar is a
    # chart, so it is a figure with a caption and a labelled SVG.
    bars = page.locator("figure").filter(
        has=page.locator("figcaption", has_text=PROBABILITY_CAPTION)
    )
    assert bars.count() >= 1, "the pre-match view renders no probability figure"
    expect(bars.first.locator('svg[role="img"]')).to_have_accessible_name(re.compile(r"\S+"))


@pytest.mark.loaded
def test_a_played_match_shows_the_pre_match_view_and_the_result(
    page: Page, dashboard_url: str
) -> None:
    page.goto(f"{dashboard_url}/matchday?matchday=1")

    played = page.locator("article").filter(has_text="Played")
    if played.count() == 0:
        pytest.skip(
            "no card on matchday 1 is marked Played: the loaded dataset must "
            "already have played a matchday of the current season"
        )

    href = played.first.get_by_role("link").first.get_attribute("href")
    assert href is not None and MATCH_URL.search(href), (
        f"the played card links to {href!r} instead of a match page"
    )

    page.click(f'a[href="{href}"]')
    page.wait_for_url(re.compile(re.escape(href) + r"$"))

    # The heading tells the reader the probabilities below are pre-kickoff.
    expect(page.get_by_role("heading", name="Pre-match view")).to_have_count(1)
    expect(page.get_by_role("heading", name="Result")).to_have_count(1)

    # The statistics table, or the honest statement that the source published
    # none: the result itself is stored either way.
    statistics = page.locator("table").filter(
        has=page.locator("caption", has_text="Match statistics")
    )
    if statistics.count() == 0:
        expect(page.locator("main")).to_contain_text("No statistics row for this match")
    else:
        expect(
            statistics.first.locator("th").filter(has_text=re.compile(r"^Shots$"))
        ).to_have_count(1)


MARKET_CAPTION = "Market implied probabilities"
MARKET_NOTE = re.compile(r"^Pre-closing odds, margin removed, from \S+\.$")
H2H_CAPTION = re.compile(r"^The \d+ meetings? of .+ before this fixture")
LAST_MEETINGS_CAPTION = "Last 10 meetings, newest first"
MAX_LAST_MEETINGS = 10
PROFILE_CAPTION = re.compile(r" per-match profile$")


def _played_match_link(page: Page, dashboard_url: str) -> str:
    """Return the match link of a played card on matchday 1, or skip."""
    page.goto(f"{dashboard_url}/matchday?matchday=1")
    played = page.locator("article").filter(has_text="Played")
    if played.count() == 0:
        pytest.skip("no card on matchday 1 is marked Played in the loaded dataset")
    href = played.first.get_by_role("link").first.get_attribute("href")
    assert href is not None and MATCH_URL.search(href), f"the played card links to {href!r}"
    return href


@pytest.mark.loaded
def test_a_played_match_shows_the_market_bar_with_its_label(page: Page, dashboard_url: str) -> None:
    """Guard the market reference: a labelled bar whose note names the price used."""
    href = _played_match_link(page, dashboard_url)
    page.goto(f"{dashboard_url}{href}")

    market = page.locator("figure").filter(has=page.locator("figcaption", has_text=MARKET_CAPTION))
    if market.count() == 0:
        pytest.skip("the played fixture carries no pre-closing price, so no market bar")
    expect(market).to_have_count(1)
    expect(market.locator('svg[role="img"]')).to_have_count(1)
    expect(market.locator(":scope > p")).to_have_text(MARKET_NOTE)


@pytest.mark.loaded
def test_the_head_to_head_and_profile_render(page: Page, dashboard_url: str) -> None:
    """Guard the head-to-head totals, the last meetings and the opponent profile."""
    href = _first_card_link(page, dashboard_url)
    page.goto(f"{dashboard_url}{href}")

    totals = page.locator("table").filter(has=page.locator("caption", has_text=H2H_CAPTION))
    expect(totals).to_have_count(1)
    expect(totals.locator("th[scope='col']").first).to_be_attached()

    last = page.locator("table").filter(has=page.locator("caption", has_text=LAST_MEETINGS_CAPTION))
    expect(last).to_have_count(1)
    rows = last.locator(":scope > tbody > tr")
    assert 1 <= rows.count() <= MAX_LAST_MEETINGS, (
        f"the last-meetings table lists {rows.count()} rows, expected 1 to {MAX_LAST_MEETINGS}"
    )

    profiles = page.locator("figure").filter(
        has=page.locator("figcaption", has_text=PROFILE_CAPTION)
    )
    expect(profiles).to_have_count(2)
    for index in range(2):
        expect(profiles.nth(index).locator('svg[role="img"]')).to_have_accessible_name(
            re.compile(r"\S")
        )
