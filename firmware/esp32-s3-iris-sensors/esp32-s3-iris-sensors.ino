/*
 * ============================================================================
 *  IRIS S3 NODE  —  the robot's face, senses and voice on one ESP32-S3
 * ============================================================================
 *
 *  WHAT RUNS WHERE
 *  IRIS itself — the agent loop, the LLM gateway, the voice pipeline — is a
 *  Python application. It runs on your PC or on a VPS. It cannot run on this
 *  chip and does not need to: this board is the robot's face, senses and
 *  voice, and it talks to IRIS over the network. One brain, many bodies.
 *
 *  WHAT THIS BOARD DOES
 *    - two 128x64 SSD1306 OLEDs as expressive eyes (14 emotions, blinking,
 *      idle glances, breathing, and a bounce while IRIS speaks)
 *    - PIR motion, MQ-2 gas, LDR light, flame, TWO HC-SR04 distance sensors
 *      (front and rear), DHT11/DHT22 temperature and humidity
 *    - an I2S microphone and speaker: talk to it, it answers out loud
 *    - a web dashboard for testing all of it with nothing installed
 *
 *  ── TWO WAYS IT REACHES IRIS ───────────────────────────────────────────────
 *
 *  A. IRIS ON YOUR OWN NETWORK (PC, or a Pi at home)
 *     Leave CLOUD_HOST empty. IRIS calls this board's IP. Simplest, fastest,
 *     nothing exposed. Register with:  add device face at <ip> as face
 *
 *  B. IRIS ON A VPS (the cloud)
 *     Set CLOUD_HOST / CLOUD_TOKEN. This board then dials OUT to IRIS and
 *     holds a WebSocket open; commands come back down it. That is the only way
 *     round that works: this board is behind your router's NAT, so there is no
 *     address the internet can call. Outbound connections are exactly what NAT
 *     allows, so this needs no port-forwarding, no static IP, no dynamic DNS.
 *     IRIS registers the device by itself the moment it connects.
 *
 *  Both can be on at once. The local web page keeps working either way, which
 *  is what makes a wiring fault diagnosable when the cloud link is down.
 *
 *  ── SENSORS: what gets sent, and when ──────────────────────────────────────
 *    every ~5 s          all readings, so "any motion?" needs no round trip
 *    the moment it moves motion appearing, distance changing a lot
 *    immediately         flame or gas — IRIS says it out loud without asking
 *
 *  ── WIRING WARNINGS ────────────────────────────────────────────────────────
 *  ESP32-S3 pins are 3.3V and NOT 5V tolerant.
 *    HC-SR04 ECHO outputs 5V  -> divider: ECHO --[1k]--+--[2k]-- GND, tap +
 *                                (BOTH of them — one divider each)
 *    MQ-2 AO can reach ~4V    -> same 1k/2k divider on AO
 *    PIR HC-SR501 out is 3.3V — direct. Flame module DO is 3.3V — direct.
 *    DHT11/DHT22 DATA is 3.3V — direct, and its VCC goes to 3.3V not 5V.
 *  Analog sensors must be on GPIO 1..10 (ADC1). GPIO 11..20 are ADC2, which
 *  stops working once WiFi is up and silently returns garbage; setup() warns.
 *
 *  HTTP API (also reachable as commands over the cloud link)
 *    GET /                     dashboard
 *    GET /status               identity, face, link and sensor state
 *    GET /sensors              fresh readings JSON
 *    GET /face?emotion=happy[&hold_ms=][&speak_ms=][&look_x=][&look_y=][&blink=]
 *    GET /face/list            the emotions this firmware knows
 *    GET /speak?ms=2500        talking bounce for N ms (0 stops)
 *    GET /look?x=-100..100&y=-100..100
 *    GET /blink[?count=2]
 *
 *  Every numeric argument is parsed strictly: a typo is an error, never 0.
 * ============================================================================
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define FIRMWARE_VERSION "iris-s3-node-2.0"

#include "eyes.h"
#include "face.h"
#include "sensors.h"
#include "cloud.h"
#include "voice.h"
#include "page.h"

/* ══════════════════════════ CONFIG ══════════════════════════ */

