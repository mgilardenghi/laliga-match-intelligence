"""The browser tests of the match page.

The page answers two questions about one fixture: what a reader could have known
before its kickoff — the models' probabilities, the market price, both form
strips, the head-to-head record and the two profiles — and, once the fixture has
been played, the result and how the stored forecast compared with it. A route
segment that names no fixture falls through to the not-found page rather than to
the error boundary.

The page renders one section per answer, each with a fixed `id`, and a section
nav that links to every one of them. All of that is asserted here so the page a
reader lands on from `/matchday` can be rebuilt without silently losing a
section, a link target, a heading or a reading guide. The empty tier holds on a
stack with no data; the rest needs the ingested fixtures and their stored
forecasts, so they are loaded-tier tests.

Every assertion is about structure, a role, a link target, a heading, a class
substring or a sentence the design fixes, and never about a probability, a score
or a count a particular dataset happens to produce. The only counts that depend
on the dataset are the last-meetings bound and `min(3, n)` recent meetings, which
the design itself fixes.
"""

import re

import pytest
from playwright.sync_api import Locator, Page, expect

MATCH_URL = re.compile(r"/matches/\d+$")
FIXTURE_HEADING = re.compile(r" vs ")
FORM_CAPTION = re.compile(r"^Form: ")
PROBABILITY_CAPTION = re.compile(r"probabilities$")
MARKET_NOTE = re.compile(r"^Pre-closing odds, margin removed, from \S+\.$")
H2H_CAPTION = re.compile(r"^The \d+ meetings? of .+ before this fixture")
LAST_MEETINGS_CAPTION = re.compile(r"^Last (\d+) meetings?, newest first$")
PROFILE_CAPTION = re.compile(r" per-match profile$")
# The header sentence that names the main forecast: the model (or the models as a
# group), what it sees, and the optional sentence that says the two models
# disagreed. A played fixture conjugates it in the past tense, a scheduled one in
# the present, so both forms are allowed here and the test rejects the wrong tense.
HEADER_SUMMARY = re.compile(
    r"^(The models|Dixon-Coles|Elo) "
    r"(sees? no clear favourite|saw no clear favourite|"
    r"leans? towards .+, but not by much|leaned towards .+, but not by much|"
    r"makes? .+ the favourite|made .+ the favourite|"
    r"rates? a draw as the most likely result|rated a draw as the most likely result)"
    r"\.( Dixon-Coles and Elo disagreed? on the most likely result\.)?$"
)
# The templates above allow either tense, and they allow the model names in
# every position, so they also allow a sentence whose subject and verb disagree.
# This pins the agreement: "The models" takes the plural verb, a named model the
# singular one.
WRONG_AGREEMENT = re.compile(
    r"\bThe models (?:sees|makes|rates|leans)\b"
    r"|\b(?:Dixon-Coles|Elo) (?:see|make|rate|lean)\b"
)
# The header sentence a played fixture adds: the result, and where the stored
# forecast ranked it.
RESULT_FORECAST = re.compile(
    r"^Result: (Home win|Draw|Away win)\. "
    r"The stored .+ forecast gave it \d+\.\d%, its (first|second|third) most likely outcome\.$"
)
NO_PRICE_NOTE = (
    "No pre-closing price is stored for this fixture: the current sources publish "
    "odds only with results."
)

PLAYED = "Played"
SCHEDULED = "Scheduled"
FIRST_MATCHDAY = 1
SEARCHED_MATCHDAYS = 3
MAX_LAST_MEETINGS = 10
RECENT_MEETINGS = 3
PHONE_WIDTH = 390
PHONE_HEIGHT = 844
SECTION_NAV = 'nav[aria-label="Sections of this page"]'
HELP_SECTIONS = ("models", "form", "head-to-head", "profile")
BOX_SCRIPT = "(element) => ({ scrollWidth: element.scrollWidth, clientWidth: element.clientWidth })"


