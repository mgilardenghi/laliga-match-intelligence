"""Browser test of the development chart gallery.

The page renders inline fixture data, so it holds on the empty stack. The test
checks the chart structure and its text alternatives, never the fixture values.
"""

import pytest
from playwright.sync_api import Locator, Page, Response, expect

GALLERY_PATH = "/dev/charts"
CHART_COUNT = 9


def _text_content(locator: Locator) -> str:
    return (locator.text_content() or "").strip()


@pytest.mark.empty
def test_chart_gallery_renders_every_chart(page: Page, dashboard_url: str) -> None:
    url = f"{dashboard_url}{GALLERY_PATH}"
    response: Response | None = page.goto(url)

    assert response is not None, f"{url} returned no navigation response"
    assert response.ok, f"{url} answered HTTP {response.status}"

    heading = page.get_by_role("heading", level=1)
    expect(heading).to_have_count(1)
    expect(heading).to_have_text("Chart gallery")

    figures = page.locator("figure")
    expect(figures).to_have_count(CHART_COUNT)

    for index in range(figures.count()):
        figure = figures.nth(index)
        caption = figure.locator("figcaption")
        expect(caption).to_have_count(1)
        caption_text = caption.inner_text().strip()
        assert caption_text, f"figure {index} has an empty figcaption"

        plots = figure.locator('[role="img"]')
        expect(plots).to_have_count(1)
        plot = plots.first
        # A chart labels its marks with two ids: the figcaption that names it and
        # the text that describes it. The element that draws the marks may be an
        # SVG or, once the axis labels move to HTML, a plain container, so the
        # test resolves the ids from the page instead of from inside the SVG.
        labelled_by = plot.get_attribute("aria-labelledby")
        assert labelled_by is not None, f"figure {index} role=img has no aria-labelledby"
        tokens = labelled_by.split()
        assert len(tokens) == 2, (
            f"figure {index} should label with a title and a description, got {labelled_by!r}"
        )
        title_id, desc_id = tokens
        # The title id belongs to the figcaption the reader sees, not to a
        # `<title>` inside the drawing: a chart that names itself inside a
        # scaled SVG could announce one string while the visible heading says
        # another. Requiring the caption makes the two the same element, so the
        # name cannot drift from the heading a reader reads.
        caption_id = caption.get_attribute("id")
        assert caption_id == title_id, (
            f"figure {index} ({caption_text!r}) labels its plot with the title id "
            f"{title_id!r}, but its figcaption id is {caption_id!r}"
        )
        # The caption is also the single element with that id, so the name the
        # plot announces is neither duplicated nor hidden inside the marks.
        title_element = page.locator(f"[id='{title_id}']")
        expect(title_element).to_have_count(1)
        title_text = _text_content(title_element)
        assert title_text == caption_text, (
            f"figure {index} title {title_text!r} does not match figcaption {caption_text!r}"
        )
        desc_element = page.locator(f"[id='{desc_id}']")
        expect(desc_element).to_have_count(1)
        desc_text = _text_content(desc_element)
        assert desc_text, f"figure {index} has an empty description"
        expect(plot).to_have_accessible_name(f"{title_text} {desc_text}")

        details = figure.locator("details")
        expect(details).to_have_count(1)
        expect(details.locator("summary")).to_have_text("Data table")
        table = details.locator("table")
        expect(table).to_have_count(1)
        caption_cell = table.locator("caption")
        expect(caption_cell).to_have_count(1)
        assert _text_content(caption_cell), f"figure {index} data table has an empty caption"

        header_cells = table.locator('thead th[scope="col"]')
        assert header_cells.count() > 0, f"figure {index} data table has no column headers"
        body_rows = table.locator("tbody tr")
        assert body_rows.count() > 0, f"figure {index} data table has no body row"

    form_section = page.locator("section").filter(
        has=page.get_by_role("heading", name="Recent form", exact=True)
    )
    form_texts = form_section.locator('svg[role="img"] text').all_text_contents()
    letters = [text.strip() for text in form_texts if text.strip() in {"W", "D", "L"}]
    assert len(letters) == 5, f"form strip renders {len(letters)} outcome letters"
    assert set(letters) == {"W", "D", "L"}, f"form strip letters are {letters}"

    probability_section = page.locator("section").filter(
        has=page.get_by_role("heading", name="Home win probability", exact=True)
    )
    # The probability bar is an HTML plot: its percentages are HTML text, not
    # text inside a scaled viewBox, so the check reads the plot's own text.
    probability_plot = probability_section.locator('[role="img"]')
    expect(probability_plot).to_have_count(1)
    percentages = [token for token in probability_plot.inner_text().split() if "%" in token]
    assert len(percentages) >= 3, (
        f"probability bar renders {len(percentages)} percentages in its HTML text"
    )


