/*
 * ============================================================================
 *  IRIS ROBOT NODE  —  2 x BTS7960, 4-wheel skid steer, ESP32
 * ============================================================================
 *
 *  WHAT THIS IS
 *  A WiFi motor controller for the IRIS assistant. IRIS calls plain HTTP
 *  endpoints; this board drives two BTS7960 half-bridge modules (one per side,
 *  each carrying two motors in parallel).
 *
 *  WHY YOU DO NOT NEED TO EDIT PINS OR DIRECTIONS ANY MORE
 *  Everything about the wiring — which pins, which side is which, which way is
 *  "forward", how fast each side runs — is CALIBRATED AT RUNTIME from the
 *  built-in web page and saved to flash. If a wheel spins the wrong way or a
 *  whole side looks dead, you fix it by clicking a button, not by re-wiring or
 *  re-flashing.
 *
 *  FIRST RUN
 *   1. Set WIFI_SSID / WIFI_PASS below. (That is the only edit required.)
 *   2. Upload, open Serial Monitor @115200, note the printed address.
 *      No router / wrong password? The board serves its OWN WiFi instead
 *      ("iris-robot", password below) — calibrate on the bench, no network.
 *   3. Open that address in a browser — the CALIBRATION page.
 *   4. Press "A fwd" and "B fwd" and watch which wheels move. Use the
 *      toggles until forward is really forward and left is really left.
 *      Press SAVE. Done, permanently.
 *   5. In IRIS:  add device robot at <that-ip> as motor
 *
 *  WIRING (per BTS7960 module)
 *    RPWM  -> an ESP32 GPIO      (PWM, "this way")
 *    LPWM  -> an ESP32 GPIO      (PWM, "the other way")
 *    R_EN + L_EN  -> tied TOGETHER to one GPIO (or straight to 3.3V).
 *                    Both modules may share ONE enable GPIO if you prefer.
 *    VCC   -> 5V   <-- REQUIRED. The BTS7960 logic side CONSUMES 5V; it does
 *                      not generate it. A module with VCC unconnected looks
 *                      completely dead. This is the #1 cause of "half my
 *                      driver does nothing".
 *    GND   -> ESP32 GND *and* battery minus (all grounds common)
 *    B+/B- -> motor battery (never the ESP32's 5V pin)
 *
 *  Default pins (changeable live from the web page):
 *    Side A: RPWM 25, LPWM 26, EN 27
 *    Side B: RPWM 32, LPWM 33, EN 14
 *
 *  HOW COMMANDS GET IN — three doors, one set of handlers
 *    UDP :8267    payload is exactly what would follow the host in a URL, e.g.
 *                 "/motor?dir=forward&speed=200". One datagram, no handshake:
 *                 this is the millisecond path, and what IRIS uses.
 *    ws  :81      the calibration page's control socket. Same strings, with a
 *                 request id, and /status is PUSHED back 10x a second.
 *    HTTP :80     the endpoints below, unchanged, plus the page itself.
 *  The HTTP server is the slow door and always was: it serves ONE client at a
 *  time and holds a socket that has connected but not yet spoken for up to five
 *  seconds. A browser opens exactly such idle sockets, so an open calibration
 *  page was enough to queue the next drive command behind it. That is where the
 *  "why is it so slow" came from; the two fast doors do not go through it.
 *  See fastlink.h.
 *
 *  HTTP API (unchanged for IRIS compatibility)
 *    GET /status                      full state + config JSON
 *    GET /motor?dir=forward|backward|left|right|stop|brake
 *                   [&speed=0..255][&ms=0]        drive; ms auto-stops
 *    GET /tank?left=-255..255&right=-255..255[&ms=]   per-side direct
 *    GET /drive?y=-255..255&x=-255..255[&ms=]         arcade mixing
 *    GET /test?side=a|b&dir=forward|backward[&speed=][&ms=]  RAW side test,
 *                   ignores swap/invert — the diagnosis primitive
 *    GET /selftest                    runs a timed A/B sequence, poll /status
 *    GET /stop                        immediate coast stop
 *    GET /config?...                  live calibration (see page)
 *    GET /save                        persist config to flash
 *    GET /reset                       restore defaults
 *
 *  Every numeric argument is parsed strictly: a typo is answered with HTTP 400,
 *  never silently treated as 0. On a motor controller "it quietly did something
 *  else" is the worst possible failure mode.
 *
 *  SAFETY
 *   - Motion stops automatically if no command arrives within failsafe_ms
 *     (default 10 s) — a dropped WiFi link can never leave motors running.
 *   - The web page drives only while a key or button is HELD (dead-man's
 *     switch); letting go stops immediately.
 *   - Motion stops immediately if the WiFi link carrying commands drops.
 *   - Speed ramps instead of stepping, so 4 motors cannot brown-out the board.
 *   - "Stop" really coasts: the bridges are DISABLED, not just set to 0 duty
 *     (enabled + 0 duty is a low-side short, i.e. locked wheels).
 * ============================================================================
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>

#include "robot_config.h"
#include "cloud_args.h"
#include "cloud.h"
#include "fastlink.h"
#include "page.h"

/* ══════════════════════ EDIT THESE TWO LINES ══════════════════════ */
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";
const char* DEVICE_NAME = "robot";      /* also becomes http://robot.local */

/* ── IRIS in the cloud. Leave CLOUD_HOST empty for a LAN-only setup. ──
 * The robot is behind your router's NAT, so a VPS-hosted IRIS cannot call it;
 * with these set the board dials OUT instead and commands come back down the
 * same socket. CLOUD_TOKEN must equal NODE_LINK_TOKEN in IRIS's .env. */
const char* CLOUD_HOST  = "";           /* "iris.example.com" or an IP        */
const uint16_t CLOUD_PORT = 443;        /* 443 for https/wss, else yours      */
const bool CLOUD_TLS    = true;         /* false only on your own LAN         */
const char* CLOUD_TOKEN = "";           /* = NODE_LINK_TOKEN                  */
/* Optional CA (PEM, with the BEGIN/END lines). With it the certificate is
 * checked; empty means encrypted but unverified, and the board says so. */
const char* CLOUD_CA_CERT = "";
CloudLink cloud;
/* The millisecond command path: UDP :8267 for IRIS, a pushed WebSocket :81 for
 * the calibration page. Both bypass the HTTP server, which serves one client
 * at a time and can stall for seconds behind a browser's idle pre-connect
 * socket. See fastlink.h. */
FastLink fast;
const char* AP_PASSWORD = "iriscalib";  /* fallback network, min 8 chars */

/* AP_ONLY: skip the router entirely and serve just my own WiFi.
 *
 * Worth it for a robot. Its own AP is the strongest link it will ever have —
 * you are standing next to it — with no router hop, no phone-hotspot client
 * isolation, and no 25 s join wait before the calibration page exists. That
 * matters most in exactly the situation where you need the page: a robot that
 * drives away from the access point.
 *
 * The cost is real and worth stating: a laptop joined to this AP is on the
 * robot's network and nothing else. IRIS running on that laptop can drive the
 * robot, but cannot reach the sensor or relay boards on your house WiFi, and
 * has no internet — so no LLM. Use AP_ONLY to calibrate and drive by hand;
 * leave it false for voice control alongside the other boards. */
const bool AP_ONLY = false;
/* ═════════════════════════════════════════════════════════════════ */

/* ───────────────────────── PWM back end ─────────────────────────── */
/* Explicit LEDC, configured ONCE. The Arduino analogWrite() helper
 * re-runs ledcSetup() on every write, which re-initialises the shared
 * hardware timer under a running channel (glitches, and silently does
 * nothing at all when its channel pool is exhausted). Motor PWM must
 * never be that fragile. Channels 0..3 -> timers 0,0,1,1 (same freq,
 * so sharing is safe). */
#define PWM_BITS       8            /* duty 0..255; PWM_DUTY_MAX in robot_config.h */
#define CH_A_R 0
#define CH_A_L 1
#define CH_B_R 2
#define CH_B_L 3

#define MS_MAX         600000L     /* longest timed move: 10 minutes */
#define WIFI_JOIN_MS   25000UL     /* then fall back to our own network */

