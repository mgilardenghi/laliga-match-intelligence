"""The browser tests of the table page.

They cover every state of `/` a reader can land on. The empty tier proves the
page names what is missing instead of drawing an empty table. The loaded tier
proves the desktop table in both of its widths, the "race at a glance" cards,
the two projection figures, the definition behind every metric header and the
section navigation; and it measures the phone version of the same page: the
list that replaces the table, the text that list keeps readable, and the one
sideways scroller the page leaves. Every assertion is about structure — a
column key, a sentence built from a stored template, an attribute the server
rendered, a measured width — and never about a value a particular dataset
happens to produce, except the fixed table size of the competition and the
390 px phone viewport.
"""

import re
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Locator, Page, expect

H1_TEXT = "LaLiga Match Intelligence"
TABLE_NAME = re.compile("^La Liga table for")
CAPTION = re.compile(r"^La Liga table for \d{4}-\d{2}, after \d+ results?$")
TEAM_HREF = re.compile(r"^/teams/\d+$")
POSITION_TEXT = re.compile(r"^\d+\s*(?:UCL|Europe|Relegation)?$")
FORM_TITLE = re.compile(r"^Form: [WDL](, [WDL])*$")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")
POINT_COUNT = re.compile(r"^\d+$")

# The zone a phone row carries, read twice: `data-zone` on the row and the name
# in the row's own text, which a screen reader hears with the rank.
ZONE_NAMES = {"ucl": "UCL", "europe": "Europe", "relegation": "Relegation"}
NO_ZONE_NAME = "No qualification place"

# The likelihood line of an opened phone row: the run's likeliest finishes, or
# the sentence a run whose positions all stay under one percent earns instead.
FINISH = r"\d+(?:st|nd|rd|th) (?:<1%|>99%|\d+%)"
LIKELIEST = re.compile(
    rf"^Likeliest finishes: (?:no position reaches 1%\.|{FINISH}(?:, {FINISH}){{0,2}}\.)$"
)

# One club and the chance a ranked sentence prints after it (see `pct0`).
RANKED_ITEM = re.compile(r"^(?P<club>.+?) (?P<chance><1%|>99%|\d+%)$")
# The clause a ranked sentence closes with once it stops listing clubs.
RANKED_TAIL = re.compile(r" (?:Nobody else reaches \d+%\.|\d+ more clubs? reach(?:es)? \d+%\.)$")
# The sure part of the Champions League card, which precedes its contenders.
CL_SURE_PART = re.compile(
    r"^(?P<clubs>.+?) reach(?:es)? the top \d+ in at least \d+% of seasons\. ?"
)
CONTENDING = "Contending: "
# The separator `joinNames` writes before the last of several names.
NAME_JOIN = re.compile(r", | and ")

# The column keys of the header row, in the order the table draws them. The
# default width is the laptop view; `?columns=all` adds the result breakdown and
# the Europe chance. A season without a run carries the standings only.
DEFAULT_COLUMN_KEYS = (
    "pos",
    "club",
    "p",
    "gd",
    "pts",
    "form",
    "xpts",
    "title",
    "ucl",
    "rel",
)
ALL_COLUMN_KEYS = (
    "pos",
    "club",
    "p",
    "w",
    "d",
    "l",
    "gf",
    "ga",
    "gd",
    "pts",
    "form",
    "xpts",
    "title",
    "ucl",
    "europe",
    "rel",
)
CURRENT_COLUMN_KEYS = ("pos", "club", "p", "gd", "pts", "form")

# The labels the columns carry whatever the run stores. The two qualification
# headers are named by the run, so they are matched as a template instead.
FIXED_COLUMN_LABELS = {
    "pos": "#",
    "club": "Club",
    "p": "P",
    "w": "W",
    "d": "D",
    "l": "L",
    "gf": "GF",
    "ga": "GA",
    "gd": "GD",
    "pts": "Pts",
    "form": "Last 5",
    "xpts": "Final pts",
    "title": "Title",
    "rel": "Relegated",
}
QUALIFICATION_LABEL = re.compile(r"^Top (\d+)$")
METRIC_HELP_COLUMNS = ("form", "xpts", "title", "ucl", "europe", "rel")
WHAT_MEANS = re.compile(r"^What .+ means$")

# The cards of "the race at a glance". Their own labels carry the stored
# qualification counts, so the count is read back and compared with the table.
CHAMPIONS_LEAGUE_LABEL = re.compile(r"^Champions League \(top (\d+)\)$")
RELEGATION_LABEL = re.compile(r"^Relegation \(bottom (\d+)\)$")

