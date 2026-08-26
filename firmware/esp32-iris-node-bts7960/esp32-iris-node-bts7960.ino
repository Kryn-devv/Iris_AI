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
 *   2. Upload, open Serial Monitor @115200, note the printed IP.
 *   3. Open http://<that-ip>/ in a browser — the CALIBRATION page.
 *   4. Press "A fwd" and "B fwd" and watch which wheels move. Use the
 *      toggles until forward is really forward and left is really left.
 *      Press SAVE. Done, permanently.
 *   5. In IRIS:  add device robot at <that-ip> as motor
 *
 *  WIRING (per BTS7960 module)
 *    RPWM  -> an ESP32 GPIO      (PWM, "this way")
 *    LPWM  -> an ESP32 GPIO      (PWM, "the other way")
 *    R_EN + L_EN  -> tied TOGETHER to one GPIO (or straight to 3.3V)
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
 *  SAFETY
 *   - Motion stops automatically if no command arrives within failsafe_ms
 *     (default 10 s) — a dropped WiFi link can never leave motors running.
 *   - Motion stops immediately if WiFi drops.
 *   - Speed ramps instead of stepping, so 4 motors cannot brown-out the board.
 * ============================================================================
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>

#include "page.h"

/* ══════════════════════ EDIT THESE TWO LINES ══════════════════════ */
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";
const char* DEVICE_NAME = "robot";      /* also becomes http://robot.local */
/* ═════════════════════════════════════════════════════════════════ */

/* ───────────────────────── PWM back end ─────────────────────────── */
/* Explicit LEDC, configured ONCE. The Arduino analogWrite() helper
 * re-runs ledcSetup() on every write, which re-initialises the shared
 * hardware timer under a running channel (glitches, and silently does
 * nothing at all when its channel pool is exhausted). Motor PWM must
 * never be that fragile. Channels 0..3 -> timers 0,0,1,1 (same freq,
 * so sharing is safe). */
#define PWM_BITS       8            /* duty 0..255 */
#define PWM_DUTY_MAX   255
#define CH_A_R 0
#define CH_A_L 1
#define CH_B_R 2
#define CH_B_L 3

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

struct Config {
  uint8_t  aR, aL, aEn;      /* side A pins */
  uint8_t  bR, bL, bEn;      /* side B pins */
  bool     swapSides;        /* true: config side A is physically the RIGHT side */
  bool     invA, invB;       /* true: positive speed spins that side backwards */
  uint8_t  trimA, trimB;     /* 0..100 % scaling, to make it drive straight */
  uint16_t pwmFreq;          /* Hz (BTS7960 tolerates <= 25 kHz) */
  uint16_t failsafeMs;       /* auto-stop when no command; 0 disables */
  uint16_t rampMs;           /* 0..255 duty ramp time; 0 = instant */
  uint8_t  defaultSpeed;
  uint8_t  minDuty;          /* below this a motor only whines; 0 = off */
  bool     brakeOnStop;      /* true = active brake, false = coast */
};

static Config cfg;
static Preferences prefs;

static void configDefaults() {
  Config& c = cfg;
  c.aR = 25; c.aL = 26; c.aEn = 27;
  c.bR = 32; c.bL = 33; c.bEn = 14;
  c.swapSides = false;
  c.invA = false; c.invB = false;
  c.trimA = 100; c.trimB = 100;
  c.pwmFreq = 20000;          /* above hearing: no motor whine */
  c.failsafeMs = 10000;
  c.rampMs = 180;
  c.defaultSpeed = 200;
  c.minDuty = 0;
  c.brakeOnStop = false;
}

/* GPIOs that must never drive a motor pin on a classic ESP32:
 * 6..11 are wired to the SPI flash (using them crashes the board),
 * 34..39 are input-only. */
