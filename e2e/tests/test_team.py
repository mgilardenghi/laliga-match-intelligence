"""The browser tests of the team page.

They cover the three states of `/teams/<id>`: a segment that names no club at
all, a season with stored results, and the focus form the page carries. The
empty tier proves the route validates the segment itself and lands on the
not-found page; the loaded tier proves every section renders from the stored
data and that the focus form stores the club, returns to the page and makes
`/focus` redirect to it. The redirect test is the hardening regression: a
submitted `redirectTo` is user input, and the browser's own submit must stay on
the dashboard even when that value names another host.

Every assertion is about structure — a heading, a caption, a column, a link
target, a cookie — and never about a value a particular dataset happens to
produce.
"""

import re
from urllib.parse import urljoin, urlsplit

import pytest
from playwright.sync_api import Locator, Page, expect

SEASON_LINE = re.compile(r"^Season (\d{4}-\d{2})$")
CLUB_HREF = re.compile(r"^/teams/(\d+)$")
FORM_TITLE = re.compile(r"^Form: [WDL](, [WDL])*$")
RESULT_CELL = re.compile(r"^[WDL]$")

# The page's sections, in the order the page renders them, and the fixed labels
# and columns of each. These are the page's contract, not the dataset's.
SECTION_NAMES = (
    "Season at a glance",
    "Home and away",
    "For and against",
    "Results",
    "Next fixtures",
)
GLANCE_TERMS = ("Position", "Points", "Market xPts", "Goal difference", "P(relegation)")
RECORD_COLUMNS = ("Venue", "P", "W", "D", "L", "GF", "GA", "Pts", "PPG")
RESULT_COLUMNS = ("Date", "MD", "Venue", "Opponent", "Score", "Result")
PROFILE_ROWS = (
    "Goals",
    "Shots",
    "Shots on target",
    "Expected goals",
    "Fouls",
    "Yellow cards",
    "Red cards",
)

PROFILE_CAPTION = "Per-match averages against the league average"
NO_FORECAST = "No Dixon-Coles forecast is stored for this fixture yet"
FOCUS_BUTTON = "Make this my focus team"
FOCUS_COOKIE = "lmi_focus_team"
MAX_FIXTURES = 5
REDIRECT_STATUSES = (301, 302, 307, 308)

# The values a reader can type into the hidden `redirectTo` input. Each one has
# a browser reading that differs from a naive leading-slash test: a network-path
# reference, a backslash in the authority position, and a string the URL parser
# refuses outright.
HOSTILE_REDIRECTS = (
    pytest.param("//evil.example", id="network-path"),
    pytest.param("/\\evil.example", id="backslash-authority"),
    pytest.param("//[", id="unparseable"),
)


def _first_club(page: Page, dashboard_url: str) -> tuple[str, str]:
    """Open the team page of the first club the teams index links."""
    page.goto(f"{dashboard_url}/teams")
    link = page.locator('tbody a[href^="/teams/"]').first
    href = link.get_attribute("href")
    assert href is not None, "the teams index links no club"
    match = CLUB_HREF.fullmatch(href)
    assert match is not None, f"the teams index links {href!r}, which names no club"
    name = link.inner_text().strip()
    page.goto(f"{dashboard_url}/teams/{match.group(1)}")
    return match.group(1), name


def _season(page: Page) -> str:
    """Return the season the page resolved, read from its own season line."""
    line = page.locator("p").filter(has_text=SEASON_LINE)
    expect(line).to_have_count(1)
    match = SEASON_LINE.fullmatch(line.inner_text().strip())
    assert match is not None, f"the season line is {line.inner_text()!r}"
    return match.group(1)


def _figure(page: Page, caption: str) -> Locator:
    """Return the chart figure whose `<figcaption>` contains `caption`."""
    return page.locator("figure").filter(has=page.locator("figcaption", has_text=caption))


def _column_headers(table: Locator) -> list[str]:
    """Return the trimmed column headers of a table, in order."""
    return [text.strip() for text in table.locator(":scope > thead > tr > th").all_inner_texts()]


def _assert_column_scopes(table: Locator) -> None:
    """Assert that every column header names its own column as its scope."""
    headers = table.locator(":scope > thead > tr > th").all()
    assert headers, "the table must render column headers"
    scopes = [header.get_attribute("scope") for header in headers]
    assert scopes == ["col"] * len(headers), f"every column header needs scope=col, found {scopes}"


def _row_headers(table: Locator) -> list[str]:
    """Return the trimmed row headers of a table's body, in order.

    The text is read with `text_content()`, because a data table inside a closed
    `<details>` renders no inner text until the reader opens it.
    """
    rows = table.locator(":scope > tbody > tr")
    headers: list[str] = []
    for index in range(rows.count()):
        header = rows.nth(index).locator(':scope > th[scope="row"]')
        headers.append((header.text_content() or "").strip())
    return headers


