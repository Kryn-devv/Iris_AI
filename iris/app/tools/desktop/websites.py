"""Website opening tools: alias-driven URL resolution and browser launching.

Gives IRIS a spoken-language friendly way to open websites in the user's
default browser:

* :class:`OpenWebsiteTool`   — "open youtube", "search amazon for headphones"
* :class:`PlayOnYouTubeTool` — "play lo-fi beats on youtube"

The heart of the module is :data:`SITE_SPECS`, a rich alias table mapping the
names people say ("maps", "stack overflow", "prime video") to canonical URLs
plus, where the site supports it, a direct search-URL template. The pure
resolver :func:`resolve_site` is exported so the deterministic NLU layer and
the tests can build URLs without opening anything.

Resolution rules:

* Known alias        -> canonical https URL (or its search URL when a query
  is given; sites without a native search fall back to a Google ``site:``
  search).
* Bare domain        -> ``https://`` is enforced ("example.com" -> "https://example.com").
* Full http(s) URL   -> passed through untouched; other schemes are rejected.
* Anything else      -> a plain Google search for the text.

Nothing here touches the network — ``webbrowser.open`` merely hands the URL
to the local default browser, hence ``network=False``.

On a headless host there is no local browser to hand it to. Rather than fail,
the URL is published on the event bus and the open happens in whichever browser
is showing the dashboard. That is the difference between "open youtube" being
broken on a cloud install and it opening a tab on the machine the user is
actually sitting at.
"""

from __future__ import annotations

import re
import webbrowser
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit

from iris.app.core.bus import Topics, default_event_bus
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import has_display
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.desktop.websites")

__all__ = [
    "SiteSpec",
    "SITE_SPECS",
    "SITE_ALIASES",
    "resolve_site",
    "describe_site",
    "open_url_somewhere",
    "OpenWebsiteTool",
    "PlayOnYouTubeTool",
    "get_tools",
]


# =============================================================================
# Alias table
# =============================================================================


@dataclass(frozen=True)
class SiteSpec:
    """One well-known website: canonical URL plus optional search template."""

    key: str
    label: str
    url: str
    #: Search URL template containing ``{query}``; ``None`` means no native search.
    search: str | None = None
    #: How to encode the query: "param" -> quote_plus (?q=a+b),
    #: "path" -> quote (…/search/a%20b).
    search_style: str = "param"


#: Canonical site table, keyed by stable identifier.
SITE_SPECS: dict[str, SiteSpec] = {
    spec.key: spec
    for spec in (
        SiteSpec("youtube", "YouTube", "https://www.youtube.com",
                 "https://www.youtube.com/results?search_query={query}"),
        SiteSpec("google", "Google", "https://www.google.com",
                 "https://www.google.com/search?q={query}"),
        SiteSpec("gmail", "Gmail", "https://mail.google.com"),
        SiteSpec("github", "GitHub", "https://github.com",
                 "https://github.com/search?q={query}"),
        SiteSpec("whatsapp", "WhatsApp Web", "https://web.whatsapp.com"),
        SiteSpec("twitter", "X (Twitter)", "https://x.com",
                 "https://x.com/search?q={query}"),
        SiteSpec("instagram", "Instagram", "https://www.instagram.com"),
        SiteSpec("facebook", "Facebook", "https://www.facebook.com"),
        SiteSpec("hotstar", "Hotstar", "https://www.hotstar.com"),
        SiteSpec("netflix", "Netflix", "https://www.netflix.com",
                 "https://www.netflix.com/search?q={query}"),
        SiteSpec("prime_video", "Prime Video", "https://www.primevideo.com"),
        SiteSpec("chatgpt", "ChatGPT", "https://chatgpt.com"),
        SiteSpec("claude", "Claude", "https://claude.ai"),
        SiteSpec("maps", "Google Maps", "https://www.google.com/maps",
                 "https://www.google.com/maps/search/{query}", search_style="path"),
        SiteSpec("translate", "Google Translate", "https://translate.google.com"),
        SiteSpec("drive", "Google Drive", "https://drive.google.com"),
        SiteSpec("docs", "Google Docs", "https://docs.google.com"),
        SiteSpec("sheets", "Google Sheets", "https://sheets.google.com"),
        SiteSpec("slides", "Google Slides", "https://slides.google.com"),
        SiteSpec("reddit", "Reddit", "https://www.reddit.com",
                 "https://www.reddit.com/search/?q={query}"),
        SiteSpec("stackoverflow", "Stack Overflow", "https://stackoverflow.com",
                 "https://stackoverflow.com/search?q={query}"),
        SiteSpec("linkedin", "LinkedIn", "https://www.linkedin.com"),
        SiteSpec("amazon", "Amazon", "https://www.amazon.com",
                 "https://www.amazon.com/s?k={query}"),
        SiteSpec("flipkart", "Flipkart", "https://www.flipkart.com",
                 "https://www.flipkart.com/search?q={query}"),
        SiteSpec("wikipedia", "Wikipedia", "https://www.wikipedia.org",
                 "https://en.wikipedia.org/wiki/Special:Search?search={query}"),
        SiteSpec("spotify", "Spotify", "https://open.spotify.com",
                 "https://open.spotify.com/search/{query}", search_style="path"),
        SiteSpec("twitch", "Twitch", "https://www.twitch.tv",
                 "https://www.twitch.tv/search?term={query}"),
        SiteSpec("pinterest", "Pinterest", "https://www.pinterest.com",
                 "https://www.pinterest.com/search/pins/?q={query}"),
        SiteSpec("canva", "Canva", "https://www.canva.com"),
        SiteSpec("figma", "Figma", "https://www.figma.com"),
        SiteSpec("notion", "Notion", "https://www.notion.so"),
        SiteSpec("outlook", "Outlook", "https://outlook.live.com"),
        SiteSpec("calendar", "Google Calendar", "https://calendar.google.com"),
    )
}


