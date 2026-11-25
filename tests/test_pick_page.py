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


# ---------------------------------------------------------------- pick-help (Nov 25, 2026)

def _seed_form_and_weather(con):
    from app import db as dbmod
    form = [
        {"mlb_id": 11, "is_pitcher": 0, "hits_last_7d": 9,
         "ab_last_7d": 24, "streak": 5, "season_avg": 0.280,
         "hit_prob_pct": 74},
        {"mlb_id": 12, "is_pitcher": 0, "hits_last_7d": 9,
         "ab_last_7d": 22, "streak": 5, "season_avg": 0.275,
         "hit_prob_pct": 74},
        {"mlb_id": 14, "is_pitcher": 0, "hits_last_7d": 7,
         "ab_last_7d": 20, "streak": 3, "season_avg": 0.260,
         "hit_prob_pct": 68},
        {"mlb_id": 21, "is_pitcher": 0, "hits_last_7d": 7,
         "ab_last_7d": 21, "streak": 3, "season_avg": 0.251,
         "hit_prob_pct": 68},
        {"mlb_id": 24, "is_pitcher": 0, "hits_last_7d": 6,
         "ab_last_7d": 18, "streak": 6, "season_avg": 0.270,
         "hit_prob_pct": 71},
        {"mlb_id": 203, "is_pitcher": 1, "last_start_pitches": 97,
         "last_start_k": 8, "five_k_prob_pct": 71},
        {"mlb_id": 206, "is_pitcher": 1, "last_start_pitches": 91,
         "last_start_k": 6, "five_k_prob_pct": 62},
    ]
    for row in form:
        dbmod.upsert_player_form(con, 2026, 1, row)
    dbmod.upsert_game_weather(con, 101, {
        "venue": "Fenway Park", "temp_f": 68,
        "conditions": "Partly cloudy", "precip_prob": 10,
        "wind_mph": 11, "wind_compass": "NW", "delay_risk": False})
    dbmod.upsert_game_weather(con, 102, {
        "venue": "Yankee Stadium", "temp_f": 64, "conditions": "Rain",
        "precip_prob": 55, "wind_mph": 8, "wind_compass": "E",
        "delay_risk": True})
    con.commit()


def test_pick_page_shows_form_lines_probabilities_and_guide(con, client):
    # Build the week first (the form rows FK to it), then seed form
    # + weather, then load the page. with_stats=False keeps the
    # Friday pre-game, so the rows show probabilities instead of
    # final points — the probabilities are a pre-game aid.
    build_week(con, with_stats=False)
    m = make_member(con, "Ada")
    client.get(f"/submit/{m['pick_token']}")
    _seed_form_and_weather(con)
    page = client.get("/picks")
    assert page.status_code == 200
    # Form lines under the player names.
    assert "9 hits last 7 days · 5-game hit streak" in page.text
    assert "6 hits last 7 days · 6-game hit streak" in page.text
    assert "Last start: 97 pitches · 8 K" in page.text
    # Friday probabilities on the right of the rows.
    assert "74%" in page.text and "hit Friday" in page.text
    assert "71%" in page.text and "5+ K Fri" in page.text
    # The Form guide at the top, open by default.
    assert "Form guide" in page.text
    assert 'class="form-guide" open' in page.text
    assert "Hottest batters" in page.text
    assert "Top Friday starters" in page.text
    assert "Longest streaks:" in page.text
    assert "Ramos 6" in page.text
    assert "How it" in page.text and "calculated" in page.text
    # Weather in the game headers, with the delay-risk flag.
    assert ("Fenway Park · 68°F · Partly cloudy"
            " · Wind 11 mph from NW") in page.text
    assert "Yankee Stadium · 64°F · Rain · Wind 8 mph from E" in page.text
    assert "Delay risk" in page.text


def test_pick_page_without_form_or_weather_renders_clean(con, client):
    # No form/weather rows yet (the poller hasn't run): no guide, no
    # weather lines, no probabilities — and the page still works.
    page = _pick_page(client, con)
    assert page.status_code == 200
    assert "Form guide" not in page.text
    assert "Fenway Park" not in page.text
    assert "hit Friday" not in page.text
    assert "5+ K Fri" not in page.text


def test_pick_page_renders_real_seed_form_and_weather(con, client):
    """End to end with the real seed file: the backfilled Sep 18–24
    form and Sep 25 weather render on the pick page (form lines,
    probabilities, Form guide, weather headers)."""
    import json
    import os

    from app import db as dbmod

    with open(os.path.join(os.path.dirname(__file__), "..", "data",
                           "seed_friday_2026-09-25.json")) as f:
        seed = json.load(f)
    dbmod.ensure_week(con, seed["season"], seed["week"],
                      seed["friday_date"], label=seed.get("label"),
                      playground=seed.get("playground", False))
    for game in seed["games"]:
        dbmod.upsert_game(con, {**game, "season": seed["season"],
                                 "week": seed["week"],
                                 "friday_date": seed["friday_date"]})
    for player in seed["players"]:
        dbmod.upsert_player(con, player)
    for row in seed.get("rosters", []):
        dbmod.add_roster(con, row["game_pk"], row["mlb_id"])
    for row in seed["lineups"]:
        dbmod.set_lineup(con, row["game_pk"], row["role"], row["mlb_id"],
                         row.get("batting_order"))
    for stat in seed["stats"]:
        dbmod.upsert_stats(con, stat["game_pk"], stat)
    assert seed.get("form") and seed.get("weather")
    for row in seed["form"]:
        dbmod.upsert_player_form(con, seed["season"], seed["week"], row)
    for row in seed["weather"]:
        dbmod.upsert_game_weather(con, row["game_pk"], row)
    con.commit()

    m = make_member(con, "Ada")
    client.get(f"/submit/{m['pick_token']}")
    page = client.get("/picks")
    assert page.status_code == 200
    # Real backfilled values (not the mock's sample numbers). The
    # seed Friday is Final, so rows show final points on the right
    # and the probabilities live in the Form guide — the per-row %
    # is a pre-game aid (see the with_stats=False test above).
    # Arenado: 12 hits in the window, 6-game streak, 81% hit Fri.
    assert "12 hits last 7 days · 6-game hit streak" in page.text
    assert "81%" in page.text
    # Bello's last start (93 pitches · 11 K); Yan's 94% tops the
    # starters list in the guide.
    assert "Last start: 93 pitches · 11 K" in page.text
    assert "94%" in page.text
    # The guide and the weather headers.
    assert "Form guide" in page.text
    assert "Hottest batters" in page.text
    assert "Fenway Park · 66°F · Overcast · Wind 17 mph from NNE" in page.text
    assert "Yankee Stadium · 70°F · Overcast · Wind 15 mph from NNE" in page.text
    # No delay-risk flag anywhere: the highest precip probability
    # that Friday was 21%.
    assert "Delay risk" not in page.text
