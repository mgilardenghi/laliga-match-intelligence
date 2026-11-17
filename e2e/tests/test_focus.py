"""The browser tests of the team focus page.

`/focus/[teamId]` is one club's season: the header, a story card that opens with
one sentence and the four numbers behind it, the nav that reaches the sections,
and four sections that each stream in with their own data. The empty tier holds
on a stack with no data and covers what the route answers for a club it does not
know. The loaded tier needs the ingested data and the stored simulation, and
covers the default club, the story and the four sections below it, the nav links
that reach them, the help control of every metric, the trend sparklines, the
relegation race with its threshold and its two tables, the swing of the next
match, the collapsed race of a club the stored runs never put in danger, the
season the club never played, and the page at the width of a phone.

Every assertion is about structure — a heading, a caption, a link target, a
role, a sentence the page fixes — and never about a value a particular dataset
happens to produce, except where the design names the default focus club
itself. The redirect `/focus` makes and the focus cookie are covered by
`test_shell.py`; this module starts from the path it resolves.
"""

import re

import pytest
from playwright.sync_api import Locator, Page, Response, expect

FOCUS_PATH = "/focus"
UNKNOWN_CLUB_PATH = "/focus/999999999"
UNKNOWN_SEASON_QUERY = "?season=1999-00"
# The club the default focus resolves to, as the design configures it.
DEFAULT_CLUB = "Valencia"

# The four sections below the story card, in the order the page renders them.
# The story card is the fifth `main section[aria-labelledby]`, and its heading is
# the sentence about the season, so its name is not one of these.
SECTION_NAMES = (
    "Season diagnosis",
    "Relegation race",
    "Next match",
    "Multi-season trend",
)
STORY_HEADING = "#story-heading"
STORY_SECTION = "section[aria-labelledby='story-heading']"
DIAGNOSIS_SECTION = "section[aria-labelledby='diagnosis-heading']"
TREND_SECTION = "section[aria-labelledby='trend-heading']"
RACE_SECTION = "section[aria-labelledby='race-heading']"
NEXT_MATCH_SECTION = "section[aria-labelledby='next-match-heading']"

# The sentence the story card is titled with: one of the design's bands and then
# the facts it was read with. A heading that opens with anything else is a band
# the design never names, or a season described with another vocabulary.
STORY_SENTENCE = re.compile(
    r"^(In a relegation fight|In the title race|Chasing a top-\d+ place|"
    r"Chasing a Champions League place|Chasing a European place|"
    r"Between the European places and the drop|"
    r"No simulation stored for \d{4}-\d{2} yet):"
)
# The label of a story tile, found by the local part of its CSS module class.
# The shared stylesheet uppercases the label it paints, so a test that compares
# the label to the design's wording reads the node's source text instead.
STORY_TILE_LABEL = '[class*="statTileLabel"]'
STORY_TILE_NAMES = ("Position", "Points vs market", "Expected final points")
# The fourth tile is the chance of the band the season falls in, and its label
# names that band ("Relegation chance", "Top-4 chance", "Europe chance"), so only
# the word it ends with is fixed here.
CHANCE_TILE_NAME = re.compile("chance$")

# The nav above the sections: the four anchors the page fixes, and the label the
# shared `SectionNav` gives the bar.
SECTION_NAV_LABEL = "Sections of this page"
SECTION_NAV_LINKS = 4

# The help controls of the page that are not story tiles, by the metric each one
# explains. The swing card's heading is "What this match changes" and its help is
# labelled "this match changes", so `MetricHelp`'s template names it once.
SECTION_HELP_LABELS = (
    "Against whom",
    "Points against expectation",
    "Projected finish",
    "Next fixtures",
    "this match changes",
)

# The guide the page closes with, and the four numbers it has to explain: the
# market's expected points, the relegation chance, the survival line the race
# draws, and the swing of the next match. A definition is a sentence, not a
# restated term, so the shortest one still runs to a few words.
READING_GUIDE_HEADING = "How to read this page"
READING_GUIDE_TERMS = (
    "Market expected points",
    "Relegation chance",
    "Survival line",
    "Swing",
)
MINIMUM_DEFINITION_WORDS = 8

