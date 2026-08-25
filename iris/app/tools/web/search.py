"""Web search tools: DuckDuckGo HTML search with layered fallbacks.

Gives IRIS the ability to search the open web without any API key:

* :class:`WebSearchTool`   — "search the web for rust async tutorials"
* :class:`QuickAnswerTool` — "what is the speed of light" (instant answers)

Provider strategy for :class:`WebSearchTool`:

1. **Primary** — the DuckDuckGo HTML endpoint (``https://html.duckduckgo.com/html/``),
   parsed with a small stdlib :class:`html.parser.HTMLParser` subclass. No
   JavaScript, no key, and stable CSS class names (``result__a`` for the link,
   ``result__snippet`` for the blurb).
2. **Secondary** — the DuckDuckGo instant-answer JSON API
   (``https://api.duckduckgo.com/``), whose abstract and related topics are
   reshaped into result rows when the HTML endpoint fails or returns nothing.
3. **Tertiary** — a self-hosted / public SearXNG instance, used *first* when the
   user sets ``WEB_SEARCH_PROVIDER=searx`` (with the DuckDuckGo chain kept as
   a fallback behind it).

The HTML extraction lives in the pure function :func:`parse_ddg_html` and the
redirect decoding in :func:`decode_ddg_redirect`, so both are unit-testable
against saved fixture strings without touching the network.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.web.search")

__all__ = [
    "decode_ddg_redirect",
    "parse_ddg_html",
    "parse_instant_answer",
    "WebSearchTool",
    "QuickAnswerTool",
    "get_tools",
]

#: DuckDuckGo endpoints (module constants so tests can assert against them).
DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_API_URL = "https://api.duckduckgo.com/"

#: Hard ceiling on how many results a single search may return.
MAX_RESULTS_CAP = 15
#: Per-request network timeout in seconds.
REQUEST_TIMEOUT = 10.0


def _default_headers() -> dict[str, str]:
    return {"User-Agent": settings.WEB_USER_AGENT}


# =============================================================================
# Pure parsing helpers
# =============================================================================


def decode_ddg_redirect(href: str) -> str:
    """Turn a DuckDuckGo result ``href`` into the real destination URL.

    DuckDuckGo wraps organic results in a redirect of the form
    ``//duckduckgo.com/l/?uddg=<percent-encoded url>&rut=...``. This unwraps
    the ``uddg`` parameter (percent-decoding it), upgrades scheme-relative
    hrefs to ``https``, and returns ``""`` for ad/tracking links
    (``duckduckgo.com/y.js``) so callers can skip them.
    """
    if not href:
        return ""
    candidate = "https:" + href if href.startswith("//") else href
    parts = urlsplit(candidate)
    host = (parts.hostname or "").lower()

    if host.endswith("duckduckgo.com"):
        if parts.path.startswith("/l/"):
            uddg = parse_qs(parts.query).get("uddg", [""])[0]
            # parse_qs already percent-decodes once; unquote again defensively
            # for doubly-encoded values (a no-op on plain URLs).
            target = unquote(uddg) if "%" in uddg else uddg
            return target if target.startswith(("http://", "https://")) else ""
        # y.js and friends are ad click-trackers, not results.
        return ""

    return candidate if candidate.startswith(("http://", "https://")) else ""


class _DDGHtmlExtractor(HTMLParser):
    """Extract ``result__a`` links and ``result__snippet`` blurbs from DDG HTML.

    The extractor collects text (including text inside nested inline tags such
    as ``<b>``) between the opening and closing tag of each interesting
    element, pairing every snippet with the most recent title that does not
    have one yet.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._collect_mode: str | None = None  # "title" | "snippet"
        self._collect_tag: str = ""
        self._depth = 0
        self._buffer: list[str] = []
        self._href = ""

    # ------------------------------------------------------------- callbacks
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = (attr_map.get("class") or "").split()

        if self._collect_mode is None:
            if tag == "a" and "result__a" in classes:
                self._collect_mode = "title"
                self._collect_tag = tag
                self._depth = 1
                self._buffer = []
                self._href = attr_map.get("href") or ""
            elif "result__snippet" in classes:
                self._collect_mode = "snippet"
                self._collect_tag = tag
                self._depth = 1
                self._buffer = []
        elif tag == self._collect_tag:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._collect_mode is None or tag != self._collect_tag:
            return
        self._depth -= 1
        if self._depth > 0:
            return

        text = " ".join("".join(self._buffer).split())
        if self._collect_mode == "title":
            url = decode_ddg_redirect(self._href)
            if url and text:
                self.results.append({"title": text, "url": url, "snippet": ""})
        elif self.results and text:
            for entry in reversed(self.results):
                if not entry["snippet"]:
                    entry["snippet"] = text
                    break

        self._collect_mode = None
        self._collect_tag = ""
        self._buffer = []
        self._href = ""

    def handle_data(self, data: str) -> None:
        if self._collect_mode is not None:
            self._buffer.append(data)


