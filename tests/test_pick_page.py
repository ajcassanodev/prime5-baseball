"""Pick-page redesign (Nov 24, 2026): search, lineup-only toggle,
collapsible game groups, sticky lineup bar with the lock button."""
from conftest import build_week, make_member


def _pick_page(client, con):
    build_week(con)
    m = make_member(con, "Ada")
    client.get(f"/submit/{m['pick_token']}")
    return client.get("/picks")


def test_pick_page_has_search_and_lineup_only(con, client):
    page = _pick_page(client, con)
    assert page.status_code == 200
    assert 'id="pickSearch"' in page.text
    assert 'type="search"' in page.text
    assert 'placeholder="Search players' in page.text
    assert 'id="lineupOnly"' in page.text
    assert "In lineup only" in page.text
    # The filter/search JS is on the page.
    assert "applyFilters" in page.text
    assert "rowVisible" in page.text


def test_pick_page_game_headers_are_collapsible(con, client):
    page = _pick_page(client, con)
    # One toggle per game per section (3 games, batters + pitchers).
    assert page.text.count('data-game-toggle="b-') == 3
    assert page.text.count('data-game-toggle="p-') == 3
    assert 'data-game-rows="b-101"' in page.text
    assert 'data-game-rows="p-103"' in page.text
    # Chevron + expanded state rendered server-side.
    assert 'class="chev"' in page.text
    assert 'aria-expanded="true"' in page.text
    assert 'aria-expanded="false"' in page.text
    # First game open in each section, the other two collapsed.
    assert page.text.count('class="game-rows collapsed"') == 4
    # Visible-row counts in the headers.
    assert "batters" in page.text
    assert "pitchers" in page.text
    assert 'data-count-for="b-101"' in page.text
    assert 'data-count-for="p-102"' in page.text


def test_pick_page_sticky_bar_holds_lock_and_chips(con, client):
    page = _pick_page(client, con)
    assert 'class="sticky-bar"' in page.text
    assert 'id="pickChips"' in page.text
    assert 'id="lineupSummary"' in page.text
    assert "renderChips" in page.text
    # The lock submit lives in the sticky bar — exactly one submit on
    # the page, and the bottom card keeps only the info note.
    assert page.text.count('type="submit"') == 1
    assert 'id="lockBtn"' in page.text
    assert 'id="clearBtn"' in page.text
    assert "Picks lock at each game's first pitch" in page.text
