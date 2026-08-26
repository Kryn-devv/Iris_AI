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
#include "page.h"

/* ══════════════════════ EDIT THESE TWO LINES ══════════════════════ */
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";
const char* DEVICE_NAME = "robot";      /* also becomes http://robot.local */
const char* AP_PASSWORD = "iriscalib";  /* fallback network, min 8 chars */
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

  /* A corrupt or hand-edited pin set must not brick the board on boot. */
  if (!pinsAllUsable(cfg) || pinConflict(cfg) >= 0) {
    Serial.println("[cfg] stored pins invalid or clashing — reverting to defaults");
    configDefaults();
  }
  configApplyClamps(cfg);
}

static bool configSave() {
  if (!prefs.begin("irisbot", false)) return false;
  prefs.putUChar("aR", cfg.aR);   prefs.putUChar("aL", cfg.aL);   prefs.putUChar("aEn", cfg.aEn);
  prefs.putUChar("bR", cfg.bR);   prefs.putUChar("bL", cfg.bL);   prefs.putUChar("bEn", cfg.bEn);
  prefs.putBool("swap", cfg.swapSides);
  prefs.putBool("invA", cfg.invA);   prefs.putBool("invB", cfg.invB);
  prefs.putUChar("trimA", cfg.trimA); prefs.putUChar("trimB", cfg.trimB);
  prefs.putUShort("freq", cfg.pwmFreq);
  prefs.putUShort("fail", cfg.failsafeMs);
  prefs.putUShort("ramp", cfg.rampMs);
  prefs.putUChar("dspd", cfg.defaultSpeed);
  prefs.putUChar("mind", cfg.minDuty);
  prefs.putBool("brake", cfg.brakeOnStop);
  prefs.end();
  return true;
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

  /* Enables start LOW: nothing is moving yet, so coast. */
  pinMode(cfg.aEn, OUTPUT); digitalWrite(cfg.aEn, LOW); claimedEn[0] = cfg.aEn;
  pinMode(cfg.bEn, OUTPUT); digitalWrite(cfg.bEn, LOW); claimedEn[1] = cfg.bEn;
  enA = enB = false;
}

/* Map a logical (left,right) request onto the physical sides, applying
 * calibration: swap -> invert -> trim -> deadband. */
static void applyLogical(int leftReq, int rightReq) {
  int a = cfg.swapSides ? rightReq : leftReq;
  int b = cfg.swapSides ? leftReq  : rightReq;
  if (cfg.invA) a = -a;
  if (cfg.invB) b = -b;
  a = (int)((long)a * cfg.trimA / 100);
  b = (int)((long)b * cfg.trimB / 100);
  if (cfg.minDuty) {
    if (a != 0 && abs(a) < cfg.minDuty) a = (a > 0 ? cfg.minDuty : -cfg.minDuty);
    if (b != 0 && abs(b) < cfg.minDuty) b = (b > 0 ? cfg.minDuty : -cfg.minDuty);
  }
  targetA = a; targetB = b;
  brakingA = brakingB = false;
  rawMode = false;
  syncEnables();     /* raise now, so the first ramp tick already has a bridge */
}

