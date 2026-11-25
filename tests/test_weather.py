"""Game-day weather (Nov 25, 2026): compass, conditions, delay-risk
threshold, forecast parsing, and the venue map."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app import weather as wx

ET = ZoneInfo("America/New_York")


def _payload(hours):
    """hours: {hour: {temp, prob, precip, wind, deg, code}} -> a raw
    Open-Meteo hourly payload for 2026-09-25."""
    times, temp, prob, precip, wind, deg, code = [], [], [], [], [], [], []
    for h in sorted(hours):
        v = hours[h]
        times.append(f"2026-09-25T{h:02d}:00")
        temp.append(v.get("temp"))
        prob.append(v.get("prob"))
        precip.append(v.get("precip"))
        wind.append(v.get("wind"))
        deg.append(v.get("deg"))
        code.append(v.get("code"))
    return {"hourly": {"time": times, "temperature_2m": temp,
                       "precipitation_probability": prob,
                       "precipitation": precip, "wind_speed_10m": wind,
                       "wind_direction_10m": deg, "weathercode": code}}


def test_compass_sixteen_points():
    assert wx.compass(0) == "N"
    assert wx.compass(11) == "N"
    assert wx.compass(12) == "NNE"
    assert wx.compass(36) == "NE"
    assert wx.compass(90) == "E"
    assert wx.compass(180) == "S"
    assert wx.compass(270) == "W"
    assert wx.compass(315) == "NW"
    assert wx.compass(337) == "NNW"
    assert wx.compass(349) == "N"
    assert wx.compass(359) == "N"
    assert wx.compass(None) is None


def test_conditions_text():
    assert wx.conditions_text(0) == "Clear"
    assert wx.conditions_text(2) == "Partly cloudy"
    assert wx.conditions_text(3) == "Overcast"
    assert wx.conditions_text(61) == "Light rain"
    assert wx.conditions_text(95) == "Thunderstorm"
    assert wx.conditions_text(None) is None
    assert wx.conditions_text(999) == "Cloudy"


def test_parse_forecast_first_pitch_hour():
    raw = _payload({
        13: {"temp": 68.4, "prob": 10, "wind": 11.2, "deg": 315, "code": 2},
        17: {"temp": 62.1, "prob": 55, "wind": 14.6, "deg": 39, "code": 3},
    })
    row = wx.parse_forecast(
        raw, datetime(2026, 9, 25, 13, 5, tzinfo=ET), "open")
    assert row == {"temp_f": 68, "conditions": "Partly cloudy",
                   "precip_prob": 10, "wind_mph": 11,
                   "wind_compass": "NW", "delay_risk": False}


def test_delay_risk_threshold_is_50_percent():
    raw = _payload({
        13: {"temp": 64, "prob": 49, "wind": 8, "deg": 90, "code": 61},
        17: {"temp": 62, "prob": 50, "wind": 8, "deg": 90, "code": 61},
    })
    assert wx.parse_forecast(
        raw, datetime(2026, 9, 25, 13, 5, tzinfo=ET), "open")["delay_risk"] is False
    assert wx.parse_forecast(
        raw, datetime(2026, 9, 25, 17, 5, tzinfo=ET), "open")["delay_risk"] is True


def test_delay_risk_never_fires_under_a_dome():
    raw = _payload({
        13: {"temp": 72, "prob": 90, "wind": 2, "deg": 0, "code": 63},
    })
    row = wx.parse_forecast(
        raw, datetime(2026, 9, 25, 13, 5, tzinfo=ET), "dome")
    assert row["delay_risk"] is False
    assert row["precip_prob"] == 90


def test_parse_forecast_archive_fallback_uses_precipitation():
    # The archive API has no precipitation_probability: any
    # precipitation in the first-pitch hour is a delay risk, and
    # precip_prob comes back None.
    raw = {"hourly": {"time": ["2026-09-25T13:00"],
                      "temperature_2m": [64.3],
                      "precipitation": [0.4],
                      "wind_speed_10m": [14.0],
                      "wind_direction_10m": [36],
                      "weathercode": [3]}}
    row = wx.parse_forecast(
        raw, datetime(2026, 9, 25, 13, 5, tzinfo=ET), "open")
    assert row["precip_prob"] is None
    assert row["delay_risk"] is True
    assert row["wind_compass"] == "NE"


def test_parse_forecast_missing_hour_is_blank_not_invented():
    raw = _payload({17: {"temp": 62, "prob": 4, "wind": 13, "deg": 30,
                         "code": 3}})
    row = wx.parse_forecast(
        raw, datetime(2026, 9, 25, 13, 5, tzinfo=ET), "open")
    assert row["temp_f"] is None
    assert row["conditions"] is None
    assert row["delay_risk"] is False


def test_venue_map_covers_all_30_clubs():
    from app.mlb import TEAM_ABBR
    assert set(wx.VENUES) == set(TEAM_ABBR.values())
    for abbr, (name, lat, lon, roof) in wx.VENUES.items():
        assert name and -90 <= lat <= 90 and -180 <= lon <= 180
        assert roof in ("open", "retractable", "dome")
    assert wx.VENUES["BOS"][0] == "Fenway Park"
    assert wx.VENUES["NYY"][0] == "Yankee Stadium"
    assert wx.VENUES["TB"][3] == "dome"


def test_first_pitch_local_uses_et_hour():
    # 17:05 UTC is 13:05 ET — the hour string the forecast lookup
    # uses, whatever the park's own timezone.
    assert wx.first_pitch_local("2026-09-25T17:05:00Z").strftime(
        "%Y-%m-%dT%H:00") == "2026-09-25T13:00"
