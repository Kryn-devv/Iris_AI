"""Tests for the web tool modules (search, fetch, wiki).

All network interaction is faked with ``httpx.MockTransport`` — nothing here
ever touches the real internet. Pure helpers (HTML parsing, redirect
decoding, SSRF screening, sentence trimming) are tested directly on embedded
fixture strings.
"""

from __future__ import annotations

import json

import httpx
import pytest

from iris.app.schemas.tools import ToolCategory
from iris.app.core.security import PermissionLevel
from iris.app.tools.web import fetch as fetch_mod
from iris.app.tools.web import search as search_mod
from iris.app.tools.web import wiki as wiki_mod
from iris.app.tools.web.fetch import (
    FetchPageTool,
    extract_title,
    html_to_text,
    is_private_host,
    validate_fetch_url,
)
from iris.app.tools.web.search import (
    QuickAnswerTool,
    WebSearchTool,
    decode_ddg_redirect,
    parse_ddg_html,
    parse_instant_answer,
)
from iris.app.tools.web.wiki import WikipediaTool, trim_sentences
from iris.app.tools.base import ToolError


# =============================================================================
# Fixture: a trimmed-down capture of the DuckDuckGo HTML endpoint markup
# =============================================================================

DDG_HTML_FIXTURE = """
<!DOCTYPE html>
<html><head><title>python programming at DuckDuckGo</title></head>
<body>
<div class="serp__results">
  <div class="result results_links results_links_deep web-result">
    <div class="links_main links_deep result__body">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a"
           href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2F&amp;rut=abc123">
           Welcome to <b>Python</b>.org</a>
      </h2>
      <a class="result__snippet"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2F&amp;rut=abc123">
         The official home of the <b>Python</b> Programming&nbsp;Language.</a>
    </div>
  </div>
  <div class="result results_links results_links_deep web-result result--ad">
    <div class="links_main links_deep result__body">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a"
           href="https://duckduckgo.com/y.js?ad_provider=fake&amp;u3=tracker">Sponsored thing</a>
      </h2>
      <a class="result__snippet" href="#">Buy python courses now!</a>
    </div>
  </div>
  <div class="result results_links results_links_deep web-result">
    <div class="links_main links_deep result__body">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a" href="https://docs.python.org/3/tutorial/">
           The Python Tutorial &mdash; Python 3 documentation</a>
      </h2>
      <a class="result__snippet" href="https://docs.python.org/3/tutorial/">
         Python is an easy to learn, powerful
         programming language.</a>
    </div>
  </div>
</div>
</body></html>
"""


# =============================================================================
# search.py: pure helpers
# =============================================================================


def test_parse_ddg_html_extracts_results_in_order():
    results = parse_ddg_html(DDG_HTML_FIXTURE)
    assert len(results) == 2  # the ad row is dropped

    first, second = results
    assert first["title"] == "Welcome to Python.org"
    assert first["url"] == "https://www.python.org/"
    assert "official home" in first["snippet"]
    assert "Programming" in first["snippet"]

    assert second["url"] == "https://docs.python.org/3/tutorial/"
    assert "Python Tutorial" in second["title"]
    # entity decoded (&mdash;) and whitespace collapsed
    assert "—" in second["title"]
    assert "\n" not in second["snippet"]
    assert second["snippet"] == "Python is an easy to learn, powerful programming language."


def test_parse_ddg_html_empty_and_garbage():
    assert parse_ddg_html("") == []
    assert parse_ddg_html("<html><body><p>no results markup here</p></body></html>") == []
    # malformed markup must not raise
    assert parse_ddg_html("<a class='result__a' href='https://x.example'>unclosed") == []


def test_decode_ddg_redirect_unwraps_uddg():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage%3Fa%3D1%26b%3D2&rut=zzz"
    assert decode_ddg_redirect(href) == "https://example.com/page?a=1&b=2"


def test_decode_ddg_redirect_passthrough_and_ads():
    assert decode_ddg_redirect("https://example.com/x") == "https://example.com/x"
    assert decode_ddg_redirect("//example.com/x") == "https://example.com/x"
    assert decode_ddg_redirect("https://duckduckgo.com/y.js?u3=tracker") == ""
    assert decode_ddg_redirect("") == ""
    assert decode_ddg_redirect("javascript:alert(1)") == ""


