"""Registration flow + commissioner-approval admin.

Invite -> register -> pending -> Adam approves (pick link emailed) or
declines. No password anywhere: the pick link is the sign-in, and a
pending/declined member's token is unusable until approved.
"""
import os

import pytest

from app import db as dbmod
from app import emailer
from app.main import app
from conftest import make_member

ADMIN = "test-admin-token"


@pytest.fixture()
def admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN)
    # Mail is never configured in these tests: approval must still
    # succeed and just log the pick link.
    monkeypatch.delenv("RAVIX_SMTP_USER", raising=False)
    monkeypatch.delenv("RAVIX_SMTP_PASS", raising=False)
    return ADMIN


def _register(client, **overrides):
    data = {"first_name": "Joe", "last_name": "Pagliaro",
            "email": "joe@example.com", "phone": "",
            "returning": "yes", "referrer": "", "invite_token": ""}
    data.update(overrides)
    return client.post("/register", data=data)


def _approve(client, token, member_id):
    return client.post(f"/admin/{token}/approve/{member_id}",
                       follow_redirects=False)


def _decline(client, token, member_id):
    return client.post(f"/admin/{token}/decline/{member_id}",
                       follow_redirects=False)


# ---------------------------------------------------------------- register page

def test_register_page_renders_form(con, client):
    r = client.get("/register")
    assert r.status_code == 200
    assert "Join the league" in r.text
    assert 'name="first_name"' in r.text
    assert 'name="email"' in r.text
    assert "Did you play in 2026?" in r.text
    assert "Request to join" in r.text
    assert "$45 entry" in r.text


def test_register_prefills_email_and_invite_token(con, client):
    r = client.get("/register?invite=inv-123&email=joe%40example.com")
    assert r.status_code == 200
    assert 'value="joe@example.com"' in r.text
    assert 'name="invite_token" value="inv-123"' in r.text


# ---------------------------------------------------------------- pending member

def test_registration_creates_pending_member(con, client):
    r = _register(client, phone="(585) 555-0100", referrer="Tim Farrell",
                  invite_token="inv-123")
    assert r.status_code == 200
    assert "Request received" in r.text
    assert "Adam reviews every registration" in r.text
    assert "your personal pick link arrives by email" in r.text
    m = dbmod.member_by_email(con, "joe@example.com")
    assert m is not None
    assert m["name"] == "Joe Pagliaro"
    assert m["status"] == "pending"
    assert m["is_active"] == 0
    assert m["phone"] == "(585) 555-0100"
    assert m["played_2026"] == 1
    assert m["referrer"] == "Tim Farrell"
    assert m["invite_token"] == "inv-123"
    assert m["pick_token"]  # token exists at registration, unusable until approval


def test_registration_returning_no_and_blanks(con, client):
    r = _register(client, returning="no")
    assert r.status_code == 200
    m = dbmod.member_by_email(con, "joe@example.com")
    assert m["played_2026"] == 0
    assert m["phone"] is None
    assert m["referrer"] is None
    assert m["invite_token"] is None


