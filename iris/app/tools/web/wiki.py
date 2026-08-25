"""Wikipedia lookup tool built on the key-free Wikimedia REST API.

* :class:`WikipediaTool` — "what does wikipedia say about black holes"

Flow: the page-summary REST endpoint
(``https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}``) is tried
first with the topic as-is. When that returns 404 (no exact page), the classic
``opensearch`` API is asked for the closest matching title and the summary is
re-fetched with it. The returned extract is trimmed to a requested number of
sentences by the pure helper :func:`trim_sentences`.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.web.wiki")

__all__ = [
    "trim_sentences",
    "summary_url",
    "opensearch_url",
    "WikipediaTool",
    "get_tools",
]

#: Per-request network timeout in seconds.
REQUEST_TIMEOUT = 10.0
#: Bounds on the ``sentences`` argument.
DEFAULT_SENTENCES = 4
MAX_SENTENCES = 10

_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")

#: Sentence boundary: ., ! or ? followed by whitespace and an opening
#: bracket/quote/uppercase/digit. Common abbreviations are guarded.
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<!\b[A-Z]\.)(?<!\bDr\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bMrs\.)(?<!\bSt\.)"
    r"(?<!\bNo\.)(?<!\betc\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bvs\.)"
    r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])"
)


def trim_sentences(text: str, count: int) -> str:
    """Return the first ``count`` sentences of ``text`` (pure function).

    Splitting is heuristic: sentence-ending punctuation followed by whitespace
    and a capital letter/digit, with guards for common abbreviations such as
    "Dr." and "e.g.". Whitespace is normalized first.
    """
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    count = max(1, count)
    sentences = _SENTENCE_SPLIT_RE.split(cleaned)
    return " ".join(sentences[:count]).strip()


def summary_url(language: str, title: str) -> str:
    """Build the REST page-summary URL for a language and (raw) page title."""
    encoded = quote(title.strip().replace(" ", "_"), safe="")
    return f"https://{language}.wikipedia.org/api/rest_v1/page/summary/{encoded}"


def opensearch_url(language: str) -> str:
    """Base URL of the classic opensearch (title search) API for a language."""
    return f"https://{language}.wikipedia.org/w/api.php"


class WikipediaTool(BaseTool):
    """Look up a topic on Wikipedia and return a short summary."""

    name = "wikipedia"
    description = "Looks up a topic on Wikipedia and returns a short summary with a link."
    permission_level = PermissionLevel.NETWORK_ACTION
    category = ToolCategory.WEB
    aliases = ("wiki", "wikipedia_summary")
    network = True
    mutating = False
    examples = (
        ToolExample(utterance="what does wikipedia say about black holes",
                    arguments={"topic": "black hole"}),
        ToolExample(utterance="wikipedia alan turing in two sentences",
                    arguments={"topic": "Alan Turing", "sentences": 2}),
        ToolExample(utterance="busca eiffel tower en la wikipedia en español",
                    arguments={"topic": "Torre Eiffel", "language": "es"}),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "topic": {
                "type": "string",
                "description": "The subject to look up, e.g. 'Alan Turing'.",
            },
            "language": {
                "type": "string",
                "description": "Wikipedia language code (default 'en').",
                "default": "en",
            },
            "sentences": {
                "type": "integer",
                "description": f"How many sentences of the summary to return (default {DEFAULT_SENTENCES}, max {MAX_SENTENCES}).",
                "default": DEFAULT_SENTENCES,
            },
        },
        required=["topic"],
    )

    #: Optional httpx transport override, used by tests to inject MockTransport.
    transport: httpx.AsyncBaseTransport | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": settings.WEB_USER_AGENT, "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            transport=self.transport,
        )

    # --------------------------------------------------------------- helpers
    async def _fetch_summary(
        self, client: httpx.AsyncClient, language: str, title: str
    ) -> dict[str, Any] | None:
        """Return the summary payload for a title, or ``None`` on 404."""
        response = await client.get(summary_url(language, title), params={"redirect": "true"})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def _closest_title(
        self, client: httpx.AsyncClient, language: str, topic: str
    ) -> str | None:
        """Ask the opensearch API for the closest matching page title."""
        response = await client.get(
            opensearch_url(language),
            params={
                "action": "opensearch",
                "search": topic,
                "limit": "1",
                "namespace": "0",
                "format": "json",
            },
        )
        response.raise_for_status()
        payload = response.json()
        titles = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
        return titles[0] if titles else None

    # ------------------------------------------------------------------ run
    async def _run(
        self,
        topic: str = "",
        language: str = "en",
        sentences: int = DEFAULT_SENTENCES,
        **kwargs: Any,
    ) -> dict[str, Any]:
        topic = (topic or "").strip()
        if not topic:
            raise ToolError("A topic is required.", speech="What should I look up on Wikipedia?")

        language = (language or "en").strip().lower() or "en"
        if not _LANGUAGE_RE.match(language):
            raise ToolError(
                f"'{language}' is not a valid Wikipedia language code (e.g. 'en', 'de', 'pt-br').",
                speech="That language code doesn't look right.",
            )
        try:
            sentence_count = int(sentences)
        except (TypeError, ValueError):
            sentence_count = DEFAULT_SENTENCES
        sentence_count = max(1, min(sentence_count, MAX_SENTENCES))

        async with self._client() as client:
            try:
                data = await self._fetch_summary(client, language, topic)
                matched_via_search = False
                if data is None:
                    closest = await self._closest_title(client, language, topic)
                    if closest and closest.strip() != topic:
                        logger.info("wikipedia: '%s' not found, retrying as '%s'", topic, closest)
                        data = await self._fetch_summary(client, language, closest)
                        matched_via_search = data is not None
            except (httpx.HTTPError, ValueError) as exc:
                raise ToolError(
                    f"Wikipedia lookup failed: {exc}",
                    speech="I couldn't reach Wikipedia just now.",
                ) from exc

        if data is None:
            raise ToolError(
                f"No Wikipedia article found for '{topic}' ({language}).",
                speech=f"I couldn't find a Wikipedia article about {topic}.",
            )

        title = data.get("title") or topic
        extract = (data.get("extract") or "").strip()
        page_url = (
            (data.get("content_urls") or {}).get("desktop", {}).get("page")
            or f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='')}"
        )
        description = (data.get("description") or "").strip()
        is_disambiguation = data.get("type") == "disambiguation"

        if not extract:
            if is_disambiguation:
                raise ToolError(
                    f"'{title}' is a disambiguation page — try a more specific topic.",
                    speech=f"{title} could mean several things; can you be more specific?",
                )
            raise ToolError(
                f"The Wikipedia article '{title}' has no summary text.",
                speech=f"The article about {title} has no summary I can read out.",
            )

        trimmed = trim_sentences(extract, sentence_count)
        return {
            "topic": topic,
            "title": title,
            "description": description,
            "extract": trimmed,
            "url": page_url,
            "language": language,
            "matched_via_search": matched_via_search,
            "disambiguation": is_disambiguation,
            "speech": trimmed if len(trimmed) <= 400 else trim_sentences(extract, 1),
            "display": f"{title} — {description}\n{trimmed}\n{page_url}" if description
            else f"{title}\n{trimmed}\n{page_url}",
        }


def get_tools() -> list[BaseTool]:
    return [WikipediaTool()]
