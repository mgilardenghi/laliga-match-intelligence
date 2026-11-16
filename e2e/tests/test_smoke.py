"""The browser smoke test of the dashboard.

It holds in both tiers: the empty stack proves the page renders without data,
and the loaded stack proves the same page still renders with data in it.
"""

import pytest
from playwright.sync_api import Page, Response, expect


@pytest.mark.empty
@pytest.mark.loaded
def test_home_renders(page: Page, dashboard_url: str) -> None:
    response: Response | None = page.goto(dashboard_url)

    assert response is not None, f"{dashboard_url} returned no navigation response"
    assert response.ok, f"{dashboard_url} answered HTTP {response.status}"
    expect(page.get_by_role("heading", level=1)).to_have_text("LaLiga Match Intelligence")
    expect(page.locator("body")).not_to_contain_text("API unreachable")


@pytest.mark.empty
@pytest.mark.loaded
def test_home_reports_the_api_as_online(page: Page, dashboard_url: str) -> None:
    page.goto(dashboard_url)
    status = page.locator("p", has_text="API status:")

    expect(status).to_contain_text("online")
    expect(status).not_to_contain_text("offline")
