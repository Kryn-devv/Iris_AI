"""Tests for the WebGL scene that renders IRIS's face.

The scene itself is WebGL and cannot run here, but two things about it are
plain data and both rot silently if left unchecked:

* **the tool to sub-agent map.** Every registered tool belongs to exactly one
  of the five specialists in the constellation. A tool that is added or renamed
  without being mapped means a real action lights up nothing — the constellation
  quietly stops reflecting what IRIS is doing, which is the whole point of it.

* **the script load order.** The scene installs itself behind the same global
  name the canvas-2D hologram used, and only works if it loads after that
  hologram (to capture it as a fallback) and before app.js (which constructs
  it). Getting that order wrong leaves a working page with the old hologram and
  no error anywhere.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "iris" / "app" / "static"
SCENE = STATIC / "scene"

MODULES = ("orb.js", "sky.js", "states.js", "audio.js", "agents.js",
           "reactions.js", "wiring.js", "scene.js")


@pytest.fixture(scope="module")
def wiring_js() -> str:
    return (SCENE / "wiring.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css() -> str:
    return (STATIC / "style.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tool_agent_map(wiring_js) -> dict[str, str]:
    """Parse AGENT_TOOLS out of wiring.js into {tool: agent}."""
    block = re.search(r"var AGENT_TOOLS = \{(.*?)\n  \};", wiring_js, re.S)
    assert block, "AGENT_TOOLS not found in wiring.js"
    mapping: dict[str, str] = {}
    agent = None
    for line in block.group(1).splitlines():
        header = re.match(r"\s*(\w+):\s*\[", line)
        if header:
            agent = header.group(1)
            continue
        for tool in re.findall(r'"([a-z0-9_]+)"', line):
            assert agent, f"tool {tool!r} appeared before any agent"
            assert tool not in mapping, (
                f"'{tool}' is mapped to both '{mapping.get(tool)}' and '{agent}'"
            )
            mapping[tool] = agent
    return mapping


@pytest.fixture(scope="module")
def registered_tools() -> dict[str, str]:
    """Every tool the app actually registers, with its category."""
    from iris.app.tools.loader import load_all_tools
    from iris.app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    load_all_tools(registry, quiet=True)
    tools = getattr(registry, "_tools", None)
    assert tools, "could not read the tool registry's contents"
    return {
        name: str(getattr(tool, "category", "?")).split(".")[-1].lower()
        for name, tool in tools.items()
    }


class TestToolAgentMap:
    def test_every_registered_tool_has_a_specialist(self, tool_agent_map, registered_tools):
        """An unmapped tool lights up nothing when it runs."""
        missing = sorted(set(registered_tools) - set(tool_agent_map))
        assert not missing, (
            "these tools have no sub-agent — add them to AGENT_TOOLS in "
            f"wiring.js: {missing}"
        )

    def test_no_mapped_tool_has_been_renamed_away(self, tool_agent_map, registered_tools):
        """A stale name is dead weight that hides a real gap."""
        unknown = sorted(set(tool_agent_map) - set(registered_tools))
        assert not unknown, (
            f"wiring.js maps tools that no longer exist: {unknown}"
        )

    def test_the_map_is_exactly_the_registry(self, tool_agent_map, registered_tools):
        assert set(tool_agent_map) == set(registered_tools)

    def test_each_tool_belongs_to_one_specialist_only(self, tool_agent_map):
        # The parser asserts on duplicates, so reaching here proves uniqueness;
        # this states the invariant explicitly for anyone reading the file.
        assert len(tool_agent_map) == len(set(tool_agent_map))

    def test_the_five_specialists_are_all_used(self, tool_agent_map):
        expected = {"operator", "scout", "scribe", "relay", "sentinel"}
        assert set(tool_agent_map.values()) == expected

    def test_every_specialist_owns_a_meaningful_share(self, tool_agent_map):
        """A specialist with one tool is not a specialist, it is a label."""
        counts: dict[str, int] = {}
        for agent in tool_agent_map.values():
            counts[agent] = counts.get(agent, 0) + 1
        for agent, count in counts.items():
            assert count >= 5, f"'{agent}' owns only {count} tools"

    def test_the_risky_tools_belong_to_the_warm_specialist(self, tool_agent_map):
        """Sentinel is the one warm colour in a cool palette specifically
        because it holds the actions that can end your session."""
        for tool in ("run_command", "shutdown_pc", "restart_pc", "kill_process"):
            assert tool_agent_map.get(tool) == "sentinel", (
                f"'{tool}' should belong to sentinel, not {tool_agent_map.get(tool)}"
            )

    def test_device_tools_belong_to_relay(self, tool_agent_map):
        """The robot, the relays and the face are all one specialist's job."""
        for tool in ("device_motor", "device_switch", "device_sensors", "face_emotion"):
            assert tool_agent_map.get(tool) == "relay"

    def test_the_constellation_defaults_match_the_mapped_ids(self, tool_agent_map):
        agents_js = (SCENE / "agents.js").read_text(encoding="utf-8")
        block = re.search(r"var DEFAULT_AGENTS = \[(.*?)\n  \];", agents_js, re.S)
        assert block, "DEFAULT_AGENTS not found in agents.js"
        ids = set(re.findall(r'id:\s*"([a-z]+)"', block.group(1)))
        assert ids == set(tool_agent_map.values()), (
            "the agents drawn in the constellation and the agents tools map to "
            "have drifted apart"
        )


