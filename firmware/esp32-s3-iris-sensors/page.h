/*
 * Dashboard for the IRIS S3 node — face control and live sensor readings.
 *
 * In its own header because the Arduino .ino preprocessor scans the sketch for
 * function definitions to auto-generate prototypes and does not understand raw
 * string literals: an "async function" in the JavaScript below becomes a bogus
 * C++ prototype and the build fails. Headers are not scanned.
 */
#pragma once

static const char FACE_PAGE[] PROGMEM = R"HTML(<!DOCTYPE html><html><head>
<meta name=viewport content='width=device-width,initial-scale=1'>
<title>IRIS face</title><style>
:root{--bg:#05070f;--pa:#0d1224;--ln:rgba(94,234,212,.25);--ac:#5eead4;--tx:#e6edf7;--dm:#8b96ad}
*{box-sizing:border-box}body{margin:0;padding:18px;font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx)}
h1{font-size:15px;letter-spacing:.22em;color:var(--ac);margin:0 0 4px}
h2{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--dm);margin:22px 0 8px;font-weight:600}
.wrap{max-width:560px;margin:0 auto}
.sub{font-size:12px;color:var(--dm);margin-bottom:6px}
button{font:inherit;font-size:13px;padding:12px 8px;border-radius:11px;border:1px solid var(--ln);
background:var(--pa);color:var(--tx);cursor:pointer}
button:active,button.on{background:var(--ac);color:#05070f}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:7px}
.pad{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;max-width:260px}
.pad button{height:46px}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.row button{flex:1;min-width:96px}
.go{border-color:#5eead455}
label{display:flex;align-items:center;gap:9px;font-size:13px;color:var(--dm);padding:7px 0}
pre{background:#00000055;border:1px solid rgba(148,163,199,.12);border-radius:10px;padding:10px;
font-size:11.5px;color:var(--dm);overflow-x:auto;white-space:pre-wrap;margin:0}
.st{font-size:12px;color:var(--ac);min-height:17px;margin-bottom:6px}
.tip{font-size:11.5px;color:var(--dm);line-height:1.55;background:#5eead40d;border-left:2px solid var(--ln);
padding:8px 10px;border-radius:0 8px 8px 0;margin-bottom:8px}
</style></head><body><div class=wrap>
<h1>IRIS FACE</h1><div class=sub>two 128&times;64 OLED eyes &middot; sensors &middot; live</div>
<div class=st id=st>&nbsp;</div>

<h2>Expressions</h2>
<div class=tip>Tap one and watch the eyes glide into it. IRIS sets these by
itself while it talks &mdash; this page is for checking the wiring and for fun.</div>
<div class=grid id=emos></div>

<h2>Speaking</h2>
<div class=tip>The talking bounce is time-limited on the board, so a lost
&ldquo;stopped speaking&rdquo; message can never leave the eyes bouncing forever.</div>
<div class=row>
 <button class=go onclick="api('/speak?ms=2500')">talk 2.5s</button>
 <button onclick="api('/speak?ms=8000')">talk 8s</button>
 <button onclick="api('/speak?ms=0')">stop</button>
 <button onclick="api('/blink')">blink</button>
</div>

<h2>Look</h2>
<div class=pad>
 <button onclick="look(-90,-70)">&#8598;</button><button onclick="look(0,-90)">&#8593;</button><button onclick="look(90,-70)">&#8599;</button>
 <button onclick="look(-100,0)">&#8592;</button><button onclick="look(0,0)">centre</button><button onclick="look(100,0)">&#8594;</button>
 <button onclick="look(-90,80)">&#8601;</button><button onclick="look(0,90)">&#8595;</button><button onclick="look(90,80)">&#8600;</button>
</div>

<h2>Live</h2><pre id=out>loading...</pre>
</div><script>
const $=i=>document.getElementById(i);
function msg(m){$('st').textContent=m;clearTimeout(window._m);
 window._m=setTimeout(()=>$('st').innerHTML='&nbsp;',2500)}
async function api(u){
 try{const r=await fetch(u);const j=await r.json().catch(()=>null);
  if(!r.ok){msg(j&&j.error?j.error:'refused ('+r.status+')');return null}
  msg(u.split('?')[0]+' ok');return j}
 catch(e){msg('unreachable');return null}}
const look=(x,y)=>api('/look?x='+x+'&y='+y);

/* The board is the source of truth for which emotions exist, so this page can
   never drift out of sync with the firmware's table. */
async function buildEmotions(){
 const j=await api('/face/list');if(!j||!j.emotions)return;
 $('emos').innerHTML='';
 for(const name of j.emotions){
  const b=document.createElement('button');
  b.textContent=name;b.dataset.e=name;
  b.onclick=()=>api('/face?emotion='+name).then(()=>mark(name));
  $('emos').appendChild(b)}}
function mark(name){
 document.querySelectorAll('#emos button').forEach(b=>
  b.classList.toggle('on',b.dataset.e===name))}

async function tick(){try{const j=await(await fetch('/status')).json();
 mark(j.face.emotion);
 let s='face    '+j.face.emotion+(j.face.speaking?'   [speaking]':'')
  +(j.face.dozing?'   [dozing]':'')
  +'\neyes    '+(j.face.eyes_ok?'both OLEDs found':'** an OLED did NOT respond **')
  +'\nfps     '+j.face.fps+'   look '+j.face.look_x+','+j.face.look_y
  +'\nlink    '+j.link+(j.ap_mode?' (own network)':'')+'  '+j.rssi+'dBm'
  +'\nup      '+j.uptime_s+'s   heap '+j.free_heap;
 const r=await(await fetch('/sensors')).json();
 s+='\n\nmotion  '+(r.motion?'YES':(r.motion_recent?'recent':'no'));
 if('gas_raw' in r) s+='\ngas     '+r.gas_raw+(r.gas_alarm?'   ** ALARM **':'  (normal)');
 if('light_percent' in r) s+='\nlight   '+r.light_percent+'%';
 if('distance_cm' in r) s+='\ndistance '+r.distance_cm+' cm';
 $('out').textContent=s}catch(e){$('out').textContent='offline'}}

buildEmotions();tick();setInterval(tick,900);
</script></body></html>)HTML";
