"""Tests for the web weather (Open-Meteo) and news (RSS/Atom) tools.

Everything runs offline on headless Linux: all HTTP is served through
``httpx.MockTransport`` handlers so no real network request is ever made.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any

import httpx
import pytest

from iris.app.core.config import settings
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory
from iris.app.tools.web.news import (
    MAX_LIMIT,
    NewsTool,
    dedupe_headlines,
    filter_by_topic,
    normalize_limit,
    normalize_title,
    parse_date,
    parse_feed,
)
from iris.app.tools.web.weather import (
    CURRENT_FIELDS,
    DAILY_FIELDS,
    WMO_CODES,
    WeatherTool,
    build_forecast_params,
    compose_weather_display,
    compose_weather_speech,
    describe_weather_code,
    format_daily,
    normalize_days,
)


# =============================================================================
# Fixtures: canned API payloads and feed documents
# =============================================================================

GEOCODE_HIT = {
    "results": [
        {
            "name": "Pune",
            "latitude": 18.52,
            "longitude": 73.86,
            "country": "India",
            "admin1": "Maharashtra",
            "timezone": "Asia/Kolkata",
        }
    ]
}

FORECAST_JSON = {
    "timezone": "Asia/Kolkata",
    "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
    "current": {
        "temperature_2m": 24.3,
        "apparent_temperature": 25.1,
        "relative_humidity_2m": 58,
        "weather_code": 2,
        "wind_speed_10m": 11.4,
    },
    "daily": {
        "time": ["2026-08-25", "2026-08-26", "2026-08-27"],
        "temperature_2m_max": [29.4, 30.1, 28.0],
        "temperature_2m_min": [21.2, 20.8, 19.5],
        "weather_code": [2, 61, 95],
        "precipitation_probability_max": [10, 40, 80],
    },
}

RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:media="http://search.yahoo.com/mrss/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Example News</title>
    <link>https://news.example.com</link>
    <item>
      <media:title>thumbnail caption that must not win</media:title>
      <title>Markets rally after rate cut</title>
      <link>https://news.example.com/markets</link>
      <pubDate>Tue, 25 Aug 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>New AI model released
        by lab</title>
      <link>https://news.example.com/ai</link>
      <pubDate>Mon, 24 Aug 2026 09:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Monsoon arrives early in Pune</title>
      <link>https://news.example.com/monsoon</link>
      <pubDate>Sun, 23 Aug 2026 08:30:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Wire</title>
  <link rel="self" href="https://atom.example.com/feed.xml"/>
  <entry>
    <title>Quantum breakthrough announced</title>
    <link rel="self" href="https://atom.example.com/self/1"/>
    <link rel="alternate" href="https://atom.example.com/quantum"/>
    <published>2026-08-25T12:00:00Z</published>
  </entry>
  <entry>
    <title>Open source project hits 1.0</title>
    <link href="https://atom.example.com/opensource"/>
    <updated>2026-08-24T06:00:00+02:00</updated>
  </entry>
</feed>
"""


def weather_transport(
    recorded: list[httpx.Request],
    geocode_json: dict[str, Any],
    forecast_json: dict[str, Any] | None = None,
) -> httpx.MockTransport:
    """MockTransport serving the geocoding and forecast endpoints."""

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json=geocode_json)
        assert request.url.host == "api.open-meteo.com"
        return httpx.Response(200, json=forecast_json or FORECAST_JSON)

    return httpx.MockTransport(handler)


# =============================================================================
# Weather: pure helpers
# =============================================================================


def test_wmo_table_is_complete() -> None:
    expected = {
        0, 1, 2, 3, 45, 48,
        51, 53, 55, 56, 57, 61, 63, 65, 66, 67,
        71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99,
    }
    assert set(WMO_CODES) == expected
    for code, (text, emoji) in WMO_CODES.items():
        assert text and text[0].isupper(), f"bad text for code {code}"
        assert emoji, f"missing emoji for code {code}"