static void doStop(bool brake) {
  targetA = targetB = 0;
  liveA = liveB = 0;                 /* stopping is immediate, never ramped */
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

static void rampTick() {
  const unsigned long now = millis();
  const unsigned long dt = now - lastRampMs;
  if (dt < 10) return;                       /* 100 Hz is plenty */
  lastRampMs = now;

  int step = PWM_DUTY_MAX;                   /* rampMs 0 => instant */
  if (cfg.rampMs > 0) {
    step = (int)((long)PWM_DUTY_MAX * dt / cfg.rampMs);
    if (step < 1) step = 1;
  }
  bool changed = (brakingA != wroteBrakeA) || (brakingB != wroteBrakeB);
  if (liveA != targetA) {
    if (abs(targetA - liveA) <= step) liveA = targetA;
    else liveA += (targetA > liveA) ? step : -step;
    changed = true;
  }
  if (liveB != targetB) {
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

static void sendJson(int code, const String& body) {
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
  if (!server.hasArg(name)) return true;
  long v;
  if (!parseLong(server.arg(name), v)) { argFail = name; return false; }
  out = (v < lo) ? lo : (v > hi ? hi : v);
  return true;
}

/* Same, but out of range is refused rather than clamped — for values where
 * silently substituting a neighbour changes the meaning (ms=-1 clamped to 0
 * would mean "no timed stop at all", the opposite of a short move). */
static bool argRange(const char* name, long lo, long hi, long& out) {
  if (!server.hasArg(name)) return true;
  long v;
  if (!parseLong(server.arg(name), v)) { argFail = name; return false; }
  if (v < lo || v > hi) { argFail = name; return false; }
  out = v;
  return true;
}

static bool argBool(const char* name, bool& out) {
  if (!server.hasArg(name)) return true;
  const String s = server.arg(name);
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
  j += ",\"bridges\":{\"a\":" + String(enA ? "true" : "false") +
       ",\"b\":" + String(enB ? "true" : "false") + "}";
  j += ",\"failsafe_tripped\":" + String(failsafeTripped ? "true" : "false");
  j += ",\"raw_mode\":" + String(rawMode ? "true" : "false");
  j += ",\"selftest_step\":" + String(selfStep);
  j += ",\"selftest_label\":\"" + String(selfStep >= 0 && selfStep < SELF_STEPS ? SELF_LABELS[selfStep] : "idle") + "\"";
  j += ",\"free_heap\":" + String((uint32_t)ESP.getFreeHeap());
  j += ",\"arduino_core\":" + String(ESP_ARDUINO_VERSION_MAJOR);
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
  String dir = server.hasArg("dir") ? server.arg("dir") : "stop";
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
  String side = server.hasArg("side") ? server.arg("side") : "a";
  String dir  = server.hasArg("dir")  ? server.arg("dir")  : "forward";
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

  char label[24];
  snprintf(label, sizeof(label), "test:%s:%s", side.c_str(), dir.c_str());
  acceptCommand(label, ms);   /* leaves rawMode set: applyLogical(), which is
                               * what clears it, is deliberately skipped here */

  String pins = (side == "a")
    ? "{\"rpwm\":" + String(cfg.aR) + ",\"lpwm\":" + String(cfg.aL) + ",\"en\":" + String(cfg.aEn) + "}"
    : "{\"rpwm\":" + String(cfg.bR) + ",\"lpwm\":" + String(cfg.bL) + ",\"en\":" + String(cfg.bEn) + "}";
  sendJson(200, "{\"test\":{\"side\":\"" + side + "\",\"dir\":\"" + dir +
                "\",\"speed\":" + String(speed) + ",\"ms\":" + String(ms) +
                "},\"pins\":" + pins + ",\"note\":\"raw mode: swap/invert/trim bypassed\"}");
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
    if (!server.hasArg(arg)) return true;
    pinArgSeen = true;
    long v;
    if (!parseLong(server.arg(arg), v) || !pinUsable((int)v)) { argFail = arg; return false; }
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
  if (!argClamp("trim_a", 0, 100, trimA) ||
      !argClamp("trim_b", 0, 100, trimB) ||
      !argClamp("failsafe_ms", 0, 60000, fail) ||
      !argClamp("ramp_ms", 0, 3000, ramp) ||
      !argClamp("default_speed", 0, PWM_DUTY_MAX, dspd) ||
      !argClamp("min_duty", 0, MIN_DUTY_MAX, mind) ||
      !argClamp("pwm_freq", 100, 25000, freq) ||
      !argBool("swap_sides", next.swapSides) ||
      !argBool("invert_a", next.invA) ||
      !argBool("invert_b", next.invB) ||
      !argBool("brake_on_stop", next.brakeOnStop)) { sendBadArg(); return; }

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

static void handleRoot() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send_P(200, "text/html", PAGE);
}

/* ───────────────────────── setup / loop ─────────────────────────── */

static void announceSta() {
  Serial.println();
  Serial.println("=================================");
  Serial.print  ("  IRIS robot (BTS7960 x2) online:  http://");
  Serial.println(WiFi.localIP());
  Serial.println("  Calibrate:        open that address in a browser");
  Serial.println("  Register in IRIS: add device " + String(DEVICE_NAME) +
                 " at " + WiFi.localIP().toString() + " as motor");
  Serial.println("=================================");
  if (MDNS.begin(DEVICE_NAME)) MDNS.addService("http", "tcp", 80);
  staAnnounced = true;
}

/* No router, wrong password, or out of range: serve our own network so the
 * calibration page is still reachable. Blocking in setup() until a router
 * appears left the board with no HTTP server at all — the one thing guaranteed
 * to make a wiring problem impossible to diagnose. */
static void startFallbackAp() {
  const String ssid = String("iris-") + DEVICE_NAME;
  WiFi.mode(WIFI_AP_STA);
  if (!WiFi.softAP(ssid.c_str(), AP_PASSWORD)) {
    Serial.println("  could not start fallback WiFi either — check the board");
    return;
  }
  apMode = true;
  Serial.println();
  Serial.println("=================================");
  Serial.println("  No router reached. Serving my own WiFi:");
  Serial.println("    network:  " + ssid);
  Serial.println("    password: " + String(AP_PASSWORD));
  Serial.println("    then open http://" + WiFi.softAPIP().toString());
  Serial.println("  Still retrying your router in the background.");
  Serial.println("=================================");
}

void setup() {
  Serial.begin(115200);
  delay(80);
  bootMs = millis();
  lastCommandMs = millis();
  lastRampMs = millis();

  configLoad();
  armHardware();

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);                /* motor commands must not wait on power save */
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting to WiFi");
  const unsigned long joinDeadline = millis() + WIFI_JOIN_MS;
  while (WiFi.status() != WL_CONNECTED && (long)(millis() - joinDeadline) < 0) {
    delay(250);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) announceSta();
  else startFallbackAp();

  server.on("/",         handleRoot);
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
}

void loop() {
  server.handleClient();
  selfTestTick();
  rampTick();

  /* timed move finished */
  if (autoStopAt && (long)(millis() - autoStopAt) >= 0)   /* rollover-safe */
    doStop(cfg.brakeOnStop);

  /* failsafe: never keep driving into the unknown. This is the real safety net
   * — it holds whichever network the commands arrived on, and whether or not
   * any network is up at all. */
  const bool moving = (targetA != 0 || targetB != 0);
  if (moving && cfg.failsafeMs && selfStep < 0 &&
      (millis() - lastCommandMs) > cfg.failsafeMs) {
    Serial.println("[failsafe] no command in time — stopping");
    failsafeTripped = true;
    doStop(cfg.brakeOnStop);
  }

  if (WiFi.status() != WL_CONNECTED) {
    /* Losing the router is an instant stop only when the router is what was
     * carrying commands. In AP fallback there is no router to lose, and
     * stopping there would cut off someone driving from the fallback network. */
    if (!apMode && moving) {
      Serial.println("[failsafe] WiFi lost — stopping");
      failsafeTripped = true;
      doStop(cfg.brakeOnStop);
    }
    staAnnounced = false;
    static unsigned long lastRetry = 0;
    if (millis() - lastRetry > 5000) {
      lastRetry = millis();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
    }
  } else if (!staAnnounced) {
    announceSta();                     /* joined (or re-joined) the router */
  }
}