static void pwmInit(uint8_t pin, uint8_t channel, uint32_t freq) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  (void)channel;
  ledcAttach(pin, freq, PWM_BITS);
#else
  ledcSetup(channel, freq, PWM_BITS);
  ledcAttachPin(pin, channel);
#endif
}

static void pwmWrite(uint8_t pin, uint8_t channel, uint32_t duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  (void)channel;
  ledcWrite(pin, duty);
#else
  (void)pin;
  ledcWrite(channel, duty);
#endif
}

/* Release a pin from its LEDC channel and hold it low.
 * ledcAttachPin() only ADDS a GPIO-matrix route; it never removes the previous
 * one. Without this, re-assigning a pin leaves the abandoned GPIO still driven
 * by the same channel — i.e. a motor input nobody thinks is connected any more. */
static void pwmDetach(uint8_t pin) {
  if (pin > 39) return;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcDetach(pin);
#else
  ledcDetachPin(pin);
#endif
  pinMode(pin, OUTPUT);
  digitalWrite(pin, LOW);      /* a BTS7960 input must never be left floating */
}

/* ───────────────────────── configuration ────────────────────────── */

/* The Config type, the pin rules and the clamps live in robot_config.h — the
 * .ino preprocessor hoists prototypes above the sketch body, so a top-level
 * function here could not name Config in its signature. */

static Config cfg;
static Preferences prefs;
/* True when the stored pin set had to be rejected at boot. Surfaced in
 * /status so the dashboard can say it, rather than only the serial console. */
static bool configReverted = false;

static void configDefaults() { configFillDefaults(cfg); }

static void configLoad() {
  configDefaults();
  if (!prefs.begin("irisbot", true)) return;      /* read-only; absent = defaults */
  cfg.aR  = prefs.getUChar("aR",  cfg.aR);
  cfg.aL  = prefs.getUChar("aL",  cfg.aL);
  cfg.aEn = prefs.getUChar("aEn", cfg.aEn);
  cfg.bR  = prefs.getUChar("bR",  cfg.bR);
  cfg.bL  = prefs.getUChar("bL",  cfg.bL);
  cfg.bEn = prefs.getUChar("bEn", cfg.bEn);
  cfg.swapSides = prefs.getBool("swap", cfg.swapSides);
  cfg.invA = prefs.getBool("invA", cfg.invA);
  cfg.invB = prefs.getBool("invB", cfg.invB);
  cfg.trimA = prefs.getUChar("trimA", cfg.trimA);
  cfg.trimB = prefs.getUChar("trimB", cfg.trimB);
  cfg.pwmFreq = prefs.getUShort("freq", cfg.pwmFreq);
  cfg.failsafeMs = prefs.getUShort("fail", cfg.failsafeMs);
  cfg.rampMs = prefs.getUShort("ramp", cfg.rampMs);
  cfg.defaultSpeed = prefs.getUChar("dspd", cfg.defaultSpeed);
  cfg.minDuty = prefs.getUChar("mind", cfg.minDuty);
  cfg.brakeOnStop = prefs.getBool("brake", cfg.brakeOnStop);
  prefs.end();

  /* A corrupt or hand-edited pin set must not brick the board on boot — but
   * only the PINS are suspect, so only the pins go back. The old recovery
   * called configDefaults() and threw away swap, invert, trim, ramp, freq,
   * min_duty and failsafe as well: a whole afternoon of calibration lost to
   * one bad GPIO number.
   *
   * Reported through /status too, not just Serial. Someone driving from a
   * phone on the fallback AP has no console, and "the robot forgot its
   * calibration" is not something to leave them to deduce. */
  if (!pinsAllUsable(cfg) || pinConflict(cfg) >= 0) {
    Serial.println("[cfg] stored pins invalid or clashing — reverting the PINS "
                   "to defaults (the rest of your calibration is kept)");
    Config d;
    configFillDefaults(d);
    cfg.aR = d.aR; cfg.aL = d.aL; cfg.aEn = d.aEn;
    cfg.bR = d.bR; cfg.bL = d.bL; cfg.bEn = d.bEn;
    configReverted = true;
  }
  configApplyClamps(cfg);
}

/* Every put* answers the number of bytes written, and 0 when it did not write
 * — NVS full, a corrupt page, a handle opened read-only. Dropping those and
 * returning true meant the page said "saved to flash" over a calibration that
 * was partly or wholly still the old one, and you would only find out on the
 * next power-up, by which time the SAVE you remember pressing is evidence
 * against the wiring. Each put commits on its own, so a partial write is a
 * real state, not a theoretical one. */
static bool configSave() {
  if (!prefs.begin("irisbot", false)) return false;
  bool ok = true;
  ok &= prefs.putUChar("aR", cfg.aR) > 0;
  ok &= prefs.putUChar("aL", cfg.aL) > 0;
  ok &= prefs.putUChar("aEn", cfg.aEn) > 0;
  ok &= prefs.putUChar("bR", cfg.bR) > 0;
  ok &= prefs.putUChar("bL", cfg.bL) > 0;
  ok &= prefs.putUChar("bEn", cfg.bEn) > 0;
  ok &= prefs.putBool("swap", cfg.swapSides) > 0;
  ok &= prefs.putBool("invA", cfg.invA) > 0;
  ok &= prefs.putBool("invB", cfg.invB) > 0;
  ok &= prefs.putUChar("trimA", cfg.trimA) > 0;
  ok &= prefs.putUChar("trimB", cfg.trimB) > 0;
  ok &= prefs.putUShort("freq", cfg.pwmFreq) > 0;
  ok &= prefs.putUShort("fail", cfg.failsafeMs) > 0;
  ok &= prefs.putUShort("ramp", cfg.rampMs) > 0;
  ok &= prefs.putUChar("dspd", cfg.defaultSpeed) > 0;
  ok &= prefs.putUChar("mind", cfg.minDuty) > 0;
  ok &= prefs.putBool("brake", cfg.brakeOnStop) > 0;
  prefs.end();
  if (!ok) Serial.println("[cfg] SAVE FAILED — flash write refused");
  return ok;
}

/* ───────────────────────── motor state ──────────────────────────── */

WebServer server(80);

static int   targetA = 0, targetB = 0;   /* wanted signed duty, -255..255 */
static int   liveA   = 0, liveB   = 0;   /* actual, ramped toward target  */
static bool  brakingA = false, brakingB = false;
/* What was last actually written to the hardware. rampTick() only touches the
 * bridges when something changed, so the braking flags must be part of that
 * comparison — otherwise clearing a brake updates the flags while the wheels
 * stay physically locked. */
static bool  wroteBrakeA = false, wroteBrakeB = false;
/* A BTS7960 with EN high and both IN low turns its LOW-side FETs on, shorting
 * the motor: that is a brake, not a coast. Real coasting needs the bridges
 * disabled, so the enables are part of the stop state — and they are tracked
 * PER SIDE, because enabling both because one side is driving low-side-shorts
 * the idle one. That is what made a raw /test of side A lock side B's wheels,
 * during the one diagnostic that has to show each side in isolation. */
static bool  enA = false, enB = false;
/* Duty owed but not yet handed out, carried between ramp ticks.
 *
 * step = PWM_DUTY_MAX * dt / rampMs is integer division, and the 2 ms gate
 * makes the truncation bite five times harder than the old 10 ms one did: at
 * the default 90 ms ramp, 255*2/90 is 5 where the true step is 5.67, so the
 * ramp ran 12% slower than it was asked for — and the old "if (step < 1)
 * step = 1" meant every ramp_ms above 510 truncated to 0, was floored to 1,
 * and silently became the same ~510 ms. Carrying the remainder makes a ramp
 * take the time it was asked for, at any gate and any ramp_ms. */
static long  rampCarry = 0;
static unsigned long lastRampMs   = 0;
static unsigned long lastCommandMs = 0;
static unsigned long autoStopAt    = 0;  /* 0 = no timed stop pending */
static unsigned long bootMs        = 0;
static bool  failsafeTripped = false;
static uint32_t commandCount = 0;
static bool  apMode = false;             /* serving our own network */
static bool  staAnnounced = false;