#: Spoken/typed name -> canonical SITE_SPECS key (pre-normalization applied).
_RAW_ALIASES: dict[str, tuple[str, ...]] = {
    "youtube": ("youtube", "yt", "you tube"),
    "google": ("google", "google search", "search"),
    "gmail": ("gmail", "google mail", "mail", "email"),
    "github": ("github", "git hub"),
    "whatsapp": ("whatsapp", "whatsapp web", "whats app"),
    "twitter": ("twitter", "x"),
    "instagram": ("instagram", "insta", "ig"),
    "facebook": ("facebook", "fb"),
    "netflix": ("netflix",),
    "prime_video": ("prime video", "primevideo", "amazon prime", "prime"),
    "chatgpt": ("chatgpt", "chat gpt", "openai"),
    "claude": ("claude", "claude ai", "anthropic"),
    "maps": ("maps", "google maps", "map"),
    "translate": ("translate", "google translate", "translator"),
    "drive": ("drive", "google drive"),
    "docs": ("docs", "google docs"),
    "sheets": ("sheets", "google sheets"),
    "slides": ("slides", "google slides"),
    "reddit": ("reddit",),
    "stackoverflow": ("stackoverflow", "stack overflow"),
    "linkedin": ("linkedin", "linked in"),
    "amazon": ("amazon",),
    "flipkart": ("flipkart",),
    "wikipedia": ("wikipedia", "wiki"),
    "spotify": ("spotify", "spotify web"),
    "twitch": ("twitch",),
    "pinterest": ("pinterest",),
    "canva": ("canva",),
    "figma": ("figma",),
    "notion": ("notion",),
    "outlook": ("outlook", "outlook mail", "hotmail"),
    "calendar": ("calendar", "google calendar"),
}


def _normalize_site_name(name: str) -> str:
    text = (name or "").strip().lower()
    text = text.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def _build_alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for key, aliases in _RAW_ALIASES.items():
        index[_normalize_site_name(key)] = key
        index[_normalize_site_name(SITE_SPECS[key].label)] = key
        for alias in aliases:
            index[_normalize_site_name(alias)] = key
    return index


#: Normalized alias -> canonical key, exported for the NLU layer.
SITE_ALIASES: dict[str, str] = _build_alias_index()


def _host_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