const char* WIFI_SSID   = "YOUR_WIFI_NAME";
const char* WIFI_PASS   = "YOUR_WIFI_PASSWORD";
const char* DEVICE_NAME = "face";
const char* AP_PASSWORD = "iriscalib";     /* fallback network, min 8 chars */

/* ── B. IRIS in the cloud. Leave CLOUD_HOST empty for a LAN-only setup. ── */
const char* CLOUD_HOST  = "";              /* "iris.example.com" or an IP    */
const uint16_t CLOUD_PORT = 443;           /* 443 for https/wss, else yours  */
const bool CLOUD_TLS    = true;            /* false only on your own LAN     */
/* Certificate checking, for BOTH the link and the voice upload.
 *
 * Empty: TLS still encrypts, but nothing authenticates the server — safe from a
 * passive listener, wide open to an active one. Paste your server's CA (PEM,
 * including the BEGIN/END lines) to close that. The board reports which of the
 * two it got at boot rather than letting "TLS is on" imply the stronger one. */
const char* CLOUD_CA_CERT = "";
const char* CLOUD_TOKEN = "";              /* must equal NODE_LINK_TOKEN     */

/* ── the eyes ── */
const bool SHARED_BUS   = false;  /* true only if you moved one OLED to 0x3D */
const int  PIN_L_SDA    = 9;      /* left eye  */
const int  PIN_L_SCL    = 10;
const int  PIN_R_SDA    = 11;     /* right eye (ignored when SHARED_BUS)     */
const int  PIN_R_SCL    = 12;
const uint8_t OLED_ADDR_L = 0x3C;
const uint8_t OLED_ADDR_R = 0x3C;  /* set to 0x3D when SHARED_BUS is true    */
const uint32_t I2C_HZ   = 800000;  /* 400000 if an eye ever glitches         */
const bool SWAP_EYES    = false;   /* true if left/right came out reversed   */

/* ── the sensors ──  set a pin to -1 to disable one you have not wired ── */
const int PIN_PIR       = 4;      /* HC-SR501 OUT (digital)                  */
const int PIN_GAS_ADC   = 5;      /* MQ-2 AO through divider  (ADC1: 1..10)  */
const int PIN_LDR_ADC   = 6;      /* LDR divider midpoint     (ADC1: 1..10)  */
const int PIN_US_TRIG   = 7;      /* HC-SR04 #1 (front) TRIG                 */
const int PIN_US_ECHO   = 8;      /* HC-SR04 #1 (front) ECHO through divider */
const int PIN_US_TRIG2  = 38;     /* HC-SR04 #2 (rear) TRIG,  -1 if unfitted */
const int PIN_US_ECHO2  = 39;     /* HC-SR04 #2 (rear) ECHO through divider  */
const int PIN_DHT       = 40;     /* DHT11/DHT22 DATA (digital)              */
const uint8_t DHT_KIND  = DHT11;  /* DHT11 (blue) or DHT22 (white)           */
const int PIN_FLAME     = 13;     /* flame module DO (digital)               */
const bool FLAME_ACTIVE_LOW = true;  /* most IR flame modules pull DO LOW    */
const int GAS_ALARM_RAW = 1800;   /* watch /sensors in clean air, add ~800   */

/* ── the voice (I2S mic + I2S amplifier) ── set to -1 to leave one out ── */
const int PIN_MIC_SCK   = 14;     /* INMP441 SCK                             */
const int PIN_MIC_WS    = 15;     /* INMP441 WS                              */
const int PIN_MIC_DATA  = 16;     /* INMP441 SD                              */
const int PIN_AMP_BCLK  = 17;     /* MAX98357A BCLK                          */
const int PIN_AMP_LRC   = 18;     /* MAX98357A LRC                           */
const int PIN_AMP_DATA  = 21;     /* MAX98357A DIN                           */
const int PIN_PTT       = -1;     /* optional push-to-talk button to GND     */
const uint8_t MIC_GAIN  = 4;      /* raise if IRIS mishears, lower if it clips */