/* Fixed buffer, not a String: this is rewritten on every command, and heap
 * churn in a device meant to run for weeks is a slow leak waiting to happen. */
static char lastCommand[24] = "stop";
static void setLastCommand(const char* s) {
  strncpy(lastCommand, s, sizeof(lastCommand) - 1);
  lastCommand[sizeof(lastCommand) - 1] = '\0';
}

/* raw-mode bypasses swap/invert/trim: used by /test to identify hardware */
static bool rawMode = false;

/* self-test sequence (declared before doStop, which cancels it) */
static int  selfStep = -1;                 /* -1 = idle */
static unsigned long selfStepAt = 0;
static const char* SELF_LABELS[] = {
  "A forward", "A backward", "B forward", "B backward",
  "both forward", "spin left", "spin right", "done"
};
static const int SELF_STEPS = 7;

/* 0 is the "nothing pending" sentinel, so a deadline that lands exactly on 0
 * — once per millis() wrap — must be nudged rather than silently cancelled. */
static unsigned long deadlineFromNow(long ms) {
  const unsigned long t = millis() + (unsigned long)ms;
  return t ? t : 1;
}

/* Raise or drop the two BTS7960 enable inputs. Dropping one is the only way to
 * make that side truly free-wheel; raising it must precede any non-zero duty.
 *
 * The two may legitimately be the SAME GPIO — tying every R_EN/L_EN of both
 * modules together is a normal way to wire this — in which case the pin has to
 * be high whenever EITHER side needs its bridge, so both are always resolved
 * together rather than written independently. */
static void setEnables(bool a, bool b) {
  if (a == enA && b == enB) return;
  enA = a; enB = b;
  if (cfg.aEn == cfg.bEn) {
    digitalWrite(cfg.aEn, (enA || enB) ? HIGH : LOW);
    return;
  }
  digitalWrite(cfg.aEn, enA ? HIGH : LOW);
  digitalWrite(cfg.bEn, enB ? HIGH : LOW);
}

/* Each side's bridge is live exactly while that side has something to do: a
 * target, a ramp still winding down, or a brake being held. */
static void syncEnables() {
  setEnables((targetA != 0) || (liveA != 0) || brakingA,
             (targetB != 0) || (liveB != 0) || brakingB);
}

/* Write one physical side. signed: -255..255, negative = LPWM side. */
static void writeSide(bool sideA, int signedDuty, bool brake) {
  const uint8_t rp = sideA ? cfg.aR : cfg.bR;
  const uint8_t lp = sideA ? cfg.aL : cfg.bL;
  const uint8_t rc = sideA ? CH_A_R : CH_B_R;
  const uint8_t lc = sideA ? CH_A_L : CH_B_L;

  if (brake) {                       /* both high = active brake */
    pwmWrite(rp, rc, PWM_DUTY_MAX);
    pwmWrite(lp, lc, PWM_DUTY_MAX);
    return;
  }
  int duty = signedDuty;
  if (duty > PWM_DUTY_MAX) duty = PWM_DUTY_MAX;
  if (duty < -PWM_DUTY_MAX) duty = -PWM_DUTY_MAX;
  if (duty >= 0) {
    pwmWrite(lp, lc, 0);
    pwmWrite(rp, rc, (uint32_t)duty);
  } else {
    pwmWrite(rp, rc, 0);
    pwmWrite(lp, lc, (uint32_t)(-duty));
  }
}

/* Claim every configured pin for PWM/enable use, releasing whatever was
 * claimed before. Safe to call at boot and on every live pin/frequency change;
 * it is the single place that touches the hardware routing. */
static uint8_t claimedPwm[4] = {255, 255, 255, 255};
static uint8_t claimedEn[2]  = {255, 255};

static void armHardware() {
  for (int i = 0; i < 4; i++) {
    if (claimedPwm[i] != 255) pwmDetach(claimedPwm[i]);
    claimedPwm[i] = 255;
  }
  for (int i = 0; i < 2; i++) {
    if (claimedEn[i] != 255) { pinMode(claimedEn[i], OUTPUT); digitalWrite(claimedEn[i], LOW); }
    claimedEn[i] = 255;
  }

  /* PWM first: enabling a bridge while its inputs are still undriven inputs
   * leaves the module reading floating pins, which can twitch a motor. */
  const uint8_t pins[4]  = { cfg.aR, cfg.aL, cfg.bR, cfg.bL };
  const uint8_t chans[4] = { CH_A_R, CH_A_L, CH_B_R, CH_B_L };
  for (int i = 0; i < 4; i++) {
    pwmInit(pins[i], chans[i], cfg.pwmFreq);
    claimedPwm[i] = pins[i];
  }
  writeSide(true, 0, false);
  writeSide(false, 0, false);
  wroteBrakeA = wroteBrakeB = false;
  rampCarry = 0;                     /* pins or frequency just moved under us;
                                      * duty owed to the old routing is not
                                      * owed to the new one */

  /* Enables start LOW: nothing is moving yet, so coast. */
  pinMode(cfg.aEn, OUTPUT); digitalWrite(cfg.aEn, LOW); claimedEn[0] = cfg.aEn;
  pinMode(cfg.bEn, OUTPUT); digitalWrite(cfg.bEn, LOW); claimedEn[1] = cfg.bEn;
  enA = enB = false;
}

/* Defined with the ramp below; declared here because every function that
 * accepts a movement calls it to write the hardware immediately. */
static void rampKick();

/* Map a logical (left,right) request onto the physical sides, applying
 * calibration: swap -> invert -> trim -> deadband. */
static void applyLogical(int leftReq, int rightReq) {
  int a = cfg.swapSides ? rightReq : leftReq;
  int b = cfg.swapSides ? leftReq  : rightReq;
  if (cfg.invA) a = -a;
  if (cfg.invB) b = -b;
  /* Trim is integer scaling, and it must never turn a request to move into a
   * stop: a low speed times a low trim rounds down to 0, and the command then
   * answers 200 and does nothing. The deadband below cannot rescue that,
   * because it only lifts values that are already non-zero. */
  const int wantA = a, wantB = b;
  a = (int)((long)a * cfg.trimA / 100);
  b = (int)((long)b * cfg.trimB / 100);
  if (wantA != 0 && a == 0) a = (wantA > 0) ? 1 : -1;
  if (wantB != 0 && b == 0) b = (wantB > 0) ? 1 : -1;
  if (cfg.minDuty) {
    if (a != 0 && abs(a) < cfg.minDuty) a = (a > 0 ? cfg.minDuty : -cfg.minDuty);
    if (b != 0 && abs(b) < cfg.minDuty) b = (b > 0 ? cfg.minDuty : -cfg.minDuty);
  }
  targetA = a; targetB = b;
  brakingA = brakingB = false;
  rawMode = false;
  syncEnables();     /* raise now, so the first ramp tick already has a bridge */
  rampKick();        /* ...and take that tick now rather than up to 2 ms later */
}

static void doStop(bool brake) {
  targetA = targetB = 0;
  liveA = liveB = 0;                 /* stopping is immediate, never ramped */
  rampCarry = 0;                     /* no fractional duty owed to a move that
                                      * is over; carrying it would hand the
                                      * next move a head start it did not ask
                                      * for */
  brakingA = brakingB = brake;
  rawMode = false;
  autoStopAt = 0;
  /* A stop must also end a running self-test, or selfTestTick() re-energises
   * the motors ~900 ms later and undoes the failsafe / WiFi-loss stop. */
  selfStep = -1;
  if (brake) {
    syncEnables();                   /* braking, so both bridges stay live */
    writeSide(true,  0, true);       /* both inputs high = high-side brake */
    writeSide(false, 0, true);
  } else {
    writeSide(true,  0, false);      /* zero the inputs before cutting power */
    writeSide(false, 0, false);
    syncEnables();                   /* bridges off = genuine free-wheel */
  }
  wroteBrakeA = wroteBrakeB = brake;
  setLastCommand(brake ? "brake" : "stop");
}

/* direction -> logical (left,right) */
static bool directionToPair(const String& dir, int speed, int& l, int& r) {
  if (dir == "forward")  { l =  speed; r =  speed; return true; }
  if (dir == "backward") { l = -speed; r = -speed; return true; }
  if (dir == "left")     { l = -speed; r =  speed; return true; }
  if (dir == "right")    { l =  speed; r = -speed; return true; }
  return false;
}

