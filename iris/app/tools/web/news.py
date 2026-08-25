"""Headline aggregation over the user's configured RSS/Atom feeds.

The :class:`NewsTool` fetches every feed in ``settings.NEWS_FEEDS``
concurrently, parses both RSS 2.0 (``<item>``) and Atom (``<entry>``)
documents with the standard library, merges the results, drops
near-duplicate titles, sorts newest-first and answers with the top N.

Feed failures are *tolerated per feed*: unreachable or malformed feeds are
reported in the result's ``skipped`` list, and the tool only fails outright
when every single feed failed.

Pure helpers (exported for tests, no I/O):

* :func:`parse_feed`        — RSS/Atom XML text -> list of headline dicts
* :func:`parse_date`        — best-effort RFC 2822 / ISO 8601 date parsing
* :func:`normalize_title`   — canonical form used for de-duplication
* :func:`dedupe_headlines`  — drop near-identical titles across feeds
* :func:`filter_by_topic`   — case-insensitive topic filter
* :func:`normalize_limit`   — clamp the requested headline count to 1..20
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.web.news")

__all__ = [
    "parse_feed",
    "parse_date",
    "normalize_title",
    "dedupe_headlines",
    "filter_by_topic",
    "normalize_limit",
    "NewsTool",
    "get_tools",
]

DEFAULT_LIMIT = 8
MAX_LIMIT = 20

#: Similarity ratio at or above which two normalized titles count as the
#: same story ("OpenAI releases new model" vs "OpenAI Releases New Model!").
_NEAR_DUPLICATE_RATIO = 0.92

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# =============================================================================
# Pure helpers (no I/O — unit-testable)
# =============================================================================


def _localname(tag: Any) -> str:
    """Strip the ``{namespace}`` prefix from an ElementTree tag."""
    text = str(tag)
    return text.rsplit("}", 1)[-1] if "}" in text else text


def _children_named(element: ET.Element, name: str) -> list[ET.Element]:
    """Direct children whose local (namespace-free) tag name matches."""
    return [child for child in element if _localname(child.tag) == name]


def _child_text(element: ET.Element, *names: str) -> str:
    """Text of the first direct child matching any of ``names``.

    Exact (un-namespaced) tag matches win over namespaced local-name matches
    so ``<title>`` beats ``<media:title>`` regardless of document order.
    """
    for name in names:
        for child in element:
            if child.tag == name and child.text:
                return child.text.strip()
    for name in names:
        for child in element:
            if _localname(child.tag) == name and child.text:
                return child.text.strip()
    return ""


def _atom_link(entry: ET.Element) -> str:
    """Best ``href`` for an Atom entry: prefer ``rel='alternate'`` links."""
    links = _children_named(entry, "link")
    for link in links:
        rel = link.get("rel", "alternate")
        if rel == "alternate" and link.get("href"):
            return link.get("href", "").strip()
    for link in links:
        if link.get("href"):
            return link.get("href", "").strip()
    for link in links:
        if link.text:
            return link.text.strip()
    return ""


def parse_feed(xml_text: str, source: str = "") -> list[dict[str, str]]:
    """Parse RSS 2.0 / RDF / Atom XML into headline dictionaries.

    Returns ``[{"title", "link", "published", "source"}, ...]``. The feed's
    own title is used as the source unless ``source`` is supplied. Raises
    ``ValueError`` on unparseable XML so callers can count the feed as
    skipped; individual bad entries are simply dropped.
    """
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        raise ValueError(f"Not valid RSS/Atom XML: {exc}") from exc

    items: list[dict[str, str]] = []

    if _localname(root.tag) == "feed":  # ------------------------------- Atom
        feed_title = source or _child_text(root, "title")
        for entry in root.iter():
            if _localname(entry.tag) != "entry":
                continue
            title = _child_text(entry, "title")
            if not title:
                continue
            items.append(
                {
                    "title": re.sub(r"\s+", " ", title),
                    "link": _atom_link(entry),
                    "published": _child_text(entry, "published", "updated"),
                    "source": feed_title,
                }
            )
        return items

    # ------------------------------------------------------------ RSS / RDF
    channels = [el for el in root.iter() if _localname(el.tag) == "channel"]
    feed_title = source or (_child_text(channels[0], "title") if channels else "")
    for item in root.iter():
        if _localname(item.tag) != "item":
            continue
        title = _child_text(item, "title")
        if not title:
            continue
        items.append(
            {
                "title": re.sub(r"\s+", " ", title),
                "link": _child_text(item, "link"),
                "published": _child_text(item, "pubDate", "date", "published", "updated"),
                "source": feed_title,
            }
        )
    return items


def parse_date(text: Any) -> datetime | None:
    """Best-effort parse of RFC 2822 ('Tue, 25 Aug 2026 ...') or ISO 8601 dates.

    Naive results are assumed UTC so every returned datetime is comparable.
    Returns ``None`` when the text is empty or unparseable.
    """
    raw = str(text or "").strip()
    if not raw:
        return None

    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        parsed = None

    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def normalize_title(title: Any) -> str:
    """Canonical form of a headline used for near-duplicate detection."""
    lowered = re.sub(r"[^a-z0-9 ]+", " ", str(title or "").lower())
    return re.sub(r"\s+", " ", lowered).strip()


def dedupe_headlines(
    items: list[dict[str, Any]], threshold: float = _NEAR_DUPLICATE_RATIO
) -> list[dict[str, Any]]:
    """Drop items whose titles are identical or nearly identical to a kept one.

    First occurrence wins, so call this *after* sorting newest-first to keep
    the freshest copy of a syndicated story.
    """
    kept: list[dict[str, Any]] = []
    seen: list[str] = []
    for item in items:
        norm = normalize_title(item.get("title"))
        if not norm:
            continue
        if any(
            norm == prior or SequenceMatcher(None, norm, prior).ratio() >= threshold
            for prior in seen
        ):
            continue
        seen.append(norm)
        kept.append(item)
    return kept


def filter_by_topic(items: list[dict[str, Any]], topic: Any) -> list[dict[str, Any]]:
    """Case-insensitive substring filter on headline titles; no-op if empty."""
    needle = str(topic or "").strip().lower()
    if not needle:
        return list(items)
    return [item for item in items if needle in str(item.get("title", "")).lower()]


def normalize_limit(limit: Any) -> int:
    """Coerce and clamp the requested headline count to ``1..20``."""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, value))


def _brief_error(exc: BaseException) -> str:
    """One-line description of a per-feed failure for the ``skipped`` list."""
    text = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
    return text[:200]


# =============================================================================
# Tool
# =============================================================================


class NewsTool(BaseTool):
    """Top headlines aggregated from the configured RSS/Atom feeds."""

    name = "news"
    description = "Fetches the latest news headlines from the configured RSS/Atom feeds."
    permission_level = PermissionLevel.NETWORK_ACTION
    category = ToolCategory.WEB
    aliases = ("headlines", "latest_news", "news_headlines")
    network = True
    mutating = False
    examples = (
        ToolExample(utterance="what's in the news", arguments={}),
        ToolExample(utterance="give me five tech headlines", arguments={"topic": "tech", "limit": 5}),
        ToolExample(
            utterance="headlines from hacker news",
            arguments={"feed_url": "https://hnrss.org/frontpage"},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "topic": {
                "type": "string",
                "description": "Optional keyword to filter headlines by (case-insensitive).",
            },
            "limit": {
                "type": "integer",
                "description": "How many headlines to return (1-20).",
                "minimum": 1,
                "maximum": MAX_LIMIT,
                "default": DEFAULT_LIMIT,
            },
            "feed_url": {
                "type": "string",
                "description": "Optional single RSS/Atom feed URL to use instead of the configured feeds.",
            },
        },
        required=[],
    )

    #: Optional httpx transport override — tests inject ``httpx.MockTransport``.
    _transport: httpx.AsyncBaseTransport | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            headers={
                "User-Agent": settings.WEB_USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            },
            follow_redirects=True,
            transport=self._transport,
        )

    async def _fetch_feed(self, client: httpx.AsyncClient, url: str) -> list[dict[str, str]]:
        """Fetch and parse one feed; any exception marks the feed as skipped."""
        response = await client.get(url)
        response.raise_for_status()
        if len(response.content) > settings.WEB_FETCH_MAX_BYTES:
            raise ValueError(
                f"Feed exceeds the {settings.WEB_FETCH_MAX_BYTES}-byte limit and was skipped."
            )
        return parse_feed(response.text)

    async def _run(
        self,
        topic: str | None = None,
        limit: int = DEFAULT_LIMIT,
        feed_url: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        count = normalize_limit(limit)
        override = (feed_url or "").strip()
        feeds = [override] if override else [str(url).strip() for url in settings.NEWS_FEEDS if str(url).strip()]
        if not feeds:
            raise ToolError(
                "No news feeds are configured. Set NEWS_FEEDS in the IRIS settings "
                "or pass a feed_url argument.",
                speech="I don't have any news feeds configured yet.",
            )

        async with self._client() as client:
            outcomes = await asyncio.gather(
                *(self._fetch_feed(client, url) for url in feeds),
                return_exceptions=True,
            )

        merged: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for url, outcome in zip(feeds, outcomes):
            if isinstance(outcome, BaseException):
                logger.warning("News feed %s failed: %s", url, outcome)
                skipped.append({"feed": url, "error": _brief_error(outcome)})
            else:
                merged.extend(outcome)

        if skipped and len(skipped) == len(feeds):
            details = "; ".join(f"{entry['feed']} ({entry['error']})" for entry in skipped)
            raise ToolError(
                f"All {len(feeds)} news feeds failed: {details}",
                speech="I couldn't reach any of the news feeds right now.",
            )

        # Newest first; undated items sink to the end in their original order.
        merged.sort(key=lambda item: parse_date(item.get("published")) or _EPOCH, reverse=True)
        selected = dedupe_headlines(filter_by_topic(merged, topic))[:count]

        headlines = [
            {
                "rank": index + 1,
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "published": item.get("published", ""),
                "source": item.get("source", ""),
            }
            for index, item in enumerate(selected)
        ]

        if headlines:
            about = f" about {topic.strip()}" if topic and str(topic).strip() else ""
            speech = f"Here are the top {len(headlines)} headlines{about}."
        elif topic and str(topic).strip():
            speech = f"I couldn't find any headlines about {str(topic).strip()}."
        else:
            speech = "I couldn't find any headlines right now."

        display_lines = [
            f"{entry['rank']}. {entry['title']}"
            + (f" — {entry['source']}" if entry["source"] else "")
            for entry in headlines
        ]
        if skipped:
            display_lines.append(f"(Skipped {len(skipped)} unreachable feed(s).)")

        return {
            "headlines": headlines,
            "count": len(headlines),
            "topic": (str(topic).strip() if topic else None),
            "sources": sorted({entry["source"] for entry in headlines if entry["source"]}),
            "feeds_checked": len(feeds),
            "skipped": skipped,
            "speech": speech,
            "display": "\n".join(display_lines) if display_lines else speech,
        }


def get_tools() -> list[BaseTool]:
    return [NewsTool()]