DEFAULT_FOCUS_PATH = re.compile(r"^/focus/\d+$")
TEAM_HREF = re.compile(r"^/teams/(\d+)$")
STANDINGS_TABLE = re.compile("^La Liga table for")
RIVALS_CAPTION = re.compile("^Rivals and their relegation probability")
FIXTURES_CAPTION = re.compile("^Remaining fixtures")
PROJECTED_CAPTION = "Projected final points"
# The sentence a trend figure publishes about the steepest decline of its
# metric: the two seasons it spans, and then the signed delta that tells a
# reader how far the metric fell.
LARGEST_DROP = re.compile(r"fell most from \d{4}-\d{2} to \d{4}-\d{2}")
POSITION_LABEL = "League position"
POINTS_LABEL = "Points per match"
# The hint span of a sparkline: its class comes from the CSS module, and the
# local part the module keeps is what names it here, so the label is found the
# way `Sparkline.tsx` renders it instead of by an arrow character in SVG text.
HIGHLIGHT_LABEL_SELECTOR = '[class*="sparklineHintLabel"]'
# What a figure publishes about the segment its drawing highlights: the hidden
# description the chart wires into `aria-labelledby`, whose id ends in `-desc`,
# and the note paragraph under the plot. The caption is not this text, because
# it names the metric and never the highlight, and the label above is not
# either, because the label is what the test checks and cannot also be its
# evidence. The note is found by its tag, so no class name has to hold.
HIGHLIGHT_TEXT_SELECTOR = "[id$='-desc'], p"

NOT_FOUND_HEADING = "Page not found"
NO_SEASON_HEADING = "No focus view for this season"
COLLAPSED_NOTE = "Not in the relegation race"
THRESHOLD_LABEL = "Survival threshold"
# The note the swing panel prints instead of the three outcomes: the panel is
# worth reading only when the stored run carries a swing for the listed fixture.
PENDING_SWING = "No swing is stored for this match yet"
MISSING_PROBABILITIES = "model probabilities not available yet"

CUMULATIVE_CAPTION = "Cumulative points against market expectation"
ROLLING_CAPTION = "Rolling five-match averages"
ROLLING_SUMMARY = "Rolling five-match form"
ABOVE_THE_LINE = "Above the line = results better than the market expected."
COVERAGE = re.compile(r"for \d+ of \d+ played matches")
SPLIT_CAPTIONS = (
    re.compile(r"^Home and away record"),
    re.compile(r"^Record against top, middle and bottom opponents"),
    re.compile(r"^Discipline per match"),
)
# The disclosure that keeps the other six trend metrics out of the first read.
ALL_METRICS_SUMMARY = "All metrics"

MODEL_BAR_CAPTION = re.compile(r": home win, draw and away win$")
MATCH_HREF = re.compile(r"^/matches/\d+$")

# The swing table: the baseline row, the three outcomes, and the columns the
# design fixes. The featured probability column names the metric of the season's
# band, so it is read by its position between the chance and the change instead.
SWING_CAPTION = re.compile("^Swing of the next match")
SWING_ROWS = ("Now", "Win", "Draw", "Loss")
SWING_OUTCOMES = ("Win", "Draw", "Loss")
CHANCE_COLUMN = "Chance of result"
CHANGE_COLUMN = "Change"
# A cell of the fixtures table: one decimal place and a percent sign, so a value
# rounded to a whole number, or a dash, is not a priced fixture.
FIXTURE_PERCENT = re.compile(r"^\d+\.\d%$")
PERCENT = re.compile(r"^(-?\d+(?:\.\d+)?)%$")
# The identity a reader can check on the swing table: the chance of each result
# times the probability it leads to is the probability the season already has.
# The displayed values carry one decimal place, so the sum is allowed the point
# that rounding the cells can move.
SWING_TOLERANCE_PP = 1.0

# A phone in portrait: the width every drawing and table has to live inside.
PHONE_WIDTH = 390
PHONE_HEIGHT = 844
# The markup that must fit the column that holds it at that width. A table
# scrolls inside its own wrapper by design; a drawing inside its own figure. A
# 1 px slack covers the sub-pixel rounding of the layout, not a clipped cell.
PHONE_FIT_SELECTORS = ("main table", "main figure [role='img']")
PHONE_FIT_SLACK_PX = 1

INSIDE_FIGURE_DETAILS = "element => element.closest('figure details') !== null"
ELEMENT_STATE = """
element => ({
  scroll: element.parentElement.scrollWidth,
  client: element.parentElement.clientWidth,
  text: (element.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
})
"""
DISCLOSURE_IS_OPEN = "summary => summary.closest('details')?.hasAttribute('open') === true"
CLOSE_OPEN_DISCLOSURES = """
() => {
  for (const details of document.querySelectorAll("details[open]")) {
    details.removeAttribute("open");
  }
}
"""


def _open(page: Page, dashboard_url: str, path: str) -> None:
    """Open one dashboard URL and fail with the status when it is not a 200."""
    url = f"{dashboard_url}{path}"
    response: Response | None = page.goto(url)
    assert response is not None, f"{url} returned no navigation response"
    assert response.status == 200, f"{url} answered HTTP {response.status}"


