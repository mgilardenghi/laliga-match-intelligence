"""The browser test of the performance page.

The page renders two API reports whose content depends on the dataset, so this
module asserts the structure the design fixes: the two labelled sections, the
subset control, one metrics row per model, the calibration panels and the live
counts. Nothing here reads a value a particular dataset happens to produce. The
empty tier holds on a stack with no data, where each section must name the
command that fills it; the loaded tier needs the ingested data and the stored
backtest report, which the local stack produces.

The redesign of the page adds a section navigation, a verdict sentence about the
betting market, a priority-ordered scores table, a ranking figure, a calibration
picker and a phone layout. The tests at the end of the module hold that contract:
every fragment the section navigation names is exactly one element id, the verdict
is one of the template sentences, each score column states its best value at both
widths, no panel hides content behind a sideways scroll on a phone, every metric
header carries a definition a keyboard and a finger can open, no text in the charts
or the ranking leaves the readable range, and the calibration picker keeps the
model and the subset the URL asked for.
"""

import re
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from playwright.sync_api import Browser, Page, Response, expect

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

# The redesign fixes the section navigation, the order of the score columns, the
# columns a phone drops and the note that accounts for them, so those names are
# the design's, not a reading of one dataset.
MINIMUM_SECTION_LINKS = 2
BACKTEST_ID = "#backtest"
METRICS_TABLE = "#backtest-metrics"
METRIC_HELP = "summary[aria-label^='What ']"
PHONE_ONLY_NOTE = (
    "Kind, Brier, Matches and Coverage appear on a wider screen; "
    "Brier's best value is in the tiles above."
)
HEADER_ORDER = (
    "Model",
    "Kind",
    "RPS",
    "Log loss",
    "Brier",
    "Accuracy",
    "Matches",
    "Coverage",
)
PHONE_HIDDEN_HEADERS = ("Kind", "Brier", "Matches", "Coverage")
PHONE_VISIBLE_HEADERS = ("Model", "RPS", "Log loss", "Accuracy")
SCORE_COLUMNS = ("RPS", "Log loss", "Brier", "Accuracy")
LOWER_IS_BETTER_METRICS = ("RPS", "Log loss", "Brier")
HIGHER_IS_BETTER_METRICS = ("Accuracy", "Coverage")
BETTER_WHEN_LOWER = "lower value is better"
BETTER_WHEN_HIGHER = "higher value is better"
VERDICT_SELECTOR = "p#performance-verdict"
# The six sentences the design allows, as written. A verdict outside them means
# the page states a conclusion nobody reviewed.
VERDICT_TEMPLATE = re.compile(
    r"^(The backtest stores no common-match scores"
    r"|Only [\d,]+ matches are shared"
    r"|.+ beats the betting market on the same"
    r"|.+ and the betting market are level"
    r"|The betting market is the best forecaster"
    r"|.+ is the best model)"
)
# A verdict states the comparison. Explaining it away with a cause is the failure
# the template rules out.
VERDICT_FORBIDDEN = ("because", "thanks to")

# The two sentences that rank nothing: their template names no model, so they are
# the only verdicts the table cannot corroborate. Both reach the same closing words,
# which is what stops an unranked prefix from carrying a ranking behind it.
VERDICT_UNRANKED = ("The backtest stores no common-match scores", "Only ")
VERDICT_UNRANKED_ENDING = "so the page does not rank the models."

# The kind the table prints for a fitted model, so a baseline or the market never
# stands in for the model the verdict names.
MODEL_KIND = "Model"
MINIMUM_TEXT_PX = 11
MAXIMUM_TEXT_PX = 18
PHONE_WIDTH = 390
PHONE_HEIGHT = 844
WIDE_WIDTH = 1280
WIDE_HEIGHT = 800
# The metrics table's own container, the two backtest figures and the panel around
# the calibration chart are four targets on a loaded stack. Three is the floor that
# keeps the sideways-scroll check from passing on a page that rendered nothing.
MINIMUM_SCROLL_TARGETS = 3


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
@pytest.mark.parametrize(
    ("query", "caption"),
    [
        ("", "Overall backtest metrics, all forecasts"),
        ("?subset=all", "Overall backtest metrics, all forecasts"),
        ("?subset=common", "Overall backtest metrics, common matches"),
    ],
)
def test_the_overall_table_caption_names_the_subset(
    page: Page, dashboard_url: str, query: str, caption: str
) -> None:
    """Guard the subset switch: the table states which cohort it scores.

    The loaded data gives the same scores for both cohorts, so the caption, not a
    number, is what proves the page read the requested subset.
    """
    _open(page, dashboard_url, f"{PERFORMANCE_PATH}{query}")
    backtest = page.locator(BACKTEST_SECTION)
    expect(backtest.locator("table > caption", has_text=caption)).to_have_count(1)


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