#define WIFI_JOIN_MS 25000UL      /* then fall back to our own network */

/* ══════════════════════════ STATE ══════════════════════════ */

WebServer server(80);
Adafruit_SSD1306 eyeLeft(EYE_W, EYE_H, &Wire, -1);
Adafruit_SSD1306 eyeRight(EYE_W, EYE_H, SHARED_BUS ? &Wire : &Wire1, -1);
FaceAnimator face;
Sensors sensors;
CloudLink cloud;
NodeVoice voice;

unsigned long bootMillis = 0;
bool eyeLeftOk = false, eyeRightOk = false;
bool apMode = false, staAnnounced = false;
uint16_t framesLastSecond = 0, fps = 0;
unsigned long fpsWindowMs = 0;
SensorReading lastReading;
bool lastDanger = false;
bool lastMotionRecent = false;
long lastDistanceSent = -1;
long lastRearSent = -1;

/* ═════════════════════ argument access ═════════════════════ */

/* The same command arrives two ways — as HTTP query parameters, and as a
 * params object over the cloud socket. Both are presented through this, so
 * every endpoint below is written once and works over either transport. */
class Args {
 public:
  static Args fromServer() {
    Args a;
    a.fromServer_ = true;
    return a;
  }

  static Args fromQuery(const String& query) {
    Args a;
    a.fromServer_ = false;
    int at = 0;
    while (at < (int)query.length() && a.count_ < MAX_ARGS) {
      int amp = query.indexOf('&', at);
      if (amp < 0) amp = query.length();
      const int eq = query.indexOf('=', at);
      if (eq > at && eq < amp) {
        a.keys_[a.count_] = urlDecode(query.substring(at, eq));
        a.values_[a.count_] = urlDecode(query.substring(eq + 1, amp));
        a.count_++;
      }
      at = amp + 1;
    }
    return a;
  }

  bool has(const char* name) const {
    if (fromServer_) return server.hasArg(name);
    for (uint8_t i = 0; i < count_; i++) if (keys_[i] == name) return true;
    return false;
  }

  String get(const char* name) const {
    if (fromServer_) return server.arg(name);
    for (uint8_t i = 0; i < count_; i++) if (keys_[i] == name) return values_[i];
    return "";
  }

 private:
  static String urlDecode(const String& text) {
    String out;
    for (int i = 0; i < (int)text.length(); i++) {
      const char c = text[i];
      if (c == '+') { out += ' '; }
      else if (c == '%' && i + 2 < (int)text.length()) {
        out += (char)strtol(text.substring(i + 1, i + 3).c_str(), nullptr, 16);
        i += 2;
      } else out += c;
    }
    return out;
  }

  static const uint8_t MAX_ARGS = 8;
  bool fromServer_ = true;
  uint8_t count_ = 0;
  String keys_[MAX_ARGS];
  String values_[MAX_ARGS];
};

struct CmdResult {
  int code;
  String body;
};

/* String::toInt() answers 0 for "", "abc" and "twelve". A silent 0 here means
 * "look dead ahead" or "stop speaking" — the opposite of what was asked. */
static bool parseLong(const String& s, long& out) {
  const int n = s.length();
  if (n == 0 || n > 11) return false;
  int i = 0;
  bool neg = false;
  if (s[0] == '+' || s[0] == '-') { neg = (s[0] == '-'); i = 1; }
  if (i >= n) return false;
  long v = 0;
  for (; i < n; i++) {
    if (s[i] < '0' || s[i] > '9') return false;
    v = v * 10 + (s[i] - '0');
    if (v > 2000000L) return false;
  }
  out = neg ? -v : v;
  return true;
}

/* Present but malformed => false. Absent => keep the default. Out of range is
 * clamped: look_x=500 plainly means "hard right". */
static bool argClamp(const Args& args, const char* name, long lo, long hi,
                     long& out, String& bad) {
  if (!args.has(name)) return true;
  long v;
  if (!parseLong(args.get(name), v)) { bad = name; return false; }
  out = (v < lo) ? lo : (v > hi ? hi : v);
  return true;
}