/* 500 Hz. The old 100 Hz gate meant an accepted command could sit for up to
 * 10 ms before a single duty cycle was written — a tenth of the whole ramp
 * spent doing nothing, on a board whose job is to react now. dt is measured,
 * not assumed, so a finer gate makes the ramp smoother without making it
 * longer. */
#define RAMP_TICK_MS   2

static void rampApply(unsigned long dt) {
  int step = PWM_DUTY_MAX;                   /* rampMs 0 => instant */
  if (cfg.rampMs > 0) {
    const long owed = (long)PWM_DUTY_MAX * (long)dt + rampCarry;
    step = (int)(owed / cfg.rampMs);
    rampCarry = owed % cfg.rampMs;
  }
  bool changed = (brakingA != wroteBrakeA) || (brakingB != wroteBrakeB);
  /* step can legitimately be 0 for a tick or two on a very long ramp — the
   * carry guarantees it will not stay 0 — and writing an unchanged duty is
   * just two wasted ledcWrite calls, so wait for a step that moves something. */
  if (liveA != targetA && step > 0) {
    if (abs(targetA - liveA) <= step) liveA = targetA;
    else liveA += (targetA > liveA) ? step : -step;
    changed = true;
  }
  if (liveB != targetB && step > 0) {
    if (abs(targetB - liveB) <= step) liveB = targetB;
    else liveB += (targetB > liveB) ? step : -step;
    changed = true;
  }
  if (changed) {
    /* Raise before writing duty, drop after: a bridge must never be asked for
     * a non-zero duty while it is disabled, and must never be cut while its
     * inputs still carry one. */
    setEnables(enA || (targetA != 0) || brakingA,
               enB || (targetB != 0) || brakingB);
    writeSide(true,  liveA, brakingA);
    writeSide(false, liveB, brakingB);
    wroteBrakeA = brakingA;
    wroteBrakeB = brakingB;
  }
  /* Symmetric release, and deliberately outside the dirty check: rampTick()
   * only ever RAISED the enables, so a request for zero (speed=0,
   * /tank?left=0&right=0, a ramp that has just arrived at zero) left the
   * bridges enabled with both inputs low — a low-side short, i.e. the wheels
   * locked, that the failsafe could never clear because nothing was "moving"
   * and the auto-stop could never clear because no `ms` was pending. */
  syncEnables();
}

static void rampTick() {
  const unsigned long now = millis();
  const unsigned long dt = now - lastRampMs;
  if (dt < RAMP_TICK_MS) return;
  lastRampMs = now;
  rampApply(dt);
}

/* Write the hardware NOW, in the same millisecond the command was accepted,
 * instead of waiting for the next tick. Called from every accepted drive
 * command: the bridge is already raised by then, so this is what turns "the
 * board has agreed to move" into "the wheels are being driven".
 *
 * A full RAMP_TICK_MS is charged rather than the real (near-zero) elapsed
 * time, so the first step is a normal-sized one and the ramp keeps its shape
 * — crediting 0 ms would make step 0, i.e. exactly the dead tick this exists
 * to remove. */
static void rampKick() {
  lastRampMs = millis();
  rampApply(RAMP_TICK_MS);
}

/* ───────────────────────── self test ────────────────────────────── */

static void selfTestApply(int step) {
  const int s = cfg.defaultSpeed;
  switch (step) {
    /* Steps 0..3 test one module at a time, so only that module's bridge comes
     * up — the other side has to free-wheel or it drags against the test. */
    case 0: rawMode = true; targetA =  s; targetB = 0; break;
    case 1: rawMode = true; targetA = -s; targetB = 0; break;
    case 2: rawMode = true; targetA = 0; targetB =  s; break;
    case 3: rawMode = true; targetA = 0; targetB = -s; break;
    case 4: applyLogical( s,  s); break;
    case 5: applyLogical(-s,  s); break;
    case 6: applyLogical( s, -s); break;
    default: doStop(false); return;
  }
  brakingA = brakingB = false;
  syncEnables();
  rampKick();
}

static void selfTestTick() {
  if (selfStep < 0) return;
  if ((long)(millis() - selfStepAt) < 0) return;   /* rollover-safe */
  selfStep++;
  if (selfStep >= SELF_STEPS) { selfStep = -1; doStop(false); return; }
  selfTestApply(selfStep);
  selfStepAt = millis() + 900;
  lastCommandMs = millis();                  /* keep failsafe quiet */
}

/* ───────────────────────── HTTP helpers ─────────────────────────── */

/* ─────────────────── where a reply goes, where args come from ───────────────
 * A command arrives four ways: an HTTP request, a UDP datagram, a frame on the
 * page's control socket, and a frame on the cloud socket. Rather than write
 * eleven handlers four times — and watch the copies nobody tests drift — the
 * argument source and the reply sink are redirected around one set of handlers.
 *
 * curArgs is never null. It points at httpArgs (which reads the live
 * WebServer request) except while a non-HTTP frame is being served. The
 * previous version branched on a nullable pointer instead, and the HTTP arm of
 * that branch was written `argHas(name)` where it meant `server.hasArg(name)`:
 * an infinite self-call that hung the board on the first drive command and
 * looked exactly like dead wiring. See cloud_args.h.
 *
 * Commands are served one at a time from loop(), so a single slot is correct
 * here for the same reason argFail below is.                                */
static const Args httpArgs = Args::fromServer();
static const Args* curArgs = &httpArgs;
static String* cloudOut = nullptr;
static int cloudCode = 200;

static bool argHas(const char* name) { return curArgs->has(name); }
static String argGet(const char* name) { return curArgs->get(name); }

static void sendJson(int code, const String& body) {
  if (cloudOut) { *cloudOut = body; cloudCode = code; return; }
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(code, "application/json", body);
}

/* Name of the argument that failed to parse. Requests are handled one at a
 * time from loop(), so a single slot is enough and saves threading an error
 * object through every handler. */
static const char* argFail = NULL;

/* Present but malformed => false (the caller answers 400). Absent => the
 * caller's default is kept. Out of range is clamped, because "speed=300 means
 * full speed" is helpful whereas "speed=fast means stopped" is a trap. */
static bool argClamp(const char* name, long lo, long hi, long& out) {
  if (!argHas(name)) return true;
  long v;
  if (!parseLong(argGet(name), v)) { argFail = name; return false; }
  out = (v < lo) ? lo : (v > hi ? hi : v);
  return true;
}

/* Same, but out of range is refused rather than clamped — for values where
 * silently substituting a neighbour changes the meaning (ms=-1 clamped to 0
 * would mean "no timed stop at all", the opposite of a short move). */
static bool argRange(const char* name, long lo, long hi, long& out) {
  if (!argHas(name)) return true;
  long v;
  if (!parseLong(argGet(name), v)) { argFail = name; return false; }
  if (v < lo || v > hi) { argFail = name; return false; }
  out = v;
  return true;
}

static bool argBool(const char* name, bool& out) {
  if (!argHas(name)) return true;
  const String s = argGet(name);
  if (s == "true"  || s == "on"  || s == "yes") { out = true;  return true; }
  if (s == "false" || s == "off" || s == "no")  { out = false; return true; }
  long v;
  if (!parseLong(s, v)) { argFail = name; return false; }
  out = (v != 0);
  return true;
}

static void sendBadArg() {
  sendJson(400, String("{\"error\":\"bad value for '") + (argFail ? argFail : "?") +
                "'\",\"hint\":\"must be a whole number within range\"}");
}

