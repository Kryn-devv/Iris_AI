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
    setupModal: $("setupModal"), setupTitle: $("setupTitle"), setupIntro: $("setupIntro"),
    setupProvider: $("setupProvider"), setupNote: $("setupNote"), setupKey: $("setupKey"),
    setupName: $("setupName"), setupMsg: $("setupMsg"), setupFoot: $("setupFoot"),
    btnSetupSave: $("btnSetupSave"), btnSetupSkip: $("btnSetupSkip"), btnSetupOpen: $("btnSetupOpen"),
  };

  const urlToken = new URLSearchParams(location.search).get("token");
  if (urlToken) localStorage.setItem("iris_token", urlToken);
  const token = () => { try { return localStorage.getItem("iris_token") || ""; } catch { return ""; } };
  const authHeaders = () => token() ? { "X-Iris-Token": token() } : {};

  // ───────────────────────────── Themes ─────────────────────────────
  /* A theme is the accent, one secondary, and the void it sits in. Everything
     else in the stylesheet is derived from those three, so a theme is six
     values rather than a second stylesheet — and the same accent is handed to
     the 3D scene so the orb, nebula and rings move with the chrome instead of
     staying teal in a violet room.

     Amber (listening) and red (error) are deliberately NOT themed: they carry
     meaning rather than identity, and "I am recording you" has to look the same
     in every theme. */
  const THEMES = [
    { id: "teal",    name: "Teal",    accent: "#5eead4", accent2: "#818cf8", bg: "#020308", bgSoft: "#060a14" },
    { id: "violet",  name: "Violet",  accent: "#a78bfa", accent2: "#22d3ee", bg: "#06040f", bgSoft: "#0c0918" },
    { id: "ice",     name: "Ice",     accent: "#7dd3fc", accent2: "#c4b5fd", bg: "#020610", bgSoft: "#060d1c" },
    { id: "ember",   name: "Ember",   accent: "#fb923c", accent2: "#f472b6", bg: "#0a0503", bgSoft: "#150b06" },
    { id: "lime",    name: "Lime",    accent: "#a3e635", accent2: "#34d399", bg: "#040803", bgSoft: "#0a1006" },
    { id: "rose",    name: "Rose",    accent: "#fb7185", accent2: "#c084fc", bg: "#0a0409", bgSoft: "#160a14" },
    { id: "mono",    name: "Mono",    accent: "#cbd5e1", accent2: "#94a3b8", bg: "#050608", bgSoft: "#0b0d12" },
  ];
  const DEFAULT_THEME = "teal";

  /* rgba() strings from a hex, because several of the tokens are the accent at
     a low alpha and a browser cannot do that from a variable alone. */
  const rgba = (hex, a) => {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  };

  function themeById(id) {
    return THEMES.find((t) => t.id === id) || THEMES[0];
  }

  function savedThemeId() {
    try { return localStorage.getItem("iris_theme") || DEFAULT_THEME; }
    catch { return DEFAULT_THEME; }
  }

  function applyTheme(id, { persist = true } = {}) {
    const t = themeById(id);
    const r = document.documentElement.style;
    r.setProperty("--accent", t.accent);
    r.setProperty("--accent-2", t.accent2);
    r.setProperty("--accent-dim", rgba(t.accent, 0.55));
    r.setProperty("--accent-glow", rgba(t.accent, 0.14));
    r.setProperty("--line", rgba(t.accent, 0.10));
    r.setProperty("--line-strong", rgba(t.accent, 0.28));
    r.setProperty("--bg", t.bg);
    r.setProperty("--bg-soft", t.bgSoft);
    document.documentElement.dataset.theme = t.id;
    /* The scene may not exist yet on first call, and may be the flat fallback
       hologram, which has no themes. Either way the chrome still changes. */
    if (typeof holo !== "undefined" && holo && holo.setTheme) {
      holo.setTheme({ accent: t.accent, accent2: t.accent2 });
    }
    if (persist) { try { localStorage.setItem("iris_theme", t.id); } catch {} }
    renderThemeGrid();
  }

  function renderThemeGrid() {
    const grid = $("themeGrid");
    if (!grid) return;
    const active = document.documentElement.dataset.theme || DEFAULT_THEME;
    grid.innerHTML = "";
    for (const t of THEMES) {
      const wrap = document.createElement("div");
      const b = document.createElement("button");
      b.className = "theme-swatch";
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(t.id === active));
      b.setAttribute("aria-label", t.name);
      b.title = t.name;
      b.style.background = `radial-gradient(circle at 50% 45%, ${t.bgSoft}, ${t.bg})`;
      const ring = document.createElement("span");
      ring.className = "sw-ring";
      ring.style.color = t.accent2;
      const orb = document.createElement("span");
      orb.className = "sw-orb";
      orb.style.color = t.accent;
      b.append(ring, orb);
      b.onclick = () => applyTheme(t.id);
      const label = document.createElement("div");
      label.className = "theme-name";
      label.textContent = t.name;
      wrap.append(b, label);
      grid.appendChild(wrap);
    }
  }

  // ───────────────────────────── Hologram ─────────────────────────────
  const activeTheme = themeById(savedThemeId());
  const holo = new window.IrisHologram(els.holo, {
    accent: activeTheme.accent,
    quality: matchMedia("(max-width: 640px)").matches ? "medium" : "high",
    reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
  });
  /* Apply the saved theme once the scene exists, so the very first frame is
     already the right colour instead of flashing teal. */
  applyTheme(activeTheme.id, { persist: false });

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
  /* Kept across reloads on purpose. The thread lives server-side keyed by this
     id, so minting a fresh one every page load meant she could never know you
     had been away — the "back after three hours" greeting had no thread to
     measure the gap against, and neither did the rolling summary. Closing the
     tab was amnesia. */
  const conversationId = (() => {
    try {
      const saved = localStorage.getItem("iris_conv");
      if (saved) return saved;
    } catch { /* private mode */ }
    const fresh = "conv_" + Math.random().toString(36).slice(2, 10);
    try { localStorage.setItem("iris_conv", fresh); } catch { }
    return fresh;
  })();

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
      case "agent.completed":
        setState("idle", "ready");
        // Text-only (speech off) still hands you the floor to reply.
        if (!els.speakToggle.checked) openFollowUpWindow();
        break;
      case "agent.failed": setState("error", "failed"); break;
      case "voice.speaking":
        if (shouldBrowserSpeak(p.engine)) speakBrowser(p.text, p.language, { filler: !!p.filler });
        else if (LOCAL_SERVER_ENGINES.has(p.engine)) beginServerSpeech(p);
        /* Anything else — "node" above all — is audio playing somewhere that
           is not this machine: the robot's own speaker, in another room. It is
           not ours to wait for, and treating it as local used to block this
           page from speaking at all. */
        break;
      case "voice.spoken":
        endServerSpeech(p.id);
        break;
      case "reminder.due":
      case "routine.fired": {
        const kind = p.kind === "timer" ? "⏰ Timer" : "🔔 Reminder";
        addMessage("iris", `${kind}: ${p.text}`);
        /* Not spoken here. The scheduler hands the same sentence to the voice
           service, which publishes "voice.speaking" — so saying it again from
           this branch was a reminder read out twice, in two different voices
           when a server engine was installed. */
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
    /* The server now sends a spoken line for every reply — her own opening
       sentences, minus the code blocks — so the old "only if it is under 300
       characters" rule is gone. That rule is why every answer worth hearing
       used to arrive as silence.

       `speakingInBrowser` is the other half: when audio is playing on the
       machine's own speakers we cannot stop it from here, so talking over it
       is how two voices ended up going at once. Let it finish. */
    /* Only what the server marked as sayable. It sends a spoken lead with
       every reply now, so an ABSENT one is a decision, not an omission: the
       answer was nothing but a code block, and reading a code block out loud
       is the thing we were trying to stop. */
    if (els.speakToggle.checked && r.speech) {
      if (current && current.source === "server") {
        /* The speakers are busy with something we cannot stop — a reminder, the
           camera. Wait for it rather than talking over it, and rather than
           swallowing the answer, which is what dropping it here amounted to. */
        deferredReply = { text: r.speech, lang: r.response_language };
      } else {
        speakBrowser(r.speech, r.response_language);
      }
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

  /* ── Conversation mode ──────────────────────────────────────────────────
   * Wake-word mode threw away anything not prefixed with "Iris", so you could
   * never simply *reply* to her — every sentence meant summoning her again.
   * That is what made talking to her feel like using a vending machine.
   *
   * Now, the moment she finishes speaking, a window opens: for FOLLOW_UP_MS
   * anything you say is the next turn, no wake word. Each exchange extends it,
   * so a real back-and-forth flows; go quiet and she drops back to wake-word
   * only, which is what keeps the room's conversation out of her input.
   *
   * Two guards stop her from talking to herself:
   *   - nothing heard WHILE she is speaking counts as a command (that is her
   *     own voice arriving through the microphone), except to interrupt her;
   *   - a transcript that mostly matches what she just said is dropped — the
   *     recognizer often delivers her last sentence a beat after she stops. */
  const FOLLOW_UP_MS = 9000;
  /* How long after she stops that a matching transcript is still her echo.
     Without a lifetime, lastSpokenText never expires and *any* later attempt
     to say what she once said is silently dropped — ask her to "open youtube",
     and every future "open youtube" disappears. */
  const ECHO_WINDOW_MS = 2500;
  let followUpUntil = 0;
  let lastSpokenText = "";
  let lastSpeechEndedAt = 0;
  let followUpSafety = null;
  /* Browser speech can be cancelled; server-side speech cannot. Pretending we
     stopped audio we cannot stop is what turns her own voice into a command. */
  let speakingInBrowser = false;

  function estimateSpeechMs(text) {
    const words = (text || "").trim().split(/\s+/).filter(Boolean).length;
    return Math.min(60000, Math.max(700, Math.round((words / 2.6) * 1000)));  // ~155 wpm
  }
  function openFollowUpWindow() {
    followUpUntil = Date.now() + FOLLOW_UP_MS;
    if (wakeMode && !speaking) setState("listening", "your turn");
  }
  function inFollowUpWindow() { return Date.now() < followUpUntil; }

  /* Browsers stop firing "end" on long utterances — Chrome gives up silently
     somewhere past a dozen seconds — so a substantial answer would leave the
     floor with her forever and conversation mode would quietly end after every
     real reply. This is the backstop: hand the floor back once she has had
     time to finish, whatever the engine did or did not tell us. */
  function armFollowUpSafety(text, id) {
    clearTimeout(followUpSafety);
    followUpSafety = setTimeout(() => {
      // Only if this utterance still holds the mouth: a backstop that fires
      // for a reply two turns ago hands the floor away mid-sentence.
      endSpeech(id, { wasFiller: false });
    }, Math.min(90000, estimateSpeechMs(text) + 1500));
  }

  /* Is this transcript just her own voice coming back? Exact containment
     catches the clean case; the overlap ratio catches the recognizer mangling
     a word or two of it, which it usually does. */
  function looksLikeOwnVoice(text) {
    if (!lastSpokenText) return false;
    // Stale: she said this long enough ago that saying it back is a request.
    if (!speaking && Date.now() - lastSpeechEndedAt > ECHO_WINDOW_MS) return false;
    const norm = (t) => t.toLowerCase().replace(/[^a-z0-9\u0900-\u097f ]+/g, " ").replace(/\s+/g, " ").trim();
    const heard = norm(text), said = norm(lastSpokenText);
    if (!heard) return true;
    if (said.includes(heard)) return true;
    const saidWords = new Set(said.split(" "));
    const heardWords = heard.split(" ");
    const hits = heardWords.filter((w) => saidWords.has(w)).length;
    // Two words is enough to be an echo of "one sec" or "all set".
    return heardWords.length >= 2 && hits / heardWords.length > 0.6;
  }

  /* Is this her own voice arriving through the microphone? While she is
     speaking, always. Just after, only if it matches what she said. Push-to-
     talk is deliberate — the user held the button — so only the first test
     applies there. */
  function isEcho(text, deliberate) {
    if (speaking) return true;
    if (deliberate) return false;
    if (Date.now() - lastSpeechEndedAt < 700) return true;
    return looksLikeOwnVoice(text);
  }

  function shouldBrowserSpeak(engine) {
    return els.speakToggle.checked && (engine === "browser" || !engine);
  }

  /* Engines whose audio comes out of THIS machine's speakers. "node" is
     deliberately absent: that plays on the robot. */
  const LOCAL_SERVER_ENGINES = new Set(["piper", "pyttsx3", "espeak", "edge", "gtts"]);

  /* ── Who is talking ──────────────────────────────────────────────────────
   * There is one mouth, and exactly one utterance owns it. `current` is that
   * utterance; every callback checks that it still owns the mouth before
   * touching shared state.
   *
   * Two booleans and a shared onend handler could not express that. A filler's
   * "one sec" and the answer queued behind it are two utterances in flight at
   * once, and the filler's callback — firing while the answer plays — cleared
   * the answer's safety timer, declared speech over, and killed barge-in for
   * the rest of the reply. Ownership is the fix: a callback whose id is stale
   * cleans up after itself and leaves everything else alone. */
  let utterSeq = 0;
  let current = null;            // {id, source:"browser"|"server", filler, timer}
  let deferredReply = null;      // a reply that arrived while the speakers were busy

  function owns(id) { return current !== null && current.id === id; }

  function beginSpeech(record) {
    if (current && current.timer) clearTimeout(current.timer);
    current = record;
    speaking = true;
    speakingInBrowser = record.source === "browser";
    setState("speaking", "speaking");
  }

  function endSpeech(id, { wasFiller } = {}) {
    if (!owns(id)) return false;   // something newer owns the mouth now
    if (current.timer) clearTimeout(current.timer);
    current = null;
    speaking = false;
    speakingInBrowser = false;
    lastSpeechEndedAt = Date.now();
    holo.setLevel(0);
    if (!wasFiller) openFollowUpWindow();
    if (currentState === "speaking") {
      setState(listening || wakeMode ? "listening" : "idle",
               listening || wakeMode ? "listening" : "ready");
    }
    flushDeferredReply();
    return true;
  }

  function beginServerSpeech(p) {
    /* Audio on this machine's speakers that we cannot stop. What we CAN stop
       is our own: letting both run is the two-voices bug, and the browser's is
       the one with a cancel button. */
    if (speakingInBrowser) { try { window.speechSynthesis.cancel(); } catch { } }
    clearTimeout(followUpSafety);
    lastSpokenText = p.text || "";
    const id = p.id || ("srv" + (++utterSeq));
    const wasFiller = !!p.filler;
    beginSpeech({
      id, source: "server", filler: wasFiller,
      /* A backstop only. The real end arrives as voice.spoken, because this
         estimate starts before synthesis and a networked engine can spend
         seconds there — the window used to expire mid-sentence. */
      timer: setTimeout(() => endSpeech(id, { wasFiller }), estimateSpeechMs(p.text) + 4000),
    });
  }

  function endServerSpeech(id) {
    if (!id || !current || current.source !== "server") return;
    endSpeech(current.id, { wasFiller: current.filler });
  }

  function flushDeferredReply() {
    if (!deferredReply || current) return;
    const pending = deferredReply;
    deferredReply = null;
    speakBrowser(pending.text, pending.lang);
  }


  function speakBrowser(text, lang, opts) {
    if (!("speechSynthesis" in window) || !text) return;
    const isFiller = !!(opts && opts.filler);
    try {
      /* A filler is not cancelled by the answer that follows it — half of
         "one se—" sounds like a fault — so the answer queues behind. Anything
         else replaces what came before. */
      const queueBehindFiller = current && current.source === "browser" && current.filler && !isFiller;
      if (!queueBehindFiller) window.speechSynthesis.cancel();

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

      const id = "b" + (++utterSeq);
      beginSpeech({ id, source: "browser", filler: isFiller, timer: null });
      // Kept for the echo guard either way: hearing her own "one sec" come
      // back through the microphone must not read as a command.
      lastSpokenText = text;
      if (!isFiller) armFollowUpSafety(text, id);
      holo.setLevel(0.6);

      utter.onend = utter.onerror = () => {
        if (!owns(id)) {
          /* A newer utterance already owns the mouth — this is a filler
             finishing while the answer it queued ahead of plays on. Clearing
             `speaking` here is what used to kill barge-in for the whole reply
             and let her own voice back in as a command. */
          return;
        }
        clearTimeout(followUpSafety);        // the engine told us; no backstop needed
        endSpeech(id, { wasFiller: isFiller });
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
      if (interim) {
        els.transcript.textContent = interim;
        holo.setLevel(Math.min(1, interim.length / 40));
        // Barge-in. Start talking while she is mid-sentence and she stops, the
        // way a person does. Two words minimum so a cough does not cut her off,
        // and never on her own voice echoing back.
        // Only when the browser is the one talking: server-side audio (edge,
        // piper) cannot be stopped from here, and clearing `speaking` while it
        // plays on would switch the echo guard off mid-sentence and let her
        // own voice back in as a command.
        if (speaking && speakingInBrowser && (wakeMode || listening)
            && interim.trim().split(/\s+/).length >= 3 && !looksLikeOwnVoice(interim)) {
          try { window.speechSynthesis.cancel(); } catch { }
          clearTimeout(followUpSafety);
          deferredReply = null;     // they talked over it; do not play it later
          if (current) endSpeech(current.id, { wasFiller: current.filler });
        }
      }
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
        addMessage("iris", micBlockedReason(), { error: true });
      }
    };
    try { rec.start(); } catch { return null; }
    return rec;
  }

  /* Why the microphone was refused — the two causes need different fixes, and
     naming the wrong one wastes the user's time.

     Browsers only expose a microphone on a SECURE origin: https, or localhost.
     Reached over plain http from another machine, the permission is not merely
     denied, it is never offered — so "allow it in site settings" points at a
     switch that is not there. Anything served over https, or opened locally on
     the machine IRIS runs on, is a genuine permission problem. */
  function micBlockedReason() {
    if (!window.isSecureContext) {
      return "**The microphone needs a secure connection.** This page is on " +
        "`http://`, and browsers only allow microphone access over `https://` " +
        "(or on localhost) — so there is no permission to grant in site " +
        "settings. Put TLS in front of IRIS, or use the robot's own microphone. " +
        "Typing works either way.";
    }
    return "Microphone access is blocked. Allow it in your browser's site settings.";
  }

  function onSpeechFinal(text) {
    if (!text) return;
    // Her own voice through the speakers is never a command.
    if (isEcho(text, listening)) return;
    if (wakeMode && !listening) {
      const lower = text.toLowerCase();
      const wake = (voiceStatus.wake_words || ["iris"]).find((w) => lower.includes(w));
      if (wake) {
        // Slice the original text, not the lower-cased copy: "iris open
        // YouTube" used to reach the tools as "open youtube".
        const cmd = text.slice(lower.indexOf(wake) + wake.length).replace(/^[,.!?\s]+/, "");
        if (cmd) send(cmd);
        else { openFollowUpWindow(); setState("listening", "yes?"); speakBrowser("Yes?"); }
        return;
      }
      // No wake word — but we are mid-conversation, so this is simply her turn
      // to listen. That is the whole difference between talking to someone and
      // filing requests with them.
      if (inFollowUpWindow()) { send(text); return; }
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
    if (!els.speakToggle.checked) {
      deferredReply = null;
      window.speechSynthesis && window.speechSynthesis.cancel();
      if (current) endSpeech(current.id, { wasFiller: current.filler });
    }
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
  els.btnSettings.onclick = () => {
    els.drawer.classList.remove("hidden");
    renderThemeGrid();     /* purely local — no network, so it is never blank */
    loadDrawer();
  };
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
        list.textContent = "none registered — say “add device robot at 192.168.1.60 as motor”";
      } else {
        list.innerHTML = "";
        for (const dev of d.devices) {
          const row = document.createElement("div");
          row.className = "device-row";
          const dot = dev.online ? '<span class="ok">●</span>' : '<span class="bad">●</span>';
          row.innerHTML = `${dot} <b>${escapeHtml(dev.name)}</b> <span class="muted">${escapeHtml(dev.kind)}</span>`;
          if (dev.kind === "motor") {
            /* The one button worth having next to a robot is the one that
             * stops it. A 4xx is reported as a failure, not as "stopped". */
            const btn = document.createElement("button");
            btn.className = "btn ghost small dev-toggle";
            btn.textContent = "stop";
            btn.onclick = async () => {
              btn.disabled = true;
              try {
                const res = await fetch(`/api/v1/devices/${encodeURIComponent(dev.name)}/motor`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json", ...authHeaders() },
                  body: JSON.stringify({ action: "stop" }),
                });
                if (!res.ok) throw new Error(String(res.status));
                tick(`${dev.name} stopped`, "ok");
              } catch { tick(`${dev.name} did not answer`, "fail"); }
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
      const state = await refreshSetupState();
      let skipped = false;
      try { skipped = sessionStorage.getItem("iris_setup_skipped") === "1"; } catch { }
      if (state && !state.configured && !skipped) openSetup();
    } catch { /* setup is an offer, never a gate */ }
    try {
      const v = await jfetch("/api/v1/voice/status");
      voiceStatus = v;
      els.chipVoice.textContent = v.tts_engine === "browser" ? "browser voice" : v.tts_engine;
    } catch { }
  })();

  /* ─────────────────────────── First-run setup ───────────────────────────
   * Editing .env and restarting is a reasonable way to configure a server and
   * a poor way to start talking to an assistant — especially the second time,
   * when the only thing changing is one key. So the same configuration lives
   * on the screen that is already open.
   *
   * It is never a wall: IRIS runs every command with no key at all, so the
   * dialog offers to step aside and says so. Skipping is remembered for the
   * session only — a browser that forgot would nag on every reload, and one
   * that remembered forever would hide the way in. */
  let setupState = null;

  async function jpost(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch { /* empty or HTML error body */ }
    if (!res.ok) {
      const detail = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data || {};
  }

  function setupMessage(text, kind) {
    if (!els.setupMsg) return;
    els.setupMsg.textContent = text || "";
    els.setupMsg.className = "setup-msg" + (text ? ` ${kind || ""}` : " hidden");
  }

  function renderSetupProviders() {
    if (!setupState || !els.setupProvider) return;
    els.setupProvider.innerHTML = setupState.providers
      .map((p) => `<option value="${escapeHtml(p.name)}">${escapeHtml(p.label)}` +
                  `${p.configured ? " — connected" : ""}</option>`)
      .join("");
    describeProvider();
  }

  function describeProvider() {
    if (!setupState) return;
    const chosen = setupState.providers.find((p) => p.name === els.setupProvider.value)
      || setupState.providers[0];
    if (!chosen) return;
    const where = chosen.configured ? ` Currently set to ${escapeHtml(chosen.masked)}.` : "";
    els.setupNote.innerHTML =
      `${escapeHtml(chosen.note || "")} ${escapeHtml(chosen.hint || "")}. ` +
      `<a href="${escapeHtml(chosen.signup)}" target="_blank" rel="noopener">Get a free key</a>.${where}`;
    els.setupKey.placeholder = chosen.configured ? "paste a new key to replace it" : "paste it here";
  }

  function openSetup() {
    if (!els.setupModal) return;
    setupMessage("", "");
    els.setupModal.classList.remove("hidden");
    const configured = setupState && setupState.configured;
    els.setupTitle.textContent = configured ? "Change the model" : "Connect a model";
    els.setupIntro.textContent = configured
      ? "Paste a different key, or just change what she calls you."
      : "Commands already work offline. A free API key is what turns this into a "
        + "conversation — and lets the camera say what it is looking at.";
    els.setupFoot.textContent = setupState && setupState.env_writable === false
      ? `Heads up: ${setupState.env_path} is not writable, so a key would apply now but be forgotten on restart.`
      : setupState ? `Saved to ${setupState.env_path}` : "";
    if (setupState && setupState.user_name && !els.setupName.value) {
      els.setupName.value = setupState.user_name;
    }
    setTimeout(() => els.setupKey.focus(), 50);
  }

  function closeSetup() {
    if (els.setupModal) els.setupModal.classList.add("hidden");
  }

  async function submitSetup() {
    const key = (els.setupKey.value || "").trim();
    const name = (els.setupName.value || "").trim();
    if (!key && !name) { setupMessage("Paste a key, or type a name.", "bad"); return; }

    els.btnSetupSave.disabled = true;
    setupMessage(key ? "Checking that key with the provider…" : "Saving…", "busy");
    try {
      const body = { user_name: name || undefined };
      if (key) { body.provider = els.setupProvider.value; body.api_key = key; }
      const out = await jpost("/api/v1/setup", body);
      els.setupKey.value = "";
      if (out.warning) {
        setupMessage(out.warning, "bad");
      } else {
        setupMessage(key ? `Connected to ${out.provider} (${out.model}).` : "Saved.", "ok");
        setTimeout(closeSetup, 900);
      }
      await refreshSetupState();
      try {
        const llm = await jfetch("/api/v1/llm/status");
        els.chipProvider.textContent = llm.provider || "local";
      } catch { /* the chip is cosmetic */ }
      tick(key ? "model connected" : "name saved", "ok");
    } catch (err) {
      // The provider's own reason, already turned into English by the API.
      setupMessage(String(err.message || err), "bad");
    } finally {
      els.btnSetupSave.disabled = false;
    }
  }

  async function refreshSetupState() {
    try {
      setupState = await jfetch("/api/v1/setup/status");
      renderSetupProviders();
      return setupState;
    } catch { return null; }
  }

  if (els.btnSetupSave) {
    els.btnSetupSave.onclick = submitSetup;
    els.btnSetupSkip.onclick = () => {
      try { sessionStorage.setItem("iris_setup_skipped", "1"); } catch { }
      closeSetup();
    };
    els.setupProvider.onchange = describeProvider;
    [els.setupKey, els.setupName].forEach((field) => {
      field.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); submitSetup(); }
      });
    });
    els.setupModal.addEventListener("click", (e) => {
      if (e.target === els.setupModal) closeSetup();   // click the backdrop
    });
  }
  if (els.btnSetupOpen) {
    els.btnSetupOpen.onclick = async () => { await refreshSetupState(); openSetup(); };
  }

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