/* ═════════════════════════ endpoints ═════════════════════════ */

static String sensorsJson() {
  return sensors.toJson(lastReading, (millis() - bootMillis) / 1000);
}

static String faceJson() {
  const unsigned long now = millis();
  String j = "{\"emotion\":\"" + String(face.emotionName()) + "\"";
  j += ",\"speaking\":" + String(face.speaking(now) ? "true" : "false");
  j += ",\"dozing\":" + String(face.dozing ? "true" : "false");
  j += ",\"look_x\":" + String(face.gazeX) + ",\"look_y\":" + String(face.gazeY);
  j += ",\"eyes_ok\":" + String((eyeLeftOk && eyeRightOk) ? "true" : "false");
  j += ",\"left_eye_ok\":" + String(eyeLeftOk ? "true" : "false");
  j += ",\"right_eye_ok\":" + String(eyeRightOk ? "true" : "false");
  j += ",\"fps\":" + String(fps);
  j += ",\"commands\":" + String(face.commandCount);
  j += "}";
  return j;
}

static String linkJson() {
  String j = "{\"cloud_configured\":" + String(cloud.enabled() ? "true" : "false");
  j += ",\"cloud_linked\":" + String(cloud.connected() ? "true" : "false");
  j += ",\"cloud_commands\":" + String(cloud.commandsHandled());
  j += ",\"telemetry_sent\":" + String(cloud.telemetrySent());
  j += ",\"mic\":" + String(voice.micReady() ? "true" : "false");
  j += ",\"speaker\":" + String(voice.speakerReady() ? "true" : "false");
  j += ",\"listening\":" + String(voice.capturing() ? "true" : "false");
  j += ",\"exchanges\":" + String(voice.exchanges());
  j += ",\"voice_failures\":" + String(voice.failures());
  j += "}";
  return j;
}

static String statusJson() {
  String j = "{\"name\":\"" + String(DEVICE_NAME) + "\",\"kind\":\"face\"";
  j += ",\"firmware\":\"" + String(FIRMWARE_VERSION) + "\"";
  j += ",\"ip\":\"" + (apMode && WiFi.status() != WL_CONNECTED
                        ? WiFi.softAPIP().toString() : WiFi.localIP().toString()) + "\"";
  j += ",\"link\":\"" + String(WiFi.status() == WL_CONNECTED ? "sta" : (apMode ? "ap" : "down")) + "\"";
  j += ",\"ap_mode\":" + String(apMode ? "true" : "false");
  j += ",\"rssi\":" + String(WiFi.RSSI());
  j += ",\"uptime_s\":" + String((millis() - bootMillis) / 1000);
  j += ",\"free_heap\":" + String((uint32_t)ESP.getFreeHeap());
  j += ",\"face\":" + faceJson();
  j += ",\"cloud\":" + linkJson();
  j += ",\"sensors\":" + sensors.namesJson();
  j += ",\"readings\":" + sensorsJson();
  j += "}";
  return j;
}

static CmdResult badArg(const String& name) {
  return {400, "{\"error\":\"bad value for '" + name +
               "'\",\"hint\":\"must be a whole number\"}"};
}