static String configJson() {
  String j = "{";
  j += "\"pins\":{\"a_rpwm\":" + String(cfg.aR) + ",\"a_lpwm\":" + String(cfg.aL) +
       ",\"a_en\":" + String(cfg.aEn) + ",\"b_rpwm\":" + String(cfg.bR) +
       ",\"b_lpwm\":" + String(cfg.bL) + ",\"b_en\":" + String(cfg.bEn) + "}";
  j += ",\"swap_sides\":" + String(cfg.swapSides ? "true" : "false");
  j += ",\"invert_a\":" + String(cfg.invA ? "true" : "false");
  j += ",\"invert_b\":" + String(cfg.invB ? "true" : "false");
  j += ",\"trim_a\":" + String(cfg.trimA) + ",\"trim_b\":" + String(cfg.trimB);
  j += ",\"pwm_freq\":" + String(cfg.pwmFreq);
  j += ",\"failsafe_ms\":" + String(cfg.failsafeMs);
  j += ",\"ramp_ms\":" + String(cfg.rampMs);
  j += ",\"default_speed\":" + String(cfg.defaultSpeed);
  j += ",\"min_duty\":" + String(cfg.minDuty);
  j += ",\"brake_on_stop\":" + String(cfg.brakeOnStop ? "true" : "false");
  j += "}";
  return j;
}

static void handleStatus() {
  String j = "{\"name\":\"" + String(DEVICE_NAME) + "\",\"kind\":\"motor\"";
  j += ",\"driver\":\"bts7960x2\",\"firmware\":\"iris-robot-2.1\"";
  j += ",\"ip\":\"" + (apMode && WiFi.status() != WL_CONNECTED
                        ? WiFi.softAPIP().toString() : WiFi.localIP().toString()) + "\"";
  j += ",\"link\":\"" + String(WiFi.status() == WL_CONNECTED ? "sta" : (apMode ? "ap" : "down")) + "\"";
  j += ",\"ap_mode\":" + String(apMode ? "true" : "false");
  j += ",\"rssi\":" + String(WiFi.RSSI());
  j += ",\"uptime_s\":" + String((millis() - bootMs) / 1000);
  j += ",\"motors\":true";
  j += ",\"last_direction\":\"" + String(lastCommand) + "\"";
  j += ",\"commands\":" + String(commandCount);
  j += ",\"live\":{\"a\":" + String(liveA) + ",\"b\":" + String(liveB) + "}";
  j += ",\"target\":{\"a\":" + String(targetA) + ",\"b\":" + String(targetB) + "}";
  j += ",\"moving\":" + String((liveA || liveB) ? "true" : "false");
  /* With one GPIO carrying both R_EN/L_EN pairs the hardware cannot hold the
   * two sides apart, so reporting them separately would be reporting a state
   * the board is not in. shared_enable says which reading applies. */
  const bool sharedEnable = (cfg.aEn == cfg.bEn);
  const bool anyBridge = enA || enB;
  j += ",\"bridges\":{\"a\":" + String((sharedEnable ? anyBridge : enA) ? "true" : "false") +
       ",\"b\":" + String((sharedEnable ? anyBridge : enB) ? "true" : "false") +
       ",\"shared_enable\":" + String(sharedEnable ? "true" : "false") + "}";
  j += ",\"failsafe_tripped\":" + String(failsafeTripped ? "true" : "false");
  j += ",\"config_reverted\":" + String(configReverted ? "true" : "false");
  j += ",\"raw_mode\":" + String(rawMode ? "true" : "false");
  j += ",\"selftest_step\":" + String(selfStep);
  j += ",\"selftest_label\":\"" + String(selfStep >= 0 && selfStep < SELF_STEPS ? SELF_LABELS[selfStep] : "idle") + "\"";
  j += ",\"free_heap\":" + String((uint32_t)ESP.getFreeHeap());
  j += ",\"arduino_core\":" + String(ESP_ARDUINO_VERSION_MAJOR);
  /* Advertised so IRIS can find the fast path without being told about it, and
   * so an older IRIS that ignores these keys keeps working over HTTP. */
  j += ",\"fast\":{\"udp\":" + String(fast.udpPort()) +
       ",\"ws\":" + String(fast.wsPort()) +
       ",\"ws_clients\":" + String(fast.wsClients()) + "}";
  j += ",\"config\":" + configJson();
  j += "}";
  sendJson(200, j);
}

/* Everything a successfully accepted drive command has in common. Called only
 * AFTER the request has been fully validated, so a rejected request can never
 * leave the machine half-changed. */
static void acceptCommand(const char* label, long ms) {
  setLastCommand(label);
  commandCount++;
  lastCommandMs = millis();
  failsafeTripped = false;
  autoStopAt = (ms > 0) ? deadlineFromNow(ms) : 0;
}

static void handleMotor() {
  /* `dir` is required, not defaulted.
   *
   * It used to default to "stop", so /motor?direction=forward — a misspelling,
   * a client that dropped the query string, a proxy that ate it — answered
   * HTTP 200, counted as a command, refreshed the failsafe, and stopped the
   * robot. A silent stop is the single worst answer a motor node can give,
   * because it is indistinguishable from unpowered drivers: the board agrees
   * with everything you ask and nothing ever turns. /stop and /motor?dir=stop
   * remain the ways to ask for a stop. */
  if (!argHas("dir")) {
    sendJson(400, "{\"error\":\"dir is required\",\"hint\":\""
                  "forward|backward|left|right|stop|brake\"}");
    return;
  }
  String dir = argGet("dir");
  dir.toLowerCase();

  long speed = cfg.defaultSpeed, ms = 0;
  if (!argClamp("speed", 0, PWM_DUTY_MAX, speed) ||
      !argRange("ms", 0, MS_MAX, ms)) { sendBadArg(); return; }

  const bool isStop  = (dir == "stop");
  const bool isBrake = (dir == "brake");
  int l = 0, r = 0;
  /* Validated BEFORE anything is mutated. Cancelling a running self-test first
   * and only then discovering the direction was a typo left the motors turning
   * with the state machine that would have stopped them switched off. */
  if (!isStop && !isBrake && !directionToPair(dir, (int)speed, l, r)) {
    sendJson(400, "{\"error\":\"dir must be forward|backward|left|right|stop|brake\"}");
    return;
  }

  selfStep = -1;                              /* an accepted command wins */

  if (isStop || isBrake) {
    doStop(isBrake);
    commandCount++;
    lastCommandMs = millis();
    failsafeTripped = false;
    sendJson(200, String("{\"motor\":\"") + (isBrake ? "brake" : "stop") + "\"}");
    return;
  }

  applyLogical(l, r);
  acceptCommand(dir.c_str(), ms);
  sendJson(200, "{\"motor\":\"" + dir + "\",\"speed\":" + String(speed) +
                ",\"ms\":" + String(ms) + ",\"left\":" + String(l) +
                ",\"right\":" + String(r) + "}");
}

static void handleTank() {
  long l = 0, r = 0, ms = 0;
  if (!argClamp("left",  -PWM_DUTY_MAX, PWM_DUTY_MAX, l) ||
      !argClamp("right", -PWM_DUTY_MAX, PWM_DUTY_MAX, r) ||
      !argRange("ms", 0, MS_MAX, ms)) { sendBadArg(); return; }
  selfStep = -1;
  applyLogical((int)l, (int)r);
  acceptCommand("tank", ms);
  sendJson(200, "{\"tank\":{\"left\":" + String(l) + ",\"right\":" + String(r) + "}}");
}

static void handleDrive() {
  long y = 0, x = 0, ms = 0;
  if (!argClamp("y", -PWM_DUTY_MAX, PWM_DUTY_MAX, y) ||
      !argClamp("x", -PWM_DUTY_MAX, PWM_DUTY_MAX, x) ||
      !argRange("ms", 0, MS_MAX, ms)) { sendBadArg(); return; }
  const int l = constrain((int)(y + x), -PWM_DUTY_MAX, PWM_DUTY_MAX);
  const int r = constrain((int)(y - x), -PWM_DUTY_MAX, PWM_DUTY_MAX);
  selfStep = -1;
  applyLogical(l, r);
  acceptCommand("drive", ms);
  sendJson(200, "{\"drive\":{\"y\":" + String(y) + ",\"x\":" + String(x) +
                "},\"left\":" + String(l) + ",\"right\":" + String(r) + "}");
}

/* RAW per-side test: deliberately ignores swap/invert/trim so the answer to
 * "which physical module and which pin pair actually responds?" is unambiguous. */
