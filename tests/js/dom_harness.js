/* A browser, reduced to the parts app.js touches, on a clock we control.
 *
 * The voice state machine is the one piece of IRIS with no Python to test: it
 * lives in app.js, it is driven by WebSocket frames, and its bugs are about
 * *timing* — who is still talking when the next thing arrives. Those are
 * invisible to a screenshot and to any test that does not own the clock.
 *
 * speechSynthesis here models the real contract: speak() ENQUEUES, and a
 * second utterance starts only when the first ends unless cancel() clears the
 * queue. Modelling it as "starts now" invents overlaps a browser would never
 * produce, and would have this file failing on correct code.
 */
/* Drive the real app.js speech machine on a virtual clock and record every
   moment two audio streams are live at once. */
const fs = require("fs");

function run(scenario, opts) {
  opts = opts || {};
  let now = 0;
  const timers = [];
  let seq = 0;
  const audio = [];               // {who, start, end}

  const setTimeout_ = (fn, ms) => { const t = {id: ++seq, at: now + (ms||0), fn, dead:false}; timers.push(t); return t.id; };
  const clearTimeout_ = (id) => { const t = timers.find(t => t.id === id); if (t) t.dead = true; };
  const tick = (to) => {
    while (true) {
      const due = timers.filter(t => !t.dead && t.at <= to).sort((a,b)=>a.at-b.at)[0];
      if (!due) break;
      due.dead = true; now = due.at; due.fn();
    }
    now = to;
  };

  /* Models the real speechSynthesis contract: speak() ENQUEUES. A second
     utterance starts only when the first ends, unless cancel() clears the
     queue. Modelling it as "starts now" invents overlaps that a browser
     would never produce. */
  const synth = {
    _queue: [],
    getVoices: () => [{name:"Microsoft Zira", lang:"en-US"}],
    _startNext() {
      if (!synth._queue.length) return;
      const item = synth._queue[0];
      if (item.started) return;
      item.started = true;
      item.rec.start = now;
      item.rec.end = now + item.dur;
      setTimeout_(() => {
        if (synth._queue[0] !== item) return;      // cancelled
        synth._queue.shift();
        if (item.u.onend) item.u.onend();
        synth._startNext();
      }, item.dur);
    },
    speak(u) {
      const dur = Math.max(300, u.text.split(/\s+/).length * 380);
      const rec = {who:"browser", start: now, end: now, text:u.text};
      audio.push(rec);
      synth._queue.push({u, dur, rec, started:false});
      if (synth._queue.length === 1) synth._startNext();
    },
    cancel() {
      for (const item of synth._queue) {
        if (item.started && item.rec.end > now) { item.rec.end = now; item.rec.cancelled = true; }
        else if (!item.started) { item.rec.end = item.rec.start; item.rec.cancelled = true; }
      }
      synth._queue = [];
    },
  };

  const style = () => ({ setProperty(){}, removeProperty(){}, getPropertyValue: () => "" });
  const el = () => ({ checked:true, classList:{add(){},remove(){},contains(){return false},toggle(){}},
                      textContent:"", innerHTML:"", appendChild(){}, removeChild(){}, remove(){},
                      focus(){}, blur(){}, scrollIntoView(){}, addEventListener(){}, setAttribute(){},
                      append(){}, prepend(){}, insertBefore(){}, closest: () => null, getContext: () => null,
                      onclick:null, onchange:null, oninput:null, onkeydown:null, value:"",
                      style: style(), dataset:{}, scrollHeight:0, scrollTop:0, children:[],
                      querySelector: () => null, querySelectorAll: () => [] });
  const els = new Proxy({}, { get: (t,k) => (t[k] || (t[k] = el())) });

  const ctx = {
    console, JSON, Math, Date: { now: () => now },
    setTimeout: setTimeout_, clearTimeout: clearTimeout_,
    setInterval: () => 0, clearInterval: () => {},
    localStorage: { getItem: () => null, setItem(){}, removeItem(){} },
    location: { protocol:"http:", host:"127.0.0.1:8756" },
    Notification: function(){},
    /* The default answers every request with a body and no ``ok``, which
     * jfetch treats as a failure — the same shape a server that is down
     * produces, and the state most of these scenarios want. Pass opts.fetch
     * to answer particular URLs instead. */
    fetch: opts.fetch || (() => Promise.resolve({json: async()=>({})})),
    WebSocket: function(){ this.send = (m) => ctx.__sent.push(m); ctx.__ws = this; },
    SpeechSynthesisUtterance: function(t){ this.text=t; this.onend=null; this.onerror=null; },
    speechSynthesis: synth,
    SpeechRecognition: function(){ this.start=()=>{}; this.stop=()=>{}; ctx.__rec = this; },
    document: { getElementById: (id) => els[id], createElement: () => el(),
                documentElement: Object.assign(el(), {style: style()}),
                addEventListener(){}, body: el(), documentElement: el(),
                querySelectorAll: () => [], querySelector: () => el(), hidden:false },
    __sent: [],
    URLSearchParams: URLSearchParams,
    URL: URL,
    requestAnimationFrame: (fn) => setTimeout_(fn, 16),
    cancelAnimationFrame: () => {},
    devicePixelRatio: 1,
    matchMedia: () => ({matches:false, addEventListener(){}, addListener(){}}),
    navigator: {userAgent:"node", language:"en-US"},
    history: {replaceState(){}},
    performance: {now: () => now},
    IrisHologram: function () {
      this.setLevel = () => {}; this.setState = () => {};
      this.handleBusEvent = () => {}; this.start = () => {}; this.resize = () => {};
    },
    IrisScene: function () { this.handleBusEvent = () => {}; },
  };
  ctx.addEventListener = () => {};
  ctx.removeEventListener = () => {};
  ctx.open = () => null;
  ctx.window = ctx;
  ctx.webkitSpeechRecognition = ctx.SpeechRecognition;
  return { ctx, tick, audio, now: () => now, synth };
}
module.exports = { run };