def _open_rendered(page: Page, dashboard_url: str, path: str) -> None:
    """Open one URL and wait until the page has finished streaming.

    The load event is not enough for this page: the story card and the four
    sections stream in after the first byte, so a page that has loaded can still
    hold skeletons where a table belongs. The network settling is the point at
    which every boundary has replaced its fallback.
    """
    url = f"{dashboard_url}{path}"
    response: Response | None = page.goto(url, wait_until="networkidle")
    assert response is not None, f"{url} returned no navigation response"
    assert response.status == 200, f"{url} answered HTTP {response.status}"
    expect(page.locator(STORY_HEADING)).to_be_visible()
    for name in SECTION_NAMES:
        expect(page.get_by_role("heading", level=2, name=name, exact=True)).to_be_visible()


def _default_focus_path(page: Page, dashboard_url: str) -> str:
    """Resolve the focus path `/focus` sends a reader with no cookie to.

    `page.request` talks to the dashboard only, which is what the suite
    requires: the redirect is the dashboard's own answer, and the browser never
    asks the API for the default club.
    """
    response = page.request.get(f"{dashboard_url}{FOCUS_PATH}", max_redirects=0)
    assert 300 <= response.status < 400, (
        f"/focus answered HTTP {response.status} instead of redirecting"
    )
    location = response.headers.get("location", "")
    assert DEFAULT_FOCUS_PATH.match(location) is not None, (
        f"/focus redirected to {location!r}, which names no team"
    )
    return location


def _figure(root: Locator, caption: str) -> Locator:
    """Return the figure under `root` whose `<figcaption>` contains `caption`.

    The filter reads the figure's own text instead of nesting a second locator:
    an inner locator rooted at `root` would be queried against each candidate
    figure and never match.
    """
    return root.locator("figure").filter(has_text=caption)


def _highlight_labels(figure: Locator) -> Locator:
    """Return the season-range label of a figure whose drop is highlighted.

    A figure without a published drop renders no such label, so the locator is
    empty rather than hidden.
    """
    return figure.locator(HIGHLIGHT_LABEL_SELECTOR)


def _highlight_text(figure: Locator) -> str:
    """Return what a figure publishes about the segment its drawing highlights.

    Both sources are read: the description is the chart's own account of its
    marks and the note repeats the published drop in words. `text_content` is
    used because the description is hidden from sight rather than empty.
    """
    return " ".join(figure.locator(HIGHLIGHT_TEXT_SELECTOR).all_text_contents())


def _percent(text: str) -> float:
    """Read a rendered percentage as the number it prints, or fail naming it.

    The swing table's cells are the evidence for the identity the swing test
    checks, so a cell that is not a percentage is a failure and not a skip.
    """
    match = PERCENT.fullmatch(text.strip())
    assert match is not None, f"the swing table renders {text!r} where a percentage belongs"
    return float(match.group(1))


def _swing_cells(table: Locator) -> tuple[list[str], dict[str, list[str]]]:
    """Return the swing table's data columns and its data cells per row.

    The row header's column is dropped from the header and the row header itself
    from every row, so one column index names the same column of the header and
    of each row: the outcome a reader reads the row by is never a data column.
    """
    header = [text.strip() for text in table.locator("thead th").all_text_contents()][1:]
    rows: dict[str, list[str]] = {}
    body = table.locator("tbody tr")
    for index in range(body.count()):
        cells = [text.strip() for text in body.nth(index).locator("th, td").all_text_contents()]
        rows[cells[0]] = cells[1:]
    return header, rows


def _disclosure_is_open(control: Locator) -> bool:
    """Whether the disclosure a help control belongs to is open."""
    return control.evaluate(DISCLOSURE_IS_OPEN)


def _assert_help_is_operable(page: Page, label: str) -> None:
    """Prove the help control of one metric opens and closes without a pointer.

    Enter, Space and a click are the three ways the page promises a native
    disclosure works, so each is used on the focused control and the state of
    its `<details>` is read back. Every disclosure is closed first, so a panel
    the control before this one left open cannot make this one look open.
    """
    page.evaluate(CLOSE_OPEN_DISCLOSURES)
    summary = page.locator(f'summary[aria-label="What {label} means"]')
    rendered = page.locator("summary[aria-label]").evaluate_all(
        "summaries => summaries.map(summary => summary.getAttribute('aria-label'))"
    )
    assert summary.count() == 1, (
        f"the page offers no help control named {label!r}; it renders {rendered}"
    )

    summary.focus()
    expect(summary).to_be_focused()
    page.keyboard.press("Enter")
    assert _disclosure_is_open(summary), f"Enter does not open the definition of {label!r}"
    page.keyboard.press("Space")
    assert not _disclosure_is_open(summary), f"Space does not close the definition of {label!r}"
    summary.click()
    assert _disclosure_is_open(summary), f"a click does not open the definition of {label!r}"