static void handleTest() {
  String side = argHas("side") ? argGet("side") : "a";
  String dir  = argHas("dir")  ? argGet("dir")  : "forward";
  side.toLowerCase(); dir.toLowerCase();

  long speed = cfg.defaultSpeed, ms = 1200;
  if (!argClamp("speed", 0, PWM_DUTY_MAX, speed) ||
      !argRange("ms", 0, MS_MAX, ms)) { sendBadArg(); return; }
  if (side != "a" && side != "b") {
    sendJson(400, "{\"error\":\"side must be a or b\"}"); return;
  }
  /* Without this, dir=stop / dir=fwd / a typo / an empty dir= all spun the
   * side FORWARD with calibration bypassed. */
  if (dir != "forward" && dir != "backward") {
    sendJson(400, "{\"error\":\"dir must be forward or backward\"}"); return;
  }
  const int signedDuty = (dir == "backward") ? -(int)speed : (int)speed;

  selfStep = -1;
  rawMode = true;
  brakingA = brakingB = false;
  if (side == "a") { targetA = signedDuty; targetB = 0; }
  else             { targetA = 0; targetB = signedDuty; }
  syncEnables();     /* only the side under test: the other must free-wheel,
                      * or its locked wheels drag the robot off the answer */
  rampKick();

  char label[24];
  snprintf(label, sizeof(label), "test:%s:%s", side.c_str(), dir.c_str());
  acceptCommand(label, ms);   /* leaves rawMode set: applyLogical(), which is
                               * what clears it, is deliberately skipped here */

  String pins = (side == "a")
    ? "{\"rpwm\":" + String(cfg.aR) + ",\"lpwm\":" + String(cfg.aL) + ",\"en\":" + String(cfg.aEn) + "}"
    : "{\"rpwm\":" + String(cfg.bR) + ",\"lpwm\":" + String(cfg.bL) + ",\"en\":" + String(cfg.bEn) + "}";
  /* One shared enable GPIO is blessed wiring (see the header), but it costs
   * this endpoint the isolation it promises: setEnables() must drive the shared
   * pin high whenever EITHER side needs its bridge, so the side NOT under test
   * sits enabled with both inputs low — a low-side short, i.e. braked. Its
   * wheels are held, not free, and dragging against the test is exactly what
   * makes a good module look weak. Said out loud rather than left to be
   * discovered, because this is the one endpoint whose whole job is to answer
   * "which module actually responds?" without ambiguity. */
  const bool sharedEn = (cfg.aEn == cfg.bEn);
  sendJson(200, "{\"test\":{\"side\":\"" + side + "\",\"dir\":\"" + dir +
                "\",\"speed\":" + String(speed) + ",\"ms\":" + String(ms) +
                "},\"pins\":" + pins +
                ",\"shared_enable\":" + String(sharedEn ? "true" : "false") +
                ",\"note\":\"raw mode: swap/invert/trim bypassed" +
                (sharedEn ? String(". Both modules share GPIO ") + String(cfg.aEn) +
                            ", so the other side is braked rather than coasting"
                          : String("")) + "\"}");
}

static void handleSelfTest() {
  /* A /test button leaves a ~1.2 s auto-stop pending; without clearing it one
   * step of the sequence would silently not move. */
  autoStopAt = 0;
  selfStep = 0;
  selfTestApply(0);
  selfStepAt = millis() + 900;
  lastCommandMs = millis();
  failsafeTripped = false;
  sendJson(200, "{\"selftest\":\"started\",\"steps\":" + String(SELF_STEPS) +
                ",\"note\":\"poll /status for selftest_label\"}");
}

static void handleStop() {
  doStop(false);
  commandCount++;
  lastCommandMs = millis();
  failsafeTripped = false;
  sendJson(200, "{\"motor\":\"stop\"}");
}

static void handleConfig() {
  /* Validate a CANDIDATE copy and only commit once the whole request is known
   * good. Mutating cfg in place and reverting on error meant reloading from
   * NVS, which silently threw away any calibration the user had not saved yet.
   * A rejected argument now fails the WHOLE request: half-applying a
   * calibration change is worse than applying none of it. */
  Config next = cfg;
  bool pinsChanged = false, freqChanged = false, pinArgSeen = false;

  auto setPin = [&](const char* arg, uint8_t& field) -> bool {
    if (!argHas(arg)) return true;
    pinArgSeen = true;
    long v;
    if (!parseLong(argGet(arg), v) || !pinUsable((int)v)) { argFail = arg; return false; }
    if (field != (uint8_t)v) pinsChanged = true;
    field = (uint8_t)v;
    return true;
  };
  if (!setPin("a_rpwm", next.aR) || !setPin("a_lpwm", next.aL) || !setPin("a_en", next.aEn) ||
      !setPin("b_rpwm", next.bR) || !setPin("b_lpwm", next.bL) || !setPin("b_en", next.bEn)) {
    sendJson(400, String("{\"error\":\"'") + argFail +
                  "' is not a GPIO that can drive a motor input\","
                  "\"unusable\":\"6-11 flash, 20/24/28-31 absent, 34-39 input-only, 1/3 serial\","
                  "\"config\":" + configJson() + "}");
    return;
  }
  if (pinArgSeen) {
    const int clash = pinConflict(next);
    if (clash >= 0) {
      sendJson(400, "{\"error\":\"GPIO " + String(clash) +
                    " would have to carry two signals\",\"config\":" + configJson() + "}");
      return;
    }
  }

  long trimA = next.trimA, trimB = next.trimB, fail = next.failsafeMs,
       ramp = next.rampMs, dspd = next.defaultSpeed, mind = next.minDuty,
       freq = next.pwmFreq;
  /* The gain floors are the page's own slider ranges. Below them a side
   * cannot turn a loaded wheel, so a stored value under one is another
   * "answers 200 and nothing moves" that survives a reboot and looks exactly
   * like a dead driver. There is no calibration that wants a side scaled to
   * nothing; that is what /stop is for. */
  if (!argClamp("trim_a", 40, 100, trimA) ||
      !argClamp("trim_b", 40, 100, trimB) ||
      !argClamp("failsafe_ms", 0, 60000, fail) ||
      !argClamp("ramp_ms", 0, 3000, ramp) ||
      !argClamp("default_speed", 60, PWM_DUTY_MAX, dspd) ||
      !argClamp("min_duty", 0, MIN_DUTY_MAX, mind) ||
      !argClamp("pwm_freq", 100, 25000, freq) ||
      !argBool("swap_sides", next.swapSides) ||
      !argBool("invert_a", next.invA) ||
      !argBool("invert_b", next.invB) ||
      !argBool("brake_on_stop", next.brakeOnStop)) { sendBadArg(); return; }

  /* 0 means "no failsafe" and is a deliberate choice. A value between there
   * and a fifth of a second is not: it auto-stops every command before a
   * wheel can turn, /save writes it to flash, and the robot is then
   * permanently dead while answering 200 to everything. Refused with the
   * reason rather than quietly rounded up, because a calibration that was
   * silently changed is a calibration you will fight later. */
  if (fail > 0 && fail < 200) {
    sendJson(400, "{\"error\":\"failsafe_ms must be 0 (off) or at least 200\","
                  "\"hint\":\"below that, every command stops itself before a "
                  "wheel can turn\"}");
    return;
  }

  next.trimA = (uint8_t)trimA;   next.trimB = (uint8_t)trimB;
  next.failsafeMs = (uint16_t)fail;
  next.rampMs = (uint16_t)ramp;
  next.defaultSpeed = (uint8_t)dspd;
  next.minDuty = (uint8_t)mind;
  if ((uint16_t)freq != next.pwmFreq) { next.pwmFreq = (uint16_t)freq; freqChanged = true; }

  /* Stop on the CURRENT pins before the mapping moves under us, then commit
   * and re-arm. armHardware() releases the pins it previously claimed. */
  if (pinsChanged || freqChanged) {
    doStop(false);
    autoStopAt = 0;
    cfg = next;
    armHardware();
  } else {
    cfg = next;
  }

  String j = "{\"ok\":true,\"rearmed\":" + String((pinsChanged || freqChanged) ? "true" : "false");
  if (configHasRiskyPin(cfg))
    j += ",\"warning\":\"a chosen GPIO (0/2/12/15) is read by the bootloader; "
         "the board may refuse to start next reset\"";
  j += ",\"config\":" + configJson() + ",\"saved\":false}";
  sendJson(200, j);
}