def test_parse_instant_answer_flattens_topics():
    data = {
        "AbstractText": "Python is a programming language.",
        "AbstractURL": "https://en.wikipedia.org/wiki/Python",
        "Heading": "Python",
        "RelatedTopics": [
            {"Text": "CPython - The reference implementation.", "FirstURL": "https://e/1"},
            {"Topics": [{"Text": "PyPy - A fast JIT.", "FirstURL": "https://e/2"}]},
            {"Text": "no url so skipped"},
        ],
    }
    rows = parse_instant_answer(data, max_results=10)
    assert [r["url"] for r in rows] == ["https://en.wikipedia.org/wiki/Python", "https://e/1", "https://e/2"]
    assert rows[1]["title"] == "CPython"
    assert parse_instant_answer(data, max_results=2) == rows[:2]


# =============================================================================
# search.py: tools via MockTransport
# =============================================================================


def _search_transport(html_status: int = 200, api_payload: dict | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "html.duckduckgo.com":
            assert request.method == "POST"
            assert b"q=python+programming" in request.content
            return httpx.Response(html_status, text=DDG_HTML_FIXTURE if html_status == 200 else "err")
        if request.url.host == "api.duckduckgo.com":
            return httpx.Response(200, json=api_payload or {})
        raise AssertionError(f"unexpected host {request.url.host}")

    return httpx.MockTransport(handler)


async def test_web_search_primary_html_path():
    tool = WebSearchTool()
    tool.transport = _search_transport()
    res = await tool.execute(query="python programming")
    assert res.success is True
    assert res.result["provider"] == "ddg_html"
    assert res.result["count"] == 2
    assert res.result["results"][0]["url"] == "https://www.python.org/"
    assert res.speech == "Found 2 results for python programming."
    assert res.result["results"][0]["title"] in res.display


async def test_web_search_falls_back_to_instant_answer_api():
    payload = {
        "AbstractText": "Python is a language.",
        "AbstractURL": "https://en.wikipedia.org/wiki/Python",
        "Heading": "Python",
        "RelatedTopics": [],
    }
    tool = WebSearchTool()
    tool.transport = _search_transport(html_status=500, api_payload=payload)
    res = await tool.execute(query="python programming")
    assert res.success is True
    assert res.result["provider"] == "ddg_api"
    assert res.result["results"][0]["snippet"] == "Python is a language."


async def test_web_search_all_providers_fail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    tool = WebSearchTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(query="anything")
    assert res.success is False
    assert "anything" in res.error


async def test_web_search_max_results_clamped():
    tool = WebSearchTool()
    tool.transport = _search_transport()
    res = await tool.execute(query="python programming", max_results=1)
    assert res.success is True
    assert res.result["count"] == 1
    # nonsense values fall back to a sane number instead of crashing
    res = await tool.execute(query="python programming", max_results="lots")
    assert res.success is True
    assert 1 <= res.result["count"] <= search_mod.MAX_RESULTS_CAP


async def test_web_search_uses_searx_first_when_configured(monkeypatch):
    from iris.app.core.config import settings

    monkeypatch.setattr(settings, "WEB_SEARCH_PROVIDER", "searx")
    monkeypatch.setattr(settings, "SEARX_BASE_URL", "https://searx.example")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "searx.example"
        assert request.url.params["q"] == "rust lang"
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={"results": [{"title": "Rust", "url": "https://rust-lang.org", "content": "A  language."}]},
        )

    tool = WebSearchTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(query="rust lang")
    assert res.success is True
    assert res.result["provider"] == "searx"
    assert res.result["results"][0] == {
        "title": "Rust",
        "url": "https://rust-lang.org",
        "snippet": "A language.",
    }


async def test_web_search_empty_query_rejected():
    tool = WebSearchTool()
    res = await tool.execute(query="   ")
    assert res.success is False
    assert "query" in res.error.lower()


