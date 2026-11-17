"""The invariants a chart reader depends on.

The gallery tests drive the development chart gallery at /dev/charts. The
gallery renders inline fixture data, so it runs on the empty stack, and each
assertion is about the rendered page: text that never scales with a drawing, a
phone page that does not scroll sideways, a label that is not cut, an axis that
fits its data, an off-scale value that is still printed, and a metric definition
that a keyboard can reach. The text-size and clipping rules run at every viewport
and theme the suite renders at, because a chart can shrink its text at one width or
in one theme alone.

One loaded-tier test leaves the gallery and applies the same text-size rule to
the pages a reader actually uses, because a chart can also live outside the
shared directory the gallery renders.
"""

import json
from urllib.parse import urlsplit

import pytest
from conftest import COLOR_SCHEMES, VIEWPORTS
from playwright.sync_api import Locator, Page, Response, expect
from test_shell import THEME_COOKIE

GALLERY_PATH = "/dev/charts"
MINIMUM_TEXT_PX = 11
MAXIMUM_TEXT_PX = 18
PHONE_WIDTH = 390
PHONE_HEIGHT = 844
TAB_BUDGET = 60

# The readability rules hold per combination, not once for whichever viewport and
# theme the browser happened to have, so the two tests below run at every width
# and both themes the suite renders at. The viewports, the theme names and the
# theme cookie name are imported rather than repeated: they are the ones the
# saved screenshots and the shell's theme round trip already use.
VIEWPORT_CASES = [
    pytest.param(width, height, id=f"{width}x{height}") for width, height in VIEWPORTS
]

# The pages a reader uses that draw charts, in the order the navigation lists
# them. An index page is a way into a chart page and not a chart page itself: the
# teams index holds a table and no figure, so the index paths below only supply
# the detail link this module follows, and the zero-figure guard covers the pages
# this tuple lists for measurement.
REAL_PAGES = ("/", "/focus", "/performance")
TEAMS_INDEX_PATH = "/teams"
MATCHDAY_INDEX_PATH = "/matchday"
# The first detail link of each index: the club pages the teams index links from
# its table body, and the match pages the matchday cards link. Both indexes list
# what the loaded dataset holds, so the first link is a page a reader lands on
# from there and the test never guesses an identifier.
CLUB_LINK_SELECTOR = 'tbody a[href^="/teams/"]'
MATCH_LINK_SELECTOR = 'a[href^="/matches/"]'

# Every element that renders a non-whitespace text node of its own, measured at
# the size the reader sees. An SVG text node is scaled by its own transform, so
# its declared font size is multiplied by the length of the CTM's x basis: a
# chart that draws its labels inside a shrunken viewBox fails here even though
# the declared size is unchanged.
TEXT_SIZE_SCRIPT = """
(figure) => {
  const ownText = (element) =>
    Array.from(element.childNodes).some(
      (node) => node.nodeType === Node.TEXT_NODE && (node.textContent || "").trim() !== ""
    );
  return Array.from(figure.querySelectorAll("*"))
    .filter((element) => ownText(element) && element.getClientRects().length > 0)
    .map((element) => {
      const declared = Number.parseFloat(getComputedStyle(element).fontSize);
      let size = declared;
      if (element instanceof SVGTextElement) {
        const matrix = element.getCTM();
        if (matrix !== null) {
          size = declared * Math.hypot(matrix.a, matrix.b);
        }
      }
      return { text: (element.textContent || "").trim(), size };
    });
}
"""

# HTML text that overflows its own box is cut for the reader. The assistive
# description span is clipped on purpose, so it is hidden rather than a label
# that was cut, and `clip` is what tells the two apart.
CLIPPED_TEXT_SCRIPT = """
(figure) => {
  const ownText = (element) =>
    Array.from(element.childNodes).some(
      (node) => node.nodeType === Node.TEXT_NODE && (node.textContent || "").trim() !== ""
    );
  return Array.from(figure.querySelectorAll("*"))
    .filter((element) => element instanceof HTMLElement)
    .filter((element) => element.closest("svg") === null)
    .filter((element) => element.clientWidth > 0)
    .filter((element) => element.getClientRects().length > 0)
    .filter((element) => getComputedStyle(element).clip === "auto")
    .filter(ownText)
    .map((element) => ({
      text: (element.textContent || "").trim(),
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
      scrollHeight: element.scrollHeight,
      clientHeight: element.clientHeight,
    }));
}
"""