static void handleSave() {
  const bool ok = configSave();
  sendJson(ok ? 200 : 500, String("{\"saved\":") + (ok ? "true" : "false") +
                            ",\"config\":" + configJson() + "}");
}

static void handleReset() {
  doStop(false);
  autoStopAt = 0;
  configDefaults();
  const bool saved = configSave();          /* reported: a silent failed write
                                             * means the old calibration comes
                                             * back at the next power-up */
  armHardware();
  sendJson(saved ? 200 : 500,
           String("{\"reset\":true,\"saved\":") + (saved ? "true" : "false") +
           ",\"config\":" + configJson() + "}");
}

/* ───────────────────────── dashboard ────────────────────────────── */

/* The page is ~12 KB and lwIP's send buffer is 5760 bytes (see
 * CONFIG_LWIP_TCP_SND_BUF_DEFAULT), so one send_P() of the whole thing parks
 * inside WiFiClient::write waiting for the browser to ACK the middle of it —
 * up to HTTP_MAX_SEND_WAIT if that browser stalls. Nothing else in loop() runs
 * meanwhile, which on this board means the ramp and the failsafe stop ticking
 * while somebody reloads the page.
 *
 * Streaming it a kilobyte at a time and pumping the drive path between chunks
 * costs a few chunk headers and keeps the motors serviced throughout. To be
 * precise about what this does and does not fix: the page still takes just as
 * long to leave, and a single chunk can still block — what changes is that
 * the ramp, the failsafe and the fast path get a turn between chunks instead
 * of waiting out the whole transfer. Getting the body under one send would
 * need it gzipped at build time, which is a generated file to keep in step
 * with this one; not worth it for a page you open to calibrate.
 *
 * Safe to re-enter: the fast path writes its replies into a String (see
 * sendJson), so it cannot interleave with this response. */
static void handleRoot() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  server.send(200, "text/html", "");

  const size_t total = strlen_P(PAGE);
  for (size_t at = 0; at < total; at += 1024) {
    const size_t n = (total - at < 1024) ? (total - at) : 1024;
    server.sendContent_P(PAGE + at, n);
    fast.loop(targetA != 0 || targetB != 0);
    rampTick();
  }
  server.sendContent("");
}

/* ───────────────────────── setup / loop ─────────────────────────── */

/* `full` prints the whole boot banner. A re-join gets one line instead:
 * HardwareSerial has no TX ring buffer on this core, so ~300 bytes at 115200
 * baud spins for roughly 26 ms — and a re-join is exactly the moment queued
 * commands arrive and the ramp needs servicing. */
static void startMdns() {
  /* Once. announceSta() runs again on every re-join, and re-entering
   * MDNS.begin() re-registers the same service each time. */
  static bool mdnsUp = false;
  if (!mdnsUp && MDNS.begin(DEVICE_NAME)) {
    MDNS.addService("http", "tcp", 80);
    mdnsUp = true;
  }
}

/* No default argument: the .ino preprocessor hoists a prototype for this, and
 * a default given in both places is a compile error. */
static void announceSta(bool full) {
  staAnnounced = true;
  startMdns();          /* also on a re-join: the board may have come up in AP
                         * fallback, where this never ran, so <name>.local
                         * would otherwise never resolve */
  if (!full) {
    Serial.println("[wifi] rejoined, http://" + WiFi.localIP().toString());
    return;
  }
  Serial.println();
  Serial.println("=================================");
  Serial.print  ("  IRIS robot (BTS7960 x2) online:  http://");
  Serial.println(WiFi.localIP());
  Serial.println("  Calibrate:        open that address in a browser");
  Serial.println("  Register in IRIS: add device " + String(DEVICE_NAME) +
                 " at " + WiFi.localIP().toString() + " as motor");
  Serial.println("  Fast path:        UDP " + String(FastLink::UDP_PORT) +
                 ", control socket ws://" + WiFi.localIP().toString() + ":" +
                 String(FastLink::WS_PORT));
  Serial.println("=================================");
}

/* No router, wrong password, or out of range: serve our own network so the
 * calibration page is still reachable. Blocking in setup() until a router
 * appears left the board with no HTTP server at all — the one thing guaranteed
 * to make a wiring problem impossible to diagnose. */
static void startFallbackAp() {
  const String ssid = String("iris-") + DEVICE_NAME;
  /* AP_STA on the fallback path so the router keeps being retried; pure AP
   * when this is the chosen mode, because a station interface nobody asked
   * for still scans and still costs airtime. */
  WiFi.mode(AP_ONLY ? WIFI_AP : WIFI_AP_STA);

  /* THE ESP32 HAS ONE RADIO, and the station side must leave the SoftAP to
   * use it. arduino-esp32 turns auto-reconnect on by default, and its handler
   * for "no AP found" is `WiFi.disconnect(); WiFi.begin();` with no backoff at
   * all — so a wrong SSID means back-to-back scans of all 13 channels, about
   * 1.5 s each, forever. The SoftAP cannot beacon, ACK or receive during any
   * of them.
   *
   * That is not a slow network, it is a network that mostly is not there: the
   * page stops refreshing and drive commands vanish, on the one link you are
   * standing next to the robot to use. Our own retry in loop() is the only
   * thing that should scan, because it knows when not to. */
  WiFi.setAutoReconnect(false);
  if (!WiFi.softAP(ssid.c_str(), AP_PASSWORD)) {
    Serial.println("  could not start my own WiFi either — check the board");
    return;
  }
  apMode = true;
  Serial.println();
  Serial.println("=================================");
  Serial.println(AP_ONLY ? "  AP_ONLY: serving my own WiFi."
                         : "  No router reached. Serving my own WiFi:");
  Serial.println("    network:  " + ssid);
  Serial.println("    password: " + String(AP_PASSWORD));
  Serial.println("    then open http://" + WiFi.softAPIP().toString());
  if (!AP_ONLY) Serial.println("  Still retrying your router in the background.");
  Serial.println("=================================");
}

/* ───────────────────────── one dispatcher, four transports ────────────────
 * Redirects the argument source and the reply sink, then calls the SAME
 * handler the HTTP route would. Used by the cloud socket, the UDP fast path
 * and the page's control socket, so every endpoint — calibration included — is
 * reachable over all of them without a second copy of any handler.
 *
 * `/` is deliberately absent: it answers HTML to a browser, and none of these
 * transports has a browser on the far end.
 *
 * `out` is filled with the JSON the HTTP route would have sent; the return
 * value is true for a 2xx/3xx. */
static bool dispatchCommand(const String& path, const String& query, String& out) {
  const Args args = Args::fromQuery(query);
  curArgs = &args;
  cloudOut = &out;
  cloudCode = 200;
  argFail = NULL;

  if (args.truncated()) {
    sendJson(400, "{\"error\":\"too many arguments\",\"hint\":\"send the "
                  "calibration in smaller batches\"}");
    curArgs = &httpArgs;
    cloudOut = nullptr;
    return false;
  }

  String p = path;
  if (!p.startsWith("/")) p = "/" + p;

  if      (p == "/status")   handleStatus();
  else if (p == "/motor")    handleMotor();
  else if (p == "/tank")     handleTank();
  else if (p == "/drive")    handleDrive();
  else if (p == "/test")     handleTest();
  else if (p == "/selftest") handleSelfTest();
  else if (p == "/stop")     handleStop();
  else if (p == "/config")   handleConfig();
  else if (p == "/save")     handleSave();
  else if (p == "/reset")    handleReset();
  else sendJson(404, "{\"error\":\"unknown endpoint\"}");

  /* Restored unconditionally: left redirected, the NEXT HTTP reply would go
   * into a dangling String instead of to the browser, and the next HTTP
   * request would read its arguments out of a dead stack frame. */
  curArgs = &httpArgs;
  cloudOut = nullptr;
  return cloudCode < 400;
}

/* Same dispatch, answering the status code rather than a bare pass/fail.
 * cloudCode is deliberately left holding the handler's answer by the call
 * above, which is what makes this a read rather than a second dispatch. The
 * fast paths use this so a refused calibration says 404 or 500 on the page
 * instead of being flattened to "400, something". */