def _normalise(text: str) -> str:
    """Collapse the whitespace a rendered paragraph puts around its words."""
    return re.sub(r"\s+", " ", text).strip()


def _texts(locator: Locator) -> list[str]:
    """Return the normalised text of every element the locator matches."""
    return [_normalise(locator.nth(index).inner_text()) for index in range(locator.count())]


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


def _fixture_link(page: Page, dashboard_url: str, status: str, matchday: int | None) -> str | None:
    """Return the match link of the first card of `status`, or None when none is."""
    suffix = "" if matchday is None else f"?matchday={matchday}"
    page.goto(f"{dashboard_url}/matchday{suffix}")
    card = page.locator("article").filter(has_text=status).first
    if card.count() == 0:
        return None
    href = card.get_by_role("link").first.get_attribute("href")
    assert href is not None and MATCH_URL.search(href), (
        f"the {status.lower()} card links to {href!r} instead of a match page"
    )
    return href


def _open_state_fixture(page: Page, dashboard_url: str, state: str) -> str:
    """Open a fixture in the requested state, or skip when the dataset has none.

    A played fixture is taken from matchday 1, the first round the loaded
    dataset is required to have played. A scheduled fixture is the first card the
    default matchday shows, because that is the page a reader lands on.
    """
    if state == "played":
        href = _fixture_link(page, dashboard_url, PLAYED, FIRST_MATCHDAY)
        reason = (
            "no card on matchday 1 is marked Played: the loaded dataset must "
            "already have played a matchday of the current season"
        )
    else:
        href = _fixture_link(page, dashboard_url, SCHEDULED, None)
        reason = (
            "no card on the current matchday is marked Scheduled: the loaded "
            "dataset must hold a fixture still to come"
        )
    if href is None:
        pytest.skip(reason)
    page.goto(f"{dashboard_url}{href}")
    return href


def _played_fixture_with_forecast(page: Page, dashboard_url: str) -> str:
    """Open a played fixture that carries a stored Dixon-Coles or Elo forecast.

    The header's forecast sentences only exist when the main model was stored, and
    the loaded dataset forecasts fixtures still to come: a played round has no
    stored row. The test therefore walks the played cards of the first rounds and
    uses the first fixture that has one, and skips with the reason when the
    dataset holds none, instead of asserting on a page that honestly says so.
    """
    candidates: list[str] = []
    for matchday in range(FIRST_MATCHDAY, FIRST_MATCHDAY + SEARCHED_MATCHDAYS):
        page.goto(f"{dashboard_url}/matchday?matchday={matchday}")
        cards = page.locator("article").filter(has_text=PLAYED)
        for index in range(cards.count()):
            href = cards.nth(index).get_by_role("link").first.get_attribute("href")
            if href is not None and MATCH_URL.search(href) and href not in candidates:
                candidates.append(href)
    for href in candidates:
        page.goto(f"{dashboard_url}{href}")
        if page.locator("header p strong").count() >= 1:
            return href
    pytest.skip(
        "no played fixture in the loaded dataset carries a stored Dixon-Coles or "
        "Elo forecast: the loader predicts the fixtures still to come, so the "
        "played header sentences cannot be exercised on this stack"
    )


def _probability_figures(page: Page) -> Locator:
    """Return every model probability figure: a caption plus a labelled plot."""
    return page.locator("figure").filter(
        has=page.locator("figcaption", has_text=PROBABILITY_CAPTION)
    )