async def test_web_search_sends_configured_user_agent():
    from iris.app.core.config import settings

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, text=DDG_HTML_FIXTURE)

    tool = WebSearchTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(query="x")
    assert res.success is True
    assert seen["ua"] == settings.WEB_USER_AGENT


async def test_quick_answer_prefers_direct_answer():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.duckduckgo.com"
        return httpx.Response(200, json={"Answer": "42", "AnswerType": "calc", "Heading": ""})

    tool = QuickAnswerTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(query="meaning of life")
    assert res.success is True
    assert res.result["answer"] == "42"
    assert res.result["type"] == "calc"
    assert res.speech == "42"


async def test_quick_answer_abstract_and_definition_fallbacks():
    def abstract_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Answer": "",
                "AbstractText": "Entropy is disorder.",
                "AbstractSource": "Wikipedia",
                "AbstractURL": "https://en.wikipedia.org/wiki/Entropy",
                "Heading": "Entropy",
            },
        )

    tool = QuickAnswerTool()
    tool.transport = httpx.MockTransport(abstract_handler)
    res = await tool.execute(query="entropy")
    assert res.success is True
    assert res.result["type"] == "abstract"
    assert res.result["source"] == "Wikipedia"
    assert res.result["url"].endswith("/Entropy")


async def test_quick_answer_no_answer_is_clean_toolerror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Answer": "", "AbstractText": "", "Definition": ""})

    tool = QuickAnswerTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(query="something obscure")
    assert res.success is False
    assert res.error == "No instant answer; try web_search."


# =============================================================================
# fetch.py: pure helpers
# =============================================================================


def test_html_to_text_strips_chrome_and_collapses_whitespace():
    html = """
    <html><head><title>My   Page</title>
      <style>body { color: red }</style>
      <script>alert("nope");</script>
    </head>
    <body>
      <nav><a href="/">Home</a><a href="/about">About</a></nav>
      <header>Site header junk</header>
      <p>Hello    <b>world</b>.</p>
      <p>Second&nbsp;paragraph
         spanning lines.</p>
      <footer>Copyright 2026</footer>
    </body></html>
    """
    text = html_to_text(html)
    assert "Hello world ." in text or "Hello world." in text.replace(" .", ".")
    assert "Second paragraph spanning lines." in text.replace("\n", " ")
    assert "alert" not in text
    assert "color: red" not in text
    assert "Home" not in text  # nav stripped
    assert "Site header junk" not in text
    assert "Copyright 2026" not in text  # footer stripped
    assert "\n\n\n" not in text
    assert "   " not in text.replace("\n", " ")


def test_html_to_text_empty_and_plain():
    assert html_to_text("") == ""
    assert html_to_text("just plain words") == "just plain words"


def test_extract_title():
    assert extract_title("<html><head><title> A &amp; B \n title </title></head></html>") == "A & B title"
    assert extract_title("<p>no title</p>") == ""
    assert extract_title("") == ""


@pytest.mark.parametrize(
    "host",
    [
        "localhost", "LOCALHOST", "sub.localhost", "127.0.0.1", "127.9.9.9",
        "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1", "0.0.0.0",
        "172.16.0.9", "fe80::1", "metadata.google.internal", "printer.local",
    ],
)
def test_is_private_host_blocks(host):
    assert is_private_host(host) is True


@pytest.mark.parametrize("host", ["example.com", "8.8.8.8", "python.org", "93.184.216.34"])
def test_is_private_host_allows_public(host):
    assert is_private_host(host) is False


def test_validate_fetch_url_schemes_and_ssrf():
    assert validate_fetch_url("example.com/x") == "https://example.com/x"
    assert validate_fetch_url("http://example.com") == "http://example.com"

    for bad in ("ftp://example.com", "file:///etc/passwd", "javascript:alert(1)"):
        with pytest.raises(ToolError):
            validate_fetch_url(bad)

    for bad in ("http://localhost:8000", "http://127.0.0.1/admin", "http://10.1.2.3",
                "http://192.168.0.1", "http://169.254.169.254/latest/meta-data",
                "http://[::1]:9200", ""):
        with pytest.raises(ToolError):
            validate_fetch_url(bad)


# =============================================================================
# fetch.py: tool via MockTransport
# =============================================================================