def _measured_overflow(page: Page, selector: str) -> tuple[int, list[str]]:
    """Measure every rendered element `selector` matches and report the ones cut.

    An element is measured only when it is rendered: a table or a drawing inside
    a closed `<details>` has no box, and the design hides it there. The container
    that holds the element is what is measured, because a table wider than its
    wrapper is data a phone reader cannot reach.
    """
    measured = 0
    offenders: list[str] = []
    elements = page.locator(selector)
    for index in range(elements.count()):
        element = elements.nth(index)
        # A closed `<details>` renders none of its content, so a table or a
        # drawing the reader has collapsed is not visible: the page hides it on
        # purpose, and asking it to fit would be asking for something nobody
        # sees.
        if not element.is_visible():
            continue
        if element.evaluate(INSIDE_FIGURE_DETAILS):
            continue
        measured += 1
        state = element.evaluate(ELEMENT_STATE)
        if state["scroll"] > state["client"] + PHONE_FIT_SLACK_PX:
            offenders.append(
                f"{state['text']!r} needs {state['scroll']} px in {state['client']} px"
            )
    return measured, offenders


@pytest.mark.empty
def test_an_unknown_club_renders_the_not_found_page(page: Page, dashboard_url: str) -> None:
    """Guard the unknown club: the route 404s and the shell stays readable."""
    url = f"{dashboard_url}{UNKNOWN_CLUB_PATH}"
    response: Response | None = page.goto(url)

    assert response is not None, f"{url} returned no navigation response"
    assert response.status == 404, f"{url} answered HTTP {response.status}"

    heading = page.get_by_role("heading", level=1)
    expect(heading).to_have_count(1)
    expect(heading).to_have_text(NOT_FOUND_HEADING)


@pytest.mark.loaded
def test_the_focus_path_lands_on_the_default_club(page: Page, dashboard_url: str) -> None:
    """Guard the default entry: `/focus` shows the configured club, not a blank."""
    _open(page, dashboard_url, FOCUS_PATH)

    heading = page.get_by_role("heading", level=1)
    expect(heading).to_have_count(1)
    expect(heading).to_contain_text(DEFAULT_CLUB)


@pytest.mark.loaded
def test_the_page_renders_the_story_and_four_sections(page: Page, dashboard_url: str) -> None:
    """Guard the outline: the story card, then four labelled sections with a heading."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    # The story streams like the sections, so the count waits for it: a page that
    # has not rendered its card would otherwise be counted while the skeleton of
    # the card is the only thing in its place.
    story = page.locator(STORY_HEADING)
    expect(story).to_be_visible()

    # The outline is counted, not read in source order: the story card plus the
    # four sections. A section that went missing and one that was duplicated both
    # change this number.
    expect(page.locator("main section[aria-labelledby]")).to_have_count(1 + len(SECTION_NAMES))

    for name in SECTION_NAMES:
        heading = page.get_by_role("heading", level=2, name=name, exact=True)
        expect(heading).to_have_count(1)
        expect(heading).to_be_visible()

    # The card is titled with the sentence about the season, and that sentence
    # opens with the band the stored run puts the club in: a sentence that opens
    # anywhere else is a claim the design never makes.
    sentence = story.inner_text().strip()
    assert STORY_SENTENCE.match(sentence) is not None, (
        f"the story heading reads {sentence!r}, which opens with no band the design names"
    )


@pytest.mark.loaded
def test_the_story_shows_four_tiles_and_both_selectors(page: Page, dashboard_url: str) -> None:
    """Guard the story card: the four numbers behind the sentence and both selectors."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    tiles = page.locator(STORY_SECTION).locator(STORY_TILE_LABEL)
    expect(tiles).to_have_count(len(STORY_TILE_NAMES) + 1)
    labels = [text.strip() for text in tiles.all_text_contents()]
    assert tuple(labels[: len(STORY_TILE_NAMES)]) == STORY_TILE_NAMES, (
        f"the story card tiles {labels} instead of the design's four, in its order"
    )
    assert CHANCE_TILE_NAME.search(labels[-1]) is not None, (
        f"the fourth tile is labelled {labels[-1]!r}, which names no chance of the band"
    )

    # Each selector wraps its `<select>` in its `<label>`, so the label text holds
    # every option; the two selects are found by what they offer instead.
    selects = page.locator("main select")
    expect(selects).to_have_count(2)
    season_select = selects.filter(
        has=page.locator("option", has_text=re.compile(r"^\d{4}-\d{2}$"))
    )
    team_select = selects.filter(has=page.locator("option", has_text=DEFAULT_CLUB))
    expect(season_select).to_have_count(1)
    expect(team_select).to_have_count(1)


