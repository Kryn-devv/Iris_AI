/* IRIS web client: WebSocket transport, browser voice (Web Speech API),
 * hologram state machine, confirmations, settings drawer. */
"use strict";

(() => {
  // ─────────────────────────────── DOM ───────────────────────────────
  const $ = (id) => document.getElementById(id);
  const els = {
    holo: $("holo"), stateLabel: $("stateLabel"), transcript: $("liveTranscript"),
    conversation: $("conversation"), messages: $("messages"), welcome: $("welcome"),
    ticker: $("ticker"), input: $("input"), btnSend: $("btnSend"), btnMic: $("btnMic"),
    connDot: $("connDot"), chipProvider: $("chipProvider"), chipVoice: $("chipVoice"),
    wakeToggle: $("wakeToggle"), speakToggle: $("speakToggle"),
    confirmModal: $("confirmModal"), confirmText: $("confirmText"),
    confirmArgs: $("confirmArgs"), btnApprove: $("btnApprove"), btnReject: $("btnReject"),
    drawer: $("drawer"), btnSettings: $("btnSettings"), btnCloseDrawer: $("btnCloseDrawer"),
    llmStatus: $("llmStatus"), voiceStatus: $("voiceStatus"), toolGrid: $("toolGrid"),
    toolCount: $("toolCount"), reminderList: $("reminderList"),
    btnPair: $("btnPair"), pairDetails: $("pairDetails"),
  };

  const urlToken = new URLSearchParams(location.search).get("token");
  if (urlToken) localStorage.setItem("iris_token", urlToken);
  const token = () => { try { return localStorage.getItem("iris_token") || ""; } catch { return ""; } };
  const authHeaders = () => token() ? { "X-Iris-Token": token() } : {};

  // ───────────────────────────── Hologram ─────────────────────────────
  const holo = new window.IrisHologram(els.holo, {
    accent: "#5eead4",
    quality: matchMedia("(max-width: 640px)").matches ? "medium" : "high",
    reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
  });

  /* If the 3D scene could not start, say so where the user is already looking.
     Silently showing the old flat hologram makes a stale install and switched-off
     WebGL look identical, and both look like "the new UI never shipped". */
  const sceneStatus = window.IrisSceneStatus || { active: false, reason: "no-scene",
    detail: "The 3D scene files did not load — this copy of IRIS may be out of date." };
  if (!sceneStatus.active) {
    console.warn("[iris] scene inactive:", sceneStatus.reason, sceneStatus.detail);
    window.addEventListener("load", () => {
      const div = addMessage("iris", `**Showing the simple hologram.** ${sceneStatus.detail}`);
      div.classList.add("error");
    });
  }

  let currentState = "idle";
  function setState(state, label) {
    currentState = state;
    holo.setState(state);
    els.stateLabel.textContent = label || state;
    els.stateLabel.classList.toggle("active", state !== "idle");
  }
  setState("idle", "ready");

  // ───────────────────────────── Transport ─────────────────────────────
  let ws = null;
  let wsReady = false;
  let reconnectDelay = 800;
  let pendingTask = null;
  const conversationId = "conv_" + Math.random().toString(36).slice(2, 10);

  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const t = token() ? `?token=${encodeURIComponent(token())}` : "";
    return `${proto}://${location.host}/api/v1/ws${t}`;
  }

  function connect() {
    ws = new WebSocket(wsUrl());
    ws.onopen = () => {
      wsReady = true;
      reconnectDelay = 800;
      els.connDot.classList.add("online");
    };
    ws.onclose = () => {
      wsReady = false;
      els.connDot.classList.remove("online");
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 1.6, 15000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (msg) => {
      let data;
      try { data = JSON.parse(msg.data); } catch { return; }
      if (data.type === "event") handleBusEvent(data);
      else if (data.type === "response") handleResponse(data);
      else if (data.type === "error") { removeTyping(); addMessage("iris", data.detail || "Error", { error: true }); }
    };
  }
  connect();

  // ─────────────────────────── Bus events ───────────────────────────
  function tick(text, cls) {
    els.ticker.innerHTML = `<span class="tk ${cls || ""}">${escapeHtml(text)}</span>`;
  }

  function handleBusEvent(ev) {
    const p = ev.payload || {};
    // Hand every event to the scene as well. It drives the sub-agent
    // constellation from real tool activity and fills in the transitions this
    // switch never covered — agent.completed and agent.failed, which is why the
    // orb used to get stuck "thinking" after a voice or Telegram turn.
    if (holo.handleBusEvent) holo.handleBusEvent(ev.topic, p);
    switch (ev.topic) {
      case "agent.started": setState("thinking", "thinking"); break;
      case "agent.thinking": setState("thinking", "thinking"); break;
      case "tool.started": tick(`${p.tool}`, "run"); setState("thinking", p.tool); break;
      case "tool.completed": tick(`${p.tool}`, "ok"); break;
      case "tool.failed": tick(`${p.tool} failed`, "fail"); break;
      // A turn that came in over voice or Telegram never produced a WS
      // "response" frame, so nothing ever moved the orb back out of thinking.
      case "agent.completed": setState("idle", "ready"); break;
      case "agent.failed": setState("error", "failed"); break;
      case "voice.speaking":
        if (shouldBrowserSpeak(p.engine)) speakBrowser(p.text, p.language);
        break;
      case "reminder.due":
      case "routine.fired": {
        const kind = p.kind === "timer" ? "⏰ Timer" : "🔔 Reminder";
        addMessage("iris", `${kind}: ${p.text}`);
        if (els.speakToggle.checked) speakBrowser(`${p.kind === "timer" ? "Timer done" : "Reminder"}: ${p.text}`);
        notifyBrowser(kind, p.text);
        break;
      }
      case "llm.route": els.chipProvider.textContent = p.provider || "local"; break;
      case "llm.fallback":
        tick("cloud AI failed — open settings for details", "fail");
        break;
      case "ui.state":
        if (p.action === "push_to_talk") (listening ? stopListening() : startListening());
        break;
      case "ui.open_url": openInThisBrowser(p.url, p.label); break;
    }
  }

  // ───────────────────────────── Chat flow ─────────────────────────────
  let typingEl = null;
  function showTyping() {
    removeTyping();
    typingEl = document.createElement("div");
    typingEl.className = "typing";
    typingEl.innerHTML = "<span></span><span></span><span></span>";
    els.messages.appendChild(typingEl);
    scrollDown();
  }
  function removeTyping() { if (typingEl) { typingEl.remove(); typingEl = null; } }

  function addMessage(who, text, opts = {}) {
    if (els.welcome) els.welcome.style.display = "none";
    const div = document.createElement("div");
    div.className = `msg ${who}${opts.error ? " error" : ""}`;
    div.innerHTML = renderMarkdown(text);

    if (opts.artifacts && opts.artifacts.length) {
      for (const a of opts.artifacts) {
        const link = document.createElement("a");
        link.className = "artifact-link";
        link.href = `/api/v1/system/artifact?path=${encodeURIComponent(a)}` + (token() ? `&token=${encodeURIComponent(token())}` : "");
        link.textContent = `📄 ${a.split(/[\\/]/).pop()}`;
        link.target = "_blank";
        div.appendChild(link);
      }
    }
    if (opts.meta) {
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.innerHTML = opts.meta.map((m) => `<span class="tag">${escapeHtml(m)}</span>`).join("");
      div.appendChild(meta);
    }
    els.messages.appendChild(div);
    scrollDown();
    return div;
  }

  function scrollDown() { els.conversation.scrollTop = els.conversation.scrollHeight; }

  /* IRIS on a server has no desktop, so "open youtube" arrives here instead:
     this tab IS the browser. window.open without a user gesture is blocked by
     most browsers, and a blocked popup returns null — so the link is always
     offered in the conversation as well, and that one always works. */
  function openInThisBrowser(url, label) {
    if (!url || !/^https?:\/\//i.test(url)) return;
    let opened = null;
    try { opened = window.open(url, "_blank", "noopener,noreferrer"); } catch (e) { /* blocked */ }
    const name = label || url;
    /* addMessage renders through renderMarkdown, which escapes — escaping here
       as well would turn an "&" in a search term into "&amp;". */
    const div = addMessage("iris", opened
      ? `Opened **${name}** in a new tab.`
      : `Tap to open **${name}** — browsers only allow new tabs you click yourself.`);
    const link = document.createElement("a");
    link.className = "artifact-link";
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = `↗ ${name}`;
    div.appendChild(link);
  }

  async function send(text) {
    text = (text || "").trim();
    if (!text) return;
    addMessage("user", text);
    els.input.value = "";
    autosize();
    setState("thinking", "thinking");
    showTyping();

    if (wsReady) {
      ws.send(JSON.stringify({ type: "chat", message: text, conversation_id: conversationId, channel: "web" }));
    } else {
      try {
        const res = await fetch("/api/v1/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({ message: text, conversation_id: conversationId, channel: "web" }),
        });
        handleResponse(await res.json());
      } catch {
        removeTyping();
        setState("idle", "offline");
        addMessage("iris", "I can't reach the IRIS server. Is it running?", { error: true });
      }
    }
  }

  function handleResponse(r) {
    removeTyping();
    tick("");

    if (r.status === "WAITING_FOR_CONFIRMATION" && r.pending_action) {
      pendingTask = { taskId: r.task_id, action: r.pending_action };
      els.confirmText.textContent = `IRIS wants to run “${r.pending_action.tool_name}”.`;
      els.confirmArgs.textContent = JSON.stringify(r.pending_action.arguments || {}, null, 2);
      els.confirmModal.classList.remove("hidden");
      setState("idle", "awaiting approval");
      return;
    }

    const failed = r.status === "FAILED";
    const meta = [];
    if (r.handler) meta.push(r.handler);
    if (r.provider && r.provider !== "iris") meta.push(r.provider);
    const msgEl = addMessage("iris", r.response || "…", { error: failed, artifacts: r.artifacts, meta });
    if (r.notice) {
      const note = document.createElement("div");
      note.className = "msg-notice";
      note.textContent = r.notice;
      msgEl.appendChild(note);
    }

    if (r.provider) els.chipProvider.textContent = r.provider;
    if (els.speakToggle.checked) {
      const sentence = r.speech || (r.response && r.response.length < 300 ? r.response : null);
      if (sentence) speakBrowser(sentence, r.response_language);
    }
    setState("idle", "ready");
  }

  // Confirmation modal
  function resolveConfirm(approved) {
    els.confirmModal.classList.add("hidden");
    if (!pendingTask) return;
    const { taskId } = pendingTask;
    pendingTask = null;
    showTyping();
    setState("thinking", approved ? "running" : "cancelling");
    if (wsReady) {
      ws.send(JSON.stringify({ type: "confirm", task_id: taskId, approved }));
    } else {
      fetch("/api/v1/chat/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ task_id: taskId, approved }),
      }).then((r) => r.json()).then(handleResponse).catch(removeTyping);
    }
  }
  els.btnApprove.onclick = () => resolveConfirm(true);
  els.btnReject.onclick = () => resolveConfirm(false);

  // ─────────────────────────── Browser voice ───────────────────────────
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognizer = null;
  let listening = false;      // push-to-talk session
  let wakeMode = false;       // continuous wake-word listening
  let speaking = false;
  let voiceStatus = { wake_words: ["iris", "hey iris", "ok iris"], browser_voice: true };

  function shouldBrowserSpeak(engine) {
    return els.speakToggle.checked && (engine === "browser" || !engine);
  }

  function speakBrowser(text, lang) {
    if (!("speechSynthesis" in window) || !text) return;
    try {
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      utter.rate = 1.02;
      utter.pitch = 1.0;
      const voices = window.speechSynthesis.getVoices();
      const female = /female|aria|zira|jenny|hazel|samantha|swara|heera|kalpana|lekha|veena/i;
      const wantHindi = /^hi/.test(lang || "") || lang === "hinglish";
      let preferred = null;
      if (wantHindi) {
        utter.lang = "hi-IN";
        preferred = voices.find((v) => v.lang.startsWith("hi") && female.test(v.name))
          || voices.find((v) => v.lang.startsWith("hi"));
      }
      preferred = preferred
        || voices.find((v) => female.test(v.name))
        || voices.find((v) => v.lang.startsWith("en"));
      if (preferred) utter.voice = preferred;
      speaking = true;
      setState("speaking", "speaking");
      holo.setLevel(0.6);
      utter.onend = utter.onerror = () => {
        speaking = false;
        holo.setLevel(0);
        if (currentState === "speaking") setState(listening || wakeMode ? "listening" : "idle", listening || wakeMode ? "listening" : "ready");
      };
      window.speechSynthesis.speak(utter);
    } catch { /* voice output unavailable */ }
  }

  function startRecognition({ continuous }) {
    if (!SR) {
      addMessage("iris", "This browser doesn't support speech recognition — try Chrome or Edge, or type instead.", { error: true });
      return null;
    }
    const rec = new SR();
    rec.continuous = continuous;
    rec.interimResults = true;
    rec.lang = "en-US";
    rec.onresult = (event) => {
      let interim = "", finalText = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const alt = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += alt;
        else interim += alt;
      }
      if (interim) { els.transcript.textContent = interim; holo.setLevel(Math.min(1, interim.length / 40)); }
      if (finalText) {
        els.transcript.textContent = "";
        holo.setLevel(0);
        onSpeechFinal(finalText.trim());
      }
    };
    rec.onend = () => {
      if (wakeMode && recognizer === rec) {
        try { rec.start(); } catch { /* restart throttled */ }
      } else if (recognizer === rec) {
        listening = false;
        els.btnMic.classList.remove("listening");
        if (!speaking) setState("idle", "ready");
      }
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed") {
        wakeMode = false; listening = false;
        els.wakeToggle.checked = false;
        els.btnMic.classList.remove("listening");
        setState("idle", "mic blocked");
        addMessage("iris", "Microphone access is blocked. Allow it in your browser's site settings.", { error: true });
      }
    };
    try { rec.start(); } catch { return null; }
    return rec;
  }

  function onSpeechFinal(text) {
    if (!text) return;
    if (wakeMode && !listening) {
      // Continuous mode: only act when a wake word prefixes the utterance.
      const lower = text.toLowerCase();
      const wake = (voiceStatus.wake_words || ["iris"]).find((w) => lower.includes(w));
      if (!wake) return;
      const cmd = lower.slice(lower.indexOf(wake) + wake.length).replace(/^[,.!?\s]+/, "");
      if (cmd) send(cmd);
      else { setState("listening", "yes?"); speakBrowser("Yes?"); }
      return;
    }
    // Push-to-talk: send whatever was said.
    stopListening();
    send(text);
  }

  function startListening() {
    if (listening) return;
    listening = true;
    els.btnMic.classList.add("listening");
    setState("listening", "listening");
    recognizer = startRecognition({ continuous: false });
    if (!recognizer) { listening = false; els.btnMic.classList.remove("listening"); }
  }

  function stopListening() {
    listening = false;
    els.btnMic.classList.remove("listening");
    if (recognizer && !wakeMode) { try { recognizer.stop(); } catch { } recognizer = null; }
    if (!speaking) setState("idle", "ready");
  }

  els.btnMic.onclick = () => (listening ? stopListening() : startListening());

  els.wakeToggle.onchange = () => {
    wakeMode = els.wakeToggle.checked;
    try { localStorage.setItem("iris_wake", wakeMode ? "1" : "0"); } catch { }
    if (wakeMode) {
      setState("listening", "say “hey iris…”");
      recognizer = startRecognition({ continuous: true });
      if (!recognizer) { wakeMode = false; els.wakeToggle.checked = false; }
    } else {
      if (recognizer) { try { recognizer.stop(); } catch { } recognizer = null; }
      setState("idle", "ready");
    }
  };

  els.speakToggle.onchange = () => {
    try { localStorage.setItem("iris_speak", els.speakToggle.checked ? "1" : "0"); } catch { }
    if (!els.speakToggle.checked) window.speechSynthesis && window.speechSynthesis.cancel();
  };

  // Restore voice preferences.
  try {
    if (localStorage.getItem("iris_speak") === "0") els.speakToggle.checked = false;
    if (localStorage.getItem("iris_wake") === "1") { els.wakeToggle.checked = true; els.wakeToggle.onchange(); }
  } catch { }

  function notifyBrowser(title, body) {
    if (!("Notification" in window)) return;
    if (Notification.permission === "granted") new Notification(title, { body });
    else if (Notification.permission !== "denied") Notification.requestPermission();
  }

  // ───────────────────────────── Input UX ─────────────────────────────
  function autosize() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 120) + "px";
  }
  els.input.addEventListener("input", autosize);
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(els.input.value); }
  });
  els.btnSend.onclick = () => send(els.input.value);
  document.querySelectorAll(".suggest").forEach((b) => (b.onclick = () => send(b.dataset.q)));

  // Keyboard shortcut: Ctrl/Cmd+K focuses input; Space toggles mic when input empty & unfocused.
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); els.input.focus(); }
  });

  // ─────────────────────────── Settings drawer ───────────────────────────
  els.btnSettings.onclick = () => { els.drawer.classList.remove("hidden"); loadDrawer(); };
  els.btnCloseDrawer.onclick = () => els.drawer.classList.add("hidden");

  async function jfetch(url) {
    const res = await fetch(url, { headers: authHeaders() });
    if (!res.ok) throw new Error(`${res.status}`);
    return res.json();
  }

  async function loadDrawer() {
    try {
      const llm = await jfetch("/api/v1/llm/status");
      const rows = (llm.providers || []).map((p) => {
        const state = p.active ? '<span class="ok">active</span>'
          : p.configured ? (p.circuit_open ? '<span class="bad">cooling down</span>' : "ready")
          : '<span class="muted">no key</span>';
        const pool = p.keys > 1 ? ` <span class="muted">(${p.keys} keys)</span>` : "";
        const err = p.configured && p.last_error
          ? `<div class="prov-err" title="${escapeHtml(p.last_error)}">${escapeHtml(String(p.last_error).slice(0, 160))}</div>`
          : "";
        return `<div>${p.name} — ${state}${pool}${err}</div>`;
      }).join("");
      els.llmStatus.innerHTML =
        `<div>mode: <b>${llm.mode}</b> · active: <b>${llm.provider}</b></div>${rows}` +
        `<div class="muted" style="margin-top:6px">Add free API keys in your .env (OpenRouter, Groq, Gemini…) to unlock full conversations. Commands work fully offline.</div>`;
    } catch { els.llmStatus.textContent = "unavailable"; }

    try {
      const v = await jfetch("/api/v1/voice/status");
      els.voiceStatus.innerHTML =
        `<div>speech-to-text: <b>${v.stt_engine}</b></div>` +
        `<div>text-to-speech: <b>${v.tts_engine}</b></div>` +
        `<div>voice: <b>${escapeHtml(v.tts_voice || "auto")}</b></div>` +
        `<div>languages: <b>${escapeHtml((v.languages || []).join(", "))}</b></div>` +
        `<div>wake words: <b>${(v.wake_words || []).join(", ")}</b></div>`;
      els.chipVoice.textContent = v.tts_engine === "browser" ? "browser voice" : v.tts_engine;
      voiceStatus = v;
    } catch { els.voiceStatus.textContent = "unavailable"; }

    try {
      const tools = await jfetch("/api/v1/tools");
      els.toolCount.textContent = `(${tools.filter((t) => t.available).length}/${tools.length})`;
      els.toolGrid.innerHTML = tools
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((t) => `<span class="tool-pill ${t.available ? "" : "off"}" title="${escapeHtml(t.unavailable_reason || t.description)}">${t.name}</span>`)
        .join("");
    } catch { els.toolGrid.textContent = "unavailable"; }

    try {
      const d = await jfetch("/api/v1/devices");
      const list = document.getElementById("deviceList");
      const count = document.getElementById("deviceCount");
      count.textContent = d.count ? `(${d.devices.filter((x) => x.online).length}/${d.count} online)` : "";
      if (!d.count) {
        list.textContent = "none registered — say “add device light at 192.168.1.50”";
      } else {
        list.innerHTML = "";
        for (const dev of d.devices) {
          const row = document.createElement("div");
          row.className = "device-row";
          const dot = dev.online ? '<span class="ok">●</span>' : '<span class="bad">●</span>';
          row.innerHTML = `${dot} <b>${escapeHtml(dev.name)}</b> <span class="muted">${escapeHtml(dev.kind)}</span>`;
          if (dev.kind !== "motor") {
            const btn = document.createElement("button");
            btn.className = "btn ghost small dev-toggle";
            btn.textContent = "toggle";
            btn.onclick = async () => {
              btn.disabled = true;
              try {
                await fetch(`/api/v1/devices/${encodeURIComponent(dev.name)}/switch`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json", ...authHeaders() },
                  body: JSON.stringify({ state: "toggle" }),
                });
                tick(`${dev.name} toggled`, "ok");
              } catch { tick(`${dev.name} unreachable`, "fail"); }
              btn.disabled = false;
            };
            row.appendChild(btn);
          }
          list.appendChild(row);
        }
      }
    } catch { /* devices panel is optional */ }

    try {
      const r = await jfetch("/api/v1/system/reminders");
      els.reminderList.innerHTML = r.count
        ? r.reminders.map((x) => `<div>• ${escapeHtml(x.text)} <span class="muted">${new Date(x.due_at).toLocaleString()}</span></div>`).join("")
        : "none scheduled";
    } catch { els.reminderList.textContent = "unavailable"; }
  }

  els.btnPair.onclick = async () => {
    try {
      const p = await jfetch("/api/v1/system/pair");
      els.pairDetails.innerHTML =
        `<div><b>${escapeHtml(p.url)}</b></div><div class="muted">${escapeHtml(p.note)}</div>` +
        `<img src="/api/v1/system/pair/qr${token() ? "?token=" + encodeURIComponent(token()) : ""}" onerror="this.remove()" alt="QR">`;
    } catch { els.pairDetails.textContent = "unavailable"; }
  };

  // ─────────────────────── Status chips at boot ───────────────────────
  (async () => {
    try {
      const llm = await jfetch("/api/v1/llm/status");
      els.chipProvider.textContent = llm.provider || "local";
    } catch { }
    try {
      const v = await jfetch("/api/v1/voice/status");
      voiceStatus = v;
      els.chipVoice.textContent = v.tts_engine === "browser" ? "browser voice" : v.tts_engine;
    } catch { }
  })();

  // ───────────────────────────── Helpers ─────────────────────────────
  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function renderMarkdown(text) {
    // Minimal, safe markdown: escape first, then re-introduce a few structures.
    let safe = escapeHtml(text);
    safe = safe.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${code.trim()}</code></pre>`);
    safe = safe.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    safe = safe.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
    safe = safe.replace(/(^|\n)[-•] (.+)/g, "$1• $2");
    return safe;
  }
})();
