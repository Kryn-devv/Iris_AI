"""Web page fetching: download a URL and reduce it to clean, readable text.

* :class:`FetchPageTool` — "read https://example.com and tell me what it says"

Safety properties:

* Only ``http``/``https`` URLs are fetched; every other scheme is refused.
* Obvious SSRF targets (loopback, RFC-1918/link-local ranges, ``localhost``
  and friends) are blocked *before* any connection is opened, and the final
  URL is re-checked after redirects — see :func:`is_private_host`.
* The body is streamed and hard-capped at ``settings.WEB_FETCH_MAX_BYTES`` so
  a hostile or huge page can never exhaust memory.

The HTML→text conversion is the pure function :func:`html_to_text`: it uses
BeautifulSoup when the optional ``bs4`` package is installed and falls back to
a stdlib :class:`html.parser.HTMLParser` subclass otherwise, in both cases
stripping ``script``/``style``/``nav``/``footer`` (and other chrome) and
collapsing whitespace.
"""

from __future__ import annotations

import ipaddress
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import httpx

from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import try_import
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.web.fetch")

__all__ = [
    "is_private_host",
    "validate_fetch_url",
    "html_to_text",
    "extract_title",
    "FetchPageTool",
    "get_tools",
]

#: Per-request network timeout in seconds.
REQUEST_TIMEOUT = 10.0
#: Default / maximum number of characters of extracted text returned.
DEFAULT_MAX_CHARS = 8000
MAX_CHARS_CAP = 100_000

#: Tags whose entire content is dropped during text extraction.
_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "template", "nav", "footer", "header",
     "aside", "iframe", "svg", "form", "button", "select", "canvas"}
)
#: Tags that imply a line break around their content.
_BLOCK_TAGS = frozenset(
    {"p", "div", "section", "article", "main", "br", "hr", "li", "ul", "ol",
     "table", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
     "blockquote", "pre", "figure", "figcaption", "dt", "dd"}
)

#: Hostname suffixes that always resolve to internal infrastructure.
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
_BLOCKED_HOSTNAMES = frozenset({"localhost", "metadata.google.internal", "metadata"})


# =============================================================================
# Pure helpers: SSRF screening
# =============================================================================


def is_private_host(host: str) -> bool:
    """True when ``host`` points at loopback, private or link-local space.

    Handles both literal addresses (``127.0.0.1``, ``10.2.3.4``, ``::1``,
    ``169.254.169.254``, ``fe80::…``) and well-known internal hostnames
    (``localhost``, ``*.local``, ``metadata.google.internal``). Names that do
    not parse as IP addresses and are not on the name blocklist are considered
    public — DNS-rebinding defence is out of scope for a desktop assistant.
    """
    name = (host or "").strip().strip("[]").rstrip(".").lower()
    if not name:
        return True
    if name in _BLOCKED_HOSTNAMES or name.endswith(_BLOCKED_HOST_SUFFIXES):
        return True

    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        # Cheap textual screens for the classic private prefixes, so even a
        # weirdly-formed literal never slips through.
        return name.startswith(("127.", "10.", "192.168.", "169.254.", "0.")) or name == "::1"

    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def validate_fetch_url(url: str) -> str:
    """Normalize and screen a user-supplied URL, raising :class:`ToolError`.

    A bare domain gets ``https://`` prepended; anything that is not
    ``http``/``https`` or that targets a private/loopback host is refused.
    Returns the normalized URL string.
    """
    candidate = (url or "").strip()
    if not candidate:
        raise ToolError("A URL is required.", speech="Which page should I read?")

    if "://" not in candidate:
        if candidate.startswith("//"):
            candidate = "https:" + candidate
        else:
            candidate = "https://" + candidate

    parts = urlsplit(candidate)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ToolError(
            f"Only http(s) URLs can be fetched, not '{scheme}'.",
            speech="I can only read web pages over H T T P.",
        )

    host = parts.hostname or ""
    if not host:
        raise ToolError(f"'{url}' has no hostname.", speech="That doesn't look like a valid web address.")
    if is_private_host(host):
        raise ToolError(
            f"Refusing to fetch '{host}': private, loopback and link-local hosts are blocked.",
            speech="I can't fetch pages from private or local network addresses.",
        )
    return candidate


# =============================================================================
# Pure helpers: HTML -> text
# =============================================================================


class _TextExtractor(HTMLParser):
    """Stdlib fallback extractor: drop chrome tags, keep readable text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _collapse_whitespace(text: str) -> str:
    """Collapse runs of spaces within lines and 3+ blank lines to one."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    collapsed = "\n".join(lines)
    collapsed = re.sub(r"\n{3,}", "\n\n", collapsed)
    return collapsed.strip()


