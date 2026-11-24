"""Shared header (Nov 24, 2026): same Ravix header as the football site —
logo + wordmark, pill nav, brand fonts, favicon."""

import os


def test_header_matches_football_site(client):
    page = client.get("/register")
    assert page.status_code == 200
    # Ravix logo + wordmark with the Prime5 Baseball sub-line.
    assert '<img src="/static/logo.png" alt="Ravix logo">' in page.text
    assert "RAVIX <em>FANTASY SPORTS</em>" in page.text
    assert "Prime5 Baseball" in page.text
    # Baseball nav: Pick, My picks, Standings, Matchups, Schedule, Help.
    assert '<a href="/picks">Pick</a>' in page.text
    assert '<a href="/dashboard">My picks</a>' in page.text
    assert '<a href="/standings">Standings</a>' in page.text
    assert '<a href="/matchups">Matchups</a>' in page.text
    assert '<a href="/schedule">Schedule</a>' in page.text
    assert 'href="mailto:help@ravixfs.com">Help</a>' in page.text
    # Favicon + brand fonts.
    assert '<link rel="icon" href="/static/logo.png">' in page.text
    assert "Space+Grotesk" in page.text
    assert "family=Inter" in page.text
    # The old plain-text header is gone.
    assert '<span class="brand">PRIME5 BASEBALL</span>' not in page.text
    assert 'class="top"' not in page.text


def test_stylesheet_link_is_versioned(client):
    # Nov 24, 2026: browsers cached the old style.css after the header
    # redesign and showed the new header unstyled. The link carries the
    # CSS file's mtime so a deploy forces one fresh fetch.
    page = client.get("/register")
    assert page.status_code == 200
    assert "/static/style.css?v=" in page.text


def test_header_wrap_is_wide_enough_for_nav():
    # Nov 24, 2026: the header shared the 720px content wrap, so brand +
    # full nav + gear (~740px) overflowed and the nav dropped to a second
    # row, doubling the header height. The header gets its own wider wrap.
    css = open(os.path.join(os.path.dirname(__file__), "..", "app",
                            "static", "style.css")).read()
    assert "header .wrap { max-width: 960px; }" in css
