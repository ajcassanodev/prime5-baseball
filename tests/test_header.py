"""Shared header (Nov 24, 2026): same Ravix header as the football site —
logo + wordmark, pill nav, brand fonts, favicon."""


def test_header_matches_football_site(client):
    page = client.get("/register")
    assert page.status_code == 200
    # Ravix logo + wordmark with the Prime5 Baseball sub-line.
    assert '<img src="/static/logo.png" alt="Ravix logo">' in page.text
    assert "RAVIX <em>FANTASY SPORTS</em>" in page.text
    assert "Prime5 Baseball" in page.text
    # Baseball nav: Pick, My picks, Standings, Help.
    assert '<a href="/picks">Pick</a>' in page.text
    assert '<a href="/dashboard">My picks</a>' in page.text
    assert '<a href="/standings">Standings</a>' in page.text
    assert 'href="mailto:help@ravixfs.com">Help</a>' in page.text
    # Favicon + brand fonts.
    assert '<link rel="icon" href="/static/logo.png">' in page.text
    assert "Space+Grotesk" in page.text
    assert "family=Inter" in page.text
    # The old plain-text header is gone.
    assert '<span class="brand">PRIME5 BASEBALL</span>' not in page.text
    assert 'class="top"' not in page.text