static CmdResult cmdFace(const Args& args) {
  const unsigned long now = millis();
  String wanted = args.has("emotion") ? args.get("emotion")
                : (args.has("e") ? args.get("e") : "");

  long holdMs = 0, speakMs = -1, lookX = face.gazeX, lookY = face.gazeY, blink = 0;
  String bad;
  if (!argClamp(args, "hold_ms", 0, 600000, holdMs, bad) ||
      !argClamp(args, "speak_ms", 0, (long)SPEAK_MAX_MS, speakMs, bad) ||
      !argClamp(args, "look_x", -100, 100, lookX, bad) ||
      !argClamp(args, "look_y", -100, 100, lookY, bad) ||
      !argClamp(args, "blink", 0, 5, blink, bad)) {
    return badArg(bad);
  }

  /* Validated before anything is applied, so a typo leaves the face exactly
   * as it was instead of half-changing it. */
  bool known = false;
  uint8_t emo = EMO_NEUTRAL;
  if (wanted.length()) {
    emo = emotionFromName(wanted, &known);
    if (!known) {
      return {400, "{\"error\":\"unknown emotion '" + wanted +
                   "'\",\"hint\":\"GET /face/list for the full set\"}"};
    }
  }

  if (known) face.setEmotion(emo, (uint32_t)holdMs, now);
  if (args.has("look_x") || args.has("look_y"))
    face.look((int16_t)lookX, (int16_t)lookY, now);
  if (speakMs >= 0) face.setSpeaking((uint32_t)speakMs, now);
  if (blink > 0) face.blinkNow(now, (uint8_t)blink);

  return {200, "{\"ok\":true,\"face\":" + faceJson() + "}"};
}

static CmdResult cmdFaceList() {
  String j = "{\"emotions\":[";
  for (uint8_t i = 0; i < EMO_COUNT; i++) {
    if (i) j += ",";
    j += "\"" + String(EMOTION_NAMES[i]) + "\"";
  }
  j += "],\"count\":" + String(EMO_COUNT) + "}";
  return {200, j};
}

static CmdResult cmdSpeak(const Args& args) {
  long ms = 2000;
  String bad;
  if (!argClamp(args, "ms", 0, (long)SPEAK_MAX_MS, ms, bad)) return badArg(bad);
  face.setSpeaking((uint32_t)ms, millis());
  return {200, "{\"ok\":true,\"face\":" + faceJson() + "}"};
}

static CmdResult cmdLook(const Args& args) {
  long x = face.gazeX, y = face.gazeY;
  String bad;
  if (!argClamp(args, "x", -100, 100, x, bad) ||
      !argClamp(args, "y", -100, 100, y, bad)) return badArg(bad);
  face.look((int16_t)x, (int16_t)y, millis());
  return {200, "{\"ok\":true,\"face\":" + faceJson() + "}"};
}

static CmdResult cmdBlink(const Args& args) {
  long count = 1;
  String bad;
  if (!argClamp(args, "count", 1, 5, count, bad)) return badArg(bad);
  face.blinkNow(millis(), (uint8_t)count);
  return {200, "{\"ok\":true}"};
}

/* The single place a command is interpreted, whichever transport delivered it. */
static CmdResult dispatch(const String& path, const Args& args) {
  if (path == "/status")    return {200, statusJson()};
  if (path == "/sensors")   return {200, sensorsJson()};
  if (path == "/face")      return cmdFace(args);
  if (path == "/face/list") return cmdFaceList();
  if (path == "/speak")     return cmdSpeak(args);
  if (path == "/look")      return cmdLook(args);
  if (path == "/blink")     return cmdBlink(args);
  return {404, "{\"error\":\"unknown endpoint '" + path + "'\"}"};
}

/* ═════════════════════════ HTTP glue ═════════════════════════ */

static void sendJson(int code, const String& body) {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(code, "application/json", body);
}

static void serveDispatch(const char* path) {
  const CmdResult result = dispatch(path, Args::fromServer());
  sendJson(result.code, result.body);
}

static void handleRoot() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send_P(200, "text/html", FACE_PAGE);
}

/* ═════════════════════════ cloud glue ═════════════════════════ */

static bool cloudCommand(const String& path, const String& query, String& out) {
  const CmdResult result = dispatch(path, Args::fromQuery(query));
  out = result.body;
  return result.code < 400;
}

/* ═══════════════════════════ display ═══════════════════════════ */

static bool startEye(Adafruit_SSD1306& d, uint8_t addr, const char* label) {
  /* periphBegin=false: the bus is already up on OUR pins, and letting the
   * library call Wire.begin() again would reset it to the default pins. */
  if (!d.begin(SSD1306_SWITCHCAPVCC, addr, true, false)) {
    Serial.printf("  [eyes] %s OLED did NOT answer at 0x%02X\n", label, addr);
    return false;
  }
  d.clearDisplay();
  d.display();
  return true;
}

