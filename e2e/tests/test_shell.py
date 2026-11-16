"""The browser test of the application shell.

It covers the parts every page inherits: the landmarks, the navigation, the
footer, the two not-found and theme behaviours, and the focus redirect. The
empty tier holds on a stack with no data; the redirect test needs the ingested
teams, so it is a loaded-tier test. Every assertion is about structure — a
landmark, a link target, an attribute the server rendered — and never about a
value a particular dataset happens to produce, except where the design names the
default focus team itself.
"""

import re

import pytest
from playwright.sync_api import Page, expect

# The navigation the design fixes: the href and the label prefix of each entry.
# The Focus entry carries the resolved team's name after the prefix.
NAV_ITEMS = (
    ("/", "Table"),
    ("/matchday", "Matchday"),
    ("/teams", "Teams"),
    ("/focus", "Focus"),
    ("/performance", "Model performance"),
)

THEME_COOKIE = "lmi_theme"
FOCUS_COOKIE = "lmi_focus_team"
THEME_BUTTON = re.compile("^Theme")
# Tab presses needed to walk from the document into the navigation. The skip
# link, the theme button and the five navigation links sit inside this budget.
TAB_BUDGET = 12


def _html_tag(html: str) -> str:
    """Return the opening `<html>` tag of a server response."""
    match = re.search(r"<html[^>]*>", html)
    assert match is not None, "the response holds no <html> tag"
    return match.group(0)


def _active_text(page: Page) -> str:
    """Return the trimmed text of the focused element, or an empty string."""
    return page.evaluate(
        "() => { const element = document.activeElement;"
        " return element ? (element.textContent || '').trim() : ''; }"
    )


@pytest.mark.empty
def test_shell_renders_landmarks_navigation_and_footer(page: Page, dashboard_url: str) -> None:
    page.goto(dashboard_url)

    expect(page.locator("header")).to_have_count(1)
    expect(page.locator("main#main")).to_have_count(1)
    expect(page.locator("footer")).to_have_count(1)

    skip_link = page.get_by_role("link", name="Skip to content")
    expect(skip_link).to_have_count(1)
    expect(skip_link).to_have_attribute("href", "#main")

    nav = page.get_by_role("navigation", name="Main")
    expect(nav).to_have_count(1)
    links = nav.get_by_role("link")
    expect(links).to_have_count(len(NAV_ITEMS))
    hrefs = [links.nth(index).get_attribute("href") for index in range(len(NAV_ITEMS))]
    assert hrefs == [href for href, _label in NAV_ITEMS], f"navigation targets are {hrefs}"
    for index, (_href, label) in enumerate(NAV_ITEMS):
        expect(links.nth(index)).to_contain_text(label)

    footer = page.locator("footer")
    expect(footer.locator("p", has_text="API status:")).to_have_count(1)
    expect(footer.get_by_role("link", name="football-data.co.uk")).to_have_attribute(
        "href", "https://www.football-data.co.uk/"
    )
    expect(footer.get_by_role("link", name="openfootball/football.json")).to_have_attribute(
        "href", "https://github.com/openfootball/football.json"
    )


@pytest.mark.empty
def test_navigation_is_reachable_by_keyboard(page: Page, dashboard_url: str) -> None:
    page.goto(dashboard_url)

    reached: list[str] = []
    table_link_outline = ""
    for _ in range(TAB_BUDGET):
        page.keyboard.press("Tab")
        text = _active_text(page)
        reached.append(text)
        if text == "Table":
            table_link_outline = page.evaluate(
                "() => getComputedStyle(document.activeElement).outlineStyle"
            )

    for _href, label in NAV_ITEMS:
        assert any(text.startswith(label) for text in reached), (
            f"{label!r} is not reachable with Tab; focus reached {reached}"
        )
    assert reached[0] == "Skip to content", (
        f"the first Tab must reach the skip link, not {reached[0]!r}"
    )
    assert table_link_outline == "solid", (
        "a keyboard-focused navigation link must show the focus ring, "
        f"outline-style was {table_link_outline!r}"
    )