# A chance is printed by `pct0`: the two near-certain ends are named, everything
# else is a whole percentage. The sentence templates below read a club name, so
# they name any non-empty run of characters instead of a club.
PCT0 = r"(?:<1%|>99%|\d+%)"
NAME_CHANCE = rf".+? {PCT0}"
MORE_CLUBS = r"\d+ more clubs? reach(?:es)?"
TITLE_SENTENCE = re.compile(
    rf"(?:No club wins the title in 1% or more of simulated seasons\.|"
    rf"{NAME_CHANCE}(?:, {NAME_CHANCE}){{0,2}}\. "
    rf"(?:Nobody else reaches 1%\.|{MORE_CLUBS} 1%\.))"
)
CL_SURETY = r".+ reach(?:es)? the top \d+ in at least 95% of seasons\."
CL_CONTENTION = rf"Contending: {NAME_CHANCE}(?:, {NAME_CHANCE}){{0,2}}\."
CL_SENTENCE = re.compile(rf"(?:{CL_SURETY}(?: {CL_CONTENTION})?|{CL_CONTENTION})")
RELEGATION_SENTENCE = re.compile(
    rf"(?:No club goes down in 10% or more of simulated seasons\.|"
    rf"{NAME_CHANCE}(?:, {NAME_CHANCE}){{0,3}}\.(?: {MORE_CLUBS} 10%\.)?)"
)
RELEGATION_NOTE = re.compile(
    rf".+ is \d+(?:st|nd|rd|th) in the table but goes down in {PCT0} of simulated seasons\."
)

# The only hard numbers this module keeps: the competition's size and the phone
# viewport the layout has to fit.
CLUB_COUNT = 20
PHONE_WIDTH = 390
PHONE_HEIGHT = 844
MINIMUM_TEXT_PX = 11
MAXIMUM_TEXT_PX = 18

HEATMAP_CAPTION = "Projected finishing positions"
INTERVALS_CAPTION = "Projected final points"
RUN_NOTE = re.compile(
    r"^Monte Carlo run as of \d{4}-\d{2}-\d{2}: \d+ simulated seasons, "
    r"last complete matchday (?:\d+|—)\."
)

SECTION_NAV = 'nav[aria-label="Sections of this page"]'
PHONE_ROWS = "[data-phone-list] ol > li"

# Every element inside one row summary that renders a text node of its own,
# measured at the size the reader sees. An SVG text node is scaled by its own
# transform, so its declared size is multiplied by the length of the CTM's x
# basis: a form strip drawn inside a shrunken viewBox fails here even though its
# declared size is unchanged, which is the rule the chart gallery applies too.
SUMMARY_TEXT_SCRIPT = """
(root) => {
  const ownText = (element) =>
    Array.from(element.childNodes).some(
      (node) => node.nodeType === Node.TEXT_NODE && (node.textContent || "").trim() !== ""
    );
  return Array.from(root.querySelectorAll("*"))
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


def _standings_rows(page: Page) -> Locator:
    """Return the desktop table's body rows, without asking the accessibility tree.

    Below the phone breakpoint the desktop table is hidden, and a hidden element
    is not in the accessibility tree, so a count read through the table's role
    would be zero on a phone even though the page rendered every row.
    """
    return page.locator("[data-table-desktop] tbody > tr")


def _unsimulated_season(page: Page, dashboard_url: str) -> str:
    """Return a season that has standings rows and no stored run.

    The loaded tier stores one simulation, for the current season only, so an
    older season with results must offer the command; the season is found by
    reading the pages themselves, never by assuming which one the data left
    unsimulated. Finding none is a bug in the stack, not a missing assertion, so
    the search fails instead of returning a placeholder. The browser is left on
    the page it read last.
    """
    page.goto(dashboard_url)
    codes = _season_codes(page)
    assert codes, "the selector offered no season"
    for code in sorted(codes):
        page.goto(f"{dashboard_url}/?season={code}")
        has_rows = _standings_rows(page).count() > 0
        asks = page.get_by_role("heading", name="No simulation for this season yet").count() == 1
        if has_rows and asks:
            return code
    raise AssertionError("no season with results shows the make simulate state")


def _column_keys(table: Locator) -> list[str]:
    """Return the `data-col` keys of the header row, in DOM order."""
    headers = table.locator("thead th[data-col]")
    keys: list[str] = []
    for index in range(headers.count()):
        key = headers.nth(index).get_attribute("data-col")
        assert key is not None, f"header {index} carries no data-col key"
        keys.append(key)
    return keys


def _column_labels(table: Locator) -> dict[str, str]:
    """Return each header's label, read from its own `data-col-label` span.

    The cell's own text also holds the ⓘ glyph of the definition control, so the
    label is the span and not the cell.
    """
    labels: dict[str, str] = {}
    headers = table.locator("thead th[data-col]")
    for index in range(headers.count()):
        header = headers.nth(index)
        key = header.get_attribute("data-col") or ""
        spans = header.locator("span[data-col-label]")
        assert spans.count() == 1, f"the {key} header holds {spans.count()} label spans"
        labels[key] = spans.first.inner_text().strip()
    return labels


def _sentence(text: str) -> str:
    """Return `text` with its whitespace collapsed, as the one line a reader sees."""
    return " ".join(text.split())


def _paragraphs(card: Locator) -> list[str]:
    """Return a card's sentences, in reading order."""
    return [_sentence(text) for text in card.locator("p").all_inner_texts()]