static void startEyes() {
  Wire.begin(PIN_L_SDA, PIN_L_SCL, I2C_HZ);
  if (!SHARED_BUS) Wire1.begin(PIN_R_SDA, PIN_R_SCL, I2C_HZ);

  eyeLeftOk  = startEye(eyeLeft,  OLED_ADDR_L, "left");
  eyeRightOk = startEye(eyeRight, SHARED_BUS ? OLED_ADDR_R : OLED_ADDR_L, "right");

  if (!eyeLeftOk || !eyeRightOk) {
    Serial.println("  [eyes] check VCC/GND/SDA/SCL. Two modules on ONE bus both");
    Serial.println("         at 0x3C cannot work — use the two-bus wiring, or");
    Serial.println("         move one to 0x3D and set SHARED_BUS = true.");
  }
}

static void drawFace(const EyePose& left, const EyePose& right) {
  Adafruit_SSD1306& lDisp = SWAP_EYES ? eyeRight : eyeLeft;
  Adafruit_SSD1306& rDisp = SWAP_EYES ? eyeLeft  : eyeRight;
  const bool lOk = SWAP_EYES ? eyeRightOk : eyeLeftOk;
  const bool rOk = SWAP_EYES ? eyeLeftOk  : eyeRightOk;

  if (lOk) {
    lDisp.clearDisplay();
    drawEye(lDisp, left, true);
    lDisp.display();
  }
  if (rOk) {
    rDisp.clearDisplay();
    drawEye(rDisp, right, false);
    rDisp.display();
  }
}

/* Pumped from inside the voice code too, so the eyes keep moving while the
 * board is uploading a phrase or playing a reply. */
static void animateOnce() {
  EyePose left, right;
  if (face.tick(millis(), left, right)) {
    drawFace(left, right);
    framesLastSecond++;
  }
}

static void onReplyStarting(uint32_t ms) {
  /* The reply is about to play, so start the talking bounce for its length —
   * bounded by the firmware, so a lost packet cannot leave it bouncing. */
  face.setSpeaking(ms, millis());
}

/* ═══════════════════════════ WiFi ═══════════════════════════ */

static void announceSta() {
  Serial.println();
  Serial.println("=================================");
  Serial.print  ("  IRIS S3 node online:  http://");
  Serial.println(WiFi.localIP());
  Serial.println("  Test everything:  open that address in a browser");
  if (cloud.enabled()) {
    Serial.println("  Cloud link:       dialling " + cloud.host() + ":" +
                   String(cloud.port()) + (cloud.tls() ? " (wss)" : " (ws)") +
                   " — IRIS registers me itself");
    if (cloud.corrections().length())
      Serial.println("  CLOUD_HOST fixed: " + cloud.corrections());
  } else {
    Serial.println("  Register in IRIS: add device " + String(DEVICE_NAME) +
                   " at " + WiFi.localIP().toString() + " as face");
  }
  Serial.println("=================================");
  if (MDNS.begin(DEVICE_NAME)) MDNS.addService("http", "tcp", 80);
  staAnnounced = true;
}

/* Blocking in setup() until a router appears left the board with no web server
 * at all — the one thing needed to work out why a sensor or an eye is silent. */
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

/* ═══════════════════════════ setup ═══════════════════════════ */

static void warnAboutAdc2(const char* label, int pin) {
  if (pin >= 11 && pin <= 20)
    Serial.printf("  [warn] %s is on GPIO %d (ADC2) — ADC2 does not work while\n"
                  "         WiFi is on. Move it to GPIO 1..10.\n", label, pin);
}

