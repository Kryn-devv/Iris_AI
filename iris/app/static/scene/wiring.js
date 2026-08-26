/* IRIS scene — Tier 6: wiring, performance, accessibility.
 *
 * Turns bus events into scene reactions, and adds the three things that decide
 * whether this is pleasant to leave open all day: a performance mode, respect
 * for "reduce motion", and not burning a battery on a hidden tab.
 *
 * The tool -> agent map below is EXPLICIT rather than keyword-matched. All 78
 * registered tools are listed, so a tool that gets renamed shows up as an
 * unmapped name in the console rather than silently lighting the wrong
 * specialist — which is the kind of wrong that nobody notices for months.
 */
(function (global) {
  "use strict";

  /* Every tool IRIS registers, grouped by the specialist that owns it.
   * operator 21 · scout 11 · scribe 17 · relay 15 · sentinel 14  = 78 */
  var AGENT_TOOLS = {
    operator: [
      /* desktop */
      "clipboard_read", "clipboard_write", "close_app", "close_window",
      "focus_window", "list_apps", "list_windows", "maximize_window",
      "minimize_window", "mouse_click", "mouse_move", "notify", "open_app",
      "press_keys", "screen_size", "scroll", "take_screenshot", "type_text",
      /* media */
      "media_control", "speak", "volume",
    ],
    scout: [
      /* web */
      "fetch_page", "news", "open_website", "play_youtube", "quick_answer",
      "weather", "web_search", "wikipedia",
      /* core — the answer is a number */
      "calculator", "string_utils", "unit_converter",
    ],
    scribe: [
      /* files */
      "copy_path", "delete_path", "file_info", "find_and_open", "list_directory",
      "move_path", "open_path", "read_file", "search_files", "write_file",
      /* content */
      "create_presentation", "create_spreadsheet", "quick_note", "write_document",
      /* code */
      "run_python", "scaffold_project", "write_code",
    ],
    relay: [
      /* automation: timers, routines, and everything on the far side of WiFi */
      "cancel_reminder", "device_command", "device_motor", "device_sensors",
      "device_servo", "device_status", "device_switch", "face_emotion", "list_devices",
      "list_reminders", "map_device_command", "register_device", "remove_device",
      "set_reminder", "set_routine", "set_timer",
    ],
    sentinel: [
      /* system — including every high-risk action, which is why it is the one
       * warm colour in the constellation */
      "autostart", "cancel_shutdown", "environment_info", "kill_process",
      "list_processes", "lock_screen", "network_info", "ping", "restart_pc",
      "run_command", "shutdown_pc", "sleep_pc", "system_info", "time",
    ],
  };

  var TOOL_AGENT = {};
  Object.keys(AGENT_TOOLS).forEach(function (agentId) {
    AGENT_TOOLS[agentId].forEach(function (tool) { TOOL_AGENT[tool] = agentId; });
  });

  var warned = {};
  function agentForTool(tool) {
    if (!tool) return null;
    var id = TOOL_AGENT[tool];
    if (id) return id;
    if (!warned[tool]) {
      warned[tool] = true;
      console.info("[iris] tool '" + tool + "' has no specialist — add it to AGENT_TOOLS.");
    }
    return null;
  }

  /* Bus topic -> orb state. Only the topics that genuinely mean a change of
   * what IRIS is doing; the rest are ignored rather than guessed at. */
  var TOPIC_STATE = {
    "agent.started": "thinking",
    "agent.thinking": "thinking",
    "agent.plan": "thinking",
    "tool.started": "thinking",
    "agent.completed": "idle",
    "agent.failed": "error",
    "tool.failed": "error",
    "llm.fallback": "error",
    "voice.speaking": "speaking",
    "voice.wake": "listening",
  };

  function install(Scene) {
    /* ---- events ---- */

    /* Called for every bus event. Safe to hand anything: an unknown topic is
     * ignored, and a malformed payload cannot throw out of here. */
    Scene.prototype.handleBusEvent = function (topic, payload) {
      payload = payload || {};
      try {
        var next = TOPIC_STATE[topic];

        if (topic === "tool.started") {
          var id = agentForTool(payload.tool);
          if (id) {
            this.dispatchAgent(id);
            this.setAgentBusy(id, true);
          }
        } else if (topic === "tool.completed" || topic === "tool.failed") {
          var done = agentForTool(payload.tool);
          if (done) this.setAgentBusy(done, false);
        } else if (topic === "agent.completed" || topic === "agent.failed") {
          /* A turn is over: nothing should still be showing as busy. */
          this.clearBusy();
        }

        if (next) this.setState(next);
      } catch (err) {
        console.debug("[iris] scene ignored a bus event:", topic, err);
      }
      return true;
    };

    Scene.prototype.clearBusy = function () {
      var self = this;
      this.constellation.agents.forEach(function (a) {
        if (a.busy) self.setAgentBusy(a.id, false);
      });
    };

    /* ---- performance ---- */

    /* Drops the BACKGROUND to a lower resolution and renders it every other
     * frame; the orb stays full quality. The background drifts slowly enough
     * that neither is visible, and it is the expensive half of the scene. */
    Scene.prototype.setPerformanceMode = function (on) {
      this.perfMode = !!on;
      this.bgDprScale = this.perfMode ? 0.6 : 1.0;
      this._resize();
      return this.perfMode;
    };

    /* ---- pausing ---- */

    /* Explicit pause, for when a full-screen panel covers the scene. Resumes
     * without a jump because the clock delta is reset on start. */
    Scene.prototype.setPaused = function (paused) {
      this._pausedByApp = !!paused;
      if (this._pausedByApp) this._stop();
      else if (!document.hidden) this._start();
      return this._pausedByApp;
    };

    /* The app's own modal and drawer cover the scene. Watching for them means
     * opening settings does not keep a WebGL scene rendering behind it. */
    Scene.prototype.watchOverlays = function (selectors) {
      var self = this;
      var list = selectors || [".modal.open", ".drawer.open", "#modal.open"];
      if (!global.MutationObserver) return;
      var check = function () {
        var covered = list.some(function (sel) {
          var el = document.querySelector(sel);
          return !!el;
        });
        if (covered !== self._coveredByOverlay) {
          self._coveredByOverlay = covered;
          self.setPaused(covered);
        }
      };
      this._overlayObserver = new MutationObserver(check);
      this._overlayObserver.observe(document.body, {
        attributes: true, subtree: true, attributeFilter: ["class"],
      });
      check();
    };
  }

  global.IrisWiring = {
    install: install,
    AGENT_TOOLS: AGENT_TOOLS,
    TOOL_AGENT: TOOL_AGENT,
    TOPIC_STATE: TOPIC_STATE,
    agentForTool: agentForTool,
  };
})(window);