def test_describe_weather_code_known_unknown_and_coercion() -> None:
    assert describe_weather_code(0) == ("Clear sky", "☀️")
    assert describe_weather_code(2) == ("Partly cloudy", "⛅")
    assert describe_weather_code("95") == ("Thunderstorm", "⛈️")
    text, emoji = describe_weather_code(42)  # not a WMO code
    assert text == "Unknown conditions" and emoji
    assert describe_weather_code(None)[0] == "Unknown conditions"
    assert describe_weather_code("garbage")[0] == "Unknown conditions"


def test_normalize_days_clamps() -> None:
    assert normalize_days(1) == 1
    assert normalize_days(7) == 7
    assert normalize_days(9) == 7
    assert normalize_days(0) == 1
    assert normalize_days(-3) == 1
    assert normalize_days("3") == 3
    assert normalize_days("junk") == 1
    assert normalize_days(None) == 1


def test_build_forecast_params_metric_and_imperial() -> None:
    metric = build_forecast_params(18.52, 73.86, days=3, units="metric")
    assert metric["latitude"] == 18.52 and metric["longitude"] == 73.86
    assert metric["current"] == CURRENT_FIELDS
    assert metric["daily"] == DAILY_FIELDS
    assert metric["timezone"] == "auto"
    assert metric["forecast_days"] == 3
    assert "temperature_unit" not in metric

    imperial = build_forecast_params(40.7, -74.0, days=99, units="imperial")
    assert imperial["temperature_unit"] == "fahrenheit"
    assert imperial["forecast_days"] == 7  # clamped


def test_format_daily_slices_and_maps_codes() -> None:
    days = format_daily(FORECAST_JSON["daily"], 2)
    assert len(days) == 2
    first = days[0]
    assert first["date"] == "2026-08-25"
    assert first["high"] == 29.4 and first["low"] == 21.2
    assert first["condition"] == "Partly cloudy" and first["emoji"] == "⛅"
    assert first["precipitation_chance"] == 10
    assert days[1]["condition"] == "Slight rain"

    # Ragged/absent arrays never raise.
    ragged = format_daily({"time": ["2026-08-25"], "weather_code": []}, 5)
    assert ragged[0]["high"] is None
    assert ragged[0]["condition"] == "Unknown conditions"
    assert format_daily({}, 3) == []


def test_compose_weather_speech_matches_contract_example() -> None:
    today = {"high": 29.4, "low": 21.2, "precipitation_chance": 10}
    speech = compose_weather_speech("Pune", 24.3, "Partly cloudy", today)
    assert speech == "It's 24° and partly cloudy in Pune. Today: high 29, low 21, 10% chance of rain."


def test_compose_weather_speech_degrades_gracefully() -> None:
    assert compose_weather_speech("Pune", 24.0, "Fog", None) == "It's 24° and fog in Pune."
    no_precip = compose_weather_speech("Oslo", -1.6, "Overcast", {"high": 2, "low": -5})
    assert no_precip == "It's -2° and overcast in Oslo. Today: high 2, low -5."
    assert compose_weather_speech("Pune", None, "Clear sky", None) == "Here's the weather for Pune."


def test_compose_weather_display_mentions_key_facts() -> None:
    current = {
        "temperature": 24.3,
        "feels_like": 25.1,
        "humidity": 58,
        "wind_speed": 11.4,
        "wind_unit": "km/h",
        "condition": "Partly cloudy",
        "emoji": "⛅",
        "temperature_unit": "°C",
    }
    display = compose_weather_display("Pune, Maharashtra, India", current, format_daily(FORECAST_JSON["daily"], 3))
    assert "Pune, Maharashtra, India" in display
    assert "24°C" in display and "partly cloudy" in display
    assert "Humidity 58%" in display and "11.4 km/h" in display
    assert "2026-08-27" in display and "80% rain" in display


# =============================================================================
# Weather: tool execution through httpx.MockTransport
# =============================================================================