static bool pinUsable(int p) {
  if (p < 0 || p > 39) return false;
  if (p >= 6 && p <= 11) return false;
  if (p >= 34) return false;
  return true;
}

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

  /* A corrupt or hand-edited pin must not brick the board on boot. */
  if (!pinUsable(cfg.aR) || !pinUsable(cfg.aL) || !pinUsable(cfg.aEn) ||
      !pinUsable(cfg.bR) || !pinUsable(cfg.bL) || !pinUsable(cfg.bEn)) {
    Serial.println("[cfg] stored pins invalid — reverting to defaults");
    configDefaults();
  }
  if (cfg.trimA > 100) cfg.trimA = 100;
  if (cfg.trimB > 100) cfg.trimB = 100;
  if (cfg.pwmFreq < 100 || cfg.pwmFreq > 25000) cfg.pwmFreq = 20000;
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
static unsigned long lastRampMs   = 0;
static unsigned long lastCommandMs = 0;
static unsigned long autoStopAt    = 0;  /* 0 = no timed stop pending */
static unsigned long bootMs        = 0;
static String lastCommand = "stop";
static bool  failsafeTripped = false;
static uint32_t commandCount = 0;

/* raw-mode bypasses swap/invert/trim: used by /test to identify hardware */
static bool rawMode = false;

/* self-test sequence */
static int  selfStep = -1;                 /* -1 = idle */
static unsigned long selfStepAt = 0;
static const char* SELF_LABELS[] = {
  "A forward", "A backward", "B forward", "B backward",
  "both forward", "spin left", "spin right", "done"
};
static const int SELF_STEPS = 7;

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

  /* EN high = module enabled. */
  pinMode(cfg.aEn, OUTPUT); digitalWrite(cfg.aEn, HIGH); claimedEn[0] = cfg.aEn;
  pinMode(cfg.bEn, OUTPUT); digitalWrite(cfg.bEn, HIGH); claimedEn[1] = cfg.bEn;

  const uint8_t pins[4]  = { cfg.aR, cfg.aL, cfg.bR, cfg.bL };
  const uint8_t chans[4] = { CH_A_R, CH_A_L, CH_B_R, CH_B_L };
  for (int i = 0; i < 4; i++) {
    pwmInit(pins[i], chans[i], cfg.pwmFreq);
    claimedPwm[i] = pins[i];
  }
  writeSide(true, 0, false);
  writeSide(false, 0, false);
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
}

static void doStop(bool brake) {
  targetA = targetB = 0;
  liveA = liveB = 0;                 /* stopping is immediate, never ramped */
  brakingA = brakingB = brake;
  rawMode = false;
  autoStopAt = 0;
  writeSide(true,  0, brake);
  writeSide(false, 0, brake);
  lastCommand = brake ? "brake" : "stop";
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
  bool changed = false;
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
    writeSide(true,  liveA, brakingA);
    writeSide(false, liveB, brakingB);
  }
}

/* ───────────────────────── self test ────────────────────────────── */

static void selfTestApply(int step) {
  const int s = cfg.defaultSpeed;
  switch (step) {
    case 0: rawMode = true; targetA =  s; targetB = 0; break;
    case 1: rawMode = true; targetA = -s; targetB = 0; break;
    case 2: rawMode = true; targetA = 0; targetB =  s; break;
    case 3: rawMode = true; targetA = 0; targetB = -s; break;
    case 4: applyLogical( s,  s); break;
    case 5: applyLogical(-s,  s); break;
    case 6: applyLogical( s, -s); break;
    default: doStop(false); break;
  }
  brakingA = brakingB = false;
}

