"""The browser tests of the table page.

They cover the two states of `/`: an empty stack with no stored result at all,
and a loaded stack with real standings plus one stored simulation. The empty
tier proves the page names what is missing instead of drawing an empty table;
the loaded tier proves the table, the projected columns, the zone tinting and
the two projection figures render from the stored run. Every assertion is about
structure — a caption, a column, a link target, an attribute the server
rendered — and never about a value a particular dataset happens to produce,
except the fixed table size of the competition and the arithmetic the heatmap
probabilities must satisfy.
"""

import re

import pytest
from playwright.sync_api import Locator, Page, expect

H1_TEXT = "LaLiga Match Intelligence"
TABLE_NAME = re.compile("^La Liga table for")
CAPTION = re.compile(r"^La Liga table for \d{4}-\d{2}, after \d+ results?$")
TEAM_HREF = re.compile(r"^/teams/\d+$")
POSITION_TEXT = re.compile(r"^\d+\s*(?:UCL|Europe|Relegation)?$")
FORM_TITLE = re.compile(r"^Form: [WDL](, [WDL])*$")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")

# The columns the page fixes: the standings, then the five projected columns a
# stored run adds. Their order is part of the table contract.
COLUMNS = ("Pos", "Team", "P", "W", "D", "L", "GF", "GA", "GD", "Pts", "Form")
PROJECTED_COLUMNS = ("xPts", "P(title)", "P(UCL)", "P(Europe)", "P(releg.)")
CLUB_COUNT = 20

HEATMAP_CAPTION = "Projected finishing positions"
INTERVALS_CAPTION = "Projected final points"


def _main_table(page: Page) -> Locator:
    """Return the standings table, named by its caption."""
    return page.get_by_role("table", name=TABLE_NAME)


def _figure(page: Page, caption: str) -> Locator:
    """Return the chart figure whose `<figcaption>` contains `caption`."""
    return page.locator("figure").filter(has=page.locator("figcaption", has_text=caption))


def _season_codes(page: Page) -> list[str]:
    """Return the season codes the selector offers, skipping the empty choice."""
    options = page.get_by_label("Season").locator("option")
    codes: list[str] = []
    for index in range(options.count()):
        value = options.nth(index).get_attribute("value")
        if value:
            codes.append(value)
    return codes


@pytest.mark.empty
def test_the_empty_dashboard_names_the_ingest_command(page: Page, dashboard_url: str) -> None:
    """Guard the empty home page: the shell still works and says what to run."""
    page.goto(dashboard_url)

    expect(page.get_by_role("heading", level=1)).to_have_text(H1_TEXT)
    expect(page.locator("body")).not_to_contain_text("API unreachable")
    expect(page.get_by_role("heading", name="No match data yet")).to_have_count(1)
    expect(page.locator("code", has_text="make ingest")).to_have_count(1)

    # The selector is built from the seeded seasons even when none has a match,
    # so a reader can still move between them.
    expect(page.get_by_label("Season")).to_be_visible()
    expect(page.get_by_label("Season").locator("option").first).to_be_attached()


@pytest.mark.empty
def test_a_season_without_fixtures_names_that_season(page: Page, dashboard_url: str) -> None:
    """Guard the no-results state: a stored but unplayed season says so."""
    page.goto(dashboard_url)
    codes = _season_codes(page)
    assert codes, "the seeded stack offered no season to follow"
    season = codes[0]

    page.goto(f"{dashboard_url}/?season={season}")

    expect(page.get_by_role("heading", level=1)).to_have_text(H1_TEXT)
    expect(page.get_by_role("heading", name=f"No results in {season} yet")).to_have_count(1)
    expect(page.locator("code", has_text="make ingest")).to_have_count(1)


@pytest.mark.loaded
def test_the_table_renders_one_ranked_row_per_club(page: Page, dashboard_url: str) -> None:
    """Guard the standings: caption, size, and the cells every row must carry."""
    page.goto(dashboard_url)

    table = _main_table(page)
    expect(table).to_have_count(1)
    expect(table.locator(":scope > caption")).to_have_text(CAPTION)

    rows = table.locator(":scope > tbody > tr")
    expect(rows).to_have_count(CLUB_COUNT)

    for index in range(3):
        row = rows.nth(index)
        position = row.locator(':scope > th[scope="row"]')
        expect(position).to_have_count(1)
        expect(position).to_have_text(POSITION_TEXT)

        team_link = row.locator(":scope > td a").first
        expect(team_link).to_have_attribute("href", TEAM_HREF)

        expect(row.locator("svg title")).to_have_text(FORM_TITLE)