def _card_label(card: Locator) -> str:
    """Return a card's own label as the server rendered it.

    The stylesheet uppercases the label for the eye, and the rendered text is
    then `TITLE`: the label itself is the raw text content, so the templates
    below read that instead of one of its presentations.
    """
    return _sentence(card.locator("h3").text_content() or "")


def _glance_card(page: Page, label: re.Pattern[str]) -> Locator:
    """Return the "race at a glance" card whose own label matches `label`."""
    cards = page.locator("#race article")
    for index in range(cards.count()):
        card = cards.nth(index)
        if label.fullmatch(_card_label(card)):
            return card
    raise AssertionError(f"no glance card is labelled {label.pattern!r}")


def _captured(pattern: re.Pattern[str], text: str, what: str) -> str:
    """Return the group `pattern` captures from `text`, or fail naming both."""
    match = pattern.fullmatch(text)
    assert match is not None, f"{what} reads {text!r}, which is not {pattern.pattern!r}"
    return match.group(1)


def _document_width(page: Page) -> int:
    """Return the width the document needs, as both the body and the root report it."""
    body = page.evaluate("() => document.body.scrollWidth")
    root = page.evaluate("() => document.documentElement.scrollWidth")
    return max(body, root)


def _chance_by_club(table: Locator, key: str) -> dict[str, float]:
    """Return each club's chance in the column `key`, as the table prints it.

    The column is found by the header's own key, so the reading follows the
    table it measures instead of a column position written twice. A club the run
    does not mention keeps an em dash in the cell, and it is left out rather than
    read as a zero the run never produced.
    """
    keys = _column_keys(table)
    index = keys.index(key) - 1  # the position cell is a row header, not a cell
    chances: dict[str, float] = {}
    rows = table.locator(":scope > tbody > tr")
    for position in range(rows.count()):
        row = rows.nth(position)
        club = row.locator("td a").first.inner_text().strip()
        printed = row.locator(":scope > td").nth(index).inner_text()
        match = PERCENT.search(printed)
        if match is not None:
            chances[club] = float(match.group(1))
    return chances


def _ranked(sentence: str) -> list[tuple[str, str]]:
    """Return the clubs a ranked sentence names, with the chance printed for each."""
    body = RANKED_TAIL.sub("", sentence).removesuffix(".")
    named: list[tuple[str, str]] = []
    for part in body.split(", "):
        match = RANKED_ITEM.match(part)
        assert match is not None, f"{part!r} is not a club and a chance"
        named.append((match.group("club"), match.group("chance")))
    return named


def _chance_band(
    chances: dict[str, float], minimum: float, maximum: float | None
) -> tuple[list[str], set[str]]:
    """Split the clubs at a chance threshold, and say which side is uncertain.

    The table prints one decimal place, so a club whose chance prints within
    half a tenth of a threshold may sit either side of it. Those clubs are
    ambiguous, and a test that knows only the printed number must not claim to
    know where they belong. The certain ones come back in the order the cards
    rank them: chance descending, then name.
    """
    certain: list[str] = []
    ambiguous: set[str] = set()
    for club, chance in chances.items():
        edges = [margin for margin in (minimum, maximum) if margin is not None]
        if any(abs(chance - edge) <= 0.05 for edge in edges):
            ambiguous.add(club)
        elif chance > minimum and (maximum is None or chance < maximum):
            certain.append(club)
    certain.sort(key=lambda club: (-chances[club], club))
    return certain, ambiguous


def _printed_chance(written: str) -> tuple[float, float]:
    """Return a written chance as the interval of percentages it stands for.

    A card writes a whole percentage and names both ends (`<1%`, `>99%`), so each
    written chance covers a range rather than one number, and a check against it
    has to allow for that range.
    """
    if written == "<1%":
        return (0.0, 1.0)
    if written == ">99%":
        return (99.0, 100.0)
    value = float(written.removesuffix("%"))
    return (value - 0.5, value + 0.5)


def _sure_clubs(sentence: str) -> list[str]:
    """Return the clubs a Champions League card names as all but certain."""
    match = CL_SURE_PART.match(sentence)
    if match is None:
        return []
    return NAME_JOIN.split(match.group("clubs"))