# The zone colour is for lines and segments. Small text filled with it measured
# 3.87:1 against the light background, below the 4.5:1 the design asks for, so
# no chart text may use it as its fill, in either colour scheme.
ZONE_COLOUR_TEXT_FILLS_SCRIPT = """
() => {
  const probe = document.createElement("span");
  probe.style.color = "var(--zone-relegation, #d55e00)";
  document.body.appendChild(probe);
  const zone = getComputedStyle(probe).color;
  probe.remove();
  return Array.from(document.querySelectorAll("figure svg text"))
    .filter((text) => getComputedStyle(text).fill === zone)
    .map((text) => (text.textContent || "").trim());
}
"""


@pytest.mark.empty
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_no_chart_text_uses_the_zone_colour_as_its_fill(
    page: Page, dashboard_url: str, scheme: str
) -> None:
    page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    url = f"{dashboard_url}{GALLERY_PATH}"
    response: Response | None = page.goto(url)

    assert response is not None, f"{url} returned no navigation response"
    assert response.ok, f"{url} answered HTTP {response.status}"
    expect(page.locator("figure")).to_have_count(CHART_COUNT)

    offenders = page.evaluate(ZONE_COLOUR_TEXT_FILLS_SCRIPT)
    assert offenders == [], f"chart text filled with the zone colour in {scheme} mode: {offenders}"


# Measure the contrast of every label that sits on a filled shape. A form-strip
# square and a heatmap cell are both a `<g>` with one rect and one text, so the
# script reads the real computed fill and ink instead of any declared value.
LABEL_CONTRAST_SCRIPT = r"""
() => {
  // Every colour is composited before it is measured. A translucent fill lets
  // the page background through and a translucent ink lets the fill through, so
  // the painted pixels are not the declared colours: reading `rgba(255, 255,
  // 255, 0.1)` as opaque white would report a contrast the reader never sees.
  const parse = (value) => {
    const match = /^rgba?\(([^)]+)\)$/.exec(value);
    if (match === null) {
      return null;
    }
    const parts = match[1]
      .split(/[ ,\/]+/)
      .filter((part) => part.length > 0);
    if (parts.length !== 3 && parts.length !== 4) {
      return null;
    }
    const numbers = parts.map(Number);
    if (numbers.some((number) => !Number.isFinite(number))) {
      return null;
    }
    return {
      red: numbers[0],
      green: numbers[1],
      blue: numbers[2],
      alpha: numbers.length === 4 ? Math.min(1, Math.max(0, numbers[3])) : 1,
    };
  };
  // Porter-Duff `over`: the colour a reader actually sees where `top` covers
  // `bottom`. The page background is opaque in both schemes, so the result is
  // opaque too.
  const over = (top, bottom) => ({
    red: top.red * top.alpha + bottom.red * (1 - top.alpha),
    green: top.green * top.alpha + bottom.green * (1 - top.alpha),
    blue: top.blue * top.alpha + bottom.blue * (1 - top.alpha),
    alpha: 1,
  });
  const luminance = (rgb) => {
    const [red, green, blue] = [rgb.red, rgb.green, rgb.blue].map((channel) => {
      const unit = channel / 255;
      return unit <= 0.03928 ? unit / 12.92 : Math.pow((unit + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
  };
  const ratio = (ink, fill, page) => {
    const inkColour = parse(ink);
    const fillColour = parse(fill);
    const pageColour = parse(page);
    if (inkColour === null || fillColour === null || pageColour === null) {
      return null;
    }
    const paintedFill = over(fillColour, pageColour);
    const paintedInk = over(inkColour, paintedFill);
    const inkLuminance = luminance(paintedInk);
    const fillLuminance = luminance(paintedFill);
    const lighter = Math.max(inkLuminance, fillLuminance);
    const darker = Math.min(inkLuminance, fillLuminance);
    return (lighter + 0.05) / (darker + 0.05);
  };
  const page = getComputedStyle(document.body).backgroundColor;
  const labels = Array.from(document.querySelectorAll("svg g"))
    .filter((group) => {
      const texts = group.querySelectorAll(":scope > text");
      return (
        group.querySelectorAll(":scope > rect").length === 1 &&
        texts.length === 1 &&
        // A centred label sits on its shape. A legend mark and its label are a
        // rect plus a start-aligned text, so they are not a label-on-fill pair.
        getComputedStyle(texts[0]).getPropertyValue("text-anchor") === "middle"
      );
    })
    .map((group) => {
      const rect = group.querySelector(":scope > rect");
      const text = group.querySelector(":scope > text");
      const ink = getComputedStyle(text).getPropertyValue("fill");
      const fill = getComputedStyle(rect).getPropertyValue("fill");
      return {
        text: (text.textContent || "").trim(),
        ink,
        fill,
        ratio: ratio(ink, fill, page),
        inFigure: text.closest("figure") !== null,
      };
    });
  return {
    form: labels.filter((label) => /^[WDL]$/.test(label.text)),
    heatmap: labels.filter((label) => !/^[WDL]$/.test(label.text)),
  };
}
"""