def parse_ddg_html(html: str) -> list[dict[str, str]]:
    """Parse DuckDuckGo HTML-endpoint markup into result rows.

    Returns a list of ``{"title", "url", "snippet"}`` dicts in page order.
    Redirect URLs are decoded to their real destination and ad rows (whose
    links stay on ``duckduckgo.com``) are dropped. Pure function — safe to
    call on any saved fixture string.
    """
    extractor = _DDGHtmlExtractor()
    extractor.feed(html or "")
    extractor.close()
    return extractor.results


def parse_instant_answer(data: dict[str, Any], max_results: int) -> list[dict[str, str]]:
    """Reshape a DuckDuckGo instant-answer JSON payload into result rows.

    Uses ``AbstractText`` for the lead result and flattens ``RelatedTopics``
    (including nested ``Topics`` groups) into further rows. Pure function.
    """
    results: list[dict[str, str]] = []

    abstract = (data.get("AbstractText") or "").strip()
    if abstract:
        results.append(
            {
                "title": data.get("Heading") or data.get("AbstractSource") or "Summary",
                "url": data.get("AbstractURL") or "",
                "snippet": abstract,
            }
        )

    def _walk(topics: list[Any]) -> None:
        for item in topics:
            if len(results) >= max_results:
                return
            if not isinstance(item, dict):
                continue
            if "Topics" in item:
                _walk(item.get("Topics") or [])
                continue
            text = (item.get("Text") or "").strip()
            url = item.get("FirstURL") or ""
            if not text or not url:
                continue
            title = text.split(" - ", 1)[0].strip() or text
            results.append({"title": title, "url": url, "snippet": text})

    _walk(data.get("RelatedTopics") or [])
    return results[:max_results]