def html_to_text(html: str) -> str:
    """Convert an HTML document into clean readable plain text.

    Strips ``script``/``style``/``nav``/``footer`` (and similar chrome tags),
    inserts line breaks around block elements, decodes entities and collapses
    whitespace. Uses BeautifulSoup when installed for better malformed-markup
    tolerance, else a stdlib :class:`html.parser.HTMLParser` subclass. Pure
    function of its input.
    """
    if not html:
        return ""

    bs4 = try_import("bs4")
    if bs4 is not None:
        soup = bs4.BeautifulSoup(html, "html.parser")
        for tag in soup(list(_SKIP_TAGS)):
            tag.decompose()
        return _collapse_whitespace(soup.get_text(separator="\n"))

    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    return _collapse_whitespace(extractor.text())


def extract_title(html: str) -> str:
    """Best-effort ``<title>`` extraction (entity-decoded, whitespace-collapsed)."""
    if not html:
        return ""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    import html as html_module

    return " ".join(html_module.unescape(match.group(1)).split())


# =============================================================================
# Tool
# =============================================================================


class FetchPageTool(BaseTool):
    """Download a web page and return its readable text content."""

    name = "fetch_page"
    description = "Fetches a web page and returns its title and readable text content."
    permission_level = PermissionLevel.NETWORK_ACTION
    category = ToolCategory.WEB
    aliases = ("read_website", "get_url", "open_page_text")
    network = True
    mutating = False
    examples = (
        ToolExample(utterance="read https://example.com and summarize it",
                    arguments={"url": "https://example.com"}),
        ToolExample(utterance="get the text of the python.org homepage",
                    arguments={"url": "https://www.python.org", "max_chars": 4000}),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "url": {
                "type": "string",
                "description": "The http(s) URL of the page to fetch.",
            },
            "max_chars": {
                "type": "integer",
                "description": f"Maximum characters of extracted text to return (default {DEFAULT_MAX_CHARS}).",
                "default": DEFAULT_MAX_CHARS,
            },
        },
        required=["url"],
    )

    #: Optional httpx transport override, used by tests to inject MockTransport.
    transport: httpx.AsyncBaseTransport | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={
                "User-Agent": settings.WEB_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
            },
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            transport=self.transport,
        )

    async def _run(self, url: str = "", max_chars: int = DEFAULT_MAX_CHARS, **kwargs: Any) -> dict[str, Any]:
        target = validate_fetch_url(url)
        try:
            limit = int(max_chars)
        except (TypeError, ValueError):
            limit = DEFAULT_MAX_CHARS
        limit = max(200, min(limit, MAX_CHARS_CAP))
        byte_cap = int(settings.WEB_FETCH_MAX_BYTES)

        chunks: list[bytes] = []
        received = 0
        truncated_bytes = False

        async with self._client() as client:
            try:
                async with client.stream("GET", target) as response:
                    final_url = str(response.url)
                    final_host = response.url.host or ""
                    if is_private_host(final_host):
                        raise ToolError(
                            f"Refusing '{target}': it redirected to the private host '{final_host}'.",
                            speech="That page redirected to a private network address, so I stopped.",
                        )
                    if response.status_code >= 400:
                        raise ToolError(
                            f"The server returned HTTP {response.status_code} for '{final_url}'.",
                            speech=f"That page returned an error, status {response.status_code}.",
                        )
                    content_type = (response.headers.get("content-type") or "").lower()
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
                        received += len(chunk)
                        if received >= byte_cap:
                            truncated_bytes = True
                            break
            except httpx.HTTPError as exc:
                raise ToolError(
                    f"Could not fetch '{target}': {exc}",
                    speech="I couldn't reach that page.",
                ) from exc

        body = b"".join(chunks)[:byte_cap]
        encoding = "utf-8"
        charset = re.search(r"charset=([\w\-]+)", content_type)
        if charset:
            encoding = charset.group(1)
        try:
            raw_text = body.decode(encoding, errors="replace")
        except LookupError:
            raw_text = body.decode("utf-8", errors="replace")

        looks_like_html = "html" in content_type or bool(
            re.search(r"<\s*(!doctype|html|body|div|p)\b", raw_text[:2048], re.IGNORECASE)
        )
        is_texty = (
            not content_type
            or content_type.startswith("text/")
            or "html" in content_type
            or "xml" in content_type
            or "json" in content_type
        )
        if not is_texty:
            raise ToolError(
                f"'{final_url}' is not a text page (content-type: {content_type}).",
                speech="That link isn't a readable text page.",
            )

        if looks_like_html:
            title = extract_title(raw_text)
            text = html_to_text(raw_text)
        else:
            title = ""
            text = raw_text.strip()

        if not text:
            raise ToolError(
                f"'{final_url}' contained no readable text.",
                speech="That page had no readable text on it.",
            )

        truncated_chars = len(text) > limit
        if truncated_chars:
            text = text[:limit].rstrip() + " …"

        host = urlsplit(final_url).hostname or final_url
        label = title or host
        return {
            "url": url,
            "final_url": final_url,
            "title": title,
            "text": text,
            "chars": len(text),
            "truncated": truncated_bytes or truncated_chars,
            "content_type": content_type,
            "speech": f"Fetched {label} — about {len(text)} characters of text.",
            "display": (f"{title}\n{final_url}\n\n{text}" if title else f"{final_url}\n\n{text}"),
        }


def get_tools() -> list[BaseTool]:
    return [FetchPageTool()]