MINIMUM_CONTRAST = 4.5


def _assert_contrast(labels: list[dict[str, object]], context: str) -> None:
    """Fail with the measured pairs when any label is below the WCAG AA ratio."""
    assert labels, f"{context} measured no labels"
    offenders: list[str] = []
    for label in labels:
        ratio = label["ratio"]
        if not isinstance(ratio, (int, float)) or ratio < MINIMUM_CONTRAST:
            offenders.append(f"{label['text']!r} {label['ink']} on {label['fill']} = {ratio}")
    assert offenders == [], f"{context} labels below {MINIMUM_CONTRAST}:1: {', '.join(offenders)}"


@pytest.mark.empty
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_chart_labels_meet_contrast_against_their_fills(
    page: Page, dashboard_url: str, scheme: str
) -> None:
    """Guard every gallery form square and heatmap cell label at 4.5:1."""
    page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    url = f"{dashboard_url}{GALLERY_PATH}"
    response: Response | None = page.goto(url)

    assert response is not None, f"{url} returned no navigation response"
    assert response.ok, f"{url} answered HTTP {response.status}"
    expect(page.locator("figure")).to_have_count(CHART_COUNT)

    measured: dict[str, list[dict[str, object]]] = page.evaluate(LABEL_CONTRAST_SCRIPT)
    _assert_contrast(measured["form"], f"form-strip labels on {GALLERY_PATH} in {scheme} mode")
    _assert_contrast(measured["heatmap"], f"heatmap cell labels on {GALLERY_PATH} in {scheme} mode")


@pytest.mark.loaded
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_compact_form_labels_meet_contrast_against_their_fills(
    page: Page, dashboard_url: str, scheme: str
) -> None:
    """Guard the table's compact form squares at 4.5:1 in both schemes."""
    page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    response: Response | None = page.goto(dashboard_url)

    assert response is not None, f"{dashboard_url} returned no navigation response"
    assert response.ok, f"{dashboard_url} answered HTTP {response.status}"

    measured: dict[str, list[dict[str, object]]] = page.evaluate(LABEL_CONTRAST_SCRIPT)
    compact = [label for label in measured["form"] if not label["inFigure"]]
    assert len(compact) == len(measured["form"]), (
        "the table rendered a form strip inside a figure; the compact variant has none"
    )
    _assert_contrast(compact, f"compact form-strip labels on / in {scheme} mode")