def _assert_box_fits(element: Locator, label: str) -> None:
    """Fail when `element` is wider than its own box, so a phone has to scroll."""
    box = element.evaluate(BOX_SCRIPT)
    assert box["scrollWidth"] <= box["clientWidth"] + 1, (
        f"{label} is {box['scrollWidth']}px wide in a {box['clientWidth']}px box, "
        "so a phone hides part of it behind a sideways scroll"
    )


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
    # A guard: the old page already satisfies every assertion here, and the new
    # one must keep doing so while it is rebuilt.
    href = _first_card_link(page, dashboard_url)

    page.click(f'a[href="{href}"]')
    page.wait_for_url(re.compile(re.escape(href) + r"$"))

    heading = page.get_by_role("heading", level=1)
    expect(heading).to_have_count(1)
    assert FIXTURE_HEADING.search(heading.inner_text()), (
        f"the match heading {heading.inner_text()!r} does not name both clubs"
    )

    # "Pre-match view" also matches the rebuilt heading
    # "Pre-match view: what each model says".
    expect(page.get_by_role("heading", name="Pre-match view")).to_have_count(1)
    expect(page.get_by_role("heading", name="Head-to-head")).to_have_count(1)

    # Both form strips: one per club, each captioned with the letters it shows.
    forms = page.locator("figure").filter(has=page.locator("figcaption", has_text=FORM_CAPTION))
    expect(forms).to_have_count(2)

    # At least one model probability bar, with an accessible name. A bar is a
    # chart, so it is a figure with a caption and a labelled plot.
    bars = _probability_figures(page)
    assert bars.count() >= 1, "the pre-match view renders no probability figure"
    expect(bars.first.locator('[role="img"]')).to_have_accessible_name(re.compile(r"\S+"))


@pytest.mark.loaded
def test_a_played_match_shows_the_pre_match_view_and_the_result(
    page: Page, dashboard_url: str
) -> None:
    # A guard: the old page already shows both headings and the statistics table
    # (or the statement that there is none), so it stays while the page is rebuilt.
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


@pytest.mark.loaded
@pytest.mark.parametrize("state", ["played", "scheduled"])
def test_the_header_summarises_the_main_forecast(
    page: Page, dashboard_url: str, state: str
) -> None:
    """The header states one model's forecast, in the tense the fixture's state uses."""
    if state == "played":
        _played_fixture_with_forecast(page, dashboard_url)
    else:
        _open_state_fixture(page, dashboard_url, state)

    lead = page.locator("header p strong").first
    assert lead.count() == 1, "the header states no forecast in a strong element"
    text = _normalise(lead.inner_text())
    match = HEADER_SUMMARY.fullmatch(text)
    assert match is not None, f"the header summarises the forecast as {text!r}"

    if state == "played":
        # The plural forms of the present tense are listed too, because "The
        # models" takes "see", "lean", "make" and "rate" without the s.
        stale = (
            "lean towards",
            "leans towards",
            "make ",
            "makes ",
            "see no",
            "sees no",
            "rate a draw",
            "rates a draw",
        )
    else:
        stale = ("leaned towards", "made ", "saw no", "rated a draw")
    wrong_tense = [phrase for phrase in stale if phrase in text]
    assert wrong_tense == [], (
        f"the {state} fixture's summary uses the wrong tense: {wrong_tense} in {text!r}"
    )

    disagreement = WRONG_AGREEMENT.search(text)
    assert disagreement is None, (
        f"the {state} fixture's summary does not agree with its subject: "
        f"{disagreement.group(0)!r} in {text!r}"
    )

    # The summary is about the main forecast, so the page draws one bar for it
    # whichever wording names the model.
    expect(_probability_figures(page)).to_have_count(1)


@pytest.mark.loaded
def test_a_played_match_compares_the_stored_forecast_with_the_result(
    page: Page, dashboard_url: str
) -> None:
    """The header says where the stored forecast ranked the result it got."""
    _played_fixture_with_forecast(page, dashboard_url)

    matches = [text for text in _texts(page.locator("header p")) if RESULT_FORECAST.fullmatch(text)]
    assert len(matches) == 1, (
        f"the header states the stored forecast's rank {len(matches)} times: {matches!r}"
    )