def _assert_shortlist(
    named: list[tuple[str, str]],
    chances: dict[str, float],
    minimum: float,
    maximum: float | None,
    cap: int,
    what: str,
) -> None:
    """Assert a card's shortlist against the chances the table prints.

    This is the check the sentence templates cannot make on their own: it ties
    the clubs a card names, their order and their number to the run the table
    shows, so a threshold moved by a hundredth or a list drawn in the wrong
    order fails here.
    """
    certain, ambiguous = _chance_band(chances, minimum, maximum)
    for club, written in named:
        assert club in chances, f"{what} names {club!r}, which has no {what} chance"
        assert club in certain or club in ambiguous, (
            f"{what} names {club!r} at {chances[club]}%, outside the band its sentence claims"
        )
        # The written chance is the table's chance at the card's own precision, so
        # a card that names the right club with the wrong number fails here.
        low, high = _printed_chance(written)
        assert low - 0.05 <= chances[club] <= high + 0.05, (
            f"{what} writes {club!r} at {written} where the table shows {chances[club]}%"
        )
    written_chances = [chances[club] for club, _ in named]
    assert written_chances == sorted(written_chances, reverse=True), (
        f"{what} lists {[club for club, _ in named]} in an order the table does not "
        f"support: {written_chances} against {chances}"
    )
    # A card names as many clubs as its band holds, up to its cap: it may not
    # quietly drop a club that qualifies, however near a threshold that club is.
    available = len(certain) + len(ambiguous)
    assert len(named) == min(available, cap), (
        f"{what} names {len(named)} clubs where {min(available, cap)} of the "
        f"{available} clubs in its band fit"
    )
    if ambiguous:
        return
    expected = certain[:cap]
    assert [club for club, _ in named] == expected, (
        f"{what} names {[club for club, _ in named]} where the run puts "
        f"{[club for club in expected]}"
    )


def _assert_fits(locator: Locator, what: str) -> None:
    """Fail when `locator` needs a sideways scroll to show everything it holds."""
    box = locator.evaluate(
        "(element) => ({ scrollWidth: element.scrollWidth, clientWidth: element.clientWidth })"
    )
    assert box["scrollWidth"] <= box["clientWidth"] + 1, (
        f"{what} is {box['scrollWidth']}px wide in a {box['clientWidth']}px container, "
        "so a phone hides part of it behind a sideways scroll"
    )


def _open_all_columns(page: Page, dashboard_url: str) -> str:
    """Open the widened table from its own switch and return the season it showed."""
    page.goto(dashboard_url)
    link = page.get_by_role("link", name="All columns")
    expect(link).to_have_count(1)
    href = link.get_attribute("href")
    assert href is not None, "the column switch rendered no href"
    query = parse_qs(urlsplit(href).query)
    assert query.get("columns") == ["all"], f"the All columns link points at {href!r}"
    season = (query.get("season") or [""])[0]
    assert season, f"the All columns link drops the season: {href!r}"
    page.goto(f"{dashboard_url}{href}")
    return season


def _assert_section_nav(page: Page, labels: list[str]) -> None:
    """Assert the page's section nav lists `labels` and every anchor resolves."""
    nav = page.locator(SECTION_NAV)
    expect(nav).to_have_count(1)
    links = nav.locator('a[href^="#"]')
    expect(links).to_have_count(len(labels))
    texts = [_sentence(links.nth(index).inner_text()) for index in range(links.count())]
    assert texts == labels, f"the section nav lists {texts}"

    for index in range(links.count()):
        href = links.nth(index).get_attribute("href")
        assert href is not None and len(href) > 1, f"section link {index} has no fragment"
        # One target, so the link cannot land on a duplicate id or on nothing.
        expect(page.locator(href)).to_have_count(1)


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

    headers = table.locator(":scope > thead > tr > th")
    scopes = [headers.nth(index).get_attribute("scope") for index in range(headers.count())]
    assert scopes == ["col"] * len(scopes), f"the column headers declare scopes {scopes}"

    rows = table.locator(":scope > tbody > tr")
    expect(rows).to_have_count(CLUB_COUNT)

    # Every row, not just the first ones: a form strip or a team link that went
    # missing halfway down the table is exactly the regression this guards.
    for index in range(rows.count()):
        row = rows.nth(index)
        position = row.locator(':scope > th[scope="row"]')
        expect(position).to_have_count(1)
        expect(position).to_have_text(POSITION_TEXT)

        team_link = row.locator(":scope > td a").first
        expect(team_link).to_have_attribute("href", TEAM_HREF)

        expect(row.locator("svg title")).to_have_text(FORM_TITLE)


@pytest.mark.loaded
def test_default_columns_and_figures_render(page: Page, dashboard_url: str) -> None:
    """Guard the projection: ten columns in the default width and two figures."""
    page.goto(dashboard_url)

    table = _main_table(page)
    keys = _column_keys(table)
    assert keys == list(DEFAULT_COLUMN_KEYS), f"the default table head is {keys}"

    labels = _column_labels(table)
    for key, label in FIXED_COLUMN_LABELS.items():
        if key in labels:
            assert labels[key] == label, f"the {key} header is labelled {labels[key]!r}"
    # The Champions League places come from the run, never from a literal.
    _captured(QUALIFICATION_LABEL, labels["ucl"], "the Champions League header")

    for caption in (HEATMAP_CAPTION, INTERVALS_CAPTION):
        figure = _figure(page, caption)
        expect(figure).to_have_count(1)
        expect(figure.locator("figcaption")).to_have_count(1)
        expect(figure.locator('svg[role="img"]')).to_have_count(1)
        expect(figure.locator("details summary")).to_have_text("Data table")
        # The run behind the figure is named in the visible note under the chart.
        expect(figure.locator(":scope > p")).to_contain_text(RUN_NOTE)


