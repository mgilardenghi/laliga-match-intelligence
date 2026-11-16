"""The browser tests of the team focus page.

`/focus/[teamId]` is one club's season: the header, the outlook tiles and five
sections that each stream in with their own data. The empty tier holds on a
stack with no data and covers what the route answers for a club it does not
know. The loaded tier needs the ingested data and the stored simulation, and
covers the default club, the five section headings, the trend sparklines, the
relegation race with its threshold and its two tables, the swing panel that
names the run it waits for, the collapsed race of a club the stored runs never
put in danger, and the season the club never played.

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

# The five sections of the page, in the order the design fixes them.
SECTION_NAMES = (
    "Points against expectation",
    "Splits and discipline",
    "Multi-season trend",
    "Relegation race",
    "Next match",
)
TREND_SECTION = "section[aria-labelledby='trend-heading']"
RACE_SECTION = "section[aria-labelledby='race-heading']"
NEXT_MATCH_SECTION = "section[aria-labelledby='next-match-heading']"

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
PENDING_SWING = "available after the next simulation run with swing support"
MISSING_PROBABILITIES = "model probabilities not available yet"


def _open(page: Page, dashboard_url: str, path: str) -> None:
    """Open one dashboard URL and fail with the status when it is not a 200."""
    url = f"{dashboard_url}{path}"
    response: Response | None = page.goto(url)
    assert response is not None, f"{url} returned no navigation response"
    assert response.status == 200, f"{url} answered HTTP {response.status}"


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
def test_the_page_renders_exactly_the_five_section_headings(page: Page, dashboard_url: str) -> None:
    """Guard the page outline: five labelled sections, each with its own `h2`."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    # The outline is counted, not read in source order: a section that went
    # missing and one that was duplicated both change this number.
    expect(page.locator("main section[aria-labelledby]")).to_have_count(len(SECTION_NAMES))

    for name in SECTION_NAMES:
        heading = page.get_by_role("heading", level=2, name=name, exact=True)
        expect(heading).to_have_count(1)
        expect(heading).to_be_visible()


@pytest.mark.loaded
def test_the_trend_draws_one_sparkline_per_metric(page: Page, dashboard_url: str) -> None:
    """Guard the trend grid: a named sparkline per metric and the published drops."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    trend = page.locator(TREND_SECTION)
    expect(trend).to_have_count(1)

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
def test_the_remaining_fixtures_table_names_the_missing_probabilities(
    page: Page, dashboard_url: str
) -> None:
    """Guard the run-in: the fixtures table stays, and says what it cannot price."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    race = page.locator(RACE_SECTION)
    expect(race).to_have_count(1)

    # The per-fixture simulation is not stored yet, so the cell says so instead
    # of printing three dashes a reader would take for a priced fixture.
    expect(race.get_by_text(MISSING_PROBABILITIES).first).to_be_visible()

    fixtures = race.get_by_role("table", name=FIXTURES_CAPTION)
    expect(fixtures).to_have_count(1)
    expect(fixtures.locator("caption")).to_have_count(1)
    assert fixtures.locator("thead th[scope='col']").count() > 0, (
        "the remaining fixtures table has no column headers"
    )


@pytest.mark.loaded
def test_the_swing_panel_names_the_run_it_waits_for(page: Page, dashboard_url: str) -> None:
    """Guard the swing panel: it says which run fills it instead of inventing one."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    next_match = page.locator(NEXT_MATCH_SECTION)
    expect(next_match).to_have_count(1)
    expect(next_match.get_by_text(PENDING_SWING).first).to_be_visible()


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


HEADER_TERMS = ("Position", "Points", "Goal difference", "Expected points", "P(relegation)")
POINTS_SECTION = "section[aria-labelledby='points-heading']"
SPLITS_SECTION = "section[aria-labelledby='splits-heading']"
CUMULATIVE_CAPTION = "Cumulative points against market expectation"
ROLLING_CAPTION = "Rolling five-match averages"
ABOVE_THE_LINE = "Above the line = results better than the market expected."
COVERAGE = re.compile(r"for \d+ of \d+ played matches")
SPLIT_CAPTIONS = (
    re.compile(r"^Home and away record"),
    re.compile(r"^Record against top, middle and bottom opponents"),
    re.compile(r"^Discipline per match"),
)
MODEL_BAR_CAPTION = re.compile(r": home win, draw and away win$")
MATCH_HREF = re.compile(r"^/matches/\d+$")


@pytest.mark.loaded
def test_the_header_shows_the_tiles_and_both_selectors(page: Page, dashboard_url: str) -> None:
    """Guard the header: the glance tiles and the season and team selectors."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    terms = [text.strip() for text in page.locator("main dl").first.locator("dt").all_inner_texts()]
    for term in HEADER_TERMS:
        assert term in terms, f"the header tiles {terms} lack {term!r}"

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
def test_the_season_diagnosis_renders_its_charts_and_tables(page: Page, dashboard_url: str) -> None:
    """Guard section 2: both charts with their reading aids, and the three tables."""
    path = _default_focus_path(page, dashboard_url)
    _open(page, dashboard_url, path)

    points = page.locator(POINTS_SECTION)
    for caption in (CUMULATIVE_CAPTION, ROLLING_CAPTION):
        figure = _figure(points, caption)
        expect(figure).to_have_count(1)
        expect(figure.locator('[role="img"]')).to_have_count(1)

    cumulative_text = _figure(points, CUMULATIVE_CAPTION).inner_text()
    assert ABOVE_THE_LINE in cumulative_text, "the cumulative chart lacks its reading aid"
    assert COVERAGE.search(cumulative_text) is not None, (
        "the cumulative chart does not state its market coverage as n of N"
    )

    splits = page.locator(SPLITS_SECTION)
    for caption in SPLIT_CAPTIONS:
        table = splits.locator("table").filter(has=page.locator("caption", has_text=caption))
        expect(table).to_have_count(1)
        expect(table.locator("tbody tr").first).to_be_attached()


@pytest.mark.loaded
def test_the_next_match_links_to_its_preview_with_model_bars(
    page: Page, dashboard_url: str
) -> None:
    """Guard section 5: the next fixture links to its page and shows the forecasts."""
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
