"""First-visit welcome pop-up: shown once, dismissed server-side
(per member), reopenable via "How to play"."""
from fastapi.testclient import TestClient

from app import db as dbmod
from app.main import app
from conftest import build_week, make_member


def _signed_in(client, member):
    client.get(f"/submit/{member['pick_token']}")
    return client.get("/picks")


def test_welcome_shown_on_first_visit(con, client):
    build_week(con)
    m = make_member(con, "Ada")
    page = _signed_in(client, m)
    assert page.status_code == 200
    assert 'data-show-welcome="1"' in page.text
    # The approved three steps, in order.
    assert "Pick 5 batters + 1 pitcher" in page.text
    assert "Green means they" in page.text
    assert "Picks lock at first pitch" in page.text
    assert "both for 2 of your 5 slots" in page.text
    assert "0.01 per pitch plus 1 per strikeout" in page.text
    # Controls: step dots, dismiss X, final button, reopen link.
    assert 'id="welcomeOverlay"' in page.text
    assert 'id="welcomeDots"' in page.text
    assert 'id="welcomeClose"' in page.text
    assert "Got it" in page.text
    assert "How to play" in page.text


def test_welcome_hidden_after_dismiss(con, client):
    build_week(con)
    m = make_member(con, "Ada")
    client.get(f"/submit/{m['pick_token']}")
    r = client.post("/welcome-seen")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # Flag persisted on the member row.
    assert dbmod.member_by_id(con, m["id"])["seen_welcome"] == 1
    page = client.get("/picks")
    assert page.status_code == 200
    assert 'data-show-welcome="0"' in page.text


def test_welcome_flag_is_per_member(con, client):
    build_week(con)
    a = make_member(con, "Ada")
    b = make_member(con, "Bo")
    client.get(f"/submit/{a['pick_token']}")
    client.post("/welcome-seen")
    assert dbmod.member_by_id(con, a["id"])["seen_welcome"] == 1
    assert dbmod.member_by_id(con, b["id"])["seen_welcome"] == 0
    # Bo's first visit still shows the welcome.
    other = TestClient(app)
    other.get(f"/submit/{b['pick_token']}")
    page = other.get("/picks")
    assert 'data-show-welcome="1"' in page.text


def test_how_to_play_reopens_after_dismiss(con, client):
    """After dismiss the modal stays in the DOM and "How to play"
    reopens it — the overlay is rendered even when not auto-shown."""
    build_week(con)
    m = make_member(con, "Ada")
    client.get(f"/submit/{m['pick_token']}")
    client.post("/welcome-seen")
    page = client.get("/picks")
    assert 'data-show-welcome="0"' in page.text
    assert 'id="welcomeOverlay"' in page.text
    assert 'id="howToBtn"' in page.text
    # The reopen handler shows the overlay from step 0.
    assert "openWelcome" in page.text


def test_final_button_and_close_share_one_dismiss_path(con, client):
    """Both the X and the final "Got it" button call dismissWelcome(),
    which is the single POST to /welcome-seen."""
    build_week(con)
    m = make_member(con, "Ada")
    page = _signed_in(client, m)
    # The X passes dismissWelcome as its click handler, and the final
    # "Got it" branch calls it — one shared dismiss path.
    assert "addEventListener('click', dismissWelcome)" in page.text
    assert "else { dismissWelcome(); }" in page.text
    assert 'id="welcomeClose"' in page.text
    assert "Got it" in page.text
    # And that path really marks the member seen.
    client.post("/welcome-seen")
    assert dbmod.member_by_id(con, m["id"])["seen_welcome"] == 1


def test_welcome_seen_requires_sign_in(con, client):
    r = client.post("/welcome-seen", follow_redirects=False)
    assert r.status_code == 303  # -> /locked
