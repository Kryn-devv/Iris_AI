"""Weather forecast tool backed by the free Open-Meteo APIs (no API key).

Flow: the spoken location ("weather in Pune") is geocoded through
``geocoding-api.open-meteo.com`` and the coordinates are fed to the
``api.open-meteo.com/v1/forecast`` endpoint, requesting the current
conditions plus a compact daily outlook. Open-Meteo reports conditions as
WMO weather interpretation codes; :data:`WMO_CODES` maps every code to a
human phrase and an emoji so results read naturally in chat and speech.

Pure helpers (exported for the NLU layer and tests, no I/O):

* :func:`describe_weather_code` — WMO code -> ("Partly cloudy", "⛅")
* :func:`build_forecast_params`  — exact query parameters for the forecast call
* :func:`format_daily`           — raw daily arrays -> list of per-day dicts
* :func:`compose_weather_speech` — "It's 24° and partly cloudy in Pune. ..."
* :func:`compose_weather_display` — multi-line transcript rendering
"""

from __future__ import annotations

from typing import Any

import httpx

from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.web.weather")

__all__ = [
    "WMO_CODES",
    "describe_weather_code",
    "normalize_days",
    "build_forecast_params",
    "format_daily",
    "compose_weather_speech",
    "compose_weather_display",
    "WeatherTool",
    "get_tools",
]

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

#: Fields requested from the ``current=`` and ``daily=`` blocks (kept in one
#: place so the URL builder and the response parser cannot drift apart).
CURRENT_FIELDS = (
    "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m"
)
DAILY_FIELDS = "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max"

MAX_FORECAST_DAYS = 7
DEFAULT_FORECAST_DAYS = 1

#: Complete WMO weather interpretation code table (as documented by
#: Open-Meteo): code -> (human phrase, emoji).
WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("Clear sky", "☀️"),
    1: ("Mainly clear", "🌤️"),
    2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"),
    45: ("Fog", "🌫️"),
    48: ("Depositing rime fog", "🌫️"),
    51: ("Light drizzle", "🌦️"),
    53: ("Moderate drizzle", "🌦️"),
    55: ("Dense drizzle", "🌧️"),
    56: ("Light freezing drizzle", "🥶"),
    57: ("Dense freezing drizzle", "🥶"),
    61: ("Slight rain", "🌦️"),
    63: ("Moderate rain", "🌧️"),
    65: ("Heavy rain", "🌧️"),
    66: ("Light freezing rain", "🧊"),
    67: ("Heavy freezing rain", "🧊"),
    71: ("Slight snowfall", "🌨️"),
    73: ("Moderate snowfall", "🌨️"),
    75: ("Heavy snowfall", "❄️"),
    77: ("Snow grains", "❄️"),
    80: ("Slight rain showers", "🌦️"),
    81: ("Moderate rain showers", "🌧️"),
    82: ("Violent rain showers", "⛈️"),
    85: ("Slight snow showers", "🌨️"),
    86: ("Heavy snow showers", "🌨️"),
    95: ("Thunderstorm", "⛈️"),
    96: ("Thunderstorm with slight hail", "⛈️"),
    99: ("Thunderstorm with heavy hail", "⛈️"),
}


# =============================================================================
# Pure helpers (no I/O — unit-testable)
# =============================================================================


def describe_weather_code(code: Any) -> tuple[str, str]:
    """Map a WMO weather code to ``(human phrase, emoji)``.

    Unknown or missing codes degrade to a neutral phrase instead of raising,
    because a weird code must never break an otherwise good forecast.
    """
    try:
        return WMO_CODES[int(code)]
    except (KeyError, TypeError, ValueError):
        return ("Unknown conditions", "🌡️")


def normalize_days(days: Any) -> int:
    """Coerce and clamp the requested forecast length to ``1..7`` days."""
    try:
        value = int(days)
    except (TypeError, ValueError):
        return DEFAULT_FORECAST_DAYS
    return max(1, min(MAX_FORECAST_DAYS, value))


def build_forecast_params(
    latitude: float, longitude: float, days: int = DEFAULT_FORECAST_DAYS, units: str = "metric"
) -> dict[str, Any]:
    """Build the exact query parameters for the Open-Meteo forecast endpoint.

    ``temperature_unit=fahrenheit`` is appended only for imperial units;
    Open-Meteo defaults to Celsius otherwise.
    """
    params: dict[str, Any] = {
        "latitude": latitude,
        "longitude": longitude,
        "current": CURRENT_FIELDS,
        "daily": DAILY_FIELDS,
        "timezone": "auto",
        "forecast_days": normalize_days(days),
    }
    if str(units).strip().lower() == "imperial":
        params["temperature_unit"] = "fahrenheit"
    return params