@pytest.mark.loaded
def test_a_scheduled_match_shows_the_preview_without_a_price(
    page: Page, dashboard_url: str
) -> None:
    """A fixture still to come has no result and says why it has no market row."""
    _open_state_fixture(page, dashboard_url, "scheduled")

    expect(page.get_by_role("heading", name="Result", exact=True)).to_have_count(0)
    nav = page.locator(SECTION_NAV)
    expect(nav).to_have_count(1)
    expect(nav.locator('a[href="#result"]')).to_have_count(0)

    models = page.locator("#models")
    expect(models).to_have_count(1)
    expect(models).to_contain_text(NO_PRICE_NOTE)

    labels = _texts(models.locator("th"))
    assert not any(label.startswith("Market price") for label in labels), (
        f"a scheduled fixture draws a market row: {labels!r}"
    )
    notes = [text for text in _texts(models.locator("p")) if MARKET_NOTE.fullmatch(text)]
    assert notes == [], f"a scheduled fixture draws a market note: {notes!r}"


@pytest.mark.loaded
def test_a_played_match_shows_the_market_row_with_its_label(page: Page, dashboard_url: str) -> None:
    """The market reference is a labelled row whose note names the price used."""
    href = _fixture_link(page, dashboard_url, PLAYED, FIRST_MATCHDAY)
    if href is None:
        pytest.skip(
            "no card on matchday 1 is marked Played: the loaded dataset must "
            "already have played a matchday of the current season"
        )
    page.goto(f"{dashboard_url}{href}")

    models = page.locator("#models")
    expect(models).to_have_count(1)

    labels = _texts(models.locator("th"))
    market_rows = [label for label in labels if label.startswith("Market price")]
    # The loaded data carries a pre-closing price for every played fixture, so a
    # missing row is a regression, not a data gap.
    assert len(market_rows) == 1, (
        f"#models renders {len(market_rows)} rows starting with 'Market price': {labels!r}"
    )

    notes = [text for text in _texts(models.locator("p")) if MARKET_NOTE.fullmatch(text)]
    assert len(notes) == 1, f"#models renders {len(notes)} market notes instead of one: {notes!r}"


@pytest.mark.loaded
def test_the_head_to_head_and_profile_render(page: Page, dashboard_url: str) -> None:
    """The head-to-head totals, the last meetings and the combined profile."""
    href = _first_card_link(page, dashboard_url)
    page.goto(f"{dashboard_url}{href}")

    record = page.locator("#head-to-head")
    expect(record).to_have_count(1)
    if record.get_by_text("No previous meetings").count() == 1:
        # Two clubs that have never met is a state the API can return, and the
        # page states it instead of drawing an empty record. The profile below
        # still renders, so the assertion after this branch still applies.
        expect(
            record.locator("table").filter(has=page.locator("caption", has_text=H2H_CAPTION))
        ).to_have_count(0)
        expect(
            record.locator("table").filter(
                has=page.locator("caption", has_text=LAST_MEETINGS_CAPTION)
            )
        ).to_have_count(0)
        # No meeting list either: a stray row or a stray link would mean the page
        # drew a record it does not have.
        expect(record.locator('a[href*="/matches/"]')).to_have_count(0)
    else:
        totals = page.locator("table").filter(has=page.locator("caption", has_text=H2H_CAPTION))
        expect(totals).to_have_count(1)

        last = page.locator("table").filter(
            has=page.locator("caption", has_text=LAST_MEETINGS_CAPTION)
        )
        expect(last).to_have_count(1)
        caption = _normalise(last.locator("caption").inner_text())
        caption_match = LAST_MEETINGS_CAPTION.fullmatch(caption)
        assert caption_match is not None, f"the last-meetings caption reads {caption!r}"
        listed = int(caption_match.group(1))
        rows = last.locator(":scope > tbody > tr")
        assert rows.count() == listed, (
            f"the last-meetings caption promises {listed} meetings but the table lists "
            f"{rows.count()} rows"
        )
        # The design bounds the list: at least one meeting since the clubs met, and
        # never more than the ten newest.
        assert 1 <= listed <= MAX_LAST_MEETINGS, (
            f"the last-meetings table lists {listed} rows, expected 1 to {MAX_LAST_MEETINGS}"
        )

        recent = record.locator("ul")
        expect(recent).to_have_count(1)
        # The design fixes the short list to three meetings, or to all of them
        # when the clubs met fewer times.
        expect(recent.locator("li")).to_have_count(min(RECENT_MEETINGS, listed))

    profiles = page.locator("figure").filter(
        has=page.locator("figcaption", has_text=PROFILE_CAPTION)
    )
    expect(profiles).to_have_count(1)
    expect(profiles.locator('[role="img"]')).to_have_accessible_name(re.compile(r"\S"))