# The section navigation promises that each of its links lands on the section it
# names. One query resolves the whole navigation, because a fragment that matches
# nothing and a fragment that matches two elements are the same failure for a
# reader: the link scrolls nowhere or scrolls somewhere unexpected.
SECTION_NAV_SCRIPT = r"""
() => {
  const navs = Array.from(
    document.querySelectorAll('nav[aria-label="Sections of this page"]')
  );
  const links = navs.length === 1 ? Array.from(navs[0].querySelectorAll("a[href]")) : [];
  const fragments = links
    .map((link) => link.getAttribute("href") || "")
    .filter((href) => href.startsWith("#") && href.length > 1)
    .map((href) => {
      let matches = -1;
      try {
        matches = document.querySelectorAll(href).length;
      } catch (error) {
        matches = -1;
      }
      return { fragment: href, matches };
    });
  return { navCount: navs.length, linkCount: links.length, fragments };
}
"""

# The width a panel reports is the width of its content, so a panel wider than its
# own box puts the difference behind a sideways scroll the reader has no hint of.
# The open metric definitions are measured as well, because a definition panel can
# be wider than the header cell it hangs from and only exists once it is opened.
SIDEWAYS_SCROLL_SCRIPT = r"""
(openHelp) => {
  const describe = (element) => {
    const tag = element.tagName.toLowerCase();
    if (element.id) {
      return `${tag}#${element.id}`;
    }
    const firstClass = element.classList.length > 0 ? `.${element.classList[0]}` : "";
    return `${tag}${firstClass}`;
  };
  const targets = [];
  const collect = (element, name) => {
    if (element === null || element === undefined) {
      return;
    }
    targets.push({
      name,
      selector: describe(element),
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
    });
  };
  const parentOf = (element) => (element === null ? null : element.parentElement);
  const backtest = document.querySelector("#backtest");
  const figures = backtest === null ? [] : Array.from(backtest.querySelectorAll("figure"));
  collect(parentOf(document.querySelector("#backtest-metrics")), "the metrics table container");
  figures.forEach((figure, index) => {
    collect(figure, `figure ${index + 1} of the backtest section`);
  });
  const calibration = figures.find((figure) => {
    const caption = figure.querySelector("figcaption");
    return caption !== null && (caption.textContent || "").includes("Calibration by outcome");
  });
  const svg = calibration === undefined ? null : calibration.querySelector("svg[role='img']");
  collect(parentOf(svg), "the panel that draws the calibration chart");
  collect(parentOf(document.querySelector("#live-metrics")), "the live metrics container");
  collect(parentOf(document.querySelector("#live-market")), "the live market container");
  if (openHelp) {
    const summaries = Array.from(
      document.querySelectorAll("#backtest-metrics thead summary[aria-label^='What ']")
    );
    summaries.forEach((summary, index) => {
      collect(summary.closest("details"), `the open definition of metric ${index + 1}`);
      collect(summary.closest("th"), `the header cell of metric ${index + 1}`);
    });
  }
  const root = document.documentElement;
  return { page: { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth }, targets };
}
"""

# Every metric definition is a `details` in its own header cell, so the state of all
# of them is one reading of the header row.
HELP_STATE_SCRIPT = r"""
() => {
  const summaries = Array.from(
    document.querySelectorAll("#backtest-metrics thead summary[aria-label^='What ']")
  );
  const open = summaries.filter((summary) => {
    const details = summary.closest("details");
    return details !== null && details.open;
  });
  return { summaries: summaries.length, open: open.length };
}
"""

# A header cell reads out its metric name. The definition panel sits inside the
# same cell, so the panel text is dropped before the name is compared.
HEADER_CELLS_SCRIPT = r"""
() => {
  const labelOf = (cell) => {
    const clone = cell.cloneNode(true);
    Array.from(clone.querySelectorAll("details")).forEach((details) => details.remove());
    return (clone.textContent || "").replace(/\s+/g, " ").trim();
  };
  return Array.from(document.querySelectorAll("#backtest-metrics thead th")).map((cell) => ({
    name: labelOf(cell),
    hasHelp: cell.querySelector("summary[aria-label^='What ']") !== null,
  }));
}
"""

