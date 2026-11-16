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

        svg = figure.locator('svg[role="img"]')
        expect(svg).to_have_count(1)
        # The chart components label the SVG with both its title and its
        # description, so the accessible name is exactly the two texts in that
        # order. Anything else means the labelledby wiring drifted.
        labelled_by = svg.get_attribute("aria-labelledby")
        assert labelled_by is not None, f"figure {index} SVG has no aria-labelledby"
        tokens = labelled_by.split()
        assert len(tokens) == 2, (
            f"figure {index} should label with a title and a description, got {labelled_by!r}"
        )
        title_element = svg.locator(f"[id='{tokens[0]}']")
        desc_element = svg.locator(f"[id='{tokens[1]}']")
        expect(title_element).to_have_count(1)
        expect(desc_element).to_have_count(1)
        assert title_element.evaluate("(element) => element.tagName.toLowerCase()") == "title"
        assert desc_element.evaluate("(element) => element.tagName.toLowerCase()") == "desc"
        title_text = _text_content(title_element)
        desc_text = _text_content(desc_element)
        assert title_text == caption_text, (
            f"figure {index} SVG title {title_text!r} does not match figcaption {caption_text!r}"
        )
        assert desc_text, f"figure {index} SVG has an empty description"
        expect(svg).to_have_accessible_name(f"{title_text} {desc_text}")

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
    probability_texts = probability_section.locator('svg[role="img"] text').all_text_contents()
    percentages = [text for text in probability_texts if "%" in text]
    assert len(percentages) >= 3, f"probability bar renders {len(percentages)} percentages"