def test_pending_member_cannot_sign_in(con, client):
    _register(client)
    m = dbmod.member_by_email(con, "joe@example.com")
    r = client.get(f"/submit/{m['pick_token']}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/locked"
    r = client.get("/picks", follow_redirects=False)
    assert r.status_code == 303


def test_registration_validation(con, client):
    for overrides in (
        {"first_name": ""},
        {"last_name": ""},
        {"email": ""},
        {"email": "not-an-email"},
        {"email": "joe@"},
    ):
        r = _register(client, **overrides)
        assert r.status_code == 200
        assert "required" in r.text.lower() or "valid email" in r.text.lower()
    assert dbmod.member_by_email(con, "joe@example.com") is None


def test_duplicate_registration_for_pending_stays_pending(con, client):
    _register(client)
    m1 = dbmod.member_by_email(con, "joe@example.com")
    r = _register(client, first_name="Joseph")
    assert r.status_code == 200
    assert "still waiting" in r.text
    m2 = dbmod.member_by_email(con, "joe@example.com")
    assert m2["id"] == m1["id"]
    assert m2["name"] == "Joe Pagliaro"  # not overwritten by the dupe
    assert m2["status"] == "pending"


def test_duplicate_registration_for_active_member(con, client):
    make_member(con, "Joe Pagliaro", email="joe@example.com")
    r = _register(client)
    assert r.status_code == 200
    assert "already registered" in r.text
    rows = con.execute(
        "SELECT * FROM members WHERE LOWER(email) = 'joe@example.com'"
    ).fetchall()
    assert len(rows) == 1


# ---------------------------------------------------------------- admin gate

def test_admin_requires_token(con, client, admin_env):
    _register(client)
    m = dbmod.member_by_email(con, "joe@example.com")
    r = client.get("/admin/wrong-token")
    assert r.status_code == 404
    r = _approve(client, "wrong-token", m["id"])
    assert r.status_code == 404
    r = _decline(client, "wrong-token", m["id"])
    assert r.status_code == 404
    assert dbmod.member_by_id(con, m["id"])["status"] == "pending"


def test_admin_denied_without_token_configured(con, client, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    r = client.get("/admin/anything")
    assert r.status_code == 404


def test_admin_page_lists_pending(con, client, admin_env):
    _register(client)
    _register(client, first_name="Matt", last_name="Mann",
              email="matt@example.com", returning="no")
    r = client.get(f"/admin/{ADMIN}")
    assert r.status_code == 200
    assert "Joe Pagliaro" in r.text
    assert "joe@example.com" in r.text
    assert "Matt Mann" in r.text
    assert "matt@example.com" in r.text
    assert "Approve" in r.text
    assert "Decline" in r.text
    assert "Pending (2)" in r.text


# ---------------------------------------------------------------- approve / decline

def test_approval_activates_and_issues_working_link(con, client, admin_env):
    _register(client)
    m = dbmod.member_by_email(con, "joe@example.com")
    r = _approve(client, ADMIN, m["id"])
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/{ADMIN}"
    m = dbmod.member_by_id(con, m["id"])
    assert m["status"] == "active"
    assert m["is_active"] == 1
    # The pick link now signs the member in and lands on the dashboard.
    r = client.get(f"/submit/{m['pick_token']}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"


def test_approval_succeeds_without_mail_configured(con, client, admin_env,
                                                   capsys):
    """Mail unconfigured must never fail the approval — the link is
    logged to stdout instead."""
    _register(client)
    m = dbmod.member_by_email(con, "joe@example.com")
    assert emailer.send_email("x@example.com", "s", "b") is False
    r = _approve(client, ADMIN, m["id"])
    assert r.status_code == 303
    assert dbmod.member_by_id(con, m["id"])["status"] == "active"
    out = capsys.readouterr().out
    assert m["pick_token"] in out
    assert "email not configured" in out


def test_decline_blocks_sign_in(con, client, admin_env):
    _register(client)
    m = dbmod.member_by_email(con, "joe@example.com")
    r = _decline(client, ADMIN, m["id"])
    assert r.status_code == 303
    m = dbmod.member_by_id(con, m["id"])
    assert m["status"] == "declined"
    assert m["is_active"] == 0
    r = client.get(f"/submit/{m['pick_token']}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/locked"
    # And the declined member no longer shows in the pending list.
    r = client.get(f"/admin/{ADMIN}")
    assert "Joe Pagliaro" not in r.text.split("Pending")[1].split("Approved")[0]


def test_declined_member_can_register_again(con, client, admin_env):
    _register(client)
    m = dbmod.member_by_email(con, "joe@example.com")
    _decline(client, ADMIN, m["id"])
    r = _register(client, first_name="Joseph", last_name="Pagliaro")
    assert r.status_code == 200
    assert "Request received" in r.text
    m2 = dbmod.member_by_email(con, "joe@example.com")
    assert m2["id"] == m["id"]
    assert m2["status"] == "pending"
    assert m2["name"] == "Joseph Pagliaro"


def test_approve_or_decline_only_applies_to_pending(con, client, admin_env):
    m = make_member(con, "Ada", email="ada@example.com")  # already active
    r = _approve(client, ADMIN, m["id"])
    assert r.status_code == 303
    assert dbmod.member_by_id(con, m["id"])["status"] == "active"
    r = _decline(client, ADMIN, m["id"])
    assert r.status_code == 303
    # Declining an active member is a no-op — approval is one-way here.
    assert dbmod.member_by_id(con, m["id"])["status"] == "active"


# ---------------------------------------------------------------- emailer

def test_valid_email():
    assert emailer.valid_email("  joe@example.com ") == "joe@example.com"
    assert emailer.valid_email("not-an-email") is None
    assert emailer.valid_email("joe@") is None
    assert emailer.valid_email("") is None
    assert emailer.valid_email(None) is None


def test_send_email_unconfigured_returns_false(monkeypatch):
    monkeypatch.delenv("RAVIX_SMTP_USER", raising=False)
    monkeypatch.delenv("RAVIX_SMTP_PASS", raising=False)
    assert emailer.send_email("joe@example.com", "s", "b") is False