# The column a phone drops has no box, and the body row repeats the same choice.
# Both readings come from one pass so the header and the rows cannot disagree
# about which column they describe.
HEADER_AND_BEST_VALUE_SCRIPT = r"""
() => {
  const table = document.querySelector("#backtest-metrics");
  if (table === null) {
    return null;
  }
  const labelOf = (cell) => {
    const clone = cell.cloneNode(true);
    Array.from(clone.querySelectorAll("details")).forEach((details) => details.remove());
    return (clone.textContent || "").replace(/\s+/g, " ").trim();
  };
  const visible = (cell) => {
    const style = getComputedStyle(cell);
    return (
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      cell.getClientRects().length > 0
    );
  };
  return {
    headers: Array.from(table.querySelectorAll("thead th")).map((cell) => ({
      name: labelOf(cell),
      visible: visible(cell),
    })),
    rows: Array.from(table.querySelectorAll("tbody tr")).map((row) =>
      Array.from(row.children).map((cell) => ({
        isData: cell.tagName === "TD",
        hasStrong: cell.querySelector("strong") !== null,
      }))
    ),
  };
}
"""

# Chart labels and the ranked model list are the text a reader compares, so both
# are measured at the size the reader sees. Element identity is reported alongside
# the size so a failure names what to fix.
BACKTEST_TEXT_SIZE_SCRIPT = r"""
() => {
  const measure = (element, group) => ({
    group,
    tag: element.tagName.toLowerCase(),
    className: element.getAttribute("class") || "",
    size: Number.parseFloat(getComputedStyle(element).fontSize),
  });
  const sizes = Array.from(document.querySelectorAll("#backtest figure svg text")).map(
    (element) => measure(element, "a chart label")
  );
  const ranking = Array.from(document.querySelectorAll("#backtest figure")).find((figure) => {
    const caption = figure.querySelector("figcaption");
    return caption !== null && (caption.textContent || "").trim().startsWith("RPS by model");
  });
  let ranked = [];
  if (ranking !== undefined) {
    const list = ranking.querySelector("ol");
    if (list !== null) {
      ranked = [list, ...Array.from(list.querySelectorAll("*"))].map((element) =>
        measure(element, "the ranked model list")
      );
    }
  }
  return { sizes, ranked, hasRanking: ranking !== undefined };
}
"""

# The picker is the figure's own set of links: only those the reader can follow to
# another model, and only the one the page marked as drawn.
CALIBRATION_PICKER_SCRIPT = r"""
() => {
  const figure = Array.from(document.querySelectorAll("#backtest figure")).find((node) => {
    const caption = node.querySelector("figcaption");
    return caption !== null && (caption.textContent || "").includes("Calibration by outcome");
  });
  if (figure === undefined) {
    return null;
  }
  const links = Array.from(figure.querySelectorAll("a[href]")).map((link) => ({
    href: link.getAttribute("href") || "",
    current: link.getAttribute("aria-current") === "true",
  }));
  return {
    svgCount: figure.querySelectorAll("svg[role='img']").length,
    pickers: links.filter((link) => link.href.includes("calibration=")),
  };
}
"""


# A tile is a label and the one value under it. The grid is the only box inside the
# scores section, so the tiles are its children and their order is the order the
# design fixes.
SCORE_TILES_SCRIPT = r"""
() => {
  const section = document.querySelector("#scores");
  if (section === null) {
    return null;
  }
  const clean = (node) =>
    node === null ? "" : (node.textContent || "").replace(/\s+/g, " ").trim();
  const grid = Array.from(section.children).find((child) => child.tagName === "DIV");
  if (grid === undefined) {
    return { count: 0, tiles: [] };
  }
  const tiles = Array.from(grid.children).map((tile) => {
    // The value is the tile's own paragraph: the definition panel inside the
    // header holds paragraphs of its own, in document order before the value.
    const values = Array.from(tile.children).filter((child) => child.tagName === "P");
    return {
      label: clean(tile.querySelector("span")),
      value: values.length === 0 ? "" : clean(values[0]),
    };
  });
  return { count: tiles.length, tiles };
}
"""