class TestSceneAssets:
    def test_every_module_is_present(self):
        for name in MODULES:
            assert (SCENE / name).is_file(), f"scene/{name} is missing"

    def test_three_js_is_vendored_not_fetched_from_a_cdn(self):
        """IRIS is offline-first: the face must render with no internet."""
        vendored = STATIC / "vendor" / "three.min.js"
        assert vendored.is_file(), "three.js is not vendored"
        assert vendored.stat().st_size > 200_000

    def test_no_module_reaches_for_a_cdn(self):
        for name in MODULES:
            body = (SCENE / name).read_text(encoding="utf-8")
            for host in ("unpkg.com", "cdn.jsdelivr.net", "cdnjs.", "esm.sh"):
                assert host not in body, f"scene/{name} references {host}"

    def test_the_dev_harness_uses_servable_paths(self):
        """It should work at /static/scene/dev.html under the real app."""
        body = (SCENE / "dev.html").read_text(encoding="utf-8")
        for src in re.findall(r'<script src="([^"]+)"', body):
            assert src.startswith("/static/"), f"{src} will 404 under the app"


class TestLoadOrder:
    def test_scripts_load_in_the_only_order_that_works(self, index_html):
        """three.js, then the old hologram (so the scene can capture it as a
        fallback), then the scene, then app.js (which constructs it)."""
        def at(needle: str) -> int:
            index = index_html.find(needle)
            assert index >= 0, f"{needle} is not loaded by index.html"
            return index

        three = at("/static/vendor/three.min.js")
        hologram = at("/static/hologram.js")
        scene = at("/static/scene/scene.js")
        app = at("/static/app.js")

        assert three < scene, "three.js must load before the scene"
        assert hologram < scene, (
            "hologram.js must load first so the scene can keep it as the "
            "no-WebGL fallback"
        )
        assert scene < app, "the scene must be installed before app.js runs"

    def test_every_module_is_loaded(self, index_html):
        for name in MODULES:
            assert f"/static/scene/{name}" in index_html, f"{name} is not loaded"

    def test_modules_load_before_scene_js(self, index_html):
        scene_at = index_html.find("/static/scene/scene.js")
        for name in MODULES:
            if name == "scene.js":
                continue
            assert index_html.find(f"/static/scene/{name}") < scene_at, (
                f"{name} must load before scene.js, which uses it"
            )

    def test_the_existing_ui_contract_is_intact(self, index_html):
        """tests/test_ui.py asserts on these, and app.js constructs against
        the #holo canvas even though the WebGL scene replaces it."""
        assert "IRIS" in index_html
        assert "app.js" in index_html
        assert 'id="holo"' in index_html
        assert 'id="stage"' in index_html