@pytest.mark.loaded
def test_all_columns_view_keeps_the_full_contract(page: Page, dashboard_url: str) -> None:
    """Guard the widened table: sixteen columns, and a switch back to the season."""
    season = _open_all_columns(page, dashboard_url)

    table = _main_table(page)
    keys = _column_keys(table)
    assert keys == list(ALL_COLUMN_KEYS), f"the widened table head is {keys}"

    labels = _column_labels(table)
    _captured(QUALIFICATION_LABEL, labels["ucl"], "the Champions League header")
    _captured(QUALIFICATION_LABEL, labels["europe"], "the Europe header")

    fewer = page.get_by_role("link", name="Fewer columns")
    expect(fewer).to_have_count(1)
    href = fewer.get_attribute("href")
    assert href is not None, "the column switch rendered no href"
    query = parse_qs(urlsplit(href).query)
    assert "columns" not in query, f"the Fewer columns link keeps the widened view: {href!r}"
    assert query.get("season") == [season], f"the Fewer columns link loses the season: {href!r}"


@pytest.mark.loaded
def test_race_at_a_glance_is_built_from_the_run(page: Page, dashboard_url: str) -> None:
    """Guard the glance cards: three labels, three sentences, one shared count."""
    page.goto(dashboard_url)

    section = page.locator("#race")
    expect(section).to_have_count(1)
    expect(section.get_by_role("heading", level=2)).to_have_text("The race at a glance")

    cards = section.locator("article")
    expect(cards).to_have_count(3)
    title_card = _glance_card(page, re.compile("^Title$"))
    ucl_card = _glance_card(page, CHAMPIONS_LEAGUE_LABEL)
    relegation_card = _glance_card(page, RELEGATION_LABEL)

    title_sentences = _paragraphs(title_card)
    assert len(title_sentences) == 1, f"the title card renders {title_sentences}"
    assert TITLE_SENTENCE.fullmatch(title_sentences[0]), (
        f"the title sentence reads {title_sentences[0]!r}"
    )

    # A run stores one position per club per season, so at least one club is
    # certain of a place or still playing for one: the card always has a sentence.
    ucl_sentences = _paragraphs(ucl_card)
    assert len(ucl_sentences) == 1, f"the Champions League card renders {ucl_sentences}"
    assert CL_SENTENCE.fullmatch(ucl_sentences[0]), (
        f"the Champions League sentence reads {ucl_sentences[0]!r}"
    )

    relegation_sentences = _paragraphs(relegation_card)
    assert len(relegation_sentences) in (1, 2), (
        f"the relegation card renders {relegation_sentences}"
    )
    assert RELEGATION_SENTENCE.fullmatch(relegation_sentences[0]), (
        f"the relegation sentence reads {relegation_sentences[0]!r}"
    )
    for note in relegation_sentences[1:]:
        assert RELEGATION_NOTE.fullmatch(note), f"the relegation note reads {note!r}"

    # The card names the places the run stores, and the table names the same
    # number in its own header: one count for one qualification rule.
    card_places = _captured(
        CHAMPIONS_LEAGUE_LABEL,
        _card_label(ucl_card),
        "the Champions League card",
    )
    header_places = _captured(
        QUALIFICATION_LABEL, _column_labels(_main_table(page))["ucl"], "the Champions League header"
    )
    assert card_places == header_places, (
        f"the card names the top {card_places} and the header the top {header_places}"
    )

    # The bars are decoration of the sentences above them: the two cards that
    # list clubs draw one, the relegation card draws none.
    for card, what in ((title_card, "title"), (ucl_card, "Champions League")):
        bar = card.locator('[aria-hidden="true"]')
        expect(bar).to_have_count(1)
        assert bar.locator("span").count() >= 1, f"the {what} bar draws no segment"
    expect(relegation_card.locator('[aria-hidden="true"]')).to_have_count(0)