# The table marks its best cell with a `strong`, and the design repeats that value
# in a tile, so one reading of both proves the repetition comes from the same
# column. A marked cell is read from its own `strong`, because the cell also
# carries the screen-reader words " (best)".
TABLE_VALUES_SCRIPT = r"""
() => {
  const table = document.querySelector("#backtest-metrics");
  if (table === null) {
    return null;
  }
  const clean = (node) =>
    node === null ? "" : (node.textContent || "").replace(/\s+/g, " ").trim();
  const headers = Array.from(table.querySelectorAll("thead th")).map((cell) => {
    const clone = cell.cloneNode(true);
    Array.from(clone.querySelectorAll("details")).forEach((details) => details.remove());
    return clean(clone);
  });
  const rows = Array.from(table.querySelectorAll("tbody tr"));
  const marked = {};
  headers.forEach((name, index) => {
    marked[name] = rows
      .map((row) => row.children[index])
      .filter((cell) => cell !== undefined && cell.querySelector("strong") !== null)
      .map((cell) => clean(cell.querySelector("strong")));
  });
  const cellText = (cell) => {
    if (cell === undefined) {
      return "";
    }
    const strong = cell.querySelector("strong");
    return clean(strong === null ? cell : strong);
  };
  const modelIndex = headers.indexOf("Model");
  const kindIndex = headers.indexOf("Kind");
  const rpsIndex = headers.indexOf("RPS");
  return {
    headers,
    marked,
    rows: rows.map((row) => ({
      model: cellText(row.children[modelIndex]),
      kind: cellText(row.children[kindIndex]),
      rps: cellText(row.children[rpsIndex]),
    })),
  };
}
"""


def _single_line(text: str) -> str:
    """Collapse the whitespace the rendered markup leaves in a sentence."""
    return " ".join(text.split())


def _best_model_rows(table: dict[str, Any]) -> list[dict[str, str]]:
    """Every fitted-model row the table shows at its lowest RPS.

    The table prints three decimals while the page ranks on the underlying scores,
    so two models whose scores round to the same text are both candidates: the page
    may name either, and a check that picked one would invent an order the rendered
    table cannot show.
    """
    scored: list[tuple[float, dict[str, str]]] = []
    for row in table["rows"]:
        if row["kind"] != MODEL_KIND:
            continue
        try:
            scored.append((float(row["rps"]), row))
        except ValueError:
            continue
    if not scored:
        return []
    lowest = min(score for score, _ in scored)
    return [row for score, row in scored if score == lowest]


def _assert_section_nav_anchors_resolve(page: Page, path: str) -> None:
    """Prove the section navigation names real targets and nothing else.

    An empty stack renders two sections, the backtest and the live report, so two
    links are the floor there; one link would mean a section has no way in.
    """
    measured: dict[str, Any] = page.evaluate(SECTION_NAV_SCRIPT)

    nav_count = measured["navCount"]
    assert nav_count == 1, (
        f"{path} renders {nav_count} navigations labelled 'Sections of this page', "
        "so a reader has no single index of the sections the page holds"
    )

    link_count = measured["linkCount"]
    assert link_count >= MINIMUM_SECTION_LINKS, (
        f"{path} offers {link_count} links in its section navigation, fewer than the "
        f"{MINIMUM_SECTION_LINKS} sections the design lists, so a section has no way in"
    )

    fragments = measured["fragments"]
    assert fragments, (
        f"{path} names no in-page fragment in its section navigation, so its links "
        "cannot jump anywhere inside the document"
    )

    resolution = ", ".join(
        f"{item['fragment']} matches {item['matches']} elements" for item in fragments
    )
    broken = [item for item in fragments if item["matches"] != 1]
    assert broken == [], (
        f"{path} links to a fragment that is not exactly one element id ({resolution}), "
        "so the link scrolls nowhere or to the wrong section"
    )


def _assert_no_sideways_scroll(page: Page, open_help: bool) -> None:
    """Fail when the page or one of its panels is wider than the room it has."""
    measured: dict[str, Any] = page.evaluate(SIDEWAYS_SCROLL_SCRIPT, open_help)

    root = measured["page"]
    assert root["scrollWidth"] <= root["clientWidth"], (
        f"the page is {root['scrollWidth']}px wide in a {root['clientWidth']}px viewport, "
        "so a phone reader has to scroll sideways to read the page"
    )

    targets = measured["targets"]
    assert len(targets) >= MINIMUM_SCROLL_TARGETS, (
        f"only {len(targets)} elements were measured for a sideways scroll, so the page "
        "rendered nothing this check could judge"
    )

    offenders = [
        f"{item['name']} ({item['selector']}) scrolls {item['scrollWidth']}px in a "
        f"{item['clientWidth']}px box"
        for item in targets
        if item["scrollWidth"] > item["clientWidth"]
    ]
    assert offenders == [], (
        "a phone hides part of the page behind a sideways scroll inside its own panel: "
        f"{'; '.join(offenders)}"
    )


