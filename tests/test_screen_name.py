"""Screen names + "Show my full name" setting (Nov 24, 2026).

Registration asks for a screen name — what other members see. Full
names show only in the admin list and, when a member opts in, under
their own screen name (first name + last initial otherwise).

Note: the header's Settings description quotes the signed-in member's
OWN full name ("Show \"Adam Cassano\" ..."), so leak checks below
assert on the standings rows / dashboard sub-line markup, not the
whole page.
"""
from datetime import datetime, timezone

from app import db as dbmod
from app.picks import submit_picks
from conftest import build_week, make_member


def _register(client, **overrides):
    data = {"first_name": "Adam", "last_name": "Cassano",
            "screen_name": "RavixRaven", "email": "adam@example.com",
            "phone": "", "returning": "yes", "referrer": "",
            "invite_token": ""}
    data.update(overrides)
    return client.post("/register", data=data)


def _sign_in(client, member):
    return client.get(f"/submit/{member['pick_token']}")


# ---------------------------------------------------------------- helpers

def test_short_name():
    assert dbmod.short_name("Adam Cassano") == "Adam C."
    assert dbmod.short_name("Joe Pagliaro") == "Joe P."
    assert dbmod.short_name("Ada") == "Ada"
    assert dbmod.short_name("  ") == ""


def test_validate_screen_name():
    assert dbmod.validate_screen_name("RavixRaven") is None
    assert dbmod.validate_screen_name("a_b1") is None
    assert dbmod.validate_screen_name("ab") is not None
    assert dbmod.validate_screen_name("x" * 21) is not None
    assert dbmod.validate_screen_name("has space") is not None
    assert dbmod.validate_screen_name("bad!") is not None
    assert dbmod.validate_screen_name("a-b") is not None
    assert dbmod.validate_screen_name("") is not None


def test_display_name_and_sub():
    legacy = {"name": "Adam Cassano", "screen_name": None,
              "show_full_name": 0}
    assert dbmod.display_name(legacy) == "Adam C."
    assert dbmod.display_sub(legacy) == ""
    short = {"name": "Adam Cassano", "screen_name": "RavixRaven",
             "show_full_name": 0}
    assert dbmod.display_name(short) == "RavixRaven"
    assert dbmod.display_sub(short) == "Adam C."
    full = {"name": "Adam Cassano", "screen_name": "RavixRaven",
            "show_full_name": 1}
    assert dbmod.display_name(full) == "RavixRaven"
    assert dbmod.display_sub(full) == "Adam Cassano"


# ---------------------------------------------------------------- registration

def test_registration_stores_screen_name(con, client):
    r = _register(client)
    assert r.status_code == 200
    assert "Request received" in r.text
    m = dbmod.member_by_email(con, "adam@example.com")
    assert m is not None
    assert m["name"] == "Adam Cassano"
    assert m["screen_name"] == "RavixRaven"
    assert m["status"] == "pending"


def test_registration_rejects_bad_screen_names(con, client):
    for bad in ("ab", "x" * 21, "has space", "bad!", "a-b", ""):
        r = _register(client, screen_name=bad,
                      email="badname@example.com")
        assert r.status_code == 200
        assert "Screen name" in r.text
        # The failed submit re-renders the form with values preserved.
        assert 'name="screen_name"' in r.text
        assert 'value="Adam"' in r.text
    assert dbmod.member_by_email(con, "badname@example.com") is None


def test_registration_rejects_duplicate_screen_name(con, client):
    r = _register(client)
    assert r.status_code == 200
    # Same name, different case, different email — still taken, and the
    # pending member holds it too.
    r = _register(client, screen_name="ravixraven",
                  email="other@example.com")
    assert r.status_code == 200
    assert "already taken" in r.text
    assert dbmod.member_by_email(con, "other@example.com") is None