#: Known host -> canonical key, so "youtube.com" or "x.com" resolve directly.
_HOST_TO_KEY: dict[str, str] = {_host_of(spec.url): spec.key for spec in SITE_SPECS.values()}
_HOST_TO_KEY.update(
    {
        "twitter.com": "twitter",
        "wikipedia.org": "wikipedia",
        "en.wikipedia.org": "wikipedia",
        "youtu.be": "youtube",
        "spotify.com": "spotify",
        "maps.google.com": "maps",
        "outlook.com": "outlook",
    }
)

#: Bare domain (optionally with a path/query suffix).
_DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+([/?#].*)?$",
    re.IGNORECASE,
)

_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")


def _lookup(site: str) -> SiteSpec | None:
    """Match a spoken name or a known domain against the alias table."""
    text = (site or "").strip().lower()
    if not text:
        return None
    key = SITE_ALIASES.get(_normalize_site_name(text))
    if key:
        return SITE_SPECS[key]
    host = text.split("/", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    key = _HOST_TO_KEY.get(host)
    return SITE_SPECS[key] if key else None


def _google_site_search(host: str, query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(f'site:{host} {query}')}"


def _search_url(spec: SiteSpec, query: str) -> str:
    """Site-specific search URL, falling back to a Google ``site:`` search."""
    if spec.search is None:
        return _google_site_search(_host_of(spec.url), query)
    if spec.search_style == "path":
        return spec.search.format(query=quote(query, safe=""))
    return spec.search.format(query=quote_plus(query))


def resolve_site(site: str, query: str | None = None) -> str:
    """Resolve a spoken site name, bare domain or URL to the URL to open.

    Pure function with no side effects — safe for the NLU layer and tests.
    Raises :class:`ValueError` for empty input or non-http(s) URL schemes.

    >>> resolve_site("youtube")
    'https://www.youtube.com'
    >>> resolve_site("youtube", "lo-fi beats")
    'https://www.youtube.com/results?search_query=lo-fi+beats'
    >>> resolve_site("example.com")
    'https://example.com'
    """
    text = (site or "").strip()
    if not text:
        raise ValueError("The 'site' argument must not be empty.")
    query = (query or "").strip() or None

    scheme_match = _SCHEME_RE.match(text)
    if scheme_match:
        scheme = scheme_match.group(1).lower()
        if scheme not in ("http", "https"):
            raise ValueError(
                f"Unsupported URL scheme '{scheme}:'. Only http and https URLs can be opened."
            )
        if query:
            return _google_site_search(urlsplit(text).netloc or text, query)
        return text

    spec = _lookup(text)
    if spec is not None:
        return _search_url(spec, query) if query else spec.url

    if _DOMAIN_RE.match(text.lower()):
        if query:
            return _google_site_search(text.split("/", 1)[0], query)
        return f"https://{text}"  # enforce https for bare domains

    terms = f"{text} {query}" if query else text
    return f"https://www.google.com/search?q={quote_plus(terms)}"


def describe_site(site: str) -> str:
    """Human-friendly label for a site argument (for speech strings)."""
    text = (site or "").strip()
    spec = _lookup(text)
    if spec is not None:
        return spec.label
    if _SCHEME_RE.match(text):
        return urlsplit(text).netloc or text
    return text


def _open_in_browser(url: str) -> bool:
    """Hand a URL to the default browser (sync; run via ``to_thread``)."""
    return webbrowser.open(url)


async def open_url_somewhere(tool: BaseTool, url: str, label: str) -> str:
    """Open ``url`` wherever there is actually a browser, and say where.

    A desktop install hands it to the local default browser. A headless install
    — IRIS on a VPS, with the user watching from a laptop — has no local
    browser, so the URL goes down the event bus and the dashboard opens it in
    the browser the user is looking at. Returns "local" or "dashboard".

    Raises :class:`ToolError` only when neither route exists, which means
    nobody is watching and there is no desktop: a genuine dead end rather than
    something to report as success.
    """
    if has_display() and await tool.to_thread(_open_in_browser, url):
        return "local"

    # The dashboard is the browser in a cloud install. Publishing is
    # unconditional so a client that connects a moment later still sees it in
    # the bus history rather than losing the request entirely.
    default_event_bus.publish(
        Topics.UI_OPEN_URL, {"url": url, "label": label},
    )
    # subscriber_count would also count the face service and anything else on
    # the bus; only a client that wants ui.open_url can actually open a tab.
    if default_event_bus.listener_count(Topics.UI_OPEN_URL) == 0:
        raise ToolError(
            f"There is no browser to open {url} in — no desktop on this machine, "
            "and no dashboard connected. Open the IRIS dashboard and ask again.",
            speech="I have nowhere to open that. Open the IRIS dashboard first.",
        )
    return "dashboard"


# =============================================================================
# Tools
# =============================================================================


class OpenWebsiteTool(BaseTool):
    """Open a website — or a site-specific search — in the default browser.

    Accepts spoken names from the alias table ("youtube", "stack overflow",
    "prime video"), bare domains ("example.com", upgraded to https) and full
    http(s) URLs. With ``query``, sites that expose a search URL (YouTube,
    Google, Amazon, Wikipedia, Maps, GitHub, Spotify, ...) get a direct search;
    everything else falls back to a Google ``site:`` search.
    """

    name = "open_website"
    description = "Opens a website or a site-specific search in the default web browser."
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.WEB
    aliases = ("open_site", "open_url", "browse")
    network = False  # only hands the URL to the local browser
    mutating = False
    examples = (
        ToolExample(utterance="open youtube", arguments={"site": "youtube"}),
        ToolExample(
            utterance="search amazon for wireless headphones",
            arguments={"site": "amazon", "query": "wireless headphones"},
        ),
        ToolExample(utterance="go to example.com", arguments={"site": "example.com"}),
        ToolExample(
            utterance="look up alan turing on wikipedia",
            arguments={"site": "wikipedia", "query": "alan turing"},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "site": {
                "type": "string",
                "description": "Site name ('youtube'), bare domain ('example.com') "
                               "or full http(s) URL.",
            },
            "query": {
                "type": "string",
                "description": "Optional search terms to look up on that site.",
            },
        },
        required=["site"],
    )

    async def _run(self, site: str = "", query: str | None = None, **kwargs: Any) -> dict[str, Any]:
        try:
            url = resolve_site(site, query)
        except ValueError as exc:
            raise ToolError(str(exc), speech="I couldn't work out which site to open.") from exc

        label = describe_site(site)
        where = await open_url_somewhere(self, url, label)

        logger.info("Opened %s (%s) via %s", label, url, where)
        cleaned_query = (query or "").strip()
        speech = f"Searching {label} for {cleaned_query}." if cleaned_query else f"Opened {label}."
        if where == "dashboard":
            speech += " In your browser."
        return {
            "site": label,
            "opened_in": where,
            "url": url,
            "query": cleaned_query or None,
            "speech": speech,
            "display": f"Opened {url} in the default browser.",
        }


class PlayOnYouTubeTool(BaseTool):
    """Open YouTube search results for a query — no API key required.

    Opens ``youtube.com/results?search_query=...`` in the default browser so
    the user can pick (or autoplay) the top result.
    """

    name = "play_youtube"
    description = "Opens YouTube results for a search query in the default browser."
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.WEB
    aliases = ("play_video", "youtube_play")
    network = False  # only hands the URL to the local browser
    mutating = False
    examples = (
        ToolExample(utterance="play lo-fi beats on youtube", arguments={"query": "lo-fi beats"}),
        ToolExample(utterance="play the latest lex fridman podcast",
                    arguments={"query": "lex fridman podcast latest"}),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "query": {
                "type": "string",
                "description": "What to play or search for on YouTube.",
            }
        },
        required=["query"],
    )

    async def _run(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        cleaned = (query or "").strip()
        if not cleaned:
            raise ToolError(
                "The 'query' argument is required.",
                speech="What should I play on YouTube?",
            )

        url = f"https://www.youtube.com/results?search_query={quote_plus(cleaned)}"
        where = await open_url_somewhere(self, url, f"YouTube — {cleaned}")

        logger.info("Playing on YouTube: %s", cleaned)
        return {
            "query": cleaned,
            "opened_in": where,
            "url": url,
            "speech": f"Playing {cleaned} on YouTube."
                      + (" In your browser." if where == "dashboard" else ""),
            "display": f"Opened YouTube results for '{cleaned}'.",
        }


def get_tools() -> list[BaseTool]:
    return [OpenWebsiteTool(), PlayOnYouTubeTool()]