@pytest.mark.loaded
def test_the_projected_columns_and_figures_render(page: Page, dashboard_url: str) -> None:
    """Guard the projection: five extra columns and two complete figures."""
    page.goto(dashboard_url)

    table = _main_table(page)
    headers = table.locator(":scope > thead > tr > th")
    texts = [headers.nth(index).inner_text().strip() for index in range(headers.count())]
    assert texts == list(COLUMNS + PROJECTED_COLUMNS), f"the table head is {texts}"

    for caption in (HEATMAP_CAPTION, INTERVALS_CAPTION):
        figure = _figure(page, caption)
        expect(figure).to_have_count(1)
        expect(figure.locator("figcaption")).to_have_count(1)
        expect(figure.locator('svg[role="img"]')).to_have_count(1)
        expect(figure.locator("details summary")).to_have_text("Data table")


@pytest.mark.loaded
def test_the_heatmap_matches_the_table_and_sums_to_one_hundred(
    page: Page, dashboard_url: str
) -> None:
    """Guard the heatmap data table: one row per club and full distributions."""
    page.goto(dashboard_url)

    club_count = _main_table(page).locator(":scope > tbody > tr").count()
    assert club_count > 0, "the standings table rendered no club"

    figure = _figure(page, HEATMAP_CAPTION)
    expect(figure).to_have_count(1)
    figure.locator("summary").click()

    data_table = figure.locator("table")
    expect(data_table).to_be_visible()
    expect(data_table.locator(":scope > caption")).to_have_text("Finishing position probabilities")

    rows = data_table.locator(":scope > tbody > tr")
    expect(rows).to_have_count(club_count)
    expect(data_table.locator(":scope > thead > tr > th")).to_have_count(club_count + 1)

    for index in range(rows.count()):
        row = rows.nth(index)
        expect(row.locator(':scope > th[scope="row"]')).to_have_count(1)
        cells = row.locator(":scope > td")
        expect(cells).to_have_count(club_count)

        total = 0.0
        for text in cells.all_inner_texts():
            match = PERCENT.fullmatch(text.strip())
            assert match is not None, f"heatmap cell {text!r} is not a percentage"
            total += float(match.group(1))
        assert abs(total - 100.0) <= 1.0, f"heatmap row {index} sums to {total}"


@pytest.mark.loaded
def test_zone_tinting_marks_the_qualification_places(page: Page, dashboard_url: str) -> None:
    """Guard the zones: the top and bottom are tinted, the middle is not."""
    page.goto(dashboard_url)

    rows = _main_table(page).locator(":scope > tbody > tr")

    first = rows.first
    expect(first).to_have_attribute("data-zone", "ucl")
    expect(first.locator(':scope > th[scope="row"]')).to_contain_text("UCL")

    last = rows.last
    expect(last).to_have_attribute("data-zone", "relegation")
    expect(last.locator(':scope > th[scope="row"]')).to_contain_text("Relegation")

    # The tint marks qualification places, not every row, so at least one middle
    # row carries no zone attribute at all.
    untinted = _main_table(page).locator(":scope > tbody > tr:not([data-zone])")
    expect(untinted.first).to_be_visible()


@pytest.mark.loaded
def test_a_season_without_a_simulation_offers_make_simulate(page: Page, dashboard_url: str) -> None:
    """Guard the unsimulated season: the real table stays, the projection asks."""
    page.goto(dashboard_url)
    codes = _season_codes(page)
    assert codes, "the selector offered no season"
    oldest = min(codes)

    page.goto(f"{dashboard_url}/?season={oldest}")

    expect(page.get_by_role("heading", level=1)).to_have_text(H1_TEXT)
    table = _main_table(page)
    expect(table).to_have_count(1)
    expect(table.locator(":scope > tbody > tr").first).to_be_visible()

    expect(page.locator("code", has_text="make simulate")).to_have_count(1)
    expect(page.get_by_role("heading", name="No simulation for this season yet")).to_have_count(1)

    expect(_figure(page, INTERVALS_CAPTION)).to_have_count(0)
    expect(table.locator(":scope > tbody > tr[data-zone]")).to_have_count(0)


@pytest.mark.empty
@pytest.mark.loaded
def test_the_footer_reports_the_api_and_the_attribution(page: Page, dashboard_url: str) -> None:
    """Guard the footer on the table page: API status and both attributions."""
    page.goto(dashboard_url)

    status = page.locator("p", has_text="API status:")
    expect(status).to_have_count(1)
    expect(status).to_contain_text("online")

    expect(page.get_by_role("link", name="football-data.co.uk")).to_have_attribute(
        "href", "https://www.football-data.co.uk/"
    )
    expect(page.get_by_role("link", name="openfootball/football.json")).to_have_attribute(
        "href", "https://github.com/openfootball/football.json"
    )