def _header_visible(measured: dict[str, Any], name: str) -> bool:
    """Return whether the scores table heads a visible column with this name."""
    for header in measured["headers"]:
        if header["name"] == name:
            return bool(header["visible"])
    raise AssertionError(
        f"the scores table heads no column named {name!r}, so a reader cannot find that score"
    )


def _assert_every_score_column_marks_a_best_value(measured: dict[str, Any], viewport: str) -> None:
    """Prove each score column marks a best value somewhere in its model rows."""
    names = [header["name"] for header in measured["headers"]]
    rows = measured["rows"]
    assert rows, f"at {viewport} the scores table lists no model row to compare"

    missing: list[str] = []
    for name in SCORE_COLUMNS:
        assert name in names, (
            f"at {viewport} the scores table has no {name!r} column, so the page does "
            "not offer that score at this width"
        )
        column = names.index(name)
        marked = [
            row[column]
            for row in rows
            if column < len(row) and row[column]["isData"] and row[column]["hasStrong"]
        ]
        if not marked:
            missing.append(name)
    assert missing == [], (
        f"at {viewport} no value is marked as the best in the {', '.join(missing)} "
        "column, so a reader cannot see which model leads that score"
    )


def _calibration_pickers(page: Page, path: str) -> dict[str, Any]:
    """Return the calibration figure's model links and its chart count."""
    measured: dict[str, Any] | None = page.evaluate(CALIBRATION_PICKER_SCRIPT)
    assert measured is not None, (
        f"{path} renders no figure captioned 'Calibration by outcome', so a reader "
        "cannot check how a model's probabilities are calibrated"
    )
    assert measured["svgCount"] == 1, (
        f"{path} draws {measured['svgCount']} calibration charts instead of one, so the "
        "picker's choice is not shown as a single chart"
    )
    assert measured["pickers"], (
        f"{path} offers no link that switches the calibration model, so a reader is "
        "stuck with whichever model the page picked"
    )
    return measured


def _current_calibration_href(measured: dict[str, Any], path: str) -> str:
    """Return the href of the one picker link marked as the drawn model."""
    current = [picker for picker in measured["pickers"] if picker["current"]]
    assert len(current) == 1, (
        f"{path} marks {len(current)} calibration links as current instead of one, so a "
        "reader cannot tell which model the chart draws"
    )
    return str(current[0]["href"])


def _calibration_model(href: str) -> str | None:
    """Return the model name a picker href selects, or None when it names none."""
    values = parse_qs(urlsplit(href).query).get("calibration")
    return values[0] if values else None


@pytest.mark.empty
def test_section_nav_anchors_resolve_on_the_empty_stack(page: Page, dashboard_url: str) -> None:
    """A reader on an empty stack can still jump to every section."""
    _open(page, dashboard_url)
    _assert_section_nav_anchors_resolve(page, PERFORMANCE_PATH)


@pytest.mark.loaded
def test_section_nav_anchors_resolve_on_the_loaded_stack(page: Page, dashboard_url: str) -> None:
    """The section navigation of the full page still names exactly one target each."""
    _open(page, dashboard_url)
    _assert_section_nav_anchors_resolve(page, PERFORMANCE_PATH)


