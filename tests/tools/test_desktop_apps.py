"""Tests for the desktop app tools (apps.py) and website tools (websites.py).

Everything runs on headless Linux: subprocess/webbrowser/psutil interactions
are monkeypatched so nothing is ever actually launched or terminated.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import webbrowser
from typing import Any

import pytest

from iris.app.core.security import PermissionLevel
from iris.app.tools.desktop import apps as apps_mod
from iris.app.tools.desktop import websites as web_mod
from iris.app.tools.desktop.apps import (
    APP_SPECS,
    CloseAppTool,
    ListAppsTool,
    OpenAppTool,
    resolve_app,
    suggest_apps,
)
from iris.app.tools.desktop.websites import (
    OpenWebsiteTool,
    PlayOnYouTubeTool,
    describe_site,
    resolve_site,
)


# =============================================================================
# Shared fakes
# =============================================================================


class FakePopen:
    """Records constructor arguments instead of spawning anything."""

    calls: list[tuple[list[str], dict[str, Any]]] = []

    def __init__(self, argv, **kwargs):
        FakePopen.calls.append((list(argv), kwargs))
        self.pid = 4242


@pytest.fixture()
def fake_popen(monkeypatch):
    FakePopen.calls = []
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    return FakePopen


@pytest.fixture()
def fake_browser(monkeypatch):
    opened: list[str] = []

    def _open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(webbrowser, "open", _open)
    return opened


class FakeProc:
    def __init__(self, pid: int, name: str):
        self.info = {"pid": pid, "name": name}
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class FakePsutil:
    """Minimal psutil stand-in for close_app tests."""

    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    def __init__(self, procs: list[FakeProc], survivors: set[int] = frozenset()):
        self._procs = procs
        self._survivors = survivors

    def process_iter(self, attrs=None):
        return list(self._procs)

    def wait_procs(self, procs, timeout=None):
        gone = [p for p in procs if p.info["pid"] not in self._survivors]
        alive = [p for p in procs if p.info["pid"] in self._survivors]
        return gone, alive


# =============================================================================
# resolve_app: alias resolution
# =============================================================================


@pytest.mark.parametrize(
    ("spoken", "expected_key"),
    [
        ("notepad", "notepad"),
        ("Notepad", "notepad"),
        ("notepad.exe", "notepad"),
        ("text editor", "notepad"),
        ("calc", "calculator"),
        ("Calculator", "calculator"),
        ("paint", "paint"),
        ("ms paint", "paint"),
        ("browser", "browser"),
        ("web browser", "browser"),
        ("chrome", "chrome"),
        ("Google Chrome", "chrome"),
        ("chromium", "chrome"),
        ("firefox", "firefox"),
        ("mozilla firefox", "firefox"),
        ("edge", "edge"),
        ("Microsoft Edge", "edge"),
        ("terminal", "terminal"),
        ("cmd", "terminal"),
        ("command prompt", "terminal"),
        ("powershell", "powershell"),
        ("pwsh", "powershell"),
        ("file manager", "file_manager"),
        ("files", "file_manager"),
        ("explorer", "file_manager"),
        ("file explorer", "file_manager"),
        ("vscode", "vscode"),
        ("vs code", "vscode"),
        ("VS Code", "vscode"),
        ("vs-code", "vscode"),
        ("code", "vscode"),
        ("visual studio code", "vscode"),
        ("word", "word"),
        ("microsoft word", "word"),
        ("excel", "excel"),
        ("powerpoint", "powerpoint"),
        ("power point", "powerpoint"),
        ("task manager", "task_manager"),
        ("taskmgr", "task_manager"),
        ("settings", "settings"),
        ("control panel", "settings"),
        ("spotify", "spotify"),
        ("vlc", "vlc"),
        ("VLC media player", "vlc"),
        ("discord", "discord"),
        ("steam", "steam"),
        ("camera", "camera"),
        ("webcam", "camera"),
        ("snipping tool", "snipping_tool"),
        ("SNIPPING  TOOL", "snipping_tool"),
        ("wordpad", "wordpad"),
        ("word pad", "wordpad"),
    ],
)
def test_resolve_app_aliases(spoken, expected_key):
    spec = resolve_app(spoken)
    assert spec is not None, f"{spoken!r} did not resolve"
    assert spec.key == expected_key


def test_resolve_app_unknown_returns_none():
    assert resolve_app("definitely-not-an-app-xyz") is None
    assert resolve_app("") is None
    assert resolve_app("   ") is None


def test_every_spec_has_launchers_and_processes():
    for key, spec in APP_SPECS.items():
        assert spec.label, key
        assert spec.group, key
        assert spec.processes, f"{key} has no process names for close_app"
        assert spec.windows or spec.linux or spec.macos, f"{key} has no launchers"
        # Its own key and label must resolve back to itself.
        assert resolve_app(key).key == key
        assert resolve_app(spec.label).key == key


def test_suggest_apps_finds_close_matches():
    suggestions = suggest_apps("chrme")
    assert suggestions, "expected at least one suggestion for 'chrme'"
    assert any("chrome" in s for s in suggestions)
    assert suggest_apps("") == []


# =============================================================================
# OpenAppTool
# =============================================================================


async def test_open_app_linux_uses_which_and_detached_popen(monkeypatch, fake_popen):
    monkeypatch.setattr(apps_mod, "current_os", lambda: "linux")
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/gedit" if name == "gedit" else None
    )

    res = await OpenAppTool().execute(app="notepad")
    assert res.success is True
    assert res.result["strategy"] == "which+popen"
    assert res.result["target"] == "/usr/bin/gedit"
    assert res.speech == "Opened Notepad."

    argv, kwargs = fake_popen.calls[0]
    assert argv == ["/usr/bin/gedit"]
    assert kwargs.get("start_new_session") is True
    assert kwargs.get("stdout") is subprocess.DEVNULL


async def test_open_app_linux_command_with_arguments(monkeypatch, fake_popen):
    """LibreOffice-style candidates ('libreoffice --writer') keep their args."""
    monkeypatch.setattr(apps_mod, "current_os", lambda: "linux")
    monkeypatch.setattr(
        shutil, "which",
        lambda name: "/usr/bin/libreoffice" if name == "libreoffice" else None,
    )

    res = await OpenAppTool().execute(app="word")
    assert res.success is True
    argv, _ = fake_popen.calls[0]
    assert argv == ["/usr/bin/libreoffice", "--writer"]


async def test_open_app_linux_not_installed(monkeypatch, fake_popen):
    monkeypatch.setattr(apps_mod, "current_os", lambda: "linux")
    monkeypatch.setattr(shutil, "which", lambda name: None)

    res = await OpenAppTool().execute(app="spotify")
    assert res.success is False
    assert "spotify" in res.error.lower()
    assert fake_popen.calls == []


async def test_open_app_windows_startfile_strategy(monkeypatch, fake_popen):
    monkeypatch.setattr(apps_mod, "current_os", lambda: "windows")
    started: list[str] = []
    monkeypatch.setattr(os, "startfile", lambda target: started.append(target), raising=False)

    res = await OpenAppTool().execute(app="notepad")
    assert res.success is True
    assert res.result["strategy"] == "os.startfile"
    assert started == ["notepad"]
    assert fake_popen.calls == []


async def test_open_app_windows_falls_back_to_cmd_start(monkeypatch, fake_popen):
    monkeypatch.setattr(apps_mod, "current_os", lambda: "windows")

    def _fail(target):
        raise OSError("not found")

    monkeypatch.setattr(os, "startfile", _fail, raising=False)
    monkeypatch.setattr(os.path, "isfile", lambda p: False)

    res = await OpenAppTool().execute(app="calculator")
    assert res.success is True
    assert res.result["strategy"] == "cmd_start"
    argv, _ = fake_popen.calls[0]
    assert argv == ["cmd", "/c", "start", "", "calc"]


async def test_open_app_macos_open_a(monkeypatch):
    monkeypatch.setattr(apps_mod, "current_os", lambda: "macos")
    runs: list[list[str]] = []

    class _Result:
        returncode = 0

    def _run(argv, **kwargs):
        runs.append(list(argv))
        return _Result()

    monkeypatch.setattr(subprocess, "run", _run)

    res = await OpenAppTool().execute(app="chrome")
    assert res.success is True
    assert res.result["strategy"] == "open -a"
    assert runs == [["open", "-a", "Google Chrome"]]


async def test_open_app_unknown_falls_back_to_path_lookup(monkeypatch, fake_popen):
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/htop" if name == "htop" else None
    )

    res = await OpenAppTool().execute(app="htop")
    assert res.success is True
    assert res.result["strategy"] == "which+popen"
    assert fake_popen.calls[0][0] == ["/usr/bin/htop"]


async def test_open_app_unknown_gives_suggestions(monkeypatch, fake_popen):
    monkeypatch.setattr(shutil, "which", lambda name: None)

    res = await OpenAppTool().execute(app="chrme")
    assert res.success is False
    assert "Did you mean" in res.error
    assert "chrome" in res.error.lower()
    assert fake_popen.calls == []


async def test_open_app_requires_app_argument():
    res = await OpenAppTool().execute(app="   ")
    assert res.success is False
    assert "required" in res.error


def test_open_app_metadata():
    tool = OpenAppTool()
    assert tool.name == "open_app"
    assert tool.permission_level == PermissionLevel.DESKTOP_ACTION
    assert tool.category == "desktop"
    assert set(tool.aliases) == {"launch_app", "start_app", "run_app"}
    assert tool.mutating is True
    assert tool.network is False
    assert tool.input_schema.required == ["app"]
    assert tool.examples


# =============================================================================
# CloseAppTool
# =============================================================================


@pytest.mark.parametrize("victim", ["systemd", "init", "winlogon", "csrss", "svchost", "lsass"])
async def test_close_app_refuses_critical_processes(victim):
    res = await CloseAppTool().execute(app=victim)
    assert res.success is False
    assert "critical" in res.error.lower()


async def test_close_app_explorer_needs_force_on_windows(monkeypatch):
    monkeypatch.setattr(apps_mod, "current_os", lambda: "windows")
    res = await CloseAppTool().execute(app="explorer")
    assert res.success is False
    assert "force" in res.error.lower()


async def test_close_app_explorer_with_force_terminates(monkeypatch):
    monkeypatch.setattr(apps_mod, "current_os", lambda: "windows")
    explorer = FakeProc(101, "explorer.exe")
    fake = FakePsutil([explorer, FakeProc(1, "winlogon.exe")])
    monkeypatch.setattr(apps_mod, "try_import", lambda name: fake)

    res = await CloseAppTool().execute(app="explorer", force=True)
    assert res.success is True
    assert explorer.terminated is True
    assert res.result["closed"] == 1


async def test_close_app_terminates_matching_processes(monkeypatch):
    procs = [
        FakeProc(11, "Spotify.exe"),
        FakeProc(12, "spotify"),
        FakeProc(13, "chrome"),          # unrelated: must be untouched
        FakeProc(1, "systemd"),          # critical: must be untouched
    ]
    fake = FakePsutil(procs)
    monkeypatch.setattr(apps_mod, "try_import", lambda name: fake)

    res = await CloseAppTool().execute(app="spotify")
    assert res.success is True
    assert res.result["matched"] == 2
    assert res.result["closed"] == 2
    assert res.speech == "Closed Spotify."
    assert procs[0].terminated and procs[1].terminated
    assert not procs[2].terminated and not procs[3].terminated


async def test_close_app_hard_kills_survivors(monkeypatch):
    stubborn = FakeProc(21, "vlc")
    fake = FakePsutil([stubborn], survivors={21})
    monkeypatch.setattr(apps_mod, "try_import", lambda name: fake)

    res = await CloseAppTool().execute(app="vlc")
    assert res.success is True
    assert stubborn.terminated is True
    assert stubborn.killed is True
    assert res.result["forced_kill"] == 1


async def test_close_app_nothing_running(monkeypatch):
    fake = FakePsutil([FakeProc(31, "bash")])
    monkeypatch.setattr(apps_mod, "try_import", lambda name: fake)

    res = await CloseAppTool().execute(app="discord")
    assert res.success is False
    assert "No running process" in res.error


async def test_close_app_without_psutil(monkeypatch):
    monkeypatch.setattr(apps_mod, "try_import", lambda name: None)
    res = await CloseAppTool().execute(app="spotify")
    assert res.success is False
    assert "psutil" in res.error


async def test_close_app_never_targets_itself(monkeypatch):
    me = FakeProc(os.getpid(), "spotify")
    fake = FakePsutil([me])
    monkeypatch.setattr(apps_mod, "try_import", lambda name: fake)

    res = await CloseAppTool().execute(app="spotify")
    assert res.success is False  # own pid skipped -> nothing matched
    assert me.terminated is False


def test_close_app_metadata():
    tool = CloseAppTool()
    assert tool.name == "close_app"
    assert tool.permission_level == PermissionLevel.CONFIRM_REQUIRED
    assert set(tool.aliases) == {"quit_app", "kill_app"}
    assert tool.mutating is True
    assert tool.input_schema.required == ["app"]


# =============================================================================
# ListAppsTool
# =============================================================================


async def test_list_apps_groups_installed(monkeypatch):
    monkeypatch.setattr(apps_mod, "current_os", lambda: "linux")
    present = {"gedit": "/usr/bin/gedit", "firefox": "/usr/bin/firefox"}
    monkeypatch.setattr(shutil, "which", lambda name: present.get(name))

    res = await ListAppsTool().execute()
    assert res.success is True
    installed = res.result["installed"]
    assert {e["app"] for e in installed["editors"]} == {"notepad"}
    browser_keys = {e["app"] for e in installed["browsers"]}
    assert {"firefox", "browser"} <= browser_keys
    assert "spotify" in res.result["missing"]
    assert res.result["installed_count"] == sum(len(v) for v in installed.values())
    assert res.speech


async def test_list_apps_none_installed(monkeypatch):
    monkeypatch.setattr(apps_mod, "current_os", lambda: "linux")
    monkeypatch.setattr(shutil, "which", lambda name: None)

    res = await ListAppsTool().execute()
    assert res.success is True
    assert res.result["installed"] == {}
    assert res.result["installed_count"] == 0
    assert len(res.result["missing"]) == len(APP_SPECS)


def test_list_apps_metadata():
    tool = ListAppsTool()
    assert tool.name == "list_apps"
    assert tool.permission_level == PermissionLevel.READ
    assert tool.mutating is False


def test_get_tools_apps():
    names = [t.name for t in apps_mod.get_tools()]
    assert names == ["open_app", "close_app", "list_apps"]


# =============================================================================
# resolve_site: alias resolution and URL building
# =============================================================================


@pytest.mark.parametrize(
    ("spoken", "expected_url"),
    [
        ("youtube", "https://www.youtube.com"),
        ("YT", "https://www.youtube.com"),
        ("google", "https://www.google.com"),
        ("gmail", "https://mail.google.com"),
        ("email", "https://mail.google.com"),
        ("github", "https://github.com"),
        ("whatsapp", "https://web.whatsapp.com"),
        ("twitter", "https://x.com"),
        ("x", "https://x.com"),
        ("instagram", "https://www.instagram.com"),
        ("facebook", "https://www.facebook.com"),
        ("netflix", "https://www.netflix.com"),
        ("prime video", "https://www.primevideo.com"),
        ("chatgpt", "https://chatgpt.com"),
        ("claude", "https://claude.ai"),
        ("maps", "https://www.google.com/maps"),
        ("google maps", "https://www.google.com/maps"),
        ("translate", "https://translate.google.com"),
        ("drive", "https://drive.google.com"),
        ("docs", "https://docs.google.com"),
        ("sheets", "https://sheets.google.com"),
        ("slides", "https://slides.google.com"),
        ("reddit", "https://www.reddit.com"),
        ("stackoverflow", "https://stackoverflow.com"),
        ("stack overflow", "https://stackoverflow.com"),
        ("Stack-Overflow", "https://stackoverflow.com"),
        ("linkedin", "https://www.linkedin.com"),
        ("amazon", "https://www.amazon.com"),
        ("flipkart", "https://www.flipkart.com"),
        ("wikipedia", "https://www.wikipedia.org"),
        ("spotify", "https://open.spotify.com"),
        ("twitch", "https://www.twitch.tv"),
        ("pinterest", "https://www.pinterest.com"),
        ("canva", "https://www.canva.com"),
        ("figma", "https://www.figma.com"),
        ("notion", "https://www.notion.so"),
        ("outlook", "https://outlook.live.com"),
        ("calendar", "https://calendar.google.com"),
    ],
)
def test_resolve_site_aliases(spoken, expected_url):
    assert resolve_site(spoken) == expected_url


@pytest.mark.parametrize(
    ("domainish", "expected_url"),
    [
        ("youtube.com", "https://www.youtube.com"),
        ("www.youtube.com", "https://www.youtube.com"),
        ("x.com", "https://x.com"),
        ("github.com", "https://github.com"),
    ],
)
def test_resolve_site_known_domains_map_to_spec(domainish, expected_url):
    assert resolve_site(domainish) == expected_url


@pytest.mark.parametrize(
    ("site", "query", "expected_url"),
    [
        ("youtube", "lo-fi beats",
         "https://www.youtube.com/results?search_query=lo-fi+beats"),
        ("google", "C++ tutorial",
         "https://www.google.com/search?q=C%2B%2B+tutorial"),
        ("amazon", "wireless headphones",
         "https://www.amazon.com/s?k=wireless+headphones"),
        ("wikipedia", "alan turing",
         "https://en.wikipedia.org/wiki/Special:Search?search=alan+turing"),
        ("maps", "coffee near me",
         "https://www.google.com/maps/search/coffee%20near%20me"),
        ("github", "iris assistant",
         "https://github.com/search?q=iris+assistant"),
        ("spotify", "lo-fi beats",
         "https://open.spotify.com/search/lo-fi%20beats"),
    ],
)
def test_resolve_site_query_urls(site, query, expected_url):
    assert resolve_site(site, query) == expected_url


def test_resolve_site_query_fallback_uses_google_site_search():
    # Gmail has no native search template -> google `site:` search.
    url = resolve_site("gmail", "meeting notes")
    assert url == "https://www.google.com/search?q=site%3Amail.google.com+meeting+notes"


def test_resolve_site_bare_domain_gets_https():
    assert resolve_site("example.com") == "https://example.com"
    assert resolve_site("sub.example.co.uk/path") == "https://sub.example.co.uk/path"


def test_resolve_site_full_urls_pass_through():
    assert resolve_site("https://example.com/a?b=c") == "https://example.com/a?b=c"
    assert resolve_site("http://legacy.example.com") == "http://legacy.example.com"


def test_resolve_site_url_with_query_becomes_site_search():
    url = resolve_site("https://example.com/docs", "installation")
    assert url == "https://www.google.com/search?q=site%3Aexample.com+installation"


def test_resolve_site_rejects_dangerous_schemes():
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,hi"):
        with pytest.raises(ValueError):
            resolve_site(bad)


def test_resolve_site_rejects_empty():
    with pytest.raises(ValueError):
        resolve_site("   ")


def test_resolve_site_free_text_becomes_google_search():
    url = resolve_site("that cool rust game engine")
    assert url == "https://www.google.com/search?q=that+cool+rust+game+engine"


def test_describe_site_labels():
    assert describe_site("youtube") == "YouTube"
    assert describe_site("stack overflow") == "Stack Overflow"
    assert describe_site("https://example.com/x") == "example.com"
    assert describe_site("example.com") == "example.com"


# =============================================================================
# OpenWebsiteTool / PlayOnYouTubeTool
# =============================================================================


async def test_open_website_opens_and_speaks(fake_browser):
    res = await OpenWebsiteTool().execute(site="youtube")
    assert res.success is True
    assert fake_browser == ["https://www.youtube.com"]
    assert res.speech == "Opened YouTube."


async def test_open_website_with_query_speaks_search(fake_browser):
    res = await OpenWebsiteTool().execute(site="youtube", query="lo-fi beats")
    assert res.success is True
    assert fake_browser == ["https://www.youtube.com/results?search_query=lo-fi+beats"]
    assert res.speech == "Searching YouTube for lo-fi beats."


async def test_open_website_rejects_bad_scheme(fake_browser):
    res = await OpenWebsiteTool().execute(site="javascript:alert(1)")
    assert res.success is False
    assert "scheme" in res.error.lower()
    assert fake_browser == []


async def test_open_website_requires_site(fake_browser):
    res = await OpenWebsiteTool().execute(site="")
    assert res.success is False
    assert fake_browser == []


async def test_open_website_reports_missing_browser(monkeypatch):
    monkeypatch.setattr(webbrowser, "open", lambda url: False)
    res = await OpenWebsiteTool().execute(site="github")
    assert res.success is False
    assert "browser" in res.error.lower()


async def test_play_youtube_opens_results(fake_browser):
    res = await PlayOnYouTubeTool().execute(query="never gonna give you up")
    assert res.success is True
    assert fake_browser == [
        "https://www.youtube.com/results?search_query=never+gonna+give+you+up"
    ]
    assert res.speech == "Playing never gonna give you up on YouTube."


async def test_play_youtube_requires_query(fake_browser):
    res = await PlayOnYouTubeTool().execute(query="  ")
    assert res.success is False
    assert fake_browser == []


def test_website_tools_metadata():
    open_site = OpenWebsiteTool()
    assert open_site.name == "open_website"
    assert open_site.permission_level == PermissionLevel.DESKTOP_ACTION
    assert set(open_site.aliases) == {"open_site", "open_url", "browse"}
    assert open_site.network is False
    assert open_site.input_schema.required == ["site"]

    play = PlayOnYouTubeTool()
    assert play.name == "play_youtube"
    assert set(play.aliases) == {"play_video", "youtube_play"}
    assert play.network is False
    assert play.input_schema.required == ["query"]


def test_get_tools_websites():
    names = [t.name for t in web_mod.get_tools()]
    assert names == ["open_website", "play_youtube"]
