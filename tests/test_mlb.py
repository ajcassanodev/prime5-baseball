"""MLB Stats API parsing (fixtures only — no network in tests)."""
from app import mlb as mlb_mod

SCHEDULE_FIXTURE = {
    "dates": [{
        "date": "2026-09-25",
        "games": [
            {
                "gamePk": 824703,
                "gameDate": "2026-09-25T17:05:00Z",
                "status": {"detailedState": "Final"},
                "teams": {
                    "away": {"team": {"id": 112, "name": "Chicago Cubs"},
                             "probablePitcher": {"id": 202,
                                                 "fullName": "Clay Holmes"}},
                    "home": {"team": {"id": 111, "name": "Boston Red Sox"},
                             "probablePitcher": {"id": 201,
                                                 "fullName": "Alec Gamboa"}},
                },
                "lineups": {
                    "homePlayers": [
                        {"id": 11, "fullName": "Roman Anthony",
                         "position": {"abbreviation": "LF"}},
                        {"id": 12, "fullName": "Adley Rutschman",
                         "position": {"abbreviation": "DH"}},
                    ],
                    "awayPlayers": [
                        {"id": 14, "fullName": "Michael Busch",
                         "position": {"abbreviation": "1B"}},
                    ],
                },
            },
            {
                "gamePk": 824706,
                "gameDate": "2026-09-25T21:35:00Z",
                "status": {"detailedState": "Final"},
                "teams": {
                    "away": {"team": {"id": 112, "name": "Chicago Cubs"}},
                    "home": {"team": {"id": 111, "name": "Boston Red Sox"}},
                },
                # No lineups key at all: not announced yet.
            },
        ],
    }],
}

BOXSCORE_FIXTURE = {
    "gamePk": 824703,
    "teams": {
        "home": {
            "team": {"abbreviation": "BOS"},
            "battingOrder": [11, 12],
            "players": {
                "ID11": {"person": {"id": 11, "fullName": "Roman Anthony"},
                         "position": {"abbreviation": "LF"},
                         "stats": {"batting": {"atBats": 3, "hits": 3,
                                               "homeRuns": 0, "rbi": 1}}},
                "ID13": {"person": {"id": 13, "fullName": "Willson Contreras"},
                         "position": {"abbreviation": "1B"},
                         "stats": {"batting": {}, "pitching": {}}},
                "ID201": {"person": {"id": 201, "fullName": "Alec Gamboa"},
                          "position": {"abbreviation": "P"},
                          "stats": {"pitching": {"gamesStarted": 1,
                                                 "numberOfPitches": 40,
                                                 "strikeOuts": 1}}},
            },
        },
        "away": {
            "team": {"abbreviation": "CHC"},
            "battingOrder": [14],
            "players": {
                "ID14": {"person": {"id": 14, "fullName": "Michael Busch"},
                         "position": {"abbreviation": "1B"},
                         "stats": {"batting": {"atBats": 3, "hits": 2,
                                               "homeRuns": 1, "rbi": 2}}},
                "ID202": {"person": {"id": 202, "fullName": "Clay Holmes"},
                          "position": {"abbreviation": "P"},
                          "stats": {"pitching": {"gamesStarted": 1,
                                                 "numberOfPitches": 78,
                                                 "strikeOuts": 3}}},
            },
        },
    },
}

GAME_LOG_HITTING = {"stats": [{"splits": [
    {"date": "2026-09-25", "game": {"gamePk": 824703},
     "stat": {"atBats": 3, "hits": 3, "homeRuns": 0, "rbi": 1}},
    {"date": "2026-09-24", "game": {"gamePk": 824700},
     "stat": {"atBats": 4, "hits": 1, "homeRuns": 0, "rbi": 0}},
]}]}

GAME_LOG_PITCHING = {"stats": [{"splits": [
    {"date": "2026-09-25", "game": {"gamePk": 824703},
     "stat": {"numberOfPitches": 78, "strikeOuts": 3}},
]}]}


def test_parse_schedule():
    games = mlb_mod.parse_schedule(SCHEDULE_FIXTURE)
    assert len(games) == 2
    g = games[0]
    assert g["game_pk"] == 824703
    assert g["home"]["abbr"] == "BOS"
    assert g["away"]["abbr"] == "CHC"
    assert g["home_starter"] == {"mlb_id": 201, "name": "Alec Gamboa"}
    assert g["away_starter"] == {"mlb_id": 202, "name": "Clay Holmes"}
    assert [p["mlb_id"] for p in g["lineups"]["home"]] == [11, 12]
    assert [p["mlb_id"] for p in g["lineups"]["away"]] == [14]
    # No probable pitcher / no lineups key -> None / empty, not a crash.
    assert games[1]["home_starter"] is None
    assert games[1]["lineups"] == {"home": [], "away": []}


def test_parse_boxscore():
    box = mlb_mod.parse_boxscore(BOXSCORE_FIXTURE)
    assert box["game_pk"] == 824703
    assert box["batting_order"] == {"home": [11, 12], "away": [14]}
    assert set(box["starters"]) == {201, 202}
    names = {p["mlb_id"]: p["name"] for p in box["players"]}
    assert names[11] == "Roman Anthony"
    assert names[13] == "Willson Contreras"  # bench, no stats
    assert not any(p["mlb_id"] == 13 for p in
                   [s for s in box["stats"] if s["hits"] or s["pitches"]])
    stats = {(s["mlb_id"]): s for s in box["stats"]}
    assert stats[11]["hits"] == 3
    assert stats[14]["hr"] == 1
    assert stats[202]["pitches"] == 78
    assert stats[202]["strikeouts"] == 3


def test_parse_game_log():
    hitting = mlb_mod.parse_game_log(GAME_LOG_HITTING, "hitting")
    assert hitting[0] == {"game_pk": 824703, "date": "2026-09-25",
                          "at_bats": 3, "hits": 3, "hr": 0, "rbi": 1}
    pitching = mlb_mod.parse_game_log(GAME_LOG_PITCHING, "pitching")
    assert pitching == [{"game_pk": 824703, "date": "2026-09-25",
                         "pitches": 78, "strikeouts": 3}]


def test_first_pitch_et():
    # September is EDT (UTC-4); January is EST (UTC-5).
    assert mlb_mod.first_pitch_et("2026-09-25T17:05:00Z") == "1:05 PM"
    assert mlb_mod.first_pitch_et("2026-09-25T21:35:00Z") == "5:35 PM"
    assert mlb_mod.first_pitch_et("2027-01-15T17:05:00Z") == "12:05 PM"


def test_team_abbr_known_clubs():
    assert mlb_mod.TEAM_ABBR[111] == "BOS"
    assert mlb_mod.TEAM_ABBR[147] == "NYY"
    assert mlb_mod.TEAM_ABBR[110] == "BAL"
    assert len(mlb_mod.TEAM_ABBR) == 30