static void selfTestTick() {
  if (selfStep < 0) return;
  if (millis() < selfStepAt) return;
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

static int argInt(const char* name, int fallback) {
  if (!server.hasArg(name)) return fallback;
  return server.arg(name).toInt();
}

static int clampSpeed(int v) {
  if (v < 0) v = 0;
  if (v > PWM_DUTY_MAX) v = PWM_DUTY_MAX;
  return v;
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
  j += ",\"driver\":\"bts7960x2\",\"firmware\":\"iris-robot-2.0\"";
  j += ",\"ip\":\"" + WiFi.localIP().toString() + "\"";
  j += ",\"rssi\":" + String(WiFi.RSSI());
  j += ",\"uptime_s\":" + String((millis() - bootMs) / 1000);
  j += ",\"motors\":true";
  j += ",\"last_direction\":\"" + lastCommand + "\"";
  j += ",\"commands\":" + String(commandCount);
  j += ",\"live\":{\"a\":" + String(liveA) + ",\"b\":" + String(liveB) + "}";
  j += ",\"target\":{\"a\":" + String(targetA) + ",\"b\":" + String(targetB) + "}";
  j += ",\"moving\":" + String((liveA || liveB) ? "true" : "false");
  j += ",\"failsafe_tripped\":" + String(failsafeTripped ? "true" : "false");
  j += ",\"selftest_step\":" + String(selfStep);
  j += ",\"selftest_label\":\"" + String(selfStep >= 0 && selfStep < SELF_STEPS ? SELF_LABELS[selfStep] : "idle") + "\"";
  j += ",\"arduino_core\":" + String(ESP_ARDUINO_VERSION_MAJOR);
  j += ",\"config\":" + configJson();
  j += "}";
  sendJson(200, j);
}

static void handleMotor() {
  String dir = server.hasArg("dir") ? server.arg("dir") : "stop";
  dir.toLowerCase();
  const int speed = clampSpeed(argInt("speed", cfg.defaultSpeed));
  const long ms = argInt("ms", 0);

  selfStep = -1;                              /* any manual command wins */

  if (dir == "stop")  { doStop(false); commandCount++; lastCommandMs = millis();
                        sendJson(200, "{\"motor\":\"stop\"}"); return; }
  if (dir == "brake") { doStop(true);  commandCount++; lastCommandMs = millis();
                        sendJson(200, "{\"motor\":\"brake\"}"); return; }

  int l, r;
  if (!directionToPair(dir, speed, l, r)) {
    sendJson(400, "{\"error\":\"dir must be forward|backward|left|right|stop|brake\"}");
    return;
  }
  applyLogical(l, r);
  lastCommand = dir;
  commandCount++;
  lastCommandMs = millis();
  failsafeTripped = false;
  autoStopAt = (ms > 0) ? millis() + (unsigned long)ms : 0;

  sendJson(200, "{\"motor\":\"" + dir + "\",\"speed\":" + String(speed) +
                ",\"ms\":" + String(ms) + ",\"left\":" + String(l) +
                ",\"right\":" + String(r) + "}");
}

static void handleTank() {
  const int l = constrain(argInt("left", 0), -PWM_DUTY_MAX, PWM_DUTY_MAX);
  const int r = constrain(argInt("right", 0), -PWM_DUTY_MAX, PWM_DUTY_MAX);
  const long ms = argInt("ms", 0);
  selfStep = -1;
  applyLogical(l, r);
  lastCommand = "tank";
  commandCount++;
  lastCommandMs = millis();
  failsafeTripped = false;
  autoStopAt = (ms > 0) ? millis() + (unsigned long)ms : 0;
  sendJson(200, "{\"tank\":{\"left\":" + String(l) + ",\"right\":" + String(r) + "}}");
}

static void handleDrive() {
  const int y = constrain(argInt("y", 0), -PWM_DUTY_MAX, PWM_DUTY_MAX);
  const int x = constrain(argInt("x", 0), -PWM_DUTY_MAX, PWM_DUTY_MAX);
  const long ms = argInt("ms", 0);
  int l = constrain(y + x, -PWM_DUTY_MAX, PWM_DUTY_MAX);
  int r = constrain(y - x, -PWM_DUTY_MAX, PWM_DUTY_MAX);
  selfStep = -1;
  applyLogical(l, r);
  lastCommand = "drive";
  commandCount++;
  lastCommandMs = millis();
  failsafeTripped = false;
  autoStopAt = (ms > 0) ? millis() + (unsigned long)ms : 0;
  sendJson(200, "{\"drive\":{\"y\":" + String(y) + ",\"x\":" + String(x) +
                "},\"left\":" + String(l) + ",\"right\":" + String(r) + "}");
}

/* RAW per-side test: deliberately ignores swap/invert/trim so the answer to
 * "which physical module and which pin pair actually responds?" is unambiguous. */
static void handleTest() {
  String side = server.hasArg("side") ? server.arg("side") : "a";
  String dir  = server.hasArg("dir")  ? server.arg("dir")  : "forward";
  side.toLowerCase(); dir.toLowerCase();
  const int speed = clampSpeed(argInt("speed", cfg.defaultSpeed));
  const long ms = argInt("ms", 1200);
  if (side != "a" && side != "b") {
    sendJson(400, "{\"error\":\"side must be a or b\"}"); return;
  }
  const int signedDuty = (dir == "backward") ? -speed : speed;

  selfStep = -1;
  rawMode = true;
  brakingA = brakingB = false;
  if (side == "a") { targetA = signedDuty; targetB = 0; }
  else             { targetA = 0; targetB = signedDuty; }
  lastCommand = "test:" + side + ":" + dir;
  commandCount++;
  lastCommandMs = millis();
  failsafeTripped = false;
  autoStopAt = (ms > 0) ? millis() + (unsigned long)ms : 0;

  String pins = (side == "a")
    ? "{\"rpwm\":" + String(cfg.aR) + ",\"lpwm\":" + String(cfg.aL) + ",\"en\":" + String(cfg.aEn) + "}"
    : "{\"rpwm\":" + String(cfg.bR) + ",\"lpwm\":" + String(cfg.bL) + ",\"en\":" + String(cfg.bEn) + "}";
  sendJson(200, "{\"test\":{\"side\":\"" + side + "\",\"dir\":\"" + dir +
                "\",\"speed\":" + String(speed) + ",\"ms\":" + String(ms) +
                "},\"pins\":" + pins + ",\"note\":\"raw mode: swap/invert/trim bypassed\"}");
}

static void handleSelfTest() {
  selfStep = 0;
  selfTestApply(0);
  selfStepAt = millis() + 900;
  lastCommandMs = millis();
  failsafeTripped = false;
  sendJson(200, "{\"selftest\":\"started\",\"steps\":" + String(SELF_STEPS) +
                ",\"note\":\"poll /status for selftest_label\"}");
}

static void handleStop() {
  selfStep = -1;
  doStop(false);
  commandCount++;
  lastCommandMs = millis();
  sendJson(200, "{\"motor\":\"stop\"}");
}

static void handleConfig() {
  bool pinsChanged = false;
  String rejected = "";

  auto setPin = [&](const char* arg, uint8_t& field) {
    if (!server.hasArg(arg)) return;
    const int v = server.arg(arg).toInt();
    if (pinUsable(v)) { field = (uint8_t)v; pinsChanged = true; }
    else { rejected += String(rejected.length() ? "," : "") + arg; }
  };
  setPin("a_rpwm", cfg.aR); setPin("a_lpwm", cfg.aL); setPin("a_en", cfg.aEn);
  setPin("b_rpwm", cfg.bR); setPin("b_lpwm", cfg.bL); setPin("b_en", cfg.bEn);

  if (server.hasArg("swap_sides")) cfg.swapSides = server.arg("swap_sides").toInt() != 0;
  if (server.hasArg("invert_a"))   cfg.invA = server.arg("invert_a").toInt() != 0;
  if (server.hasArg("invert_b"))   cfg.invB = server.arg("invert_b").toInt() != 0;
  if (server.hasArg("trim_a"))     cfg.trimA = (uint8_t)constrain(server.arg("trim_a").toInt(), 0, 100);
  if (server.hasArg("trim_b"))     cfg.trimB = (uint8_t)constrain(server.arg("trim_b").toInt(), 0, 100);
  if (server.hasArg("failsafe_ms"))cfg.failsafeMs = (uint16_t)constrain(server.arg("failsafe_ms").toInt(), 0, 60000);
  if (server.hasArg("ramp_ms"))    cfg.rampMs = (uint16_t)constrain(server.arg("ramp_ms").toInt(), 0, 3000);
  if (server.hasArg("default_speed")) cfg.defaultSpeed = (uint8_t)clampSpeed(server.arg("default_speed").toInt());
  if (server.hasArg("min_duty"))   cfg.minDuty = (uint8_t)clampSpeed(server.arg("min_duty").toInt());
  if (server.hasArg("brake_on_stop")) cfg.brakeOnStop = server.arg("brake_on_stop").toInt() != 0;

  bool freqChanged = false;
  if (server.hasArg("pwm_freq")) {
    const uint16_t f = (uint16_t)constrain(server.arg("pwm_freq").toInt(), 100, 25000);
    if (f != cfg.pwmFreq) { cfg.pwmFreq = f; freqChanged = true; }
  }

  /* Two functions sharing one GPIO is never valid: two LEDC channels fighting
   * over a pin gives garbage duty, and RPWM==LPWM makes direction meaningless.
   * Reject the whole pin change rather than half-applying it. */
  if (pinsChanged) {
    const uint8_t all[6] = { cfg.aR, cfg.aL, cfg.aEn, cfg.bR, cfg.bL, cfg.bEn };
    for (int i = 0; i < 6 && !rejected.length(); i++)
      for (int j = i + 1; j < 6; j++)
        if (all[i] == all[j]) { rejected = "duplicate_pin"; break; }
    if (rejected == "duplicate_pin") {
      configLoad();                     /* discard this request entirely */
      armHardware();
      sendJson(400, "{\"error\":\"two functions cannot share one GPIO\",\"config\":" +
                    configJson() + "}");
      return;
    }
  }

  /* Changing pins or frequency means re-arming the PWM hardware. Stop first
   * so a motor can never be left latched on an abandoned pin. */
  if (pinsChanged || freqChanged) {
    doStop(false);
    armHardware();
  }

  String j = "{\"ok\":true,\"rearmed\":" + String((pinsChanged || freqChanged) ? "true" : "false");
  if (rejected.length()) j += ",\"rejected_pins\":\"" + rejected + "\"";
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
  configDefaults();
  configSave();
  armHardware();
  sendJson(200, "{\"reset\":true,\"config\":" + configJson() + "}");
}

/* ───────────────────────── dashboard ────────────────────────────── */



static void handleRoot() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send_P(200, "text/html", PAGE);
}