PAGE_HTML = """
<html><head><title>Example Domain</title><style>h1{font-size:2em}</style></head>
<body>
  <nav>Skip me</nav>
  <h1>Example Domain</h1>
  <p>This domain is for use in illustrative examples in documents.</p>
  <script>trackEverything();</script>
  <footer>footer junk</footer>
</body></html>
"""


async def test_fetch_page_returns_title_text_and_final_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://example.com/new"})
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=PAGE_HTML)

    tool = FetchPageTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(url="https://example.com/old")
    assert res.success is True
    assert res.result["title"] == "Example Domain"
    assert res.result["final_url"] == "https://example.com/new"
    assert "illustrative examples" in res.result["text"]
    assert "trackEverything" not in res.result["text"]
    assert "Skip me" not in res.result["text"]
    assert "footer junk" not in res.result["text"]
    assert res.result["truncated"] is False
    assert "Example Domain" in res.speech


async def test_fetch_page_max_chars_trimming():
    body = "<html><body><p>" + "word " * 5000 + "</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=body)

    tool = FetchPageTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(url="https://example.com", max_chars=500)
    assert res.success is True
    assert res.result["truncated"] is True
    assert len(res.result["text"]) <= 502  # 500 + ellipsis marker


async def test_fetch_page_byte_cap_enforced(monkeypatch):
    from iris.app.core.config import settings

    monkeypatch.setattr(settings, "WEB_FETCH_MAX_BYTES", 1000)
    body = "<html><body><p>" + "x" * 50_000 + "</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=body)

    tool = FetchPageTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(url="https://example.com/big")
    assert res.success is True
    assert res.result["truncated"] is True
    assert len(res.result["text"]) < 2000


async def test_fetch_page_blocks_ssrf_without_network():
    tool = FetchPageTool()  # no transport: any real request would explode, so blocking must be pre-flight
    for bad in ("http://localhost:8000", "http://127.0.0.1", "http://10.0.0.1",
                "http://192.168.1.1", "http://169.254.169.254", "http://[::1]/"):
        res = await tool.execute(url=bad)
        assert res.success is False
        assert "private" in res.error.lower() or "blocked" in res.error.lower()


async def test_fetch_page_blocks_redirect_to_private_host():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
        return httpx.Response(200, text="secret metadata")

    tool = FetchPageTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(url="https://example.com/redir")
    assert res.success is False
    assert "private" in res.error.lower()


async def test_fetch_page_rejects_non_http_scheme_and_http_errors():
    tool = FetchPageTool()
    res = await tool.execute(url="ftp://example.com/file")
    assert res.success is False
    assert "http" in res.error.lower()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(url="https://example.com/missing")
    assert res.success is False
    assert "404" in res.error


async def test_fetch_page_rejects_binary_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG...")

    tool = FetchPageTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(url="https://example.com/logo.png")
    assert res.success is False
    assert "text" in res.error.lower()


async def test_fetch_page_plain_text_passthrough():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="  raw text body  ")

    tool = FetchPageTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(url="https://example.com/robots.txt")
    assert res.success is True
    assert res.result["text"] == "raw text body"
    assert res.result["title"] == ""


# =============================================================================
# wiki.py
# =============================================================================


def test_trim_sentences():
    text = "First sentence. Second one! Third? Fourth sentence."
    assert trim_sentences(text, 1) == "First sentence."
    assert trim_sentences(text, 2) == "First sentence. Second one!"
    assert trim_sentences(text, 99) == text
    assert trim_sentences("", 3) == ""
    # abbreviation guard: "Dr." must not end the sentence
    guarded = trim_sentences("Dr. Smith works at NASA. He is busy.", 1)
    assert guarded == "Dr. Smith works at NASA."
    # whitespace normalized
    assert trim_sentences("A  b.\n\nC d.", 2) == "A b. C d."


WIKI_SUMMARY = {
    "title": "Alan Turing",
    "description": "English computer scientist (1912-1954)",
    "extract": (
        "Alan Mathison Turing was an English mathematician and computer scientist. "
        "He was highly influential in the development of theoretical computer science. "
        "Turing is widely considered to be the father of computer science. "
        "He devised the Turing machine. "
        "After the war he worked at the National Physical Laboratory."
    ),
    "type": "standard",
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Alan_Turing"}},
}