async def test_weather_tool_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "WEATHER_UNITS", "metric")
    recorded: list[httpx.Request] = []
    tool = WeatherTool()
    tool._transport = weather_transport(recorded, GEOCODE_HIT)

    result = await tool.execute(location="Pune", days=3)

    assert result.success, result.error
    assert result.speech == (
        "It's 24° and partly cloudy in Pune. Today: high 29, low 21, 10% chance of rain."
    )
    payload = result.result
    assert payload["location"]["name"] == "Pune"
    assert payload["location"]["country"] == "India"
    assert payload["units"] == "metric"
    assert payload["current"]["temperature"] == 24.3
    assert payload["current"]["condition"] == "Partly cloudy"
    assert len(payload["daily"]) == 3

    geo_request, forecast_request = recorded
    assert geo_request.url.host == "geocoding-api.open-meteo.com"
    assert geo_request.url.params["name"] == "Pune"
    assert geo_request.url.params["count"] == "1"
    assert forecast_request.url.params["forecast_days"] == "3"
    assert forecast_request.url.params["timezone"] == "auto"
    assert "temperature_unit" not in forecast_request.url.params


async def test_weather_tool_imperial_units_and_default_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "WEATHER_UNITS", "imperial")
    monkeypatch.setattr(settings, "DEFAULT_LOCATION", "Pune")
    recorded: list[httpx.Request] = []
    tool = WeatherTool()
    tool._transport = weather_transport(recorded, GEOCODE_HIT)

    result = await tool.execute()  # no location argument at all

    assert result.success, result.error
    assert result.result["units"] == "imperial"
    forecast_request = recorded[-1]
    assert forecast_request.url.params["temperature_unit"] == "fahrenheit"
    assert recorded[0].url.params["name"] == "Pune"


async def test_weather_tool_location_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "WEATHER_UNITS", "metric")
    recorded: list[httpx.Request] = []
    tool = WeatherTool()
    tool._transport = weather_transport(recorded, {"results": []})

    result = await tool.execute(location="Atlantis-on-Mars")

    assert not result.success
    assert "not found" in (result.error or "")
    assert "Atlantis-on-Mars" in (result.error or "")
    assert len(recorded) == 1  # never reached the forecast endpoint


async def test_weather_tool_requires_some_location(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DEFAULT_LOCATION", "")
    tool = WeatherTool()
    tool._transport = httpx.MockTransport(lambda request: httpx.Response(500))

    result = await tool.execute()

    assert not result.success
    assert "DEFAULT_LOCATION" in (result.error or "")


async def test_weather_tool_service_error_is_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "WEATHER_UNITS", "metric")
    tool = WeatherTool()
    tool._transport = httpx.MockTransport(lambda request: httpx.Response(503))

    result = await tool.execute(location="Pune")

    assert not result.success
    assert "could not be reached" in (result.error or "")


def test_weather_tool_metadata() -> None:
    tool = WeatherTool()
    assert tool.name == "weather"
    assert tool.network is True and tool.mutating is False
    assert tool.permission_level == PermissionLevel.NETWORK_ACTION
    assert tool.category == ToolCategory.WEB
    assert set(tool.aliases) == {"forecast", "temperature", "weather_report"}
    assert tool.examples and tool.input_schema.properties.keys() == {"location", "days"}


# =============================================================================
# News: pure helpers
# =============================================================================


def test_parse_feed_rss() -> None:
    items = parse_feed(RSS_FIXTURE)
    assert len(items) == 3
    first = items[0]
    assert first["title"] == "Markets rally after rate cut"  # media:title did not win
    assert first["link"] == "https://news.example.com/markets"
    assert first["published"] == "Tue, 25 Aug 2026 10:00:00 GMT"
    assert first["source"] == "Example News"
    assert items[1]["title"] == "New AI model released by lab"  # whitespace collapsed


def test_parse_feed_atom() -> None:
    items = parse_feed(ATOM_FIXTURE)
    assert len(items) == 2
    assert items[0]["title"] == "Quantum breakthrough announced"
    assert items[0]["link"] == "https://atom.example.com/quantum"  # rel=alternate preferred
    assert items[0]["published"] == "2026-08-25T12:00:00Z"
    assert items[0]["source"] == "Atom Wire"
    assert items[1]["link"] == "https://atom.example.com/opensource"
    assert items[1]["published"] == "2026-08-24T06:00:00+02:00"  # falls back to <updated>