@pytest.mark.empty
def test_unknown_route_renders_the_not_found_page(page: Page, dashboard_url: str) -> None:
    url = f"{dashboard_url}/matches/999999999"
    response = page.goto(url)

    assert response is not None, f"{url} returned no navigation response"
    assert response.status == 404, f"{url} answered HTTP {response.status}"
    expect(page.get_by_role("heading", level=1)).to_have_text("Page not found")
    expect(page.get_by_role("navigation", name="Main")).to_have_count(1)


@pytest.mark.empty
def test_theme_cookie_round_trips_without_a_flash(page: Page, dashboard_url: str) -> None:
    page.goto(dashboard_url)

    # With no cookie the document follows the system: no attribute at all, so
    # the first paint is the system theme.
    initial_html = page.request.get(dashboard_url).text()
    assert "data-theme" not in _html_tag(initial_html), (
        "a reader with no cookie must get no data-theme attribute"
    )

    toggle = page.get_by_role("button", name=THEME_BUTTON)
    expect(toggle).to_have_count(1)
    for _attempt in range(3):
        if page.evaluate("() => document.documentElement.dataset.theme") == "dark":
            break
        toggle.click()

    assert page.evaluate("() => document.documentElement.dataset.theme") == "dark", (
        "the toggle must reach the dark theme"
    )

    cookies = {cookie["name"]: cookie["value"] for cookie in page.context.cookies()}
    assert cookies.get(THEME_COOKIE) == "dark", (
        f"the toggle must store the choice, cookies are {cookies}"
    )

    # The server render, not the browser, has to carry the attribute: that is
    # what makes the theme right in the first paint.
    server_html = page.request.get(dashboard_url).text()
    assert 'data-theme="dark"' in _html_tag(server_html), (
        "the server HTML must carry the stored theme"
    )

    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


@pytest.mark.loaded
def test_focus_redirects_to_the_default_team(page: Page, dashboard_url: str) -> None:
    response = page.request.get(f"{dashboard_url}/focus", max_redirects=0)

    assert response.status in (301, 302, 307, 308), (
        f"/focus answered HTTP {response.status} instead of redirecting"
    )
    location = response.headers.get("location", "")
    match = re.search(r"/focus/(\d+)$", location)
    assert match is not None, (
        f"/focus redirected to {location!r}, which names no team; the default "
        "focus team was probably not resolved"
    )

    # The redirect must land on the stored club the default name resolves to, so
    # the teams index — which links every club by identifier — is the witness.
    page.goto(f"{dashboard_url}/teams")
    club = page.get_by_role("link", name="Valencia", exact=True)
    expect(club).to_have_count(1)
    assert club.get_attribute("href") == f"/teams/{match.group(1)}", (
        "/focus did not redirect to the club the default focus name selects"
    )


@pytest.mark.loaded
def test_picking_a_focus_team_stores_it_and_returns_to_the_table(
    page: Page, dashboard_url: str
) -> None:
    page.goto(f"{dashboard_url}/teams")

    # The form renders only on a row that is not the focus team, so a row that
    # carries the button is a change of choice by construction.
    row = (
        page.locator("tbody tr")
        .filter(has=page.get_by_role("button", name="Make this my focus team"))
        .first
    )
    team_id = row.locator('input[name="teamId"]').get_attribute("value")
    club = row.locator("th").inner_text().strip()
    assert team_id is not None, "the picker must submit the club's identifier"

    row.get_by_role("button", name="Make this my focus team").click()

    # The action redirects back to the table it was submitted from and stays on
    # this dashboard: a redirect built from submitted data is the one place where
    # that is not obvious.
    marked = page.locator("tbody tr").filter(has_text="Your focus team")
    expect(marked.locator("th")).to_have_text(club)
    assert page.url.startswith(f"{dashboard_url}/teams"), (
        f"the action redirected off the table, to {page.url!r}"
    )

    cookies = {cookie["name"]: cookie["value"] for cookie in page.context.cookies()}
    assert cookies.get(FOCUS_COOKIE) == team_id, (
        f"the action must store the chosen club, cookies are {cookies}"
    )