def _round_deg(value: Any) -> int | None:
    """Round a temperature for speech ("24.3" -> 24); ``None`` passes through."""
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _at(values: Any, index: int) -> Any:
    """Safe positional access into a possibly short/absent parallel array."""
    if isinstance(values, (list, tuple)) and index < len(values):
        return values[index]
    return None


def format_daily(daily_block: dict[str, Any], days: int) -> list[dict[str, Any]]:
    """Convert Open-Meteo's parallel daily arrays into per-day dictionaries."""
    dates = daily_block.get("time") or []
    highs = daily_block.get("temperature_2m_max")
    lows = daily_block.get("temperature_2m_min")
    codes = daily_block.get("weather_code")
    precip = daily_block.get("precipitation_probability_max")

    result: list[dict[str, Any]] = []
    for index, date in enumerate(dates[: normalize_days(days)]):
        code = _at(codes, index)
        condition, emoji = describe_weather_code(code)
        result.append(
            {
                "date": date,
                "high": _at(highs, index),
                "low": _at(lows, index),
                "weather_code": code,
                "condition": condition,
                "emoji": emoji,
                "precipitation_chance": _at(precip, index),
            }
        )
    return result


def compose_weather_speech(
    location_name: str,
    temperature: Any,
    condition: str,
    today: dict[str, Any] | None = None,
) -> str:
    """Compose the spoken summary.

    Example: ``It's 24° and partly cloudy in Pune. Today: high 29, low 21,
    10% chance of rain.``
    """
    now_deg = _round_deg(temperature)
    if now_deg is None:
        sentence = f"Here's the weather for {location_name}."
    else:
        sentence = f"It's {now_deg}° and {condition.lower()} in {location_name}."

    if today:
        parts: list[str] = []
        high = _round_deg(today.get("high"))
        low = _round_deg(today.get("low"))
        if high is not None:
            parts.append(f"high {high}")
        if low is not None:
            parts.append(f"low {low}")
        chance = today.get("precipitation_chance")
        if chance is not None:
            try:
                parts.append(f"{int(round(float(chance)))}% chance of rain")
            except (TypeError, ValueError):
                pass
        if parts:
            sentence += " Today: " + ", ".join(parts) + "."
    return sentence


def compose_weather_display(
    location_label: str, current: dict[str, Any], daily: list[dict[str, Any]]
) -> str:
    """Compose the longer multi-line transcript rendering of a forecast."""
    unit = current.get("temperature_unit") or "°"
    lines: list[str] = []

    now_deg = _round_deg(current.get("temperature"))
    feels = _round_deg(current.get("feels_like"))
    head = f"{current.get('emoji', '')} {location_label}".strip()
    if now_deg is not None:
        head += f" — {now_deg}{unit}"
        if feels is not None and feels != now_deg:
            head += f" (feels like {feels}{unit})"
        head += f", {str(current.get('condition', '')).lower()}"
    lines.append(head)

    extras: list[str] = []
    if current.get("humidity") is not None:
        extras.append(f"Humidity {current['humidity']}%")
    if current.get("wind_speed") is not None:
        extras.append(f"Wind {current['wind_speed']} {current.get('wind_unit', 'km/h')}")
    if extras:
        lines.append(" · ".join(extras))

    for day in daily:
        high = _round_deg(day.get("high"))
        low = _round_deg(day.get("low"))
        piece = f"{day['date']}: {day['emoji']} {day['condition']}"
        if high is not None and low is not None:
            piece += f", high {high}{unit} / low {low}{unit}"
        if day.get("precipitation_chance") is not None:
            piece += f" · {day['precipitation_chance']}% rain"
        lines.append(piece)

    return "\n".join(lines)