def test_parse_feed_source_override_and_bad_xml() -> None:
    items = parse_feed(RSS_FIXTURE, source="Custom Source")
    assert all(item["source"] == "Custom Source" for item in items)
    with pytest.raises(ValueError):
        parse_feed("this is not xml at all <<<")


def test_parse_date_variants() -> None:
    rfc = parse_date("Tue, 25 Aug 2026 10:00:00 GMT")
    iso = parse_date("2026-08-25T12:00:00Z")
    assert rfc is not None and iso is not None
    assert rfc < iso
    naive = parse_date("2026-08-25T12:00:00")
    assert naive is not None and naive.tzinfo == timezone.utc
    assert parse_date("") is None
    assert parse_date(None) is None
    assert parse_date("next Tuesday-ish") is None


def test_normalize_title_and_dedupe() -> None:
    assert normalize_title("OpenAI Releases New Model!") == "openai releases new model"
    items = [
        {"title": "OpenAI releases new model"},
        {"title": "OpenAI Releases New Model!"},       # exact after normalization
        {"title": "OpenAI releases new model today"},   # near-identical
        {"title": "Completely different story"},
        {"title": "   "},                               # blank titles are dropped
    ]
    kept = dedupe_headlines(items)
    assert [item["title"] for item in kept] == [
        "OpenAI releases new model",
        "Completely different story",
    ]


def test_filter_by_topic() -> None:
    items = parse_feed(RSS_FIXTURE)
    assert [i["title"] for i in filter_by_topic(items, "ai MODEL")] == ["New AI model released by lab"]
    assert [i["title"] for i in filter_by_topic(items, "pune")] == ["Monsoon arrives early in Pune"]
    assert filter_by_topic(items, None) == items
    assert filter_by_topic(items, "  ") == items
    assert filter_by_topic(items, "zebra") == []


def test_normalize_limit_clamps() -> None:
    assert normalize_limit(8) == 8
    assert normalize_limit(0) == 1
    assert normalize_limit(50) == MAX_LIMIT
    assert normalize_limit("5") == 5
    assert normalize_limit("junk") == 8
    assert normalize_limit(None) == 8


# =============================================================================
# News: tool execution through httpx.MockTransport
# =============================================================================


async def test_news_tool_merges_and_tolerates_one_bad_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ok_url = "https://news.example.com/rss"
    bad_url = "https://down.example.com/rss"
    monkeypatch.setattr(settings, "NEWS_FEEDS", [ok_url, bad_url])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "news.example.com":
            return httpx.Response(200, text=RSS_FIXTURE)
        return httpx.Response(500)

    tool = NewsTool()
    tool._transport = httpx.MockTransport(handler)

    result = await tool.execute(limit=5)

    assert result.success, result.error
    payload = result.result
    assert payload["count"] == 3
    assert result.speech == "Here are the top 3 headlines."
    # Newest first by pubDate.
    assert [h["title"] for h in payload["headlines"]] == [
        "Markets rally after rate cut",
        "New AI model released by lab",
        "Monsoon arrives early in Pune",
    ]
    assert payload["headlines"][0]["rank"] == 1
    assert payload["sources"] == ["Example News"]
    assert len(payload["skipped"]) == 1
    assert payload["skipped"][0]["feed"] == bad_url
    assert payload["skipped"][0]["error"]


async def test_news_tool_dedupes_across_feeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "NEWS_FEEDS",
        ["https://a.example.com/rss", "https://b.example.com/rss"],
    )
    duplicate_feed = RSS_FIXTURE.replace(
        "Markets rally after rate cut", "Markets Rally After Rate Cut!!"
    ).replace("Example News", "Mirror Feed")

    def handler(request: httpx.Request) -> httpx.Response:
        text = RSS_FIXTURE if request.url.host == "a.example.com" else duplicate_feed
        return httpx.Response(200, text=text)

    tool = NewsTool()
    tool._transport = httpx.MockTransport(handler)

    result = await tool.execute()

    assert result.success, result.error
    titles = [h["title"] for h in result.result["headlines"]]
    assert len([t for t in titles if "rally" in t.lower()]) == 1
    assert result.result["skipped"] == []