# The axis ticks carry `data-chart-tick`, so the test reads them as data. The
# printed row values are the other HTML numbers in the figure; the data table is
# skipped because it repeats every value in the dataset, not the row label.
INTERVAL_AXIS_SCRIPT = r"""
(figure) => {
  const visible = (element) => element.getClientRects().length > 0;
  const ownText = (element) =>
    Array.from(element.childNodes).some(
      (node) => node.nodeType === Node.TEXT_NODE && (node.textContent || "").trim() !== ""
    );
  const parse = (text) => {
    const match = /^[+-]?\d+(?:\.\d+)?$/.exec((text || "").trim());
    return match === null ? null : Number.parseFloat(match[0]);
  };
  const tickElements = Array.from(figure.querySelectorAll("[data-chart-tick]"));
  const ticks = tickElements
    .filter(visible)
    .map((element) => parse(element.textContent))
    .filter((value) => value !== null);
  const values = Array.from(figure.querySelectorAll("*"))
    .filter((element) => element instanceof HTMLElement)
    .filter((element) => !tickElements.includes(element))
    .filter((element) => element.closest("details") === null)
    .filter((element) => element.closest("svg") === null)
    .filter((element) => ownText(element) && visible(element))
    .map((element) => parse(element.textContent))
    .filter((value) => value !== null);
  return { ticks, values };
}
"""

# A value outside its axis has to stay in the DOM and stay readable. The case
# names the exact entry the figure must render: the element whose own text is
# that string, found as the deepest holder so an ancestor cannot stand in for it.
OFF_SCALE_ENTRY_SCRIPT = r"""
(figure, spec) => {
  const normalise = (text) => (text || "").replace(/\s+/g, " ").trim();
  const holders = Array.from(figure.querySelectorAll("*")).filter(
    (element) => normalise(element.textContent) === spec.text
  );
  const deepest = holders.filter(
    (element) => !holders.some((other) => other !== element && element.contains(other))
  );
  return deepest.map((element) => ({
    text: normalise(element.textContent),
    visible: element.getClientRects().length > 0,
  }));
}
"""

FOCUSED_SUMMARY_SCRIPT = """
() => {
  const element = document.activeElement;
  if (element === null || element.tagName !== "SUMMARY") {
    return "";
  }
  return element.getAttribute("aria-label") || "";
}
"""

# A figure the reader has collapsed is the only figure this module cannot
# measure: a closed `<details>` renders none of its content, so its figures have
# no box and no visible text. Any other zero box means the page had not rendered
# when the figure was measured, which is a failure and not a pass with nothing
# to check, so the figure reports where it sits in the document as well.
FIGURE_BOX_SCRIPT = """
(figure) => {
  const box = figure.getBoundingClientRect();
  return {
    width: box.width,
    height: box.height,
    collapsed: figure.closest("details:not([open])") !== null,
  };
}
"""

# The figure caption and the exact entry a value outside its axis renders: the
# value and the words that say which edge it passed.
OFF_SCALE_CASES = (
    ("Model metrics compared", "uniform_baseline 1.386 above the axis"),
    (
        "Relegation probability by matchday",
        "Relegation baseline 80.00% off scale, above the axis",
    ),
)


def _open_gallery(page: Page, dashboard_url: str) -> None:
    """Load the gallery and fail on a navigation or HTTP error."""
    url = f"{dashboard_url}{GALLERY_PATH}"
    response: Response | None = page.goto(url)
    assert response is not None, f"{url} returned no navigation response"
    assert response.ok, f"{url} answered HTTP {response.status}"


def _open_gallery_in_theme(
    page: Page, dashboard_url: str, width: int, height: int, theme: str
) -> None:
    """Load the gallery at one viewport in one theme, and prove the theme applied.

    The theme is a cookie the server reads while it renders, so it is stored on the
    browser context before the navigation instead of toggled afterwards: the opening
    `<html>` tag then carries `data-theme` in the first paint. A document without the
    cookie follows the system theme, so a combination the server ignored would
    measure the same page twice and pass. Asserting the applied attribute instead of
    assuming it is what keeps each combination an honest reading.
    """
    page.set_viewport_size({"width": width, "height": height})
    page.context.add_cookies([{"name": THEME_COOKIE, "value": theme, "url": dashboard_url}])
    _open_gallery(page, dashboard_url)
    applied = page.evaluate("() => document.documentElement.dataset.theme")
    assert applied == theme, (
        f"the gallery at {width}x{height} rendered data-theme {applied!r} instead of "
        f"the requested {theme!r}, so this combination measured another theme"
    )


def _caption(figure: Locator) -> str:
    return figure.locator("figcaption").inner_text().strip()


def _figure_with_caption(page: Page, caption: str) -> Locator:
    figures = page.locator("figure")
    for index in range(figures.count()):
        figure = figures.nth(index)
        if _caption(figure) == caption:
            return figure
    raise AssertionError(f"the gallery renders no figure captioned {caption!r}")