def _wait_for_url_change(page: Page, before: str) -> None:
    """Wait until the browser's own navigation has settled on another URL."""
    page.wait_for_url(lambda url: str(url) != before, timeout=15000)


@pytest.mark.empty
def test_an_unknown_club_is_the_not_found_page(page: Page, dashboard_url: str) -> None:
    """Guard the route's own validation: an unknown club renders no page.

    The numeric segment is a club the API does not know; the non-numeric one
    must never reach the API at all, because the route validates the segment
    before it calls the client. Both are the not-found page, inside the shell.
    """
    for path in ("/teams/999999999", "/teams/not-a-number"):
        url = f"{dashboard_url}{path}"
        response = page.goto(url)

        assert response is not None, f"{url} returned no navigation response"
        assert response.status == 404, f"{url} answered HTTP {response.status}"
        expect(page.get_by_role("heading", level=1)).to_have_text("Page not found")
        expect(page.get_by_role("navigation", name="Main")).to_have_count(1)


@pytest.mark.loaded
def test_the_season_sections_render_from_the_loaded_data(page: Page, dashboard_url: str) -> None:
    """Guard the loaded page: every section, caption and label renders."""
    _team_id, club = _first_club(page, dashboard_url)
    season = _season(page)

    expect(page.get_by_role("heading", level=1)).to_have_text(club)
    expect(page.locator("p").filter(has_text=SEASON_LINE)).to_have_text(f"Season {season}")
    expect(page.get_by_role("combobox", name="Season")).to_be_visible()
    expect(page.get_by_role("button", name=FOCUS_BUTTON)).to_have_count(1)

    headings = page.locator("main section > h2")
    assert [text.strip() for text in headings.all_inner_texts()] == list(SECTION_NAMES), (
        "the page must render its five sections in the documented order"
    )
    for name in SECTION_NAMES:
        expect(page.get_by_role("region", name=name)).to_have_count(1)

    glance = page.get_by_role("region", name="Season at a glance")
    assert [text.strip() for text in glance.locator("dt").all_inner_texts()] == list(
        GLANCE_TERMS
    ), "the glance tiles must be the five documented terms in order"
    values = glance.locator("dd")
    expect(values).to_have_count(len(GLANCE_TERMS))
    for index in range(values.count()):
        value = values.nth(index).inner_text().strip()
        assert value, f"the {GLANCE_TERMS[index]!r} tile renders no value"

    record_region = page.get_by_role("region", name="Home and away")
    record = record_region.get_by_role("table", name=f"Home and away record, {season}")
    expect(record).to_have_count(1)
    assert _column_headers(record) == list(RECORD_COLUMNS), "the record table changed its columns"
    _assert_column_scopes(record)
    body_rows = record.locator(":scope > tbody > tr")
    expect(body_rows).to_have_count(2)
    assert _row_headers(record) == ["Home", "Away"], "the record must keep one row per venue"

    figure = _figure(page, PROFILE_CAPTION)
    expect(figure).to_have_count(1)
    expect(figure.locator("figcaption")).to_have_text(PROFILE_CAPTION)
    svg = figure.locator('svg[role="img"]')
    expect(svg).to_have_count(1)
    title = (svg.locator("title").text_content() or "").strip()
    description = (svg.locator("desc").text_content() or "").strip()
    assert title, "the profile chart must carry an internal title"
    assert description, "the profile chart must carry an internal description"

    # The data table lives in a closed `<details>`, so it is addressed by its
    # place in the figure rather than through the accessibility tree.
    profile_table = figure.locator("details table")
    expect(profile_table).to_have_count(1)
    expect(profile_table.locator(":scope > caption")).to_have_text(f"{PROFILE_CAPTION}, {season}")
    assert _row_headers(profile_table) == list(PROFILE_ROWS), (
        "the profile data table must name the seven metrics in order"
    )
    _assert_column_scopes(profile_table)

    results_region = page.get_by_role("region", name="Results")
    form = results_region.locator("figure")
    expect(form).to_have_count(1)
    expect(form.locator("svg title")).to_have_text(FORM_TITLE)
    assert FORM_TITLE.fullmatch(form.locator("figcaption").inner_text().strip()), (
        "the form strip's caption must name the outcomes it draws"
    )

    results = results_region.get_by_role("table", name=f"Results, {season}")
    expect(results).to_have_count(1)
    assert _column_headers(results) == list(RESULT_COLUMNS), "the results table changed its columns"
    _assert_column_scopes(results)
    result_rows = results.locator(":scope > tbody > tr")
    expect(result_rows.first).to_be_visible()
    for index in range(result_rows.count()):
        row = result_rows.nth(index)
        expect(row.locator(':scope > th[scope="row"]')).to_have_count(1)
        link = row.get_by_role("link")
        expect(link).to_have_count(1)
        href = link.get_attribute("href") or ""
        assert CLUB_HREF.fullmatch(href) is not None, (
            f"results row {index} links its opponent to {href!r}, not to a club page"
        )
        expect(row.locator("td").last).to_have_text(RESULT_CELL)

    fixtures = page.get_by_role("region", name="Next fixtures")
    items = fixtures.get_by_role("listitem")
    count = items.count()
    assert 1 <= count <= MAX_FIXTURES, f"the page rendered {count} next fixtures"
    forecasts = 0
    for index in range(count):
        item = items.nth(index)
        links = item.get_by_role("link")
        expect(links).to_have_count(2)
        hrefs = [links.nth(position).get_attribute("href") or "" for position in range(2)]
        assert all(CLUB_HREF.fullmatch(href) for href in hrefs), (
            f"fixture {index} links its clubs to {hrefs}, not to club pages"
        )
        names = [links.nth(position).inner_text().strip() for position in range(2)]

        fixture_figure = item.locator("figure")
        if fixture_figure.count() == 0:
            expect(item).to_contain_text(NO_FORECAST)
            continue
        expect(fixture_figure).to_have_count(1)
        caption = fixture_figure.locator("figcaption").inner_text().strip()
        assert caption == f"{names[0]} v {names[1]}: outcome probabilities", (
            f"fixture {index} is captioned {caption!r}"
        )
        forecasts += 1

    assert forecasts >= 1, (
        "no next fixture renders a Dixon-Coles figure; the loaded stack must "
        "have run `make predict`"
    )