@pytest.mark.loaded
def test_verdict_follows_template(page: Page, dashboard_url: str) -> None:
    """The verdict is one reviewed sentence about the model the table ranks first."""
    # The verdict always reads the common-match rows, so the page that shows the
    # same rows is the one where the sentence and the table can be compared.
    path = f"{PERFORMANCE_PATH}?subset=common"
    _open(page, dashboard_url, path)

    verdict = page.locator(VERDICT_SELECTOR)
    expect(verdict).to_have_count(1)

    text = _single_line(verdict.inner_text())
    assert VERDICT_TEMPLATE.match(text) is not None, (
        f"the verdict {text!r} matches none of the template sentences, so the page "
        "states a conclusion the design does not define"
    )

    lowered = text.lower()
    for phrase in VERDICT_FORBIDDEN:
        assert phrase not in lowered, (
            f"the verdict {text!r} explains itself with {phrase!r}, so the sentence "
            "claims a cause the backtest cannot show"
        )

    # A template match alone accepts any branch with any number in it, so the
    # sentence is also read against the column it summarises: the model it names
    # must be one of the models this page shows at its lowest score, and the score
    # it prints must be that model's own score.
    if text.startswith(VERDICT_UNRANKED):
        assert text.endswith(VERDICT_UNRANKED_ENDING), (
            f"the verdict {text!r} starts like a sentence that ranks nothing but does "
            f"not end on {VERDICT_UNRANKED_ENDING!r}, so a ranking hides behind the "
            "prefix of a sentence that claims there is none"
        )
        return

    assert text.endswith("."), (
        f"the verdict {text!r} does not end its sentence, so a template prefix can "
        "carry any claim after it"
    )

    table: dict[str, Any] | None = page.evaluate(TABLE_VALUES_SCRIPT)
    assert table is not None, (
        f"{path} ranks the models in a sentence but renders no scores table, so the "
        "sentence has no column to agree with"
    )
    candidates = _best_model_rows(table)
    assert candidates, (
        f"{path} names a best model but its scores table holds no fitted-model row, so "
        "the sentence ranks a model the page never scores"
    )
    named = [row for row in candidates if row["model"] in text]
    assert named, (
        f"the verdict {text!r} names none of the fitted models this page shows at its "
        f"lowest score ({', '.join(row['model'] for row in candidates)}), so it ranks "
        "a model the table does not"
    )
    assert named[0]["rps"] in text, (
        f"the verdict {text!r} does not print {named[0]['rps']!r}, the score this page "
        f"shows for {named[0]['model']!r}"
    )


