"""Game-day weather (Nov 25, 2026): Open-Meteo, free, no key.

- Forecast API (api.open-meteo.com/v1/forecast) for the Friday in
  question, at the first-pitch hour, keyed to the ballpark's lat/lon.
  For a past date the same API is called with past_days (up to 92
  days back) — the archive API (archive-api.open-meteo.com) carries
  no precipitation_probability, so it can't drive the delay-risk
  flag; it is used only as a fallback for dates older than 92 days,
  where precipitation (mm) stands in for the probability.
- Delay risk: precipitation probability at first pitch >= 50%,
  for open-air and retractable-roof parks only — a fixed dome never
  delays for rain, so Tropicana Field never shows the flag.
- Wind is shown as a compass direction ("from NW"), never as a
  per-park "out to LF" orientation — that would need a wind-rose
  per ballpark we don't have.

Pure parsing helpers (parse_forecast, compass, conditions_text) so
tests run on fixture payloads with no network.
"""
import json
import urllib.error
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ET = ZoneInfo("America/New_York")

# Ballpark by home-team abbreviation. Coordinates are approximate —
# weather is regional, and a few miles don't change the forecast.
# roof: 'open' | 'retractable' | 'dome'.
VENUES = {
    "ARI": ("Chase Field", 33.4453, -112.0667, "retractable"),
    "ATH": ("Sutter Health Park", 38.5788, -121.5131, "open"),
    "ATL": ("Truist Park", 33.8907, -84.4677, "open"),
    "BAL": ("Oriole Park at Camden Yards", 39.2839, -76.6217, "open"),
    "BOS": ("Fenway Park", 42.3467, -71.0972, "open"),
    "CHC": ("Wrigley Field", 41.9484, -87.6553, "open"),
    "CWS": ("Rate Field", 41.8300, -87.6338, "open"),
    "CIN": ("Great American Ball Park", 39.0974, -84.5066, "open"),
    "CLE": ("Progressive Field", 41.4962, -83.7055, "open"),
    "COL": ("Coors Field", 39.7561, -104.9942, "open"),
    "DET": ("Comerica Park", 42.3390, -83.0485, "open"),
    "HOU": ("Daikin Park", 29.7573, -95.3555, "retractable"),
    "KC": ("Kauffman Stadium", 39.0517, -94.4803, "open"),
    "LAA": ("Angel Stadium", 33.8003, -117.8827, "open"),
    "LAD": ("Dodger Stadium", 34.0739, -118.2400, "open"),
    "MIA": ("loanDepot park", 25.7781, -80.2197, "retractable"),
    "MIL": ("American Family Field", 43.0284, -87.9711, "retractable"),
    "MIN": ("Target Field", 44.9817, -93.2776, "open"),
    "NYM": ("Citi Field", 40.7571, -73.8458, "open"),
    "NYY": ("Yankee Stadium", 40.8296, -73.9262, "open"),
    "PHI": ("Citizens Bank Park", 39.9061, -75.1665, "open"),
    "PIT": ("PNC Park", 40.4469, -80.0057, "open"),
    "SD": ("Petco Park", 32.7073, -117.1566, "open"),
    "SEA": ("T-Mobile Park", 47.5914, -122.3325, "retractable"),
    "SF": ("Oracle Park", 37.7786, -122.3893, "open"),
    "STL": ("Busch Stadium", 38.6226, -90.1928, "open"),
    "TB": ("Tropicana Field", 27.7682, -82.6534, "dome"),
    "TEX": ("Globe Life Field", 32.7473, -97.0847, "retractable"),
    "TOR": ("Rogers Centre", 43.6414, -79.3894, "retractable"),
    "WSH": ("Nationals Park", 38.8730, -77.0074, "open"),
}

DELAY_PRECIP_PROB = 50  # % precipitation probability at first pitch


class WeatherError(Exception):
    """Raised when the Open-Meteo call fails."""


def _get_json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "prime5-baseball/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise WeatherError(f"Open-Meteo request failed: {url}: {exc}") from exc