def build_weather_result(
    place: dict[str, Any], data: dict[str, Any], days: int, units: str
) -> dict[str, Any]:
    """Shape a geocoding hit plus a raw forecast payload into the tool result."""
    current_block = data.get("current") or {}
    current_units = data.get("current_units") or {}
    code = current_block.get("weather_code")
    condition, emoji = describe_weather_code(code)

    default_unit = "°F" if units == "imperial" else "°C"
    current = {
        "temperature": current_block.get("temperature_2m"),
        "feels_like": current_block.get("apparent_temperature"),
        "humidity": current_block.get("relative_humidity_2m"),
        "wind_speed": current_block.get("wind_speed_10m"),
        "wind_unit": current_units.get("wind_speed_10m") or "km/h",
        "weather_code": code,
        "condition": condition,
        "emoji": emoji,
        "temperature_unit": current_units.get("temperature_2m") or default_unit,
    }

    daily = format_daily(data.get("daily") or {}, days)
    today = daily[0] if daily else None

    location = {
        "name": place.get("name", ""),
        "admin1": place.get("admin1", ""),
        "country": place.get("country", ""),
        "latitude": place.get("latitude"),
        "longitude": place.get("longitude"),
        "timezone": data.get("timezone") or place.get("timezone", ""),
    }
    label = ", ".join(p for p in (location["name"], location["admin1"], location["country"]) if p)

    return {
        "location": location,
        "units": units,
        "current": current,
        "daily": daily,
        "speech": compose_weather_speech(location["name"], current["temperature"], condition, today),
        "display": compose_weather_display(label, current, daily),
    }


# =============================================================================
# Tool
# =============================================================================


class WeatherTool(BaseTool):
    """Current conditions and daily forecast via Open-Meteo (keyless & free)."""

    name = "weather"
    description = "Gets the current weather and a short daily forecast for a location."
    permission_level = PermissionLevel.NETWORK_ACTION
    category = ToolCategory.WEB
    aliases = ("forecast", "temperature", "weather_report")
    network = True
    mutating = False
    examples = (
        ToolExample(utterance="what's the weather in Pune", arguments={"location": "Pune"}),
        ToolExample(
            utterance="three day forecast for London",
            arguments={"location": "London", "days": 3},
        ),
        ToolExample(utterance="how hot is it outside", arguments={}),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "location": {
                "type": "string",
                "description": (
                    "City or place name, e.g. 'Pune' or 'Berlin, Germany'. "
                    "Falls back to the configured DEFAULT_LOCATION when omitted."
                ),
            },
            "days": {
                "type": "integer",
                "description": "Number of forecast days to include (1-7).",
                "minimum": 1,
                "maximum": MAX_FORECAST_DAYS,
                "default": DEFAULT_FORECAST_DAYS,
            },
        },
        required=[],
    )

    #: Optional httpx transport override — tests inject ``httpx.MockTransport``.
    _transport: httpx.AsyncBaseTransport | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            headers={"User-Agent": settings.WEB_USER_AGENT},
            follow_redirects=True,
            transport=self._transport,
        )

    async def _geocode(self, client: httpx.AsyncClient, query: str) -> dict[str, Any]:
        """Resolve a place name to coordinates via Open-Meteo geocoding."""
        try:
            response = await client.get(
                GEOCODING_URL,
                params={"name": query, "count": 1, "language": "en", "format": "json"},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("Geocoding request failed for %r: %s", query, exc)
            raise ToolError(
                f"The weather service could not be reached while looking up '{query}': {exc}",
                speech="I couldn't reach the weather service.",
            ) from exc

        results = payload.get("results") or []
        if not results:
            raise ToolError(
                f"Location '{query}' not found. Check the spelling or try a nearby larger city.",
                speech=f"I couldn't find a place called {query}.",
            )
        return results[0]

    async def _forecast(
        self, client: httpx.AsyncClient, place: dict[str, Any], days: int, units: str
    ) -> dict[str, Any]:
        """Fetch the forecast payload for resolved coordinates."""
        params = build_forecast_params(
            latitude=place.get("latitude"),
            longitude=place.get("longitude"),
            days=days,
            units=units,
        )
        try:
            response = await client.get(FORECAST_URL, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.warning("Forecast request failed for %r: %s", place.get("name"), exc)
            raise ToolError(
                f"The weather service returned an error for '{place.get('name', '?')}': {exc}",
                speech="The weather service isn't responding right now.",
            ) from exc

    async def _run(
        self, location: str | None = None, days: int = DEFAULT_FORECAST_DAYS, **kwargs: Any
    ) -> dict[str, Any]:
        query = (location or "").strip() or (settings.DEFAULT_LOCATION or "").strip()
        if not query:
            raise ToolError(
                "No location given and no DEFAULT_LOCATION configured. "
                "Ask for a place ('weather in Pune') or set DEFAULT_LOCATION in the IRIS settings.",
                speech="Which city should I check the weather for?",
            )

        day_count = normalize_days(days)
        units = "imperial" if str(settings.WEATHER_UNITS).strip().lower() == "imperial" else "metric"

        async with self._client() as client:
            place = await self._geocode(client, query)
            data = await self._forecast(client, place, day_count, units)

        return build_weather_result(place, data, day_count, units)


def get_tools() -> list[BaseTool]:
    return [WeatherTool()]