@pytest.mark.loaded
def test_glance_shortlists_match_the_table_chances(page: Page, dashboard_url: str) -> None:
    """Guard the glance data: the cards name the clubs the run's chances rank.

    The shape test above reads the sentences; this one reads the run through the
    table's own chance columns and checks that each card agrees with it: the same
    clubs, in the same order, out of the same band of chances. A threshold moved
    by a hundredth, a list sorted the wrong way or a club left out fails here.
    """
    page.goto(dashboard_url)
    table = _main_table(page)
    title = _sentence(_paragraphs(_glance_card(page, re.compile("^Title$")))[0])
    ucl = _sentence(_paragraphs(_glance_card(page, CHAMPIONS_LEAGUE_LABEL))[0])
    relegation = _sentence(_paragraphs(_glance_card(page, RELEGATION_LABEL))[0])

    # The title card: every club at 1% or more, the top three named.
    title_named = [] if title.startswith("No club wins") else _ranked(title)
    _assert_shortlist(
        title_named,
        _chance_by_club(table, "title"),
        1.0,
        None,
        3,
        "the title card",
    )

    # The Champions League card: the sure clubs first, then the contenders.
    sure = CL_SURE_PART.sub("", ucl)
    contenders = sure[len(CONTENDING) :] if sure.startswith(CONTENDING) else ""
    _assert_shortlist(
        [] if contenders == "" else _ranked(contenders),
        _chance_by_club(table, "ucl"),
        5.0,
        95.0,
        3,
        "the Champions League card",
    )

    # The clubs it calls all but certain are the clubs the table's Top column
    # puts there, in the table's order: the sentence may not invent a certainty
    # the run does not store, and may not leave a certain club out.
    ucl_chances = _chance_by_club(table, "ucl")
    sure_clubs = _sure_clubs(ucl)
    for club in sure_clubs:
        assert club in ucl_chances, f"the sure clause names {club!r}, which has no chance"
        assert ucl_chances[club] >= 94.5, f"the sure clause names {club!r} at {ucl_chances[club]}%"
    certain_sure = [club for club, chance in ucl_chances.items() if chance >= 95.05]
    assert sure_clubs == [club for club in certain_sure if club in sure_clubs], (
        f"the sure clause names {sure_clubs} where the run's certain clubs are "
        f"{certain_sure}, in that order"
    )
    assert set(certain_sure) <= set(sure_clubs), (
        f"the sure clause leaves out {sorted(set(certain_sure) - set(sure_clubs))}"
    )

    # The relegation card: every club at 10% or more, the top four named.
    relegation_named = [] if relegation.startswith("No club goes down") else _ranked(relegation)
    _assert_shortlist(
        relegation_named,
        _chance_by_club(table, "rel"),
        10.0,
        None,
        4,
        "the relegation card",
    )


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

    # The middle zone is its own band: a table that jumped from UCL straight to
    # relegation would still pass the two assertions above.
    europe = _main_table(page).locator(':scope > tbody > tr[data-zone="europe"]')
    expect(europe.first).to_be_visible()
    expect(europe.first.locator(':scope > th[scope="row"]')).to_contain_text("Europe")

    # The tint marks qualification places, not every row, so at least one middle
    # row carries no zone attribute at all.
    untinted = _main_table(page).locator(":scope > tbody > tr:not([data-zone])")
    expect(untinted.first).to_be_visible()


@pytest.mark.loaded
def test_metric_help_operable_in_headers(page: Page, dashboard_url: str) -> None:
    """Guard the header definitions: one keyboard control each, opening in place."""
    # Only the widened view carries the Europe column, so this runs on it.
    _open_all_columns(page, dashboard_url)
    table = _main_table(page)

    scroller = page.locator("[data-table-desktop]")
    expect(scroller).to_have_count(1)
    width_before = scroller.evaluate("(element) => element.scrollWidth")

    for key in METRIC_HELP_COLUMNS:
        header = table.locator(f'thead th[data-col="{key}"]')
        expect(header).to_have_count(1)

        summaries = header.locator("summary")
        expect(summaries).to_have_count(1)
        summary = summaries.first
        expect(summary).to_have_accessible_name(WHAT_MEANS)

        summary.focus()
        page.keyboard.press("Enter")
        panel = header.locator("details[open] > div")
        expect(panel).to_have_count(1)
        expect(panel).to_be_visible()

        summary.focus()
        page.keyboard.press("Enter")
        expect(header.locator("details[open]")).to_have_count(0)

    # An open definition is taken out of the flow: no column grew, so the table
    # has nothing new to scroll sideways.
    width_after = scroller.evaluate("(element) => element.scrollWidth")
    assert width_after == width_before, (
        f"the table scroller is {width_after}px wide after the definitions were opened "
        f"and closed, {width_before}px before them"
    )


@pytest.mark.loaded
def test_section_nav_anchors_resolve(page: Page, dashboard_url: str) -> None:
    """Guard the section nav: it lists what rendered, and every anchor lands."""
    page.goto(dashboard_url)
    _assert_section_nav(page, ["Race at a glance", "Table", "Projection", "How to read"])

    unsimulated = _unsimulated_season(page, dashboard_url)
    page.goto(f"{dashboard_url}/?season={unsimulated}")
    _assert_section_nav(page, ["Table", "Projection"])