/* ───────────────────────── setup / loop ─────────────────────────── */

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
  uint32_t spins = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(400); Serial.print(".");
    if (++spins % 75 == 0) { Serial.println("\n  still trying (check SSID/password)"); WiFi.begin(WIFI_SSID, WIFI_PASS); }
  }
  Serial.println();
  Serial.println("=================================");
  Serial.print  ("  IRIS robot (BTS7960 x2) online:  http://");
  Serial.println(WiFi.localIP());
  Serial.println("  Calibrate:        open that address in a browser");
  Serial.println("  Register in IRIS: add device " + String(DEVICE_NAME) + " at " + WiFi.localIP().toString() + " as motor");
  Serial.println("=================================");

  if (MDNS.begin(DEVICE_NAME)) MDNS.addService("http", "tcp", 80);

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
  server.begin();
}

void loop() {
  server.handleClient();
  selfTestTick();
  rampTick();

  /* timed move finished */
  if (autoStopAt && millis() >= autoStopAt) doStop(cfg.brakeOnStop);

  /* failsafe: never keep driving into the unknown */
  const bool moving = (targetA != 0 || targetB != 0);
  if (moving && cfg.failsafeMs && selfStep < 0 &&
      (millis() - lastCommandMs) > cfg.failsafeMs) {
    Serial.println("[failsafe] no command in time — stopping");
    failsafeTripped = true;
    doStop(cfg.brakeOnStop);
  }
  if (WiFi.status() != WL_CONNECTED) {
    if (moving) {
      Serial.println("[failsafe] WiFi lost — stopping");
      failsafeTripped = true;
      doStop(cfg.brakeOnStop);
    }
    static unsigned long lastRetry = 0;
    if (millis() - lastRetry > 3000) { lastRetry = millis(); WiFi.reconnect(); }
  }
}