async def test_news_tool_topic_filter_and_atom_feed_url_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # NEWS_FEEDS must be ignored entirely when feed_url is given.
    monkeypatch.setattr(settings, "NEWS_FEEDS", ["https://must-not-be-fetched.example.com/rss"])
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(request.url.host)
        assert request.url.host == "atom.example.com"
        return httpx.Response(200, text=ATOM_FIXTURE)

    tool = NewsTool()
    tool._transport = httpx.MockTransport(handler)

    result = await tool.execute(feed_url="https://atom.example.com/feed.xml", topic="quantum")

    assert result.success, result.error
    assert fetched == ["atom.example.com"]
    payload = result.result
    assert payload["count"] == 1
    assert payload["headlines"][0]["title"] == "Quantum breakthrough announced"
    assert result.speech == "Here are the top 1 headlines about quantum."

    empty = await tool.execute(feed_url="https://atom.example.com/feed.xml", topic="zebra")
    assert empty.success
    assert empty.result["count"] == 0
    assert empty.speech == "I couldn't find any headlines about zebra."


async def test_news_tool_fails_only_when_every_feed_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "NEWS_FEEDS",
        ["https://down1.example.com/rss", "https://down2.example.com/rss"],
    )
    tool = NewsTool()
    tool._transport = httpx.MockTransport(lambda request: httpx.Response(503))

    result = await tool.execute()

    assert not result.success
    assert "feeds failed" in (result.error or "")
    assert "down1.example.com" in (result.error or "")


async def test_news_tool_limit_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "NEWS_FEEDS", ["https://big.example.com/rss"])
    words = [
        "avocado", "bulldozer", "cathedral", "dynamite", "eclipse", "falcon", "glacier",
        "harmonica", "iguana", "jasmine", "kayak", "lantern", "meteor", "nebula", "obsidian",
        "pyramid", "quasar", "rhubarb", "saxophone", "tundra", "umbrella", "volcano", "walrus",
        "xylophone", "yodel",
    ]
    items = "".join(
        f"<item><title>{words[i].capitalize()} report {i}</title>"
        f"<link>https://big.example.com/{i}</link>"
        f"<pubDate>Mon, {i + 1:02d} Jun 2026 0{i % 10}:00:00 GMT</pubDate></item>"
        for i in range(25)
    )
    big_feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f"<title>Big Feed</title>{items}</channel></rss>"
    )
    tool = NewsTool()
    tool._transport = httpx.MockTransport(lambda request: httpx.Response(200, text=big_feed))

    result = await tool.execute(limit=999)

    assert result.success, result.error
    assert result.result["count"] == MAX_LIMIT


async def test_news_tool_malformed_feed_counts_as_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "NEWS_FEEDS",
        ["https://good.example.com/rss", "https://broken.example.com/rss"],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "good.example.com":
            return httpx.Response(200, text=ATOM_FIXTURE)
        return httpx.Response(200, text="<html>definitely not a feed")

    tool = NewsTool()
    tool._transport = httpx.MockTransport(handler)

    result = await tool.execute()

    assert result.success, result.error
    assert result.result["count"] == 2
    assert len(result.result["skipped"]) == 1
    assert "broken.example.com" in result.result["skipped"][0]["feed"]


def test_news_tool_metadata() -> None:
    tool = NewsTool()
    assert tool.name == "news"
    assert tool.network is True and tool.mutating is False
    assert tool.permission_level == PermissionLevel.NETWORK_ACTION
    assert tool.category == ToolCategory.WEB
    assert set(tool.aliases) == {"headlines", "latest_news", "news_headlines"}
    assert tool.examples and tool.input_schema.properties.keys() == {"topic", "limit", "feed_url"}