@pytest.mark.loaded
def test_the_page_closes_with_the_reading_guide(page: Page, dashboard_url: str) -> None:
    """Guard the closing guide: the four numbers a first-time reader cannot read."""
    path = _default_focus_path(page, dashboard_url)
    _open_rendered(page, dashboard_url, path)

    heading = page.get_by_role("heading", name=READING_GUIDE_HEADING, exact=True)
    expect(heading).to_have_count(1)

    # The guide is the section the heading titles: the terms are read from that
    # section and not from the page, so a term the sections happen to use is not
    # mistaken for an explained one.
    guide = page.locator("section").filter(has=heading)
    expect(guide).to_have_count(1)
    terms = [text.strip() for text in guide.locator("dt").all_text_contents()]
    assert tuple(terms) == READING_GUIDE_TERMS, (
        f"the reading guide explains {terms} instead of the four numbers the page "
        f"asks a reader to trust: {READING_GUIDE_TERMS}"
    )

    # A term without a definition is a heading, not a guide: each pair has to say
    # what the number means, which is the reason the block is on the page.
    definitions = [text.strip() for text in guide.locator("dd").all_text_contents()]
    assert len(definitions) == len(READING_GUIDE_TERMS), (
        f"the reading guide has {len(definitions)} definitions for {len(READING_GUIDE_TERMS)} terms"
    )
    for term, definition in zip(READING_GUIDE_TERMS, definitions, strict=True):
        assert len(definition.split()) >= MINIMUM_DEFINITION_WORDS, (
            f"the reading guide defines {term!r} with {definition!r}, which is too "
            "short to explain the number"
        )


@pytest.mark.loaded
def test_the_trend_draws_one_sparkline_per_metric(page: Page, dashboard_url: str) -> None:
    """Guard the trend grid: a named sparkline per metric and the published drops."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    trend = page.locator(TREND_SECTION)
    expect(trend).to_have_count(1)

    # The headline grid is the four metrics a reader sees first, and the other six
    # sit behind one disclosure. A reader who wants them opens it, so the test
    # opens it too: the figures it holds are drawn only then.
    others = trend.locator("details").filter(has_text=ALL_METRICS_SUMMARY)
    expect(others).to_have_count(1)
    metrics_summary = others.locator("summary").first
    metrics_summary.click()
    assert _disclosure_is_open(metrics_summary), (
        "clicking the All metrics summary does not open the other six metrics"
    )

    figures = trend.locator("figure")
    expect(figures.first).to_be_visible()

    # Every figure is one metric: one caption to find it by and one labelled
    # sparkline to read, so a metric that lost its chart cannot hide in a grid
    # of working ones.
    for index in range(figures.count()):
        figure = figures.nth(index)
        expect(figure.locator("figcaption")).to_have_count(1)
        sparkline = figure.locator("[role='img']")
        expect(sparkline).to_have_count(1)
        expect(sparkline).to_have_accessible_name(re.compile(r"\S"))

    # The API publishes a steepest drop for most metrics, and the figure says
    # which two seasons it spans: a grid that drew the segment without the
    # sentence would leave the highlight unexplained.
    assert LARGEST_DROP.search(trend.inner_text()) is not None, (
        "no trend figure names the steepest drop it highlights"
    )

    # The league position has no published drop, so nothing on it may be
    # highlighted: a visible hint label there would be computed in the browser.
    position = _figure(trend, POSITION_LABEL)
    expect(position).to_have_count(1)
    expect(_highlight_labels(position)).to_have_count(0)

    # The other direction: every figure that publishes a steepest drop has to
    # render the label that explains its highlighted segment, so a chart that
    # lost the label fails as loudly as a no-drop chart that gained one. The
    # count is asserted below, so a wording the figures stopped publishing
    # cannot leave this direction empty and green.
    labelled = 0
    for index in range(figures.count()):
        figure = figures.nth(index)
        if LARGEST_DROP.search(_highlight_text(figure)) is None:
            continue
        labelled += 1
        expect(_highlight_labels(figure).first).to_be_visible()
    assert labelled > 0, "no trend figure publishes a steepest drop, so no label went unchecked"


@pytest.mark.loaded
def test_a_season_the_club_did_not_play_is_a_gap_in_the_trend(
    page: Page, dashboard_url: str
) -> None:
    """Guard the gaps: a missed season breaks the line instead of joining across it."""
    _open(page, dashboard_url, "/teams")
    hrefs = page.locator("main a[href^='/teams/']").evaluate_all(
        "links => links.map(link => link.getAttribute('href'))"
    )
    team_ids = sorted({match.group(1) for href in hrefs if (match := TEAM_HREF.match(href or ""))})
    assert team_ids, "the teams index lists no club to follow"

    for team_id in team_ids:
        _open(page, dashboard_url, f"/focus/{team_id}")
        figure = _figure(page.locator(TREND_SECTION), POINTS_LABEL)
        if figure.count() == 0:
            continue
        cells = figure.locator("details table tbody td").all_inner_texts()
        if "—" not in cells:
            continue
        # The figure's data table names the gap and the drawing splits there.
        assert figure.locator("svg polyline").count() >= 2, (
            f"club {team_id} has a missing season but its line is drawn in one piece"
        )
        return

    pytest.fail("no listed club has a season gap to check, so the gap rendering went untested")


@pytest.mark.loaded
def test_the_relegation_race_draws_the_survival_threshold(page: Page, dashboard_url: str) -> None:
    """Guard the race section: the threshold line, the projection and the rivals."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    race = page.locator(RACE_SECTION)
    expect(race).to_have_count(1)

    # The threshold is drawn inside the projected-points chart, so the line and
    # its label have to arrive together; a label without a line would claim a
    # comparison the picture does not make. The label is drawn as HTML above the
    # plot and the line is a mark of the drawing, so each is found where it sits.
    projected = _figure(race, PROJECTED_CAPTION)
    expect(projected).to_have_count(1)
    expect(projected.get_by_text(THRESHOLD_LABEL, exact=True).first).to_be_visible()
    expect(projected.locator("[data-chart-threshold]")).to_have_count(1)
    expect(projected.locator("[role='img']")).to_have_count(1)

    rivals = race.get_by_role("table", name=RIVALS_CAPTION)
    expect(rivals).to_have_count(1)
    expect(rivals.locator("caption")).to_have_count(1)
    assert rivals.locator("thead th[scope='col']").count() > 0, (
        "the rivals table has no column headers"
    )