@pytest.mark.loaded
def test_phone_has_no_hidden_sideways_scroll(page: Page, dashboard_url: str) -> None:
    """Nothing on the page is wider than the phone it is read on."""
    page.set_viewport_size({"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
    _open(page, dashboard_url)

    _assert_no_sideways_scroll(page, open_help=False)

    # The metric definitions are the only content the page reveals on demand, and
    # an open definition can be wider than the header cell it hangs from. At the
    # phone width the design hides the Kind, Brier, Matches and Coverage headers,
    # so their controls have no box to press; every header is visible at the wide
    # viewport, so all of them are opened there before the phone is measured again.
    page.set_viewport_size({"width": WIDE_WIDTH, "height": WIDE_HEIGHT})
    summaries = page.locator(f"{METRICS_TABLE} thead {METRIC_HELP}")
    summary_count = summaries.count()
    assert summary_count >= 1, (
        "the scores table offers no metric definition to open, so there is no panel "
        "this check could measure"
    )
    for index in range(summary_count):
        summaries.nth(index).click()

    state: dict[str, Any] = page.evaluate(HELP_STATE_SCRIPT)
    assert state["open"] == state["summaries"], (
        f"{state['open']} of {state['summaries']} metric definitions opened, so a reader "
        "cannot read what the columns score"
    )

    page.set_viewport_size({"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
    _assert_no_sideways_scroll(page, open_help=True)


@pytest.mark.loaded
def test_metric_headers_have_help(page: Page, dashboard_url: str, browser: Browser) -> None:
    """Every score column states which direction is better, for keys and fingers."""
    _open(page, dashboard_url)

    columns: list[dict[str, Any]] = page.evaluate(HEADER_CELLS_SCRIPT)
    assert columns, "the scores table renders no column header, so no definition is offered"

    definitions = [(name, BETTER_WHEN_LOWER) for name in LOWER_IS_BETTER_METRICS]
    definitions += [(name, BETTER_WHEN_HIGHER) for name in HIGHER_IS_BETTER_METRICS]
    for name, phrase in definitions:
        matching = [index for index, cell in enumerate(columns) if cell["name"] == name]
        assert len(matching) == 1, (
            f"the scores table heads {len(matching)} columns {name!r} instead of exactly "
            f"one, so a reader cannot tell which column holds the {name} score"
        )
        index = matching[0]
        assert columns[index]["hasHelp"], (
            f"the {name} header carries no 'What ...' definition, so a reader who does "
            f"not know {name} has no way to look it up"
        )

        summary = page.locator(f"{METRICS_TABLE} thead th").nth(index).locator(METRIC_HELP)
        expect(summary).to_have_count(1)
        is_open = (
            "(element) => { const details = element.closest('details');"
            " return details !== null && details.open; }"
        )

        summary.focus()
        page.keyboard.press("Enter")
        assert summary.evaluate(is_open) is True, (
            f"Enter does not open the {name} definition, so a keyboard reader cannot "
            f"learn what {name} scores"
        )

        page.keyboard.press("Enter")
        assert summary.evaluate(is_open) is False, (
            f"a second Enter does not close the {name} definition, so the open panel "
            "stays in the reader's way"
        )

        panel = _single_line(
            summary.evaluate(
                "(element) => (element.closest('details') || element.parentElement).textContent"
            )
        ).lower()
        assert phrase in panel, (
            f"the {name} definition does not say {phrase!r}, so a reader cannot tell "
            "which way a better score moves"
        )

    # A finger cannot press what only a keyboard reaches, so the same definition is
    # tapped on a touch context at the phone width the page is read at.
    context = browser.new_context(
        has_touch=True, viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT}
    )
    try:
        touch_page = context.new_page()
        _open(touch_page, dashboard_url)
        touch_summaries = touch_page.locator(f"{METRICS_TABLE} thead {METRIC_HELP}")
        assert touch_summaries.count() >= 1, (
            "the scores table offers no metric definition on a phone, so a touch "
            "reader cannot look a column up"
        )
        tapped = touch_summaries.first
        tapped.tap()
        assert tapped.evaluate(is_open) is True, (
            "tapping a metric definition does not open it, so a reader on a touch "
            "screen cannot read what that column scores"
        )
    finally:
        context.close()


@pytest.mark.loaded
def test_priority_columns(page: Page, dashboard_url: str) -> None:
    """A phone keeps the four scores and a wide screen brings the rest back."""
    page.set_viewport_size({"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
    _open(page, dashboard_url)

    phone: dict[str, Any] | None = page.evaluate(HEADER_AND_BEST_VALUE_SCRIPT)
    assert phone is not None, (
        "the backtest section renders no scores table, so a reader has no column to read"
    )

    names = [header["name"] for header in phone["headers"]]
    assert names == list(HEADER_ORDER), (
        f"the scores table heads its columns {names} instead of {list(HEADER_ORDER)}, so "
        "the page lost a column or moved one away from the order the design fixes"
    )

    for name in PHONE_HIDDEN_HEADERS:
        assert _header_visible(phone, name) is False, (
            f"the {name} column is still visible in a {PHONE_WIDTH}px viewport, so it "
            "squeezes the four scores a phone reader compares"
        )
    for name in PHONE_VISIBLE_HEADERS:
        assert _header_visible(phone, name) is True, (
            f"the {name} column is hidden in a {PHONE_WIDTH}px viewport, so a phone "
            "reader loses a score the page exists to compare"
        )
    _assert_every_score_column_marks_a_best_value(phone, f"{PHONE_WIDTH}px")

    note = page.locator(BACKTEST_ID).get_by_text(PHONE_ONLY_NOTE, exact=True)
    expect(note).to_have_count(1)
    expect(note).to_be_visible()

    page.set_viewport_size({"width": WIDE_WIDTH, "height": WIDE_HEIGHT})
    wide: dict[str, Any] | None = page.evaluate(HEADER_AND_BEST_VALUE_SCRIPT)
    assert wide is not None, (
        f"the scores table disappeared at {WIDE_WIDTH}px, so a wide screen lost the "
        "figures the phone layout kept"
    )

    hidden = [header["name"] for header in wide["headers"] if not header["visible"]]
    assert hidden == [], (
        f"at {WIDE_WIDTH}px the scores table still hides {', '.join(hidden)}, so a wide "
        "screen never recovers the columns the design restores there"
    )
    _assert_every_score_column_marks_a_best_value(wide, f"{WIDE_WIDTH}px")

    # The note only makes sense while a column is missing, so it must go once the
    # columns are back.
    expect(note).to_be_hidden()


@pytest.mark.loaded
def test_score_tiles_show_the_table_best_values(page: Page, dashboard_url: str) -> None:
    """Each headline number repeats the best value of the column it summarises."""
    _open(page, dashboard_url)

    tiles: dict[str, Any] | None = page.evaluate(SCORE_TILES_SCRIPT)
    assert tiles is not None, (
        "the page renders no scores section, so no headline number summarises the table"
    )

    labels = [tile["label"] for tile in tiles["tiles"]]
    assert tiles["count"] == len(SCORE_COLUMNS), (
        f"the scores section renders {tiles['count']} tiles ({labels}) instead of the "
        f"{len(SCORE_COLUMNS)} the design fixes, so a reader loses a headline number"
    )
    assert labels == list(SCORE_COLUMNS), (
        f"the scores section heads its tiles {labels} instead of {list(SCORE_COLUMNS)}, "
        "so a tile stands over the wrong column"
    )

    table: dict[str, Any] | None = page.evaluate(TABLE_VALUES_SCRIPT)
    assert table is not None, (
        "the page renders tiles without the scores table they summarise, so a reader "
        "cannot check a headline number"
    )

    for tile in tiles["tiles"]:
        marked = table["marked"].get(tile["label"], [])
        assert marked, (
            f"the {tile['label']} column marks no best value, so the tile above it "
            "repeats a value the table never states"
        )
        # A tie marks every model that shares the best value, and the page marks a
        # tie on purpose, so a tied column is a pass as long as every marked cell
        # states the number the tile repeats.
        assert set(marked) == {tile["value"]}, (
            f"the {tile['label']} tile reads {tile['value']!r} while the table marks "
            f"{sorted(set(marked))!r} as the best value of that column"
        )


@pytest.mark.loaded
def test_chart_text_sizes(page: Page, dashboard_url: str) -> None:
    """No label in the backtest charts or the ranking shrinks or shouts."""
    page.set_viewport_size({"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
    _open(page, dashboard_url)

    measured: dict[str, Any] = page.evaluate(BACKTEST_TEXT_SIZE_SCRIPT)
    assert measured["hasRanking"], (
        "the backtest section renders no figure captioned 'RPS by model', so a reader "
        "cannot see the model order the ranking exists to state"
    )

    chart = measured["sizes"]
    assert chart, "no backtest chart renders a text label, so there is no size to check"
    ranked = measured["ranked"]
    assert len(ranked) >= 2, (
        f"the ranked model list holds {len(ranked)} measurable elements, too few to be a "
        "list of models"
    )

    offenders = [
        f"{item['group']} {item['tag']}.{item['className']} at {item['size']:.2f}px"
        for item in chart + ranked
        if not MINIMUM_TEXT_PX <= item["size"] <= MAXIMUM_TEXT_PX
    ]
    assert offenders == [], (
        f"text in the backtest charts or the model ranking renders outside "
        f"{MINIMUM_TEXT_PX}-{MAXIMUM_TEXT_PX}px: {', '.join(offenders)}"
    )


@pytest.mark.loaded
def test_calibration_picker(page: Page, dashboard_url: str) -> None:
    """The picker keeps the chosen model and the chosen subset in every link."""
    _open(page, dashboard_url)
    default = _calibration_pickers(page, PERFORMANCE_PATH)
    default_href = _current_calibration_href(default, PERFORMANCE_PATH)
    selected = _calibration_model(default_href)
    assert selected is not None, (
        f"the current calibration link {default_href!r} names no model, so the page does "
        "not say which model it drew"
    )

    bogus_path = f"{PERFORMANCE_PATH}?calibration=bogus"
    _open(page, dashboard_url, bogus_path)
    fallback = _calibration_pickers(page, bogus_path)
    fallback_href = _current_calibration_href(fallback, bogus_path)
    fallback_model = _calibration_model(fallback_href)
    assert fallback_model == selected, (
        f"an unknown model name selects {fallback_model!r} ({fallback_href!r}) instead of "
        f"the default {selected!r}, so a stale link draws a chart nobody asked for"
    )

    common_path = f"{PERFORMANCE_PATH}?subset=common&calibration={quote(selected, safe='')}"
    _open(page, dashboard_url, common_path)
    common = _calibration_pickers(page, common_path)
    for picker in common["pickers"]:
        params = parse_qs(urlsplit(picker["href"]).query)
        assert params.get("subset") == ["common"], (
            f"the picker link {picker['href']!r} drops the common-matches subset, so "
            "switching model silently scores a different set of matches"
        )

    for name in (ALL_FORECASTS, COMMON_MATCHES):
        link = page.get_by_role("link", name=name, exact=True)
        expect(link).to_have_count(1)
        href = link.get_attribute("href") or ""
        params = parse_qs(urlsplit(href).query)
        assert params.get("calibration") == [selected], (
            f"the {name} link {href!r} drops the selected model {selected!r}, so "
            "switching the subset silently draws another model"
        )