@pytest.mark.loaded
@pytest.mark.parametrize("state", ["played", "scheduled"])
def test_section_nav_anchors_resolve(page: Page, dashboard_url: str, state: str) -> None:
    """Every section link on the page lands on exactly one element."""
    _open_state_fixture(page, dashboard_url, state)

    nav = page.locator(SECTION_NAV)
    expect(nav).to_have_count(1)
    anchors = nav.locator('a[href^="#"]')
    assert anchors.count() >= 1, "the section nav links to no section"

    for index in range(anchors.count()):
        fragment = (anchors.nth(index).get_attribute("href") or "")[1:]
        assert fragment, f"section nav link {index} names no fragment"
        expect(page.locator(f"[id='{fragment}']")).to_have_count(1)


@pytest.mark.loaded
def test_every_metric_heading_has_operable_help(page: Page, dashboard_url: str) -> None:
    """A keyboard opens and closes the reading guide of every metric heading."""
    href = _first_card_link(page, dashboard_url)
    page.goto(f"{dashboard_url}{href}")

    for section_id in HELP_SECTIONS:
        section = page.locator(f"#{section_id}")
        expect(section).to_have_count(1)
        summaries = section.locator('summary[aria-label^="What "]')
        assert summaries.count() >= 1, (
            f"#{section_id} renders no summary whose label explains a metric"
        )
        for index in range(summaries.count()):
            summary = summaries.nth(index)
            details = summary.locator("xpath=ancestor::details[1]")
            assert details.count() == 1, (
                f"the help summary {index} of #{section_id} is not inside a details"
            )
            summary.focus()
            page.keyboard.press("Enter")
            expect(details).to_have_attribute("open", "")
            page.keyboard.press("Enter")
            expect(details).not_to_have_attribute("open", "")


@pytest.mark.loaded
@pytest.mark.parametrize("state", ["played", "scheduled"])
def test_match_page_fits_a_phone(page: Page, dashboard_url: str, state: str) -> None:
    """Nothing on the match page scrolls sideways inside a phone viewport."""
    page.set_viewport_size({"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
    _open_state_fixture(page, dashboard_url, state)

    # A closed definition hides its content from the measurement, so every one
    # whose control is a reading guide is opened first. The data table is left
    # as it is: it is the alternative to a chart and not part of it.
    page.locator("details").evaluate_all(
        """(elements) => {
            for (const element of elements) {
                const summary = element.querySelector("summary");
                if (summary !== null && summary.textContent.trim() === "Data table") {
                    continue;
                }
                element.open = true;
            }
        }"""
    )

    scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
    assert scroll_width <= PHONE_WIDTH, (
        f"the match page is {scroll_width}px wide in a {PHONE_WIDTH}px viewport, "
        "so a phone has to scroll sideways"
    )

    for selector, label in (
        ('main [class*="tableWrap"]', "table wrapper"),
        ('main [class*="chartScroller"]', "chart scroller"),
    ):
        elements = page.locator(selector)
        for index in range(elements.count()):
            _assert_box_fits(elements.nth(index), f"{label} {index} of {selector}")

    plots = page.locator('main [role="img"]')
    for index in range(plots.count()):
        _assert_box_fits(
            plots.nth(index).locator("xpath=.."),
            f'parent of main [role="img"] element {index}',
        )