@pytest.mark.loaded
def test_a_season_without_a_simulation_offers_make_simulate(page: Page, dashboard_url: str) -> None:
    """Guard the unsimulated season: the real table stays, the projection asks."""
    unsimulated = _unsimulated_season(page, dashboard_url)

    page.goto(f"{dashboard_url}/?season={unsimulated}")

    expect(page.get_by_role("heading", level=1)).to_have_text(H1_TEXT)
    table = _main_table(page)
    expect(table).to_have_count(1)
    expect(table.locator(":scope > tbody > tr").first).to_be_visible()

    expect(page.locator("code", has_text="make simulate")).to_have_count(1)
    expect(page.get_by_role("heading", name="No simulation for this season yet")).to_have_count(1)

    # Neither projection chart may survive the missing run, and the table keeps
    # only its current-standings columns.
    expect(_figure(page, HEATMAP_CAPTION)).to_have_count(0)
    expect(_figure(page, INTERVALS_CAPTION)).to_have_count(0)
    expect(table.locator(":scope > tbody > tr[data-zone]")).to_have_count(0)
    keys = _column_keys(table)
    assert keys == list(CURRENT_COLUMN_KEYS), f"an unsimulated season shows the columns {keys}"

    # Without a run there is no race to glance at, and the nav offers no link to
    # a section that does not exist.
    expect(page.locator("#race")).to_have_count(0)
    expect(page.locator(f'{SECTION_NAV} a[href="#race"]')).to_have_count(0)