@pytest.mark.loaded
def test_the_focus_button_stores_the_club_and_focus_redirects_to_it(
    page: Page, dashboard_url: str
) -> None:
    """Guard the page's focus form: store the club, stay on the page, redirect.

    The action redirects to the `redirectTo` the page submitted, so the browser
    must land back on this team page; `/focus` must then name the club the
    cookie stores. Only the redirect target is asserted, not the page behind it.
    """
    team_id, _club = _first_club(page, dashboard_url)

    before = page.url
    page.get_by_role("button", name=FOCUS_BUTTON).click()
    _wait_for_url_change(page, before)

    netloc = urlsplit(dashboard_url).netloc
    resolved = urlsplit(page.url)
    assert resolved.netloc == netloc, f"the submit left the dashboard for {page.url!r}"
    assert resolved.path == f"/teams/{team_id}", f"the submit landed on {resolved.path!r}"
    assert resolved.query == "" or resolved.query.startswith("season="), (
        f"the submit landed on {page.url!r}, which carries an unexpected query"
    )

    cookies = {cookie["name"]: cookie["value"] for cookie in page.context.cookies()}
    assert cookies.get(FOCUS_COOKIE) == team_id, (
        f"the form must store the chosen club, cookies are {cookies}"
    )

    response = page.request.get(f"{dashboard_url}/focus", max_redirects=0)
    assert response.status in REDIRECT_STATUSES, (
        f"/focus answered HTTP {response.status} instead of redirecting"
    )
    location = response.headers.get("location", "")
    target = urlsplit(urljoin(dashboard_url, location))
    assert target.netloc == netloc, f"/focus redirected off the dashboard, to {location!r}"
    assert target.path == f"/focus/{team_id}", f"/focus redirected to {location!r}"


@pytest.mark.loaded
@pytest.mark.parametrize("hostile", HOSTILE_REDIRECTS)
def test_a_hostile_redirect_never_leaves_the_dashboard(
    page: Page, dashboard_url: str, hostile: str
) -> None:
    """Guard the redirect hardening: the browser's own submit stays local.

    The hidden `redirectTo` input is submitted data, so a reader can rewrite it.
    The action must resolve it against the dashboard, not against the host it
    names, and the browser must issue no request to that host while submitting.
    The check runs through the browser's own form submit, because that is the
    parse that decides whether the value leaves the origin.
    """
    page.goto(f"{dashboard_url}/teams")
    row = page.locator("tbody tr").filter(has=page.get_by_role("button", name=FOCUS_BUTTON)).first
    team_id = row.locator('input[name="teamId"]').get_attribute("value")
    assert team_id is not None, "the picker must submit the club's identifier"

    redirect = row.locator('input[name="redirectTo"]')
    expect(redirect).to_have_count(1)
    redirect.evaluate("(el, value) => { el.value = value; }", hostile)

    leaked: list[str] = []

    def record(request: object) -> None:
        url = getattr(request, "url", "")
        if "evil.example" in url:
            leaked.append(url)

    page.on("request", record)

    before = page.url
    row.get_by_role("button", name=FOCUS_BUTTON).click()
    _wait_for_url_change(page, before)

    netloc = urlsplit(dashboard_url).netloc
    resolved = urlsplit(page.url)
    assert resolved.netloc == netloc, f"the submit left the dashboard for {page.url!r}"
    assert resolved.path == f"/focus/{team_id}", f"the submit landed on {resolved.path!r}"
    assert not leaked, f"the submit requested {', '.join(leaked)}"