void setup() {
  Serial.begin(115200);
  delay(120);
  bootMillis = millis();
  fpsWindowMs = millis();

  SensorConfig sensorCfg;
  sensorCfg.pins = {PIN_PIR, PIN_GAS_ADC, PIN_LDR_ADC, PIN_FLAME,
                    PIN_US_TRIG, PIN_US_ECHO, PIN_US_TRIG2, PIN_US_ECHO2, PIN_DHT};
  sensorCfg.flameActiveLow = FLAME_ACTIVE_LOW;
  sensorCfg.gasAlarmRaw = GAS_ALARM_RAW;
  sensorCfg.motionHoldMs = 30000;
  /* Per SLOT, and the two ultrasonics alternate slots — so each one is actually
   * measured every 500 ms, which is plenty for an obstacle check. */
  sensorCfg.distanceEveryMs = 250;
  sensorCfg.climateEveryMs = 2500;   /* a DHT11 refuses to be read faster */
  sensorCfg.dhtType = DHT_KIND;
  sensors.begin(sensorCfg);
  warnAboutAdc2("gas sensor", PIN_GAS_ADC);
  warnAboutAdc2("light sensor", PIN_LDR_ADC);
  if (PIN_US_TRIG2 >= 0) Serial.println("  Two ultrasonics fitted — readings are staggered.");
  if (PIN_DHT >= 0) Serial.printf("  DHT%s on GPIO %d\n", DHT_KIND == DHT11 ? "11" : "22", PIN_DHT);

  startEyes();
  face.begin(millis());

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting to WiFi");
  const unsigned long joinDeadline = millis() + WIFI_JOIN_MS;
  while (WiFi.status() != WL_CONNECTED && (long)(millis() - joinDeadline) < 0) {
    /* Keep animating while we wait — a face frozen at boot looks broken, and
     * this is exactly when someone is staring at it wondering. */
    animateOnce();
    delay(5);
  }
  if (WiFi.status() == WL_CONNECTED) announceSta();
  else startFallbackAp();

  server.on("/",          handleRoot);
  /* Browsers ask for /favicon.ico on every page load. Without a handler each
   * one becomes a 404 plus an "[E] request handler not found" line, which
   * looks like a fault and is not one — and on a weak link that wasted round
   * trip competes with the 700 ms status poll the page depends on. 204 is the
   * correct answer: "there is no icon, stop asking." */
  server.on("/favicon.ico", []() { server.send(204); });
  server.on("/status",    []() { serveDispatch("/status"); });
  server.on("/sensors",   []() { serveDispatch("/sensors"); });
  server.on("/face",      []() { serveDispatch("/face"); });
  server.on("/face/list", []() { serveDispatch("/face/list"); });
  server.on("/speak",     []() { serveDispatch("/speak"); });
  server.on("/look",      []() { serveDispatch("/look"); });
  server.on("/blink",     []() { serveDispatch("/blink"); });
  server.onNotFound([]() { sendJson(404, "{\"error\":\"unknown endpoint\"}"); });
  server.begin();      /* unconditional: the dashboard must exist even with no
                        * router, or a wiring fault cannot be diagnosed */

  lastReading = sensors.read(millis());

  /* Dial out to IRIS, if a cloud host is configured. */
  cloud.helloSensors_ = sensors.namesJson();
  cloud.begin(CLOUD_HOST, CLOUD_PORT, "/api/v1/nodes/link", CLOUD_TOKEN,
              DEVICE_NAME, "face", CLOUD_TLS, cloudCommand, CLOUD_CA_CERT);
  if (cloud.enabled() && !cloud.tls()) {
    Serial.println("  [warn] the cloud link is unencrypted (CLOUD_TLS=false).");
    Serial.println("         Fine on your own LAN; over the internet the token");
    Serial.println("         travels in clear text. Use 443 and TLS instead.");
  } else if (cloud.enabled() && !cloud.verified()) {
    Serial.println("  [warn] TLS is on but the certificate is NOT checked.");
    Serial.println("         Safe from someone merely listening; a man in the");
    Serial.println("         middle could still present his own certificate and");
    Serial.println("         read the token. Paste your server's CA into");
    Serial.println("         CLOUD_CA_CERT to close that.");
  }
  if (CLOUD_HOST[0] != '\0' && CLOUD_TOKEN[0] == '\0') {
    Serial.println("  [warn] CLOUD_HOST is set but CLOUD_TOKEN is empty — the");
    Serial.println("         link will be refused. Copy NODE_LINK_TOKEN here.");
  }

  /* Microphone and speaker. */
  VoiceConfig voiceCfg;
  voiceCfg.micSck = PIN_MIC_SCK;
  voiceCfg.micWs = PIN_MIC_WS;
  voiceCfg.micData = PIN_MIC_DATA;
  voiceCfg.ampBclk = PIN_AMP_BCLK;
  voiceCfg.ampLrc = PIN_AMP_LRC;
  voiceCfg.ampData = PIN_AMP_DATA;
  voiceCfg.buttonPin = PIN_PTT;
  voiceCfg.gain = MIC_GAIN;
  voiceCfg.host = CLOUD_HOST;
  voiceCfg.port = CLOUD_PORT;
  voiceCfg.tls = CLOUD_TLS;
  voiceCfg.tlsVerify = (CLOUD_CA_CERT[0] != '\0');
  voiceCfg.caCert = CLOUD_CA_CERT;
  voiceCfg.token = CLOUD_TOKEN;
  voiceCfg.node = DEVICE_NAME;
  voice.onTick(animateOnce);
  voice.onSpeaking(onReplyStarting);
  voice.begin(voiceCfg);
  if (voice.micReady()) Serial.println("  Microphone ready — just talk to it.");
  if (voice.speakerReady()) Serial.println("  Speaker ready.");

  /* A wave hello, so you know it booted. */
  face.setEmotion(EMO_EXCITED, 1800, millis());
  face.blinkNow(millis(), 2);
}