def _clamp_max_results(value: Any, default: int = 6) -> int:
    """Coerce ``max_results`` into the 1..MAX_RESULTS_CAP range."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, MAX_RESULTS_CAP))


# =============================================================================
# Tools
# =============================================================================


class WebSearchTool(BaseTool):
    """Search the web via DuckDuckGo (HTML endpoint) with JSON/SearXNG fallbacks."""

    name = "web_search"
    description = "Searches the web and returns titles, URLs and snippets for a query."
    permission_level = PermissionLevel.NETWORK_ACTION
    category = ToolCategory.WEB
    aliases = ("search_web", "google_search", "internet_search", "search")
    network = True
    mutating = False
    examples = (
        ToolExample(utterance="search the web for python 3.13 release notes",
                    arguments={"query": "python 3.13 release notes"}),
        ToolExample(utterance="google the best mechanical keyboards",
                    arguments={"query": "best mechanical keyboards", "max_results": 5}),
        ToolExample(utterance="find news about the james webb telescope",
                    arguments={"query": "james webb telescope news"}),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "query": {
                "type": "string",
                "description": "What to search for, e.g. 'python 3.13 release notes'.",
            },
            "max_results": {
                "type": "integer",
                "description": f"How many results to return (default 6, max {MAX_RESULTS_CAP}).",
                "default": 6,
            },
        },
        required=["query"],
    )

    #: Optional httpx transport override, used by tests to inject MockTransport.
    transport: httpx.AsyncBaseTransport | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=_default_headers(),
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            transport=self.transport,
        )

    # ------------------------------------------------------------- providers
    async def _search_ddg_html(self, client: httpx.AsyncClient, query: str) -> list[dict[str, str]]:
        response = await client.post(DDG_HTML_URL, data={"q": query})
        response.raise_for_status()
        return parse_ddg_html(response.text)

    async def _search_ddg_api(
        self, client: httpx.AsyncClient, query: str, max_results: int
    ) -> list[dict[str, str]]:
        response = await client.get(
            DDG_API_URL,
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
        )
        response.raise_for_status()
        return parse_instant_answer(response.json(), max_results)

    async def _search_searx(self, client: httpx.AsyncClient, query: str) -> list[dict[str, str]]:
        base = settings.SEARX_BASE_URL.rstrip("/")
        response = await client.get(f"{base}/search", params={"q": query, "format": "json"})
        response.raise_for_status()
        rows = response.json().get("results") or []
        return [
            {
                "title": (row.get("title") or "").strip(),
                "url": row.get("url") or "",
                "snippet": " ".join((row.get("content") or "").split()),
            }
            for row in rows
            if row.get("url") and row.get("title")
        ]

    # ------------------------------------------------------------------ run
    async def _run(self, query: str = "", max_results: int = 6, **kwargs: Any) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            raise ToolError("A search query is required.", speech="What should I search for?")
        limit = _clamp_max_results(max_results)

        providers: list[str] = ["ddg_html", "ddg_api"]
        if settings.WEB_SEARCH_PROVIDER.strip().lower() == "searx":
            providers.insert(0, "searx")

        results: list[dict[str, str]] = []
        used_provider = ""
        errors: list[str] = []

        async with self._client() as client:
            for provider in providers:
                try:
                    if provider == "ddg_html":
                        results = await self._search_ddg_html(client, query)
                    elif provider == "ddg_api":
                        results = await self._search_ddg_api(client, query, limit)
                    else:
                        results = await self._search_searx(client, query)
                except (httpx.HTTPError, ValueError) as exc:
                    logger.info("web_search provider %s failed: %s", provider, exc)
                    errors.append(f"{provider}: {exc}")
                    continue
                if results:
                    used_provider = provider
                    break

        if not results:
            detail = "; ".join(errors) if errors else "no provider returned results"
            raise ToolError(
                f"Web search found nothing for '{query}' ({detail}).",
                speech=f"I couldn't find any results for {query}.",
            )

        results = results[:limit]
        display_lines = [
            f"{i}. {row['title']}\n   {row['url']}" + (f"\n   {row['snippet']}" if row["snippet"] else "")
            for i, row in enumerate(results, start=1)
        ]
        count = len(results)
        return {
            "query": query,
            "provider": used_provider,
            "count": count,
            "results": results,
            "speech": f"Found {count} result{'s' if count != 1 else ''} for {query}.",
            "display": "\n".join(display_lines),
        }


class QuickAnswerTool(BaseTool):
    """Answer factual questions using DuckDuckGo's instant-answer API only."""

    name = "quick_answer"
    description = "Fetches a concise instant answer, definition or abstract for a factual question."
    permission_level = PermissionLevel.NETWORK_ACTION
    category = ToolCategory.WEB
    aliases = ("instant_answer", "ask_web")
    network = True
    mutating = False
    examples = (
        ToolExample(utterance="what is the capital of iceland",
                    arguments={"query": "capital of Iceland"}),
        ToolExample(utterance="define entropy",
                    arguments={"query": "entropy"}),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "query": {
                "type": "string",
                "description": "The factual question or term, e.g. 'capital of Iceland'.",
            },
        },
        required=["query"],
    )

    #: Optional httpx transport override, used by tests to inject MockTransport.
    transport: httpx.AsyncBaseTransport | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=_default_headers(),
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            transport=self.transport,
        )

    async def _run(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            raise ToolError("A question is required.", speech="What would you like to know?")

        async with self._client() as client:
            try:
                response = await client.get(
                    DDG_API_URL,
                    params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                )
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ToolError(
                    f"Instant-answer lookup failed: {exc}",
                    speech="I couldn't reach the answer service.",
                ) from exc

        raw_answer = data.get("Answer")
        answer = raw_answer.strip() if isinstance(raw_answer, str) else ""
        abstract = (data.get("AbstractText") or "").strip()
        definition = (data.get("Definition") or "").strip()

        if answer:
            text, kind = answer, data.get("AnswerType") or "answer"
            source, url = "DuckDuckGo", ""
        elif abstract:
            text, kind = abstract, "abstract"
            source, url = data.get("AbstractSource") or "", data.get("AbstractURL") or ""
        elif definition:
            text, kind = definition, "definition"
            source, url = data.get("DefinitionSource") or "", data.get("DefinitionURL") or ""
        else:
            raise ToolError(
                "No instant answer; try web_search.",
                speech="I don't have a quick answer for that — I can run a full web search instead.",
            )

        heading = data.get("Heading") or query
        return {
            "query": query,
            "heading": heading,
            "answer": text,
            "type": kind,
            "source": source,
            "url": url,
            "speech": text if len(text) <= 280 else text[:277].rstrip() + "...",
            "display": f"{heading}: {text}" + (f"\nSource: {source} {url}".rstrip() if source or url else ""),
        }


def get_tools() -> list[BaseTool]:
    return [WebSearchTool(), QuickAnswerTool()]