@pytest.mark.loaded
def test_the_remaining_fixtures_table_prices_the_simulated_fixtures(
    page: Page, dashboard_url: str
) -> None:
    """Guard the run-in: the fixtures table stays, and the next fixture is priced."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    race = page.locator(RACE_SECTION)
    expect(race).to_have_count(1)

    fixtures = race.get_by_role("table", name=FIXTURES_CAPTION).first
    expect(fixtures).to_have_count(1)
    expect(fixtures.locator("caption")).to_have_count(1)
    assert fixtures.locator("thead th[scope='col']").count() > 0, (
        "the remaining fixtures table has no column headers"
    )

    # The loaded tier simulates after the fit, so the next fixture of the default
    # club carries its stored probabilities; the placeholder is for runs without
    # per-fixture detail and must not show here.
    first_row = fixtures.locator("tbody tr").first
    expect(first_row).to_be_visible()
    expect(first_row.get_by_text(MISSING_PROBABILITIES)).to_have_count(0)

    # The row is priced with one column per result, each a percentage rounded to
    # one decimal place: three dashes or one merged note would leave a reader
    # unable to compare the results of the fixture.
    probabilities = [text.strip() for text in first_row.locator("td").all_text_contents()]
    assert len(probabilities) == 3, (
        f"the first remaining fixture prints {probabilities} instead of win, draw and loss"
    )
    for text in probabilities:
        assert FIXTURE_PERCENT.fullmatch(text) is not None, (
            f"the first remaining fixture is priced {probabilities}, "
            "which is not three percentages with one decimal place"
        )


@pytest.mark.loaded
def test_the_swing_panel_shows_the_outcomes_of_the_next_match(
    page: Page, dashboard_url: str
) -> None:
    """Guard the swing panel: a run with detail fills it, and the waiting note goes."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    next_match = page.locator(NEXT_MATCH_SECTION)
    expect(next_match).to_have_count(1)
    expect(next_match.get_by_text(PENDING_SWING)).to_have_count(0)
    for outcome in SWING_ROWS:
        expect(next_match.get_by_role("rowheader", name=outcome, exact=True)).to_have_count(1)

    swing = page.get_by_role("table", name=SWING_CAPTION)
    expect(swing).to_have_count(1)
    header, rows = _swing_cells(swing)

    # The columns are the design's, in order: the chance of the result, then the
    # probability the season's band features, then the change against the
    # baseline and the expected points. The featured column carries the band's
    # name, so it is found by the columns around it.
    chance = header.index(CHANCE_COLUMN)
    featured = chance + 1
    assert header[featured + 1] == CHANGE_COLUMN, (
        f"the swing table's columns are {header}, so the featured probability is not "
        "between the chance of the result and the change"
    )
    for outcome in SWING_ROWS:
        assert outcome in rows, f"the swing table has no {outcome!r} row: {sorted(rows)}"

    # The outcomes are the run's own decomposition of the baseline: the chance of
    # each result times the probability it leads to is the probability the season
    # already has. A reader who multiplies the displayed cells has to land on the
    # Now row, so a column read from another run would show here.
    baseline = _percent(rows["Now"][featured])
    recombined = sum(
        _percent(rows[outcome][chance]) / 100 * _percent(rows[outcome][featured])
        for outcome in SWING_OUTCOMES
    )
    assert abs(recombined - baseline) <= SWING_TOLERANCE_PP, (
        f"the swing table's chances and probabilities recombine to {recombined:.1f} % "
        f"where the Now row prints {baseline:.1f} %, so the outcomes do not add up "
        "to the season as it stands"
    )