def compass(degrees: float | None) -> str | None:
    """Wind direction in degrees -> 16-point compass ('NW'). None in,
    None out (a calm hour has no meaningful direction)."""
    if degrees is None:
        return None
    points = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return points[int((float(degrees) + 11.25) // 22.5) % 16]


# WMO weather codes -> short conditions text for the game header.
_CONDITIONS = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light rain showers", 81: "Rain showers", 82: "Heavy rain showers",
    85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
}


def conditions_text(code: int | None) -> str | None:
    if code is None:
        return None
    return _CONDITIONS.get(int(code), "Cloudy")


def first_pitch_local(game_date_utc: str) -> datetime:
    """First-pitch time as an America/New_York datetime. The hourly
    arrays are requested in America/New_York too, so the ET hour
    string identifies the absolute hour of first pitch at any park —
    no per-park timezone math is needed for the lookup."""
    dt = datetime.fromisoformat(game_date_utc.replace("Z", "+00:00"))
    return dt.astimezone(ET)


def fetch_forecast(lat: float, lon: float, game_date: str,
                   today: str | None = None) -> dict:
    """Hourly forecast for one date. Past dates (within 92 days) use
    past_days; future dates use forecast_days. Returns the raw
    payload."""
    target = date.fromisoformat(game_date)
    today_d = date.fromisoformat(today) if today else date.today()
    base = (f"{FORECAST_URL}?latitude={lat}&longitude={lon}"
            "&hourly=temperature_2m,precipitation_probability,"
            "wind_speed_10m,wind_direction_10m,weathercode"
            "&temperature_unit=fahrenheit&wind_speed_unit=mph"
            "&timezone=America%2FNew_York")
    if target <= today_d:
        days_back = (today_d - target).days
        if days_back > 92:
            return fetch_archive(lat, lon, game_date)
        return _get_json(f"{base}&past_days={days_back}&forecast_days=1")
    ahead = (target - today_d).days + 1
    return _get_json(f"{base}&forecast_days={ahead}")


def fetch_archive(lat: float, lon: float, game_date: str) -> dict:
    """Archive fallback for dates older than 92 days. The archive
    carries no precipitation_probability, so `precipitation` (mm) is
    requested instead — parse_forecast treats any precipitation in
    the first-pitch hour as a delay risk."""
    url = (f"{ARCHIVE_URL}?latitude={lat}&longitude={lon}"
           f"&start_date={game_date}&end_date={game_date}"
           "&hourly=temperature_2m,precipitation,wind_speed_10m,"
           "wind_direction_10m,weathercode"
           "&temperature_unit=fahrenheit&wind_speed_unit=mph"
           "&timezone=America%2FNew_York")
    return _get_json(url)


def _hour_value(hourly: dict, key: str, hour_iso: str):
    """Value of an hourly variable at the given 'YYYY-MM-DDTHH:00'
    slot, or None when the slot/variable is missing."""
    times = hourly.get("time", [])
    values = hourly.get(key) or []
    try:
        idx = times.index(hour_iso)
    except ValueError:
        return None
    if idx >= len(values):
        return None
    return values[idx]


def parse_forecast(raw: dict, first_pitch: datetime, roof: str) -> dict:
    """Raw Open-Meteo payload + first-pitch local time -> weather row.

    {temp_f, conditions, precip_prob, wind_mph, wind_compass,
    delay_risk}. Missing values come back None rather than invented
    numbers; delay_risk is False for domes and when no precipitation
    signal exists.
    """
    hourly = raw.get("hourly", {})
    hour_iso = first_pitch.strftime("%Y-%m-%dT%H:00")
    temp = _hour_value(hourly, "temperature_2m", hour_iso)
    precip_prob = _hour_value(hourly, "precipitation_probability", hour_iso)
    precip_mm = _hour_value(hourly, "precipitation", hour_iso)
    wind_mph = _hour_value(hourly, "wind_speed_10m", hour_iso)
    wind_deg = _hour_value(hourly, "wind_direction_10m", hour_iso)
    code = _hour_value(hourly, "weathercode", hour_iso)
    delay = False
    if roof != "dome":
        if precip_prob is not None:
            delay = precip_prob >= DELAY_PRECIP_PROB
        elif precip_mm is not None:
            delay = precip_mm > 0
    return {
        "temp_f": round(temp) if temp is not None else None,
        "conditions": conditions_text(code),
        "precip_prob": precip_prob,
        "wind_mph": round(wind_mph) if wind_mph is not None else None,
        "wind_compass": compass(wind_deg),
        "delay_risk": delay,
    }


def fetch_game_weather(home_abbr: str, game_date_utc: str) -> dict | None:
    """Weather row for one Friday game, or None when the venue is
    unknown. The caller attaches the venue name and game_pk."""
    venue = VENUES.get(home_abbr)
    if venue is None:
        return None
    name, lat, lon, roof = venue
    first_pitch = first_pitch_local(game_date_utc)
    raw = fetch_forecast(lat, lon, first_pitch.strftime("%Y-%m-%d"))
    row = parse_forecast(raw, first_pitch, roof)
    return {"venue": name, **row}
