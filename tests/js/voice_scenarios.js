const vm = require("vm");
const fs = require("fs");
const { run } = require("./dom_harness");
let failures = 0;

const APP = process.argv[2] || require("path").join(__dirname, "..", "..", "iris", "app", "static", "app.js");
const SRC = fs.readFileSync(APP, "utf8");

function boot() {
  const h = run();
  vm.createContext(h.ctx);
  try { vm.runInContext(SRC, h.ctx, {filename:"app.js"}); } catch (e) { h.bootError = e; }
  // Reach the module internals the IIFE keeps private by evaluating in-scope.
  return h;
}

function overlaps(audio) {
  const live = audio.filter(a => a.end > a.start);
  const bad = [];
  for (let i=0;i<live.length;i++) for (let j=i+1;j<live.length;j++) {
    const a=live[i], b=live[j];
    if (a.start < b.end && b.start < a.end) bad.push([a.text.slice(0,28), b.text.slice(0,28)]);
  }
  return bad;
}

// The app.js IIFE hides its functions, so drive it the way the server does:
// through the WebSocket message handler it installs.
function scenario(name, frames) {
  const h = boot();
  if (h.bootError) { console.log(name, "BOOT ERROR:", h.bootError.message); return; }
  const ws = h.ctx.__ws;
  if (!ws || !ws.onmessage) { console.log(name, "no ws handler"); return; }
  const LOCAL = new Set(["piper","pyttsx3","espeak","edge","gtts"]);
  const serverOpen = {};
  for (const [at, frame] of frames) {
    h.tick(at);
    // The speakers are real audio too — model them, or an overlap between
    // server and browser is invisible and the test proves nothing.
    if (frame.type === "event" && frame.topic === "voice.speaking"
        && LOCAL.has(frame.payload.engine)) {
      const rec = {who:"server", start: at, end: at + 20000, text: frame.payload.text};
      h.audio.push(rec);
      serverOpen[frame.payload.id] = rec;
    }
    if (frame.type === "event" && frame.topic === "voice.spoken" && serverOpen[frame.payload.id]) {
      serverOpen[frame.payload.id].end = at;
    }
    ws.onmessage({ data: JSON.stringify(frame) });
  }
  h.tick(40000);
  const bad = overlaps(h.audio);
  const spoken = h.audio.filter(a => a.end > a.start).map(a => `${a.who[0]}:${a.start}-${a.end} ${a.text.slice(0,26)}`);
  if (bad.length) failures++;
  console.log(`${bad.length === 0 ? "OK  " : "FAIL"} ${name}`);
  console.log(`      spoken: ${JSON.stringify(spoken)}`);
  if (bad.length) console.log("      OVERLAP:", JSON.stringify(bad));
}

const reply = (t) => ({type:"response", response:t, speech:t, status:"COMPLETED", handler:"agent"});
const bus = (topic, payload) => ({type:"event", topic, payload});

scenario("reminder lands mid-reply (the finding)", [
  [2000, reply("Recursion is when a function calls itself until a base case is reached and it stops.")],
  [5000, bus("voice.speaking", {id:"u1", engine:"piper", text:"Reminder: call mom", filler:false})],
  [6800, bus("voice.spoken",   {id:"u1", engine:"piper", spoken:true})],
]);

scenario("filler then answer (queue behind)", [
  [900,  bus("voice.speaking", {id:"u2", engine:"browser", text:"One sec.", filler:true})],
  [1300, reply("It is twenty four degrees and sixty percent humidity in Bangalore right now.")],
]);

scenario("server audio first, reply deferred", [
  [1000, bus("voice.speaking", {id:"u3", engine:"piper", text:"Reminder: stand up and stretch", filler:false})],
  [1500, reply("Okay, opened YouTube for you.")],
  [4000, bus("voice.spoken",   {id:"u3", engine:"piper", spoken:true})],
]);

scenario("node engine is not our audio", [
  [1000, bus("voice.speaking", {id:"u4", engine:"node", text:"Answering on the robot", filler:false})],
  [1200, reply("Here is the answer on the laptop.")],
]);

scenario("two replies back to back", [
  [1000, reply("First answer here about the thing.")],
  [1500, reply("Second answer, replacing the first.")],
]);


process.exit(failures === 0 ? 0 : 1);