@pytest.mark.loaded
def test_a_top_four_club_collapses_the_relegation_race(page: Page, dashboard_url: str) -> None:
    """Guard the collapse: a club out of danger gets a sentence, not a flat chart."""
    page.goto(dashboard_url)

    table = page.get_by_role("table", name=STANDINGS_TABLE)
    expect(table).to_have_count(1)

    leader_link = table.locator(":scope > tbody > tr").first.locator(":scope > td a").first
    expect(leader_link).to_have_attribute("href", TEAM_HREF)
    href = leader_link.get_attribute("href")
    assert href is not None, "the first standings row links no club"
    leader = TEAM_HREF.fullmatch(href)
    assert leader is not None, f"the first standings row links {href!r}"

    _open(page, dashboard_url, f"/focus/{leader.group(1)}")

    race = page.locator(RACE_SECTION)
    expect(race).to_have_count(1)
    expect(race.get_by_text(COLLAPSED_NOTE).first).to_be_visible()

    # The collapse replaces both charts, so no projection caption may survive it:
    # a figure here would mean the section drew a risk it had just denied.
    expect(race.locator("figcaption", has_text=PROJECTED_CAPTION)).to_have_count(0)


@pytest.mark.loaded
def test_an_unknown_season_offers_a_way_out(page: Page, dashboard_url: str) -> None:
    """Guard the played-season miss: the page says so and offers the selector."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, f"{path}{UNKNOWN_SEASON_QUERY}")

    expect(page.get_by_role("heading", name=NO_SEASON_HEADING, exact=True)).to_have_count(1)

    # The selector is what makes the dead end recoverable, and it has to be a
    # combobox a reader can open, not a list of links without JavaScript.
    season = page.get_by_role("combobox", name="Season")
    expect(season).to_have_count(1)
    expect(season).to_be_visible()


@pytest.mark.loaded
def test_the_season_diagnosis_renders_its_charts_and_tables(page: Page, dashboard_url: str) -> None:
    """Guard section 1: both charts with their reading aids, and the three tables."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    diagnosis = page.locator(DIAGNOSIS_SECTION)
    expect(diagnosis).to_have_count(1)

    cumulative = _figure(diagnosis, CUMULATIVE_CAPTION)
    expect(cumulative).to_have_count(1)
    expect(cumulative.locator('[role="img"]')).to_have_count(1)

    cumulative_text = cumulative.inner_text()
    assert ABOVE_THE_LINE in cumulative_text, "the cumulative chart lacks its reading aid"
    assert COVERAGE.search(cumulative_text) is not None, (
        "the cumulative chart does not state its market coverage as n of N"
    )

    # The rolling chart moved behind a disclosure, so its figure is counted in
    # the document rather than looked for on screen: the page draws it for a
    # reader who asks for it, and a disclosure left open would push the rest of
    # the section out of the first read.
    rolling = _figure(diagnosis, ROLLING_CAPTION)
    expect(rolling).to_have_count(1)
    expect(rolling.locator('[role="img"]')).to_have_count(1)
    rolling_summary = diagnosis.locator("summary").filter(has_text=ROLLING_SUMMARY)
    expect(rolling_summary).to_have_count(1)
    assert not _disclosure_is_open(rolling_summary), (
        "the rolling five-match chart is open on arrival instead of behind its summary"
    )

    for caption in SPLIT_CAPTIONS:
        table = diagnosis.locator("table").filter(has=page.locator("caption", has_text=caption))
        expect(table).to_have_count(1)
        expect(table.locator("tbody tr").first).to_be_attached()