def _open_page(page: Page, url: str) -> None:
    """Load one dashboard page, wait for it to render, and fail on HTTP errors.

    The navigation is only the start: `/focus` answers by redirecting to the
    club page while that page is still streaming its sections, so a page that
    has merely fired its load event still holds figures with no box. Waiting for
    the network to settle covers the redirect and the last streamed section,
    which is what makes a figure readable by the time it is measured.
    """
    response: Response | None = page.goto(url, wait_until="networkidle")
    assert response is not None, f"{url} returned no navigation response"
    assert response.ok, f"{url} answered HTTP {response.status}"


def _follow_first_link(page: Page, dashboard_url: str, index_path: str, selector: str) -> str:
    """Follow the first link `selector` matches on `index_path`.

    The path the link opened is returned, so a figure that fails the text-size
    rule is named by the page that renders it.
    """
    _open_page(page, f"{dashboard_url}{index_path}")
    link = page.locator(selector).first
    assert link.count() == 1, f"{index_path} renders no link matching {selector!r}"
    href = link.get_attribute("href")
    assert href is not None, f"{index_path} renders a link without an href"
    link.click()
    page.wait_for_url(lambda url: urlsplit(str(url)).path == href, timeout=15000)
    return href


def _assert_no_scaled_text(page: Page, page_path: str) -> None:
    """Apply the module's text-size rule to every figure the page renders."""
    figures = page.locator("figure")
    assert figures.count() > 0, f"{page_path} rendered no figure to measure"
    for index in range(figures.count()):
        figure = figures.nth(index)
        caption = _caption(figure)
        box = figure.evaluate(FIGURE_BOX_SCRIPT)
        if box["width"] == 0 and box["height"] == 0:
            # A zero-box figure may only be one the reader has collapsed: inside
            # a closed `<details>` nothing is rendered, so there is no text to
            # measure and no scale to check. A figure hidden for any other
            # reason is a page that has not rendered, and skipping it silently
            # would let this assertion pass on no readings at all, so it fails
            # here and names the page and the figure.
            assert box["collapsed"], (
                f"{page_path}: figure {caption!r} has no box but is not inside a "
                "closed <details>, so the page has not rendered it"
            )
            continue
        texts = figure.evaluate(TEXT_SIZE_SCRIPT)
        assert len(texts) >= 3, (
            f"{page_path}: figure {caption!r} renders {len(texts)} visible texts, "
            "too few to test its scale"
        )
        offenders = [
            f"{text['text']!r} at {text['size']:.2f}px"
            for text in texts
            if text["size"] < MINIMUM_TEXT_PX or text["size"] > MAXIMUM_TEXT_PX
        ]
        assert offenders == [], (
            f"{page_path}: figure {caption!r} renders text outside "
            f"{MINIMUM_TEXT_PX}-{MAXIMUM_TEXT_PX}px: {', '.join(offenders)}"
        )


@pytest.mark.empty
@pytest.mark.parametrize(("width", "height"), VIEWPORT_CASES)
@pytest.mark.parametrize("theme", COLOR_SCHEMES)
def test_chart_text_never_scales(
    page: Page, dashboard_url: str, width: int, height: int, theme: str
) -> None:
    """No chart text is shrunk or blown up by the drawing it sits in."""
    _open_gallery_in_theme(page, dashboard_url, width, height, theme)
    figures = page.locator("figure")
    assert figures.count() > 0, "the gallery rendered no figure"
    for index in range(figures.count()):
        figure = figures.nth(index)
        caption = _caption(figure)
        texts = figure.evaluate(TEXT_SIZE_SCRIPT)
        assert len(texts) >= 3, (
            f"figure {caption!r} renders {len(texts)} visible texts, too few to test its scale"
        )
        offenders = [
            f"{text['text']!r} at {text['size']:.2f}px"
            for text in texts
            if text["size"] < MINIMUM_TEXT_PX or text["size"] > MAXIMUM_TEXT_PX
        ]
        assert offenders == [], (
            f"figure {caption!r} renders text outside {MINIMUM_TEXT_PX}-"
            f"{MAXIMUM_TEXT_PX}px: {', '.join(offenders)}"
        )


