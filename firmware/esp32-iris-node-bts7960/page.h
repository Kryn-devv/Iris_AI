/*
 * Calibration dashboard for the IRIS robot node.
 *
 * Kept in its own header on purpose: the Arduino .ino preprocessor scans the
 * main sketch for function definitions to auto-generate prototypes, and it
 * does not understand raw string literals — the JavaScript "async function"
 * below makes it emit a bogus C++ prototype and the build fails. Headers are
 * not scanned, so the page lives here.
 */
#pragma once

static const char PAGE[] PROGMEM = R"HTML(<!DOCTYPE html><html><head>
<meta name=viewport content='width=device-width,initial-scale=1'>
<title>IRIS robot</title><style>
:root{--bg:#05070f;--pa:#0d1224;--ln:rgba(94,234,212,.25);--ac:#5eead4;--tx:#e6edf7;--dm:#8b96ad}
*{box-sizing:border-box}body{margin:0;padding:18px;font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx)}
h1{font-size:15px;letter-spacing:.22em;color:var(--ac);margin:0 0 4px}
h2{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--dm);margin:22px 0 8px;font-weight:600}
.wrap{max-width:560px;margin:0 auto}
.sub{font-size:12px;color:var(--dm);margin-bottom:6px}
button{font:inherit;font-size:14px;padding:13px 10px;border-radius:11px;border:1px solid var(--ln);
background:var(--pa);color:var(--tx);cursor:pointer}
button:active,button.on{background:var(--ac);color:#05070f}
[data-h]{touch-action:none;user-select:none;-webkit-user-select:none}
.pad{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:8px}
.pad button{height:56px}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.row button{flex:1;min-width:88px}
.stop{border-color:#f8717155;color:#fca5a5}
.go{border-color:#5eead455}
label{display:flex;align-items:center;gap:9px;font-size:13px;color:var(--dm);padding:7px 0}
input[type=range]{width:100%}
pre{background:#00000055;border:1px solid rgba(148,163,199,.12);border-radius:10px;padding:10px;
font-size:11.5px;color:var(--dm);overflow-x:auto;white-space:pre-wrap;margin:0}
.st{font-size:12px;color:var(--ac);min-height:17px;margin-bottom:6px}
.tip{font-size:11.5px;color:var(--dm);line-height:1.55;background:#5eead40d;border-left:2px solid var(--ln);
padding:8px 10px;border-radius:0 8px 8px 0;margin-bottom:8px}
</style></head><body><div class=wrap>
<h1>IRIS ROBOT</h1><div class=sub>BTS7960 x2 &middot; calibrate here, no re-flashing</div>
<div class=st id=st>&nbsp;</div>

<h2>Drive</h2>
<div class=tip><b>Hold to drive.</b> The robot moves only while a button or an
arrow key is held down &mdash; let go and it stops at once. Space stops too.</div>
<div class=pad>
 <button data-h=left>&#8630; left</button>
 <button class=go data-h=forward>&#9650; forward</button>
 <button data-h=right>&#8631; right</button>
 <button data-h=spinL>spin L</button>
 <button class=stop id=bstop>&#9632; STOP</button>
 <button data-h=spinR>spin R</button>
</div>
<div class=row><button data-h=backward>&#9660; backward</button>
<button id=bbrake>brake</button></div>
<label>speed <input type=range id=sp min=60 max=255 value=200 oninput="sv.textContent=this.value"> <b id=sv>200</b></label>

<h2>Step 1 &middot; find your sides</h2>
<div class=tip>Press each button and watch which wheels turn. These bypass all
calibration, so they show the RAW hardware. If one module never responds, it is
wiring &mdash; check that its <b>VCC</b> has 5V and its R_EN+L_EN are tied to the EN pin.</div>
<div class=row>
 <button onclick="t('a','forward')">A fwd</button><button onclick="t('a','backward')">A rev</button>
 <button onclick="t('b','forward')">B fwd</button><button onclick="t('b','backward')">B rev</button>
</div>
<div class=row><button onclick="api('/selftest')">run full self-test</button></div>

<h2>Step 2 &middot; fix directions</h2>
<div class=tip>Now hold <b>forward</b> above. Wrong? Flip these until forward is
forward and left is left. Then press SAVE.</div>
<label><input type=checkbox id=swap onchange=push()> swap sides (A is actually the right side)</label>
<label><input type=checkbox id=ia onchange=push()> invert side A</label>
<label><input type=checkbox id=ib onchange=push()> invert side B</label>

<h2>Step 3 &middot; drive straight</h2>
<div class=tip>If it veers, trim the faster side down.</div>
<label>trim A <input type=range id=ta min=40 max=100 value=100 onchange=push() oninput="tav.textContent=this.value"> <b id=tav>100</b>%</label>
<label>trim B <input type=range id=tb min=40 max=100 value=100 onchange=push() oninput="tbv.textContent=this.value"> <b id=tbv>100</b>%</label>

<h2>Save</h2>
<div class=row><button class=go onclick="api('/save').then(j=>msg(j&&j.saved?'saved to flash':'SAVE FAILED'))">SAVE</button>
<button onclick="if(confirm('Restore factory defaults?'))api('/reset').then(load)">reset</button></div>

<h2>Status</h2><pre id=out>loading...</pre>
</div><script>
const $=i=>document.getElementById(i);
const s=()=>+$('sp').value;
function msg(m){$('st').textContent=m;clearTimeout(window._m);
 window._m=setTimeout(()=>$('st').innerHTML='&nbsp;',2500)}

/* Every endpoint answers JSON, and a refusal carries a reason. Showing it beats
   the old "assume 200" path, where a rejected calibration looked like success. */
async function api(u){
 try{const r=await fetch(u);
  const j=await r.json().catch(()=>null);
  if(!r.ok){msg(j&&j.error?j.error:'refused ('+r.status+')');return null}
  msg(u.split('?')[0]+' ok');return j}
 catch(e){msg('unreachable');return null}}

const t=(side,dir)=>api('/test?side='+side+'&dir='+dir+'&speed='+s()+'&ms=1200');

/* Dead-man's switch. A press-once-and-walk-away button meant the robot kept
   driving until the firmware failsafe expired — up to ten seconds of
   unattended motion. It now drives only while something is held, and a
   keep-alive repeat stops that hold from ever tripping the failsafe. */
let held=null,beat=null,holding=null;
function send(d){
 if(d==='spinL')return api('/tank?left=-'+s()+'&right='+s());
 if(d==='spinR')return api('/tank?left='+s()+'&right=-'+s());
 return api('/motor?dir='+d+'&speed='+s())}
function hold(d,el){
 if(held===d)return;
 held=d;holding=el||null;if(holding)holding.classList.add('on');
 send(d);
 clearInterval(beat);beat=setInterval(()=>{if(held)send(held)},500)}
function release(stop){
 if(held===null)return;
 held=null;clearInterval(beat);beat=null;
 if(holding){holding.classList.remove('on');holding=null}
 if(stop)api('/motor?dir=stop')}
const rel=()=>release(true);

document.querySelectorAll('[data-h]').forEach(b=>{
 const d=b.dataset.h;
 b.addEventListener('pointerdown',e=>{e.preventDefault();hold(d,b)});
 b.addEventListener('pointerup',rel);
 b.addEventListener('pointerleave',rel);      /* dragging off the button = let go */
 b.addEventListener('pointercancel',rel)});
addEventListener('pointerup',rel);            /* released anywhere on the page */
addEventListener('blur',rel);                 /* tab lost focus mid-hold */
document.addEventListener('visibilitychange',()=>{if(document.hidden)rel()});

$('bstop').onclick=()=>{release(false);api('/motor?dir=stop')};
$('bbrake').onclick=()=>{release(false);api('/motor?dir=brake')};

const KEYS={ArrowUp:'forward',ArrowDown:'backward',ArrowLeft:'left',ArrowRight:'right'};
addEventListener('keydown',e=>{
 if(e.repeat)return;
 if(KEYS[e.key]){e.preventDefault();hold(KEYS[e.key],null);return}
 if(e.key===' '){e.preventDefault();release(false);api('/motor?dir=stop')}});
addEventListener('keyup',e=>{if(KEYS[e.key]){e.preventDefault();rel()}});

function push(){api('/config?swap_sides='+(+$('swap').checked)+'&invert_a='+(+$('ia').checked)
 +'&invert_b='+(+$('ib').checked)+'&trim_a='+$('ta').value+'&trim_b='+$('tb').value)
 .then(j=>{if(j&&j.warning)msg(j.warning)})}

async function load(){const j=await api('/status');if(!j)return;const c=j.config;
 $('swap').checked=c.swap_sides;$('ia').checked=c.invert_a;$('ib').checked=c.invert_b;
 $('ta').value=c.trim_a;$('tav').textContent=c.trim_a;
 $('tb').value=c.trim_b;$('tbv').textContent=c.trim_b;
 $('sp').value=c.default_speed;$('sv').textContent=c.default_speed}

async function tick(){try{const j=await(await fetch('/status')).json();
 $('out').textContent='state   '+j.last_direction+(j.moving?'  (moving)':'  (idle)')
 +'\nbridges '+(j.bridges.a?'A live':'A coasting')+'  '+(j.bridges.b?'B live':'B coasting')
 +'\nside A  '+j.live.a+' -> '+j.target.a+'\nside B  '+j.live.b+' -> '+j.target.b
 +'\nselftest '+j.selftest_label+(j.raw_mode?'   [RAW: calibration bypassed]':'')
 +'\npins    A '+j.config.pins.a_rpwm+'/'+j.config.pins.a_lpwm+'/'+j.config.pins.a_en
 +'   B '+j.config.pins.b_rpwm+'/'+j.config.pins.b_lpwm+'/'+j.config.pins.b_en
 +'\nswap '+j.config.swap_sides+'  invA '+j.config.invert_a+'  invB '+j.config.invert_b
 +'\ntrim '+j.config.trim_a+'% / '+j.config.trim_b+'%   pwm '+j.config.pwm_freq+'Hz'
 +'\nlink '+j.link+(j.ap_mode?' (own network)':'')+'  '+j.rssi+'dBm  up '+j.uptime_s+'s'
 +'\ncmds '+j.commands+'  heap '+j.free_heap
 +(j.failsafe_tripped?'\n** failsafe stopped the motors **':'')}catch(e){$('out').textContent='offline'}}

load();tick();setInterval(tick,700);
</script></body></html>)HTML";