@pytest.mark.loaded
def test_the_next_match_links_to_its_preview_with_model_bars(
    page: Page, dashboard_url: str
) -> None:
    """Guard section 3: the next fixture links to its page and shows the forecasts."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    section = page.locator(NEXT_MATCH_SECTION)
    hrefs = section.locator("a").evaluate_all("links => links.map(l => l.getAttribute('href'))")
    assert any(MATCH_HREF.match(href or "") for href in hrefs), (
        f"the next-match section links to {hrefs} and none is a match page"
    )
    bars = section.locator("figure").filter(
        has=page.locator("figcaption", has_text=MODEL_BAR_CAPTION)
    )
    assert bars.count() >= 1, (
        "the next match shows no model probability bar; the loaded tier runs make predict"
    )


@pytest.mark.loaded
def test_section_nav_anchors_resolve(page: Page, dashboard_url: str) -> None:
    """Guard the nav: every link reaches an element that holds its section."""
    path = _default_focus_path(page, dashboard_url)
    # The sections stream, so the anchors are read once every one of them has
    # replaced its skeleton: a wrapper that still holds a fallback has no heading
    # to land on yet, and checking it early would only measure the wait.
    _open_rendered(page, dashboard_url, path)

    nav = page.get_by_role("navigation", name=SECTION_NAV_LABEL)
    expect(nav).to_have_count(1)
    links = nav.locator("a")
    expect(links).to_have_count(SECTION_NAV_LINKS)

    hrefs = links.evaluate_all("links => links.map(link => link.getAttribute('href'))")
    for href in hrefs:
        assert href is not None and href.startswith("#"), (
            f"the section nav links to {href!r}, which is not a fragment"
        )
        # The anchor points at the wrapper of the boundary rather than at the
        # section itself, so the target is looked up by id and has to hold the
        # section's `h2` once the section has arrived.
        target = page.evaluate(
            """
            (id) => {
              const anchor = document.getElementById(id);
              if (anchor === null) {
                return "no element with that id";
              }
              return anchor.querySelector("h2") === null
                ? "an element without a heading"
                : "the section";
            }
            """,
            href[1:],
        )
        assert target == "the section", (
            f"the nav link {href!r} resolves to {target}, so a reader who follows it "
            "lands on nothing to read"
        )


@pytest.mark.loaded
def test_every_metric_has_operable_help(page: Page, dashboard_url: str) -> None:
    """Guard the definitions: each metric the page prints has a help a keyboard opens."""
    path = _default_focus_path(page, dashboard_url)
    # The controls are pressed once the whole page has rendered: a card that is
    # still a skeleton is replaced under the keyboard, and a focused control that
    # is swapped for its own copy loses the focus this test is about.
    _open_rendered(page, dashboard_url, path)

    # The chance tile is named after the band the season falls in, so the four
    # tile labels are read from the card instead of being written here.
    tiles = page.locator(STORY_SECTION).locator(STORY_TILE_LABEL)
    expect(tiles).to_have_count(len(STORY_TILE_NAMES) + 1)
    tile_labels = [text.strip() for text in tiles.all_text_contents()]

    for label in (*tile_labels, *SECTION_HELP_LABELS):
        _assert_help_is_operable(page, label)


@pytest.mark.loaded
def test_the_focus_page_fits_a_phone(page: Page, dashboard_url: str) -> None:
    """Guard the phone layout: nothing a reader sees is wider than the screen."""
    page.set_viewport_size({"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
    path = _default_focus_path(page, dashboard_url)
    _open_rendered(page, dashboard_url, path)

    # A page wider than the screen scrolls sideways, and the columns a reader
    # compares stop lining up with what they are reading.
    width = page.evaluate("() => document.documentElement.scrollWidth")
    assert width <= PHONE_WIDTH, (
        f"the page needs {width} px at {PHONE_WIDTH} px, so a reader scrolls the "
        "whole page sideways"
    )

    for selector in PHONE_FIT_SELECTORS:
        measured, offenders = _measured_overflow(page, selector)
        assert measured > 0, (
            f"the phone page renders no visible {selector} to measure, so this check "
            "would pass on no reading at all"
        )
        assert offenders == [], (
            f"at {PHONE_WIDTH} px these {selector} are cut by the column that holds them: "
            f"{'; '.join(offenders)}"
        )