@pytest.mark.empty
def test_charts_do_not_widen_a_phone_page(page: Page, dashboard_url: str) -> None:
    """The gallery never makes a phone page scroll sideways."""
    page.set_viewport_size({"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
    _open_gallery(page, dashboard_url)
    scroll_width = page.evaluate("() => document.body.scrollWidth")
    assert scroll_width <= PHONE_WIDTH, (
        f"the gallery is {scroll_width}px wide in a {PHONE_WIDTH}px viewport, "
        "so a phone has to scroll sideways"
    )


@pytest.mark.empty
@pytest.mark.parametrize(("width", "height"), VIEWPORT_CASES)
@pytest.mark.parametrize("theme", COLOR_SCHEMES)
def test_chart_labels_are_not_clipped(
    page: Page, dashboard_url: str, width: int, height: int, theme: str
) -> None:
    """No visible HTML label overflows its own box."""
    _open_gallery_in_theme(page, dashboard_url, width, height, theme)
    figures = page.locator("figure")
    assert figures.count() > 0, "the gallery rendered no figure"
    for index in range(figures.count()):
        figure = figures.nth(index)
        caption = _caption(figure)
        labels = figure.evaluate(CLIPPED_TEXT_SCRIPT)
        offenders = [
            (
                f"{label['text']!r} scrolls {label['scrollWidth']}x{label['scrollHeight']} "
                f"in a {label['clientWidth']}x{label['clientHeight']} box"
            )
            for label in labels
            if label["scrollWidth"] > label["clientWidth"] + 1
            or label["scrollHeight"] > label["clientHeight"] + 1
        ]
        assert offenders == [], f"figure {caption!r} cuts its text: {', '.join(offenders)}"


@pytest.mark.empty
def test_interval_bars_axis_fits_its_data(page: Page, dashboard_url: str) -> None:
    """The projected-points axis hugs its values instead of hiding one."""
    _open_gallery(page, dashboard_url)
    figure = _figure_with_caption(page, "Projected final points with survival threshold")
    measured = figure.evaluate(INTERVAL_AXIS_SCRIPT)
    ticks = sorted(measured["ticks"])
    values = measured["values"]
    assert len(ticks) >= 2, f"the projected-points axis renders {len(ticks)} ticks"
    assert len(set(ticks)) == len(ticks), f"the projected-points axis repeats a tick: {ticks}"
    assert values, "the projected-points rows print no numeric value"

    step = ticks[-1] - ticks[-2]
    assert step > 0, f"the projected-points ticks do not increase: {ticks}"
    largest_value = max(values)
    smallest_value = min(values)
    assert ticks[0] <= smallest_value, (
        f"the axis starts at {ticks[0]}, above the smallest printed value {smallest_value}"
    )
    assert ticks[-1] >= largest_value, (
        f"the axis ends at {ticks[-1]}, below the largest printed value {largest_value}"
    )
    assert ticks[-1] - largest_value <= step, (
        f"the axis ends at {ticks[-1]}, more than one {step}-point step past "
        f"the largest printed value {largest_value}"
    )


@pytest.mark.empty
def test_off_scale_values_are_printed(page: Page, dashboard_url: str) -> None:
    """A value outside its axis keeps its number and names the edge it passed."""
    _open_gallery(page, dashboard_url)
    for caption, entry_text in OFF_SCALE_CASES:
        figure = _figure_with_caption(page, caption)
        entries = figure.evaluate(OFF_SCALE_ENTRY_SCRIPT, {"text": entry_text})
        assert entries, (
            f"figure {caption!r} renders no element whose text is exactly "
            f"{entry_text!r}, so its off-scale value is dropped or reworded"
        )
        visible = [entry for entry in entries if entry["visible"]]
        assert visible, (
            f"figure {caption!r} holds {entry_text!r} in the DOM but the entry "
            "that states it is not visible"
        )


@pytest.mark.empty
def test_metric_help_is_reachable(page: Page, dashboard_url: str) -> None:
    """Every metric definition is a keyboard control that opens and closes."""
    _open_gallery(page, dashboard_url)
    summaries = page.locator("summary[aria-label]")
    control_count = summaries.count()
    assert control_count >= 3, f"the gallery renders {control_count} metric help controls"
    labels = [
        summaries.nth(index).get_attribute("aria-label") or "" for index in range(control_count)
    ]

    # Walk the document once: each help control has to be a stop in the
    # sequential focus order, not only reachable with a mouse.
    reached: list[str] = []
    for _ in range(TAB_BUDGET):
        page.keyboard.press("Tab")
        label = page.evaluate(FOCUSED_SUMMARY_SCRIPT)
        if label:
            reached.append(label)
    missing = [label for label in labels if label not in reached]
    assert missing == [], f"Tab never reaches the metric help controls {missing}"

    for label in labels:
        summary = page.locator(f"summary[aria-label={json.dumps(label)}]")
        expect(summary).to_have_count(1)
        panel = summary.locator("xpath=..").locator("p").first

        summary.focus()
        page.keyboard.press("Enter")
        opened = summary.evaluate("(element) => element.parentElement.open")
        assert opened is True, f"Enter does not open the {label!r} definition"
        expect(panel).to_be_visible()

        page.keyboard.press("Enter")
        closed = summary.evaluate("(element) => element.parentElement.open")
        assert closed is False, f"a second Enter does not close the {label!r} definition"
        expect(panel).to_be_hidden()

        # Space is the other key a summary answers, so the definition has to
        # open and close with it as well as with Enter.
        summary.focus()
        page.keyboard.press("Space")
        opened = summary.evaluate("(element) => element.parentElement.open")
        assert opened is True, f"Space does not open the {label!r} definition"
        expect(panel).to_be_visible()

        page.keyboard.press("Space")
        closed = summary.evaluate("(element) => element.parentElement.open")
        assert closed is False, f"a second Space does not close the {label!r} definition"
        expect(panel).to_be_hidden()

        summary.click()
        opened = summary.evaluate("(element) => element.parentElement.open")
        assert opened is True, f"a click does not open the {label!r} definition"
        expect(panel).to_be_visible()
        summary.click()


@pytest.mark.loaded
def test_real_pages_never_scale_chart_text(page: Page, dashboard_url: str) -> None:
    """No chart on a page a reader uses scales its text with its drawing.

    The gallery above is the development surface. The pages a reader uses carry
    charts of their own, and one of them can be a chart primitive that lives
    beside its route instead of in the shared chart directory, so the text-size
    rule is applied to every figure each of those pages renders.
    """
    for path in REAL_PAGES:
        _open_page(page, f"{dashboard_url}{path}")
        _assert_no_scaled_text(page, path)

    for index_path, selector in (
        (TEAMS_INDEX_PATH, CLUB_LINK_SELECTOR),
        (MATCHDAY_INDEX_PATH, MATCH_LINK_SELECTOR),
    ):
        detail_path = _follow_first_link(page, dashboard_url, index_path, selector)
        _assert_no_scaled_text(page, detail_path)


def _wait_until_laid_out(page: Page) -> None:
    """Wait until the page has finished rendering and its layout has settled.

    The link click returns once the URL changed, which is before the streamed
    sections, the web fonts and the layout they cause have settled; a count of
    figures or a width read at that moment describes a half-built page. The wait
    covers the network going quiet, the fonts being ready and two animation frames
    in a row, so a measurement taken after it reads the page a reader sees.
    """
    page.wait_for_load_state("networkidle")
    page.evaluate(
        """() => document.fonts.ready.then(() => new Promise((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve(true)))))"""
    )


PROFILE_CAPTION_WORDS = ("per-match averages", "per-match profile")

PROFILE_FIT_SCRIPT = """
(figure) => {
  const svg = figure.querySelector("svg[role='img']");
  const holder = svg ? svg.parentElement : null;
  if (!holder) { return null; }
  return { scrollWidth: holder.scrollWidth, clientWidth: holder.clientWidth };
}
"""


@pytest.mark.loaded
def test_profile_charts_fit_a_phone_without_hidden_data(page: Page, dashboard_url: str) -> None:
    """The mirrored profile charts show both of their sides on a phone.

    Both sides of the chart carry data: the bar on one side is the club and the
    bar on the other its opponents. A drawing wider than its container would put
    one side behind a sideways scroll with no hint that it exists, and the text
    rule above would not notice, because every label in it is still 12 px. The
    drawing's own container must therefore have nothing to scroll.
    """
    page.set_viewport_size({"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
    checked = 0
    for index_path, selector in (
        (TEAMS_INDEX_PATH, CLUB_LINK_SELECTOR),
        (MATCHDAY_INDEX_PATH, MATCH_LINK_SELECTOR),
    ):
        detail_path = _follow_first_link(page, dashboard_url, index_path, selector)
        _wait_until_laid_out(page)
        figures = page.locator("figure")
        for position in range(figures.count()):
            figure = figures.nth(position)
            if not any(word in _caption(figure).lower() for word in PROFILE_CAPTION_WORDS):
                continue
            box = figure.evaluate(PROFILE_FIT_SCRIPT)
            assert box is not None, f"{detail_path}: a profile figure draws no SVG"
            assert box["scrollWidth"] <= box["clientWidth"] + 1, (
                f"{detail_path}: figure {_caption(figure)!r} is {box['scrollWidth']}px wide in a "
                f"{box['clientWidth']}px container, so a phone hides part of its data behind a "
                "sideways scroll"
            )
            checked += 1
    assert checked >= 2, f"only {checked} profile figures were measured; the pages changed"