static int dispatchCode(const String& path, const String& query, String& out) {
  dispatchCommand(path, query, out);
  return cloudCode;
}

void setup() {
  Serial.begin(115200);
  delay(80);
  bootMs = millis();
  lastCommandMs = millis();
  lastRampMs = millis();

  configLoad();
  armHardware();

  WiFi.setSleep(false);                /* motor commands must not wait on power save */
  if (AP_ONLY) {
    startFallbackAp();                 /* no join attempt, so no 25 s wait */
  } else {
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
  }

  server.on("/",         handleRoot);
  /* Browsers ask for /favicon.ico on every page load. Without a handler each
   * one becomes a 404 plus an "[E] request handler not found" line, which
   * looks like a fault and is not one — and on a weak link that wasted round
   * trip competes with the 700 ms status poll the page depends on. 204 is the
   * correct answer: "there is no icon, stop asking." */
  server.on("/favicon.ico", []() { server.send(204); });
  server.on("/status",   handleStatus);
  server.on("/motor",    handleMotor);
  server.on("/tank",     handleTank);
  server.on("/drive",    handleDrive);
  server.on("/test",     handleTest);
  server.on("/selftest", handleSelfTest);
  server.on("/stop",     handleStop);
  server.on("/config",   handleConfig);
  server.on("/save",     handleSave);
  server.on("/reset",    handleReset);
  server.onNotFound([]() { sendJson(404, "{\"error\":\"unknown endpoint\"}"); });
  server.begin();      /* unconditional: the dashboard must exist even with no
                        * router, otherwise a wiring fault cannot be diagnosed */
  /* handleClient() sleeps 1 ms per idle pass by default. Harmless on a sensor
   * node, but here it is a millisecond of jitter on the ramp and the failsafe
   * for no benefit: loopTask runs on core 1, whose idle task is not watchdogged
   * (CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU1 is off), so spinning is safe. */
  server.enableDelay(false);

  /* The millisecond path. Unconditional for the same reason as server.begin():
   * it must exist whether the robot is on your router or serving its own
   * network. */
  fast.begin(dispatchCode);

  /* Only NOW wait for the router.
   *
   * Every listener is bound above, before this wait, because they used to be
   * created after it: for the first 25 seconds of every boot there was nothing
   * on 80, 81 or 8267, so commands in that window were refused or discarded
   * with nothing anywhere saying why. And the wait itself was delay(250),
   * which left up to a quarter second of dead time after the join had actually
   * succeeded. Serving the loop while waiting costs nothing and means the
   * board is answering the instant it has an address. */
  if (!AP_ONLY) {
    Serial.print("Connecting to WiFi");
    const unsigned long joinDeadline = millis() + WIFI_JOIN_MS;
    unsigned long lastDot = 0;
    while (WiFi.status() != WL_CONNECTED && (long)(millis() - joinDeadline) < 0) {
      server.handleClient();
      fast.loop(targetA != 0 || targetB != 0);
      rampTick();
      if (millis() - lastDot > 250) { lastDot = millis(); Serial.print("."); }
    }
    if (WiFi.status() == WL_CONNECTED) announceSta(true);
    else startFallbackAp();
  }

  cloud.begin(CLOUD_HOST, CLOUD_PORT, "/api/v1/nodes/link", CLOUD_TOKEN,
              DEVICE_NAME, "motor", CLOUD_TLS, dispatchCommand, CLOUD_CA_CERT);
  if (cloud.enabled()) {
    Serial.println("  Cloud link:   dialling " + cloud.host() + ":" +
                   String(cloud.port()) + (cloud.tls() ? " (wss)" : " (ws)"));
    if (cloud.corrections().length())
      Serial.println("  CLOUD_HOST fixed: " + cloud.corrections());
    if (!cloud.tls())
      Serial.println("  ** CLOUD_TLS is off — the node token crosses the "
                     "internet in clear text. **");
    else if (!cloud.verified())
      Serial.println("  ** TLS on, certificate NOT checked: encrypted, but a "
                     "man in the middle could still read the token. Paste your "
                     "server's CA into CLOUD_CA_CERT to close that. **");
  }
}

void loop() {
  /* Order matters. The fast path is pumped FIRST and the ramp immediately
   * after, so a UDP datagram or a control-socket frame becomes duty on the
   * bridges within one pass of this loop. handleClient() comes last because it
   * is the one thing here that can hold the loop for a noticeable time: a
   * browser socket that connects and says nothing keeps it for up to five
   * seconds (HTTP_MAX_DATA_WAIT), which is exactly why the drive path no
   * longer depends on it. */
  const bool moving = (targetA != 0 || targetB != 0);
  fast.loop(moving);
  rampTick();

  /* Only pumped with a link up: a motor node whose WiFi dropped must keep
   * running its failsafe and ramp ticks below regardless.
   *
   * And only pumped while STOPPED if the socket is still down, because that
   * is when cloud.loop() DIALS — and on this build of the WebSockets library
   * the dial happens on this thread: a name lookup that waits on the DNS
   * client, then a TCP connect, then a TLS handshake. A CLOUD_HOST that has
   * gone away would otherwise freeze the ramp and the failsafe for seconds at
   * a time, over and over. Once the socket is up, loop() is cheap and is
   * pumped unconditionally. */
  if (WiFi.status() == WL_CONNECTED && (cloud.connected() || !moving))
    cloud.loop();
  selfTestTick();
  server.handleClient();
  rampTick();          /* again: handleClient() may have just accepted a move */

  /* timed move finished */
  if (autoStopAt && (long)(millis() - autoStopAt) >= 0)   /* rollover-safe */
    doStop(cfg.brakeOnStop);

  /* failsafe: never keep driving into the unknown. This is the real safety net
   * — it holds whichever network the commands arrived on, and whether or not
   * any network is up at all.
   *
   * Re-read rather than reusing `moving` from the top of the loop: a stop that
   * happened during this pass (the timed auto-stop just above, a /stop that
   * handleClient() served) would otherwise be judged against a stale "yes, it
   * is moving" and reported as a failsafe trip that never happened. */
  const bool stillMoving = (targetA != 0 || targetB != 0);
  if (stillMoving && cfg.failsafeMs && selfStep < 0 &&
      (millis() - lastCommandMs) > cfg.failsafeMs) {
    Serial.println("[failsafe] no command in time — stopping");
    failsafeTripped = true;
    doStop(cfg.brakeOnStop);
  }

  /* AP_ONLY has no station side at all: WiFi.status() is permanently not
   * connected, and calling WiFi.begin() here would switch the radio out of
   * pure AP mode and drop the very network someone is driving from. */
  if (AP_ONLY) {
    // nothing to watch — the AP is up or the board is broken.
  } else if (WiFi.status() != WL_CONNECTED) {
    /* Losing the router is an instant stop only when the router is what was
     * carrying commands. In AP fallback there is no router to lose, and
     * stopping there would cut off someone driving from the fallback network. */
    if (!apMode && stillMoving) {
      Serial.println("[failsafe] WiFi lost — stopping");
      failsafeTripped = true;
      doStop(cfg.brakeOnStop);
    }
    staAnnounced = false;

    /* Retrying the router is not free: WiFi.begin() starts a scan across every
     * channel, and the single radio has to leave whatever else it is doing to
     * run it. Every 5 s was also shorter than a real WPA2 join plus DHCP on a
     * busy router, so each retry could abort an attempt that was about to
     * succeed. Two things it must never interrupt:
     *   - someone driving from the fallback AP. If a station is joined, that
     *     is the link carrying commands; scanning would cut it repeatedly.
     *   - a move in progress, on either network.
     * Neither case loses anything: nothing here expires, and the retry
     * resumes the moment the wheels stop or the last client leaves. */
    static unsigned long lastRetry = 0;
    const unsigned long retryEvery = apMode ? 30000UL : 12000UL;
    const bool apInUse = apMode && WiFi.softAPgetStationNum() > 0;
    if (!apInUse && !stillMoving && (millis() - lastRetry) > retryEvery) {
      lastRetry = millis();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
    }
  } else if (!staAnnounced) {
    announceSta(false);                /* re-joined: one line, not the banner */
  }
}