class TestStyleIntegration:
    def test_both_canvas_layers_are_styled(self, css):
        assert "#scene-bg" in css and "#scene" in css

    def test_the_old_hologram_decorations_are_retired(self, css):
        """The CSS halo and reflection floor were drawn for the canvas-2D
        sphere; left visible they double up with the WebGL orb."""
        assert "body.iris-scene-active #holo" in css
        assert "body.iris-scene-active .halo" in css
        assert "body.iris-scene-active .stage::after" in css

    def test_the_scene_does_not_swallow_clicks(self, css):
        block = css[css.find("#scene-bg"):]
        assert "pointer-events: none" in block[:400]


class TestAccessibilityAndPerformance:
    """Verified in a real browser during the build — agent drift went to exactly
    0.0000 and camera drift and orb spin to 25% — but the source contract is
    pinned here so it cannot regress unnoticed."""

    def test_agents_are_held_still_under_reduced_motion(self):
        body = (SCENE / "agents.js").read_text(encoding="utf-8")
        assert "ctx.reducedMotion ? o.phase" in body, (
            "agents must hold at their starting angle, not stop mid-orbit"
        )

    def test_motion_is_scaled_rather_than_only_the_agents_stopped(self):
        """A frozen constellation in front of a full-speed twinkling starfield
        is not reduced motion — the starfield is the loudest thing on screen."""
        sky = (SCENE / "sky.js").read_text(encoding="utf-8")
        assert "starLayer(su, 190.0, 0.055, 3.0, t)" in sky, (
            "the star twinkle must run on motion-scaled time"
        )
        orb = (SCENE / "orb.js").read_text(encoding="utf-8")
        assert "live.churn * (0.25 + motionScale" in orb, (
            "the orb's surface churn must respect the motion scale"
        )

    def test_the_scene_pauses_when_hidden(self):
        scene = (SCENE / "scene.js").read_text(encoding="utf-8")
        assert "visibilitychange" in scene
        assert "if (document.hidden) this._stop();" in scene

    def test_the_scene_can_be_paused_by_an_overlay(self):
        scene = (SCENE / "scene.js").read_text(encoding="utf-8")
        wiring = (SCENE / "wiring.js").read_text(encoding="utf-8")
        assert "_pausedByApp" in scene
        assert "watchOverlays" in wiring

    def test_performance_mode_only_degrades_the_background(self):
        """The orb must stay full quality; the background is the expensive half
        and drifts slowly enough that half resolution is invisible."""
        wiring = (SCENE / "wiring.js").read_text(encoding="utf-8")
        assert "setPerformanceMode" in wiring
        assert "bgDprScale" in wiring
        scene = (SCENE / "scene.js").read_text(encoding="utf-8")
        assert "this.perfMode && (this._bgTick & 1)" in scene

    def test_graphics_resources_are_released(self):
        """Leaking a WebGL context is how iOS ends up refusing the next one."""
        scene = (SCENE / "scene.js").read_text(encoding="utf-8")
        assert "forceContextLoss" in scene
        for name in ("orb.js", "sky.js", "agents.js", "reactions.js"):
            body = (SCENE / name).read_text(encoding="utf-8")
            assert "dispose" in body, f"scene/{name} has no dispose path"

    def test_there_is_a_no_webgl_fallback(self):
        """CLAUDE.md's contract is that IRIS boots anywhere."""
        scene = (SCENE / "scene.js").read_text(encoding="utf-8")
        assert "webglAvailable" in scene
        assert "IrisHologramFallback" in scene
        assert (STATIC / "hologram.js").is_file(), (
            "the canvas-2D fallback must stay on disk"
        )


class TestAppBridge:
    def test_bus_events_reach_the_scene(self, app_js):
        assert "holo.handleBusEvent" in app_js

    def test_the_turn_ending_transitions_exist(self, app_js):
        """Without these the orb stayed stuck in 'thinking' after any turn that
        arrived over voice or Telegram rather than the websocket."""
        assert 'case "agent.completed"' in app_js
        assert 'case "agent.failed"' in app_js

    def test_the_hologram_is_still_constructed_the_same_way(self, app_js):
        assert "new window.IrisHologram(" in app_js
