"""The browser test of the performance page.

The page renders two API reports whose content depends on the dataset, so this
module asserts the structure the design fixes: the two labelled sections, the
subset control, one metrics row per model, the calibration panels and the live
counts. Nothing here reads a value a particular dataset happens to produce. The
empty tier holds on a stack with no data, where each section must name the
command that fills it; the loaded tier needs the ingested data and the stored
backtest report, which the local stack produces.
"""

import pytest
from playwright.sync_api import Page, Response, expect

PERFORMANCE_PATH = "/performance"
BACKTEST_SECTION = "section[aria-labelledby='backtest-heading']"
LIVE_SECTION = "section[aria-labelledby='live-heading']"
ALL_FORECASTS = "All forecasts"
COMMON_MATCHES = "Common matches"
OUTCOMES = ("H", "D", "A")
# The page must keep this sentence verbatim: it is the one place that tells a
# reader the market row was not stored before kickoff like a forecast.
MARKET_CAVEAT = (
    "The market reference is a benchmark, not a stored forecast: "
    "market uses pre-closing odds published with the results; not stored before kickoff."
)


def _open(page: Page, dashboard_url: str, path: str = PERFORMANCE_PATH) -> None:
    """Open one dashboard URL and fail with the status when it is not a 200."""
    url = f"{dashboard_url}{path}"
    response: Response | None = page.goto(url)
    assert response is not None, f"{url} returned no navigation response"
    assert response.status == 200, f"{url} answered HTTP {response.status}"


@pytest.mark.empty
def test_empty_stack_renders_both_sections_with_their_command(
    page: Page, dashboard_url: str
) -> None:
    _open(page, dashboard_url)

    heading = page.get_by_role("heading", level=1)
    expect(heading).to_have_count(1)
    expect(heading).to_have_text("Performance")

    backtest = page.locator(BACKTEST_SECTION)
    live = page.locator(LIVE_SECTION)
    expect(backtest).to_have_count(1)
    expect(live).to_have_count(1)

    # Each section names the command that produces its report, so a reader on an
    # empty stack is told what to run instead of being shown an empty chart.
    expect(backtest).to_contain_text("make backtest")
    expect(live).to_contain_text("make ingest")

    # A missing report is a state, not a zero: neither section may draw a figure
    # or a table the payload cannot fill.
    expect(backtest.locator("figure, table")).to_have_count(0)
    expect(live.locator("figure, table")).to_have_count(0)

    # The default subset is the whole forecast set, so its link is the current
    # one until the URL says otherwise.
    expect(page.get_by_role("link", name=ALL_FORECASTS, exact=True)).to_have_attribute(
        "aria-current", "page"
    )


@pytest.mark.empty
@pytest.mark.parametrize(
    ("query", "current", "other"),
    [
        ("?subset=common", COMMON_MATCHES, ALL_FORECASTS),
        ("?subset=common&subset=all", COMMON_MATCHES, ALL_FORECASTS),
        ("?subset=bogus", ALL_FORECASTS, COMMON_MATCHES),
    ],
)
def test_subset_parameter_marks_exactly_one_link_current(
    page: Page, dashboard_url: str, query: str, current: str, other: str
) -> None:
    _open(page, dashboard_url, f"{PERFORMANCE_PATH}{query}")

    expect(page.get_by_role("heading", level=1)).to_have_text("Performance")

    current_link = page.get_by_role("link", name=current, exact=True)
    expect(current_link).to_have_count(1)
    expect(current_link).to_have_attribute("aria-current", "page")

    other_link = page.get_by_role("link", name=other, exact=True)
    expect(other_link).to_have_count(1)
    # The inactive choice carries no aria-current at all, not an empty value a
    # screen reader might still announce.
    assert other_link.get_attribute("aria-current") is None, (
        f"{other!r} must not be marked current on {query}"
    )


@pytest.mark.loaded
def test_backtest_table_lists_one_row_per_model(page: Page, dashboard_url: str) -> None:
    _open(page, dashboard_url)

    models = page.locator("dl[aria-label='Models in this run'] dt")
    rows = page.locator("table#backtest-metrics tbody th[scope='row']")

    model_names = [text.strip() for text in models.all_text_contents()]
    row_names = [text.strip() for text in rows.all_text_contents()]

    assert model_names, "the latest backtest run lists no model"
    assert len(set(model_names)) == len(model_names), (
        f"the model list repeats a name: {model_names}"
    )
    # The table follows the model order of the run, so a reader can move from
    # the list to the rows without re-sorting anything.
    assert row_names == model_names, (
        f"the metrics rows {row_names} do not match the model list {model_names}"
    )

    table = page.locator("table#backtest-metrics")
    expect(table.locator("caption")).to_have_count(1)
    assert table.locator("thead th[scope='col']").count() > 0, (
        "the metrics table has no column headers"
    )


@pytest.mark.loaded
def test_calibration_chart_shows_one_panel_per_outcome(page: Page, dashboard_url: str) -> None:
    _open(page, dashboard_url)

    calibration = page.locator(BACKTEST_SECTION).locator(
        "figure", has_text="Calibration by outcome"
    )
    expect(calibration).to_have_count(1)
    expect(calibration.locator("figcaption")).to_contain_text("Calibration by outcome")

    svg = calibration.locator("svg[role='img']")
    expect(svg).to_have_count(1)
    labelled_by = svg.get_attribute("aria-labelledby")
    assert labelled_by is not None and labelled_by.strip(), (
        "the calibration chart must label its SVG with a title and a description"
    )

    # The three panel headings are the only place those letters appear, so one
    # occurrence each proves the chart drew all three outcomes.
    texts = [text.strip() for text in svg.locator("text").all_text_contents()]
    for outcome in OUTCOMES:
        assert texts.count(outcome) == 1, (
            f"the calibration chart must head exactly one panel {outcome!r}, saw {texts}"
        )


@pytest.mark.loaded
def test_live_section_reports_counts_and_metrics(page: Page, dashboard_url: str) -> None:
    _open(page, dashboard_url)

    # The page keeps one title, whichever state the live section renders.
    expect(page.get_by_role("heading", level=1)).to_have_count(1)

    live = page.locator(LIVE_SECTION)
    expect(live).to_have_count(1)

    # These three counts are the reasons a model can score fewer rows than the
    # matchday holds, so they must reach the reader even when they are zero.
    counts = live.locator("dl[aria-label='Live prediction counts']")
    expect(counts).to_have_count(1)
    for label in ("Withdrawn", "Late", "Pending"):
        expect(counts).to_contain_text(label)

    # The loaded dataset can legitimately hold only pending forecasts, so the
    # section's own state is what a reader sees: the metrics table when some
    # stored row has a score, otherwise the state that says none has one yet.
    # An absent table must never pass as an empty table.
    table = live.locator("table#live-metrics")
    if table.count() > 0:
        expect(table).to_have_count(1)
        expect(table.locator("caption")).to_have_count(1)
        assert table.locator("tbody th[scope='row']").count() > 0, "the live table has no model row"
    else:
        expect(
            live.get_by_role("heading", name="No scored predictions yet", exact=True)
        ).to_have_count(1)

    assert MARKET_CAVEAT in live.inner_text(), (
        "the live section must keep the market caveat verbatim, so a reader "
        "knows the market row was not stored before kickoff"
    )