def test_declined_reopen_updates_screen_name(con, client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    _register(client)
    m = dbmod.member_by_email(con, "adam@example.com")
    r = client.post(f"/admin/test-admin-token/decline/{m['id']}",
                    follow_redirects=False)
    assert r.status_code == 303
    r = _register(client, screen_name="RavenFan")
    assert r.status_code == 200
    assert "Request received" in r.text
    m2 = dbmod.member_by_email(con, "adam@example.com")
    assert m2["id"] == m["id"]
    assert m2["status"] == "pending"
    assert m2["screen_name"] == "RavenFan"


def test_declined_reopen_rejects_taken_screen_name(con, client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    _register(client)  # Adam / RavixRaven
    _register(client, first_name="Joe", last_name="Pagliaro",
              screen_name="Pags", email="joe@example.com")
    m = dbmod.member_by_email(con, "adam@example.com")
    client.post(f"/admin/test-admin-token/decline/{m['id']}",
                follow_redirects=False)
    # Re-registering while asking for Joe's (pending) screen name fails.
    r = _register(client, screen_name="Pags")
    assert r.status_code == 200
    assert "already taken" in r.text
    assert dbmod.member_by_email(con, "adam@example.com")["status"] == "declined"


# ---------------------------------------------------------------- standings

def _seeded_standings(con, client):
    """Two members with screen names and picks, signed in as Adam."""
    w = build_week(con)
    adam = dbmod.create_member(con, "Adam Cassano",
                               email="adam@example.com",
                               screen_name="RavixRaven")
    joe = dbmod.create_member(con, "Joe Pagliaro",
                              email="joe@example.com", screen_name="Pags")
    bats = [(w["batters"]["Roman Anthony"], w["batter_game"]["Roman Anthony"]),
            (w["batters"]["Adley Rutschman"], w["batter_game"]["Adley Rutschman"]),
            (w["batters"]["Michael Busch"], w["batter_game"]["Michael Busch"]),
            (w["batters"]["Pete Alonso"], w["batter_game"]["Pete Alonso"]),
            (w["batters"]["Heliot Ramos"], w["batter_game"]["Heliot Ramos"])]
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    submit_picks(con, adam["id"], 2026, 1, bats,
                 w["pitchers"]["Clay Holmes"], now=now)
    submit_picks(con, joe["id"], 2026, 1, bats,
                 w["pitchers"]["Brendan Beck"], now=now)
    _sign_in(client, adam)
    return adam, joe


def test_standings_shows_screen_name_and_short_sub(con, client):
    _seeded_standings(con, client)
    page = client.get("/standings")
    assert page.status_code == 200
    assert '<b>RavixRaven</b><span class="display-sub">Adam C.</span>' in page.text
    assert '<b>Pags</b><span class="display-sub">Joe P.</span>' in page.text
    # Full names never appear in the standings rows.
    table = page.text.split("<table")[1].split("</table>")[0]
    assert "Adam Cassano" not in table
    assert "Joe Pagliaro" not in table


def test_standings_shows_full_name_when_opted_in(con, client):
    adam, _joe = _seeded_standings(con, client)
    dbmod.set_show_full_name(con, adam["id"], True)
    page = client.get("/standings")
    assert page.status_code == 200
    assert '<b>RavixRaven</b><span class="display-sub">Adam Cassano</span>' in page.text
    # Joe did not opt in — his stays shortened.
    assert '<b>Pags</b><span class="display-sub">Joe P.</span>' in page.text


def test_dashboard_shows_own_screen_name(con, client):
    build_week(con)
    adam = dbmod.create_member(con, "Adam Cassano",
                               email="adam@example.com",
                               screen_name="RavixRaven")
    _sign_in(client, adam)
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert '<b>RavixRaven</b><span class="display-sub">Adam C.</span>' in page.text


# ---------------------------------------------------------------- settings

def test_settings_flips_and_persists(con, client):
    m = make_member(con, "Adam Cassano", email="adam@example.com")
    _sign_in(client, m)
    r = client.post("/settings", json={"show_full_name": True})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "show_full_name": True}
    assert dbmod.member_by_id(con, m["id"])["show_full_name"] == 1
    # The header switch now renders on.
    page = client.get("/dashboard")
    assert 'class="switch on"' in page.text
    r = client.post("/settings", json={"show_full_name": False})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "show_full_name": False}
    assert dbmod.member_by_id(con, m["id"])["show_full_name"] == 0


def test_settings_requires_sign_in(con, client):
    r = client.post("/settings", json={"show_full_name": True},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/locked"


def test_settings_rejects_invalid_body(con, client):
    m = make_member(con, "Adam Cassano", email="adam@example.com")
    _sign_in(client, m)
    for bad in ({"show_full_name": "yes"}, {"show_full_name": 1}, {},
                {"other": True}):
        r = client.post("/settings", json=bad)
        assert r.status_code == 400
    assert dbmod.member_by_id(con, m["id"])["show_full_name"] == 0


def test_header_settings_button_only_when_signed_in(con, client):
    # Anonymous pages (register, locked) have no Settings button.
    assert 'id="settingsBtn"' not in client.get("/register").text
    assert 'id="settingsBtn"' not in client.get("/locked").text
    m = make_member(con, "Adam Cassano", email="adam@example.com")
    _sign_in(client, m)
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert 'id="settingsBtn"' in page.text
    assert 'id="fullNameSwitch"' in page.text
    assert "Show my full name" in page.text
    # The description uses the member's own names.
    assert "Adam Cassano" in page.text
    assert "Adam C." in page.text