async def test_wikipedia_direct_hit_trims_sentences():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "en.wikipedia.org"
        assert request.url.path == "/api/rest_v1/page/summary/Alan_Turing"
        return httpx.Response(200, json=WIKI_SUMMARY)

    tool = WikipediaTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(topic="Alan Turing", sentences=2)
    assert res.success is True
    assert res.result["title"] == "Alan Turing"
    assert res.result["url"] == "https://en.wikipedia.org/wiki/Alan_Turing"
    assert res.result["extract"].count(".") == 2
    assert res.result["extract"].endswith("theoretical computer science.")
    assert res.result["matched_via_search"] is False


async def test_wikipedia_404_falls_back_to_opensearch():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/rest_v1/page/summary/turing_guy":
            return httpx.Response(404, json={"type": "not_found"})
        if request.url.path == "/w/api.php":
            assert request.url.params["action"] == "opensearch"
            assert request.url.params["search"] == "turing guy"
            return httpx.Response(200, json=["turing guy", ["Alan Turing"], [""], ["https://en.wikipedia.org/wiki/Alan_Turing"]])
        if request.url.path == "/api/rest_v1/page/summary/Alan_Turing":
            return httpx.Response(200, json=WIKI_SUMMARY)
        raise AssertionError(f"unexpected path {request.url.path}")

    tool = WikipediaTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(topic="turing guy")
    assert res.success is True
    assert res.result["title"] == "Alan Turing"
    assert res.result["matched_via_search"] is True
    assert "/w/api.php" in calls


async def test_wikipedia_not_found_anywhere():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/rest_v1/"):
            return httpx.Response(404, json={"type": "not_found"})
        return httpx.Response(200, json=["zzz", [], [], []])

    tool = WikipediaTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(topic="zzzqqq nonsense")
    assert res.success is False
    assert "No Wikipedia article found" in res.error


async def test_wikipedia_language_routing_and_validation():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "de.wikipedia.org"
        return httpx.Response(200, json={**WIKI_SUMMARY, "title": "Alan Turing"})

    tool = WikipediaTool()
    tool.transport = httpx.MockTransport(handler)
    res = await tool.execute(topic="Alan Turing", language="DE")
    assert res.success is True
    assert res.result["language"] == "de"

    res = await tool.execute(topic="x", language="nope!!")
    assert res.success is False
    assert "language" in res.error.lower()


async def test_wikipedia_empty_topic():
    tool = WikipediaTool()
    res = await tool.execute(topic="  ")
    assert res.success is False
    assert "topic" in res.error.lower()


# =============================================================================
# Metadata & module factories
# =============================================================================


def test_module_factories_and_metadata():
    tools = search_mod.get_tools() + fetch_mod.get_tools() + wiki_mod.get_tools()
    names = {t.name for t in tools}
    assert names == {"web_search", "quick_answer", "fetch_page", "wikipedia"}

    for tool in tools:
        meta = tool.get_metadata()
        assert meta.network is True
        assert meta.category == ToolCategory.WEB
        assert meta.permission_level == PermissionLevel.NETWORK_ACTION
        assert meta.available is True  # pure httpx tools work on headless linux
        assert meta.examples, f"{tool.name} should ship examples"
        assert meta.input_schema.required, f"{tool.name} should declare required args"
        assert tool.mutating is False
        # dicts returned by _run must be JSON-serializable in tests above;
        # here just sanity check schema shape
        assert meta.input_schema.type == "object"

    by_name = {t.name: t for t in tools}
    assert "search" in by_name["web_search"].aliases
    assert "google_search" in by_name["web_search"].aliases
    assert "instant_answer" in by_name["quick_answer"].aliases
    assert "read_website" in by_name["fetch_page"].aliases
    assert "wiki" in by_name["wikipedia"].aliases


async def test_results_are_json_serializable():
    tool = WebSearchTool()
    tool.transport = _search_transport()
    res = await tool.execute(query="python programming")
    assert res.success is True
    json.dumps(res.result)  # must not raise