@pytest.mark.loaded
@pytest.mark.parametrize("simulated", [True, False], ids=["with-run", "without-run"])
def test_phone_layout_fits_390(page: Page, dashboard_url: str, simulated: bool) -> None:
    """Guard the phone table: the list replaces the table and nothing overflows."""
    page.set_viewport_size({"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
    if simulated:
        page.goto(dashboard_url)
    else:
        unsimulated = _unsimulated_season(page, dashboard_url)
        page.goto(f"{dashboard_url}/?season={unsimulated}")

    width = _document_width(page)
    assert width <= PHONE_WIDTH, f"the phone page is {width}px wide in a {PHONE_WIDTH}px viewport"

    expect(page.locator("[data-table-desktop]")).to_be_hidden()
    expect(page.locator("[data-phone-list]")).to_be_visible()

    # The header row names the points column for a sighted reader and holds no
    # control of its own: every control is a row or a definition.
    header = page.locator("[data-phone-header]")
    expect(header).to_be_visible()
    expect(header).to_contain_text("Pts")
    expect(header.locator("a, button, summary, select, input, details")).to_have_count(0)

    rows = page.locator(PHONE_ROWS)
    expect(rows).to_have_count(CLUB_COUNT)
    for index in range(rows.count()):
        points = rows.nth(index).locator('summary [data-cell="pts"]')
        expect(points).to_have_count(1)
        text = points.inner_text().strip()
        assert POINT_COUNT.fullmatch(text), f"row {index} shows {text!r} points"

        # The summary carries the four facts the header names, and the form
        # strip that takes the place of a form column.
        summary = rows.nth(index).locator("summary").first
        for cell in ("pos", "club", "pts"):
            expect(summary.locator(f'[data-cell="{cell}"]')).to_have_count(1)
        # A cell that exists but holds nothing would pass a count alone.
        for cell in ("pos", "club", "pts"):
            text = summary.locator(f'[data-cell="{cell}"]').inner_text().strip()
            assert text != "", f"row {index} renders an empty {cell} cell"
        expect(summary.locator("svg title")).to_have_text(FORM_TITLE)

        # The zone is colour and a name: the row's own attribute, and the name a
        # screen reader hears with the rank it belongs to.
        zone = rows.nth(index).get_attribute("data-zone")
        expected = NO_ZONE_NAME if zone is None else ZONE_NAMES[zone]
        spoken = _sentence(summary.text_content() or "")
        assert expected in spoken, f"row {index} carries the zone {zone!r} but reads {spoken!r}"

    # Nothing but the hinted grid may hide data behind a sideways scroll.
    _assert_fits(page.locator("[data-phone-list]"), "the phone list")
    if simulated:
        cards = page.locator("#race article")
        assert cards.count() >= 1, "a season with a run renders no glance card"
        for index in range(cards.count()):
            _assert_fits(cards.nth(index), f"glance card {index}")

        figure = _figure(page, INTERVALS_CAPTION)
        expect(figure).to_have_count(1)
        holder = figure.locator('svg[role="img"]').locator("xpath=..")
        expect(holder).to_have_count(1)
        _assert_fits(holder, "the projected-points drawing")

        expect(page.get_by_text("This grid is wider than a phone", exact=False)).to_be_visible()
        region = page.locator("[data-heatmap-scroll]")
        expect(region).to_have_count(1)
        expect(region).to_have_attribute("tabindex", "0")
        expect(region).to_have_accessible_name(re.compile(r"\S"))
        box = region.evaluate(
            "(element) => ({ scrollWidth: element.scrollWidth, clientWidth: element.clientWidth })"
        )
        assert box["scrollWidth"] > box["clientWidth"], (
            "the heatmap scroll region does not need to scroll, so a phone "
            "cannot reach the positions it claims to hold"
        )
        region.focus()
        focused = region.evaluate("(element) => element === document.activeElement")
        assert focused, "the heatmap scroll region did not take focus"
        page.keyboard.press("ArrowRight")
        # The offset lands through the browser's own scrolling, so it is read
        # back by polling instead of one tick after the key event.
        scrolled = 0
        for _ in range(10):
            scrolled = region.evaluate("(element) => element.scrollLeft")
            if scrolled > 0:
                break
            page.wait_for_timeout(100)
        assert scrolled > 0, "ArrowRight does not scroll the focused heatmap region"

    # Everything a reader can open, opened: the page still fits the phone.
    for index in range(rows.count()):
        row = rows.nth(index)
        row.locator("summary").first.click()
        expect(row.locator('a[href^="/teams/"]')).to_be_visible()
        for term in row.locator("dl dt").all():
            summary = term.locator("summary")
            expect(summary).to_have_count(1)
            summary.click()
            expect(term.locator("details[open] > div")).to_be_visible()

    width = _document_width(page)
    assert width <= PHONE_WIDTH, (
        f"the phone page is {width}px wide with every row and definition open"
    )
    _assert_fits(page.locator("[data-phone-list]"), "the phone list with every row open")

    # Each panel ends at the club's own page, and each metric it lists carries
    # its definition.
    for index in range(rows.count()):
        row = rows.nth(index)
        link = row.locator('a[href^="/teams/"]')
        expect(link).to_have_count(1)
        expect(link).to_have_attribute("href", TEAM_HREF)

        # With a run, the panel repeats the run's likeliest finish for this club:
        # the number a phone would otherwise have to scroll the heatmap for.
        lines = [_sentence(text) for text in row.locator("p").all_inner_texts()]
        finishes = [line for line in lines if line.startswith("Likeliest finishes:")]
        if not simulated:
            assert finishes == [], f"row {index} offers a likelihood without a run"
        else:
            assert len(finishes) == 1, f"row {index} renders {finishes}"
            assert LIKELIEST.fullmatch(finishes[0]), f"row {index} says {finishes[0]!r}"

        terms = row.locator("dl dt")
        if not simulated:
            expect(terms).to_have_count(0)
            continue
        expect(terms).to_have_count(5)
        for term in range(terms.count()):
            expect(terms.nth(term).locator("summary")).to_have_count(1)


@pytest.mark.loaded
@pytest.mark.parametrize("width", [600, 768, 900, 959, 960, 1024, 1280])
def test_the_heatmap_hint_shows_exactly_where_the_grid_scrolls(
    page: Page, dashboard_url: str, width: int
) -> None:
    """A scroll region has its hint, and a region that fits does not need one.

    The hint is dropped from 60rem (960 px). Just above that width the grid must
    fit its region, or a reader would scroll sideways with no word about it; below
    it the hint stays while the grid still has to scroll.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(dashboard_url, wait_until="networkidle")
    # A late font swap can move the grid by a pixel at the tightest width.
    page.evaluate("() => document.fonts.ready.then(() => true)")
    region = page.locator("[data-heatmap-scroll]")
    expect(region).to_have_count(1)
    scrolls = region.evaluate("(element) => element.scrollWidth > element.clientWidth + 1")
    hint = page.get_by_text("This grid is wider than a phone", exact=False)
    if scrolls:
        expect(hint).to_be_visible()
    else:
        assert width >= 900, f"the heatmap fits at {width} px, narrower than expected"
    if width >= 960:
        assert not scrolls, f"the heatmap scrolls at {width} px, where its hint is hidden"
        expect(hint).to_be_hidden()


@pytest.mark.loaded
def test_phone_list_text_sizes(page: Page, dashboard_url: str) -> None:
    """Guard the phone row type: every summary text stays on the readable scale."""
    page.set_viewport_size({"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
    page.goto(dashboard_url)

    summaries = page.locator(f"{PHONE_ROWS} > details > summary")
    expect(summaries).to_have_count(CLUB_COUNT)

    offenders: list[str] = []
    for index in range(summaries.count()):
        texts = summaries.nth(index).evaluate(SUMMARY_TEXT_SCRIPT)
        assert texts, f"row {index} renders no text to measure"
        offenders += [
            f"row {index}: {text['text']!r} at {text['size']:.2f}px"
            for text in texts
            if text["size"] < MINIMUM_TEXT_PX or text["size"] > MAXIMUM_TEXT_PX
        ]
    assert offenders == [], (
        f"the phone list renders text outside {MINIMUM_TEXT_PX}-{MAXIMUM_TEXT_PX}px: "
        f"{', '.join(offenders)}"
    )


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