/* ═══════════════════════════ loop ═══════════════════════════ */

void loop() {
  const unsigned long now = millis();

  server.handleClient();
  cloud.loop();
  sensors.tick(now);
  animateOnce();
  voice.loop(now);

  /* Fresh readings, and whether anything changed enough to report early. */
  lastReading = sensors.read(now);
  const bool danger = Sensors::isDangerous(lastReading);
  bool changed = false;
  if (lastReading.motionRecent != lastMotionRecent) {
    lastMotionRecent = lastReading.motionRecent;
    changed = true;
  }
  if (lastReading.hasDistance) {
    if (lastDistanceSent < 0 || labs(lastReading.distanceCm - lastDistanceSent) > 8) {
      lastDistanceSent = lastReading.distanceCm;
      changed = true;
    }
  }
  if (lastReading.hasDistance2) {
    if (lastRearSent < 0 || labs(lastReading.distanceCm2 - lastRearSent) > 8) {
      lastRearSent = lastReading.distanceCm2;
      changed = true;
    }
  }

  /* An alert is an interruption, not a reading: sent the instant it appears,
   * once per appearance, so a sensor on its threshold cannot spam. */
  if (danger && !lastDanger) {
    if (lastReading.hasFlame && lastReading.flame) {
      Serial.println("[alert] FLAME DETECTED");
      cloud.sendAlert("flame", "flame sensor triggered");
      face.setEmotion(EMO_SURPRISED, 8000, now);
    } else if (lastReading.hasGas && lastReading.gasAlarm) {
      Serial.println("[alert] gas above the alarm level");
      cloud.sendAlert("gas", "gas above the alarm level");
      face.setEmotion(EMO_SURPRISED, 8000, now);
    }
    changed = true;
  }
  lastDanger = danger;

  cloud.sendTelemetry(sensorsJson(), now, changed);

  if ((unsigned long)(now - fpsWindowMs) >= 1000) {
    fps = framesLastSecond;
    framesLastSecond = 0;
    fpsWindowMs = now;
  }

  if (WiFi.status() != WL_CONNECTED) {
    staAnnounced = false;
    static unsigned long lastRetry = 0;
    if ((unsigned long)(now - lastRetry) > 5000) {
      lastRetry = now;
      WiFi.begin(WIFI_SSID, WIFI_PASS);
    }
  } else if (!staAnnounced) {
    announceSta();
  }
}
