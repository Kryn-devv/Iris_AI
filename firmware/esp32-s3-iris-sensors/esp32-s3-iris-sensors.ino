/*
 * ============================================================================
 *  IRIS S3 NODE  —  the robot's face, senses and voice on one ESP32-S3
 * ============================================================================
 *
 *  WHAT RUNS WHERE
 *  IRIS itself — the agent loop, the LLM gateway, the voice pipeline — is a
 *  Python application. It runs on your PC, on the same WiFi (or the same phone
 *  hotspot) as this board. It cannot run on this chip and does not need to:
 *  this board is the robot's face and senses, and it talks to IRIS over the
 *  network. One brain, many bodies.
 *
 *  WHAT THIS BOARD DOES
 *    - two 128x64 OLED eyes, SH1106 (1.3") or SSD1306 (0.96") — 14 emotions, blinking,
 *      idle glances, breathing, and a bounce while IRIS speaks)
 *    - FOUR HC-SR04 distance sensors (two ahead, two behind), PIR motion,
 *      MQ-2 gas, LDR light, flame, DHT22 temperature and humidity
 *    - an I2S microphone and speaker: talk to it, it answers out loud
 *    - a web dashboard for testing all of it with nothing installed
 *
 *  ── TWO WAYS IT REACHES IRIS ───────────────────────────────────────────────
 *
 *  A. IRIS ON YOUR OWN NETWORK — a router or a phone hotspot (the default)
 *     Leave CLOUD_HOST empty. IRIS calls this board's IP, over HTTP and over
 *     a UDP fast path (port 8267) that answers in the same millisecond it is
 *     asked. Register with:  add device face at <ip> as face
 *     The network must be 2.4 GHz: an ESP32 cannot see a 5 GHz hotspot, and
 *     that looks exactly like a wrong password.
 *
 *  B. IRIS ON A VPS (optional, off by default)
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
 *  ── WIRING ─────────────────────────────────────────────────────────────────
 *  ESP32-S3 pins are 3.3V and NOT 5V tolerant. This build runs EVERYTHING
 *  from one 3.30 V buck converter, so no signal can exceed 3.3 V and no
 *  divider is needed. Only if you ever feed a sensor 5 V does its output need
 *  a 1k/2k divider before the pin (HC-SR04 ECHO, MQ-2 AO).
 *  Analog sensors must be on GPIO 1..10 (ADC1). GPIO 11..20 are ADC2, which
 *  stops working once WiFi is up and silently returns garbage; setup() warns.
 *
 *  HTTP API (the same commands also arrive as one UDP datagram on port 8267,
 *  which is how IRIS sends them — see fastpath.h)
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

/* ── WHICH CHIP IS INSIDE EACH EYE MODULE ─────────────────────────────────
 * 0.96" modules are SSD1306. The slightly bigger ones — sold as 1.02", 1.1",
 * 1.2" or 1.3" — are almost always SH1106, which speaks a different dialect:
 * driven as an SSD1306 it lights up solid and flickers, or shows the picture
 * shifted two pixels and wrapping at the edge.
 *   1 = SH1106  (Library Manager: "Adafruit SH110X" by Adafruit)
 *   0 = SSD1306 (Library Manager: "Adafruit SSD1306")
 * Both also need "Adafruit GFX Library". The two eyes MAY differ — a 0.96" on
 * the left and a 1.3" on the right is fine — but then each needs its own pair
 * of wires (TWIN_PANELS = false below): two different chips on one bus at one
 * address would both hear every command, and one of them would be the wrong
 * one. Nothing else in this file changes; see panels.h. */
#define EYE_L_CHIP_SH1106 0   /* left eye  */
#define EYE_R_CHIP_SH1106 1   /* right eye */

#define IRIS_USE_SH1106  (EYE_L_CHIP_SH1106 || EYE_R_CHIP_SH1106)
#define IRIS_USE_SSD1306 (!EYE_L_CHIP_SH1106 || !EYE_R_CHIP_SH1106)
#include "panels.h"
#if defined(CONFIG_IDF_TARGET_ESP32S3) || defined(CONFIG_IDF_TARGET_ESP32S2)
#include "soc/usb_serial_jtag_reg.h"   /* to take GPIO 19/20 back from the USB PHY */
#endif

#define FIRMWARE_VERSION "iris-s3-node-2.1"

#include "eyes.h"
#include "face.h"
#include "sensors.h"
#include "fastpath.h"
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

/* ── the eyes ── two 128x64 OLEDs (chips chosen by EYE_*_CHIP_SH1106 above).
 *
 * TWIN_PANELS = true  : BOTH modules on ONE pair of wires (the same SDA and
 *                       SCL). Every module answers at 0x3C, so the two cannot
 *                       be told apart and always show the SAME picture — which
 *                       is a perfectly good pair of eyes. Only the wink is lost.
 *                       No extra wiring. This is the default.
 * TWIN_PANELS = false : two independent eyes. Give the right module its own
 *                       bus on PIN_R_SDA / PIN_R_SCL — two pins with nothing
 *                       else on them (38/39 by default).
 * SHARED_BUS  = true  : one bus, but you moved one module to 0x3D (solder
 *                       jumper) — independent eyes on one pair of wires.
 *
 * GPIO 19/20 are also the S3's native-USB data pins. They work as I2C as long
 * as you flash through the UART/COM port and leave the other USB port empty.
 * If an eye there stays dark, move its two wires to 15/16. */
const bool TWIN_PANELS  = false;  /* false: mixed chips need their own buses */
const bool SHARED_BUS   = false;
const int  PIN_L_SDA    = 20;     /* left eye (0.96")                         */
const int  PIN_L_SCL    = 21;
const int  PIN_R_SDA    = 38;     /* right eye (1.3"), its own two wires      */
const int  PIN_R_SCL    = 39;     /* (17/18 are taken by the MQ-2 DO and LDR) */
const uint8_t OLED_ADDR_L = 0x3C;
const uint8_t OLED_ADDR_R = 0x3C;  /* set to 0x3D when SHARED_BUS is true    */
/* 400 kHz if any eye is an SH1106 (that is its rating); the SSD1306 is happy
 * at 800 kHz — drop to 400000 if it ever glitches. */
const uint32_t I2C_HZ   = IRIS_USE_SH1106 ? 400000 : 800000;
const bool SWAP_EYES    = false;   /* true if left/right came out reversed   */

/* ── the sensors ──  set a pin to -1 to disable one you have not wired ──
 *
 * POWER: everything, this board included, runs from ONE buck converter set to
 * 3.30 V. Nothing on the robot ever makes more than 3.3 V, so NO resistor or
 * divider is needed anywhere. (Dividers are only for a 5 V supply. Some
 * HC-SR04 clones insist on 5 V and answer "no echo" at 3.3 V — the board's
 * page shows that within a minute; the fix is 5 V plus a 1k/2k divider on
 * each ECHO, or 3.3 V-rated modules.)
 *
 * ANALOG: only GPIO 1..10 can read a voltage while WiFi is on; 11..20 share
 * their ADC with the radio and return garbage. That is a fact of the chip,
 * not of this code. The MQ-2 AO (16), LDR (18) and flame AO (15) are on such
 * pins, so they are off below; the modules' DIGITAL outputs carry the alarm
 * instead. To get a gas LEVEL or a light PERCENT later, move that one wire to
 * GPIO 2 (gas) / GPIO 1 (light) and put the number here. */
/* Four HC-SR04, in this order: front-left, front-right, rear-left, rear-right. */
const int PIN_US_TRIG[US_COUNT] = { 4,  6,  8, 10};
const int PIN_US_ECHO[US_COUNT] = { 5,  7,  9, 11};
const int PIN_DHT       = 12;     /* DHT22 DATA (digital)                     */
const uint8_t DHT_KIND  = DHT22;  /* DHT11 (blue) or DHT22 (white)            */
const int PIN_PIR       = 13;     /* HC-SR501 OUT (digital)                   */
const int PIN_FLAME     = 14;     /* flame module DO: fire yes/no             */
const int PIN_FLAME_ADC = -1;     /* flame AO is on 15 (ADC2) — ignored       */
const bool FLAME_ACTIVE_LOW = true;  /* most IR flame modules pull DO LOW     */
const int PIN_GAS_ADC   = -1;     /* MQ-2 AO is on 16 (ADC2) — ignored; 2 to use it */
const int PIN_GAS_DO    = 17;     /* MQ-2 DO: gas yes/no, from the module's pot */
const bool GAS_DO_ACTIVE_LOW = true; /* MQ-2 modules pull DO LOW above the pot */
const int GAS_ALARM_RAW = 1800;   /* only used with PIN_GAS_ADC               */
const int PIN_LDR_ADC   = -1;     /* LDR is on 18 (ADC2) — ignored; 1 to use it */

/* ── the voice (I2S mic + I2S amplifier) ── all -1 = not fitted (the default).
 * If you add them later: mic SCK 38, WS 39, SD 40; amp BCLK 41, LRC 42, DIN 21. */
const int PIN_MIC_SCK   = -1;     /* INMP441 SCK                             */
const int PIN_MIC_WS    = -1;     /* INMP441 WS                              */
const int PIN_MIC_DATA  = -1;     /* INMP441 SD                              */
const int PIN_AMP_BCLK  = -1;     /* MAX98357A BCLK                          */
const int PIN_AMP_LRC   = -1;     /* MAX98357A LRC                           */
const int PIN_AMP_DATA  = -1;     /* MAX98357A DIN                           */
const int PIN_PTT       = -1;     /* optional push-to-talk button to GND     */
const uint8_t MIC_GAIN  = 4;      /* raise if IRIS mishears, lower if it clips */

#define WIFI_JOIN_MS 25000UL      /* then fall back to our own network */

/* ══════════════════════════ STATE ══════════════════════════ */

WebServer server(80);
#if EYE_L_CHIP_SH1106
Sh1106Panel  eyeLeft(EYE_W, EYE_H, &Wire, I2C_HZ);
#else
Ssd1306Panel eyeLeft(EYE_W, EYE_H, &Wire);
#endif
#if EYE_R_CHIP_SH1106
Sh1106Panel  eyeRight(EYE_W, EYE_H, SHARED_BUS ? &Wire : &Wire1, I2C_HZ);
#else
Ssd1306Panel eyeRight(EYE_W, EYE_H, SHARED_BUS ? &Wire : &Wire1);
#endif
/* Two different chips on one bus at one address cannot work: both would hear
 * every command and one of them would be the wrong one. */
static_assert(!(TWIN_PANELS && (EYE_L_CHIP_SH1106 != EYE_R_CHIP_SH1106)),
              "Different OLED chips left/right: set TWIN_PANELS=false and give the "
              "right eye its own SDA/SCL (38/39).");
FaceAnimator face;
Sensors sensors;
CloudLink cloud;
NodeVoice voice;
FastPath fast;

unsigned long bootMillis = 0;
bool eyeLeftOk = false, eyeRightOk = false;
bool apMode = false, staAnnounced = false;
uint16_t framesLastSecond = 0, fps = 0;
unsigned long fpsWindowMs = 0;
SensorReading lastReading;
bool lastDanger = false;
bool lastMotionRecent = false;
long lastUsSent[US_COUNT] = {-1, -1, -1, -1};

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
  j += ",\"eyes_ok\":" + String((eyeLeftOk && (TWIN_PANELS || eyeRightOk)) ? "true" : "false");
  j += ",\"twin_panels\":" + String(TWIN_PANELS ? "true" : "false");
  j += ",\"left_eye_ok\":" + String(eyeLeftOk ? "true" : "false");
  j += ",\"right_eye_ok\":" + String((TWIN_PANELS ? eyeLeftOk : eyeRightOk) ? "true" : "false");
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
  j += ",\"fast\":{\"udp\":" + String(fast.started() ? FastPath::UDP_PORT : 0) +
       ",\"handled\":" + String(fast.handled()) + "}";
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

/* ═════════════════════════ fast-path glue ═════════════════════════ */

/* One datagram = the URL's tail: "/face?emotion=happy". Same dispatch, same
 * JSON back, no HTTP in between. */
static void fastCommand(const String& target, String& out) {
  const int q = target.indexOf('?');
  const String path = q >= 0 ? target.substring(0, q) : target;
  const String query = q >= 0 ? target.substring(q + 1) : String();
  out = dispatch(path, Args::fromQuery(query)).body;
}

/* ═══════════════════════════ display ═══════════════════════════ */

static bool busAnswers(TwoWire& bus, uint8_t addr) {
  bus.beginTransmission(addr);
  return bus.endTransmission() == 0;
}

/* Prints every address that acknowledges on a bus, so "nothing works" turns
 * into "0x3C is there but will not initialise" or "the bus is empty" — two
 * different faults with two different fixes. */
static void scanBus(TwoWire& bus, const char* label) {
  Serial.printf("  [eyes] %s bus scan:", label);
  uint8_t found = 0;
  for (uint8_t a = 0x08; a < 0x78; a++) {
    if (busAnswers(bus, a)) { Serial.printf(" 0x%02X", a); found++; }
  }
  Serial.println(found ? "" : " nothing answered — check VCC, GND, SDA, SCL");
}

static bool startEye(EyePanel& d, TwoWire& bus, uint8_t addr,
                     int sda, int scl, const char* label) {
  const uint8_t other = (addr == 0x3C) ? 0x3D : 0x3C;
  const uint8_t tries[2] = {addr, other};
  const uint32_t clocks[2] = {I2C_HZ, 100000};
  for (uint8_t c = 0; c < 2; c++) {
    bus.setClock(clocks[c]);
    for (uint8_t t = 0; t < 2; t++) {
      if (!busAnswers(bus, tries[t])) continue;
      if (d.begin(tries[t])) {
        d.clearDisplay();
        d.display();
        Serial.printf("  [eyes] %s OLED ok at 0x%02X on SDA %d / SCL %d (%lu kHz, driven as %s)\n",
                      label, tries[t], sda, scl, (unsigned long)(clocks[c] / 1000),
                      d.chip());
        return true;
      }
      Serial.printf("  [eyes] %s: 0x%02X answered but would not initialise as %s —\n"
                    "         wrong chip for this eye? 0.96\" = SSD1306 (EYE_%c_CHIP_SH1106 0),\n"
                    "         1.02\"/1.3\" = SH1106 (EYE_%c_CHIP_SH1106 1)\n",
                    label, tries[t], d.chip(), label[0] == 'l' ? 'L' : 'R',
                    label[0] == 'l' ? 'L' : 'R');
    }
  }
  Serial.printf("  [eyes] %s OLED did NOT answer on SDA %d / SCL %d\n", label, sda, scl);
  scanBus(bus, label);
  if (isUsbPin(sda) || isUsbPin(scl))
    Serial.println("         (this bus is on the USB pins — if the module is fine on other\n"
                   "          pins, move these two wires to 41 (SDA) / 42 (SCL))");
  return false;
}

static bool isUsbPin(int pin) { return pin == 19 || pin == 20; }

/* GPIO 19/20 are wired to the S3's USB PHY, and the PHY OWNS those pads at
 * boot: the bootloader enables it, this Arduino core never disables it, and
 * a GPIO function set on the pin simply does not reach the outside world —
 * an I2C bus scan there hears nothing, exactly as if no wire were fitted.
 * Clearing the pad-enable bit gives the pads back. The "USB" socket then
 * stops working until the next flash, which is fine: everything here goes
 * through the UART/COM socket anyway. */
static void reclaimUsbPins(const char* why) {
#if defined(CONFIG_IDF_TARGET_ESP32S3) || defined(CONFIG_IDF_TARGET_ESP32S2)
  CLEAR_PERI_REG_MASK(USB_SERIAL_JTAG_CONF0_REG, USB_SERIAL_JTAG_USB_PAD_ENABLE);
  Serial.printf("  [pins] GPIO 19/20 taken back from the USB port for %s — flash and\n"
                "         monitor through the UART/COM socket, leave the USB one empty.\n", why);
#else
  (void)why;
#endif
}

static void startEyes() {
  const bool leftOnUsb = isUsbPin(PIN_L_SDA) || isUsbPin(PIN_L_SCL);
  const bool rightOnUsb = !TWIN_PANELS && !SHARED_BUS && (isUsbPin(PIN_R_SDA) || isUsbPin(PIN_R_SCL));
  if (leftOnUsb || rightOnUsb) reclaimUsbPins("the eyes");

  Wire.begin(PIN_L_SDA, PIN_L_SCL, I2C_HZ);
  if (!SHARED_BUS && !TWIN_PANELS) Wire1.begin(PIN_R_SDA, PIN_R_SCL, I2C_HZ);
  eyeLeftOk = startEye(eyeLeft, Wire, OLED_ADDR_L, PIN_L_SDA, PIN_L_SCL, "left");

  if (TWIN_PANELS) {
    /* Both modules hang on this one bus at 0x3C, so every frame written to
     * the "left" display lands on both panels. Nothing to start for the right. */
    eyeRightOk = false;
    Serial.printf("  [eyes] twin panels: both OLEDs on SDA %d / SCL %d show the same eye\n",
                  PIN_L_SDA, PIN_L_SCL);
    if (!eyeLeftOk) {
      Serial.println("  [eyes] no OLED answered. Check VCC (3.3 V), GND, SDA, SCL.");
    }
    return;
  }

  if (!SHARED_BUS && PIN_R_SDA == PIN_L_SDA && PIN_R_SCL == PIN_L_SCL) {
    Serial.println("  [eyes] both eyes are on the SAME pins with TWIN_PANELS=false —");
    Serial.println("         set TWIN_PANELS=true for that wiring, or give the right");
    Serial.println("         eye its own SDA/SCL.");
  }
  eyeRightOk = SHARED_BUS
      ? startEye(eyeRight, Wire, OLED_ADDR_R, PIN_L_SDA, PIN_L_SCL, "right")
      : startEye(eyeRight, Wire1, OLED_ADDR_L, PIN_R_SDA, PIN_R_SCL, "right");

  if (eyeLeftOk && eyeRightOk)
    Serial.printf("  [eyes] left is %s, right is %s\n", eyeLeft.chip(), eyeRight.chip());
  if (!eyeLeftOk || !eyeRightOk) {
    Serial.println("  [eyes] an eye is missing. Check VCC (3.3 V), GND, SDA, SCL on");
    Serial.println("         that side. Two modules on ONE bus both at 0x3C cannot");
    Serial.println("         work — use the two-bus wiring above, or move one to");
    Serial.println("         0x3D and set SHARED_BUS = true.");
  }
}

static void drawFace(const EyePose& left, const EyePose& right) {
  if (TWIN_PANELS) {
    if (!eyeLeftOk) return;
    /* One picture for two panels, so it has to look right on both: the
     * right eye's pose (the open one during a wink), brows made symmetric
     * so an angry slant does not read as "one angry, one sad" when the same
     * frame appears on the other side. */
    EyePose p = right;
    const int16_t brow = (int16_t)((p.browIn + p.browOut) / 2);
    p.browIn = brow;
    p.browOut = brow;
    eyeLeft.clearDisplay();
    drawEye(eyeLeft.gfx(), p, true);
    eyeLeft.display();
    fast.pump();
    return;
  }
  /* The two eyes may be different classes (different chips), so pick through
   * the common base explicitly — the ?: operator will not do it for us. */
  EyePanel& lDisp = SWAP_EYES ? static_cast<EyePanel&>(eyeRight) : static_cast<EyePanel&>(eyeLeft);
  EyePanel& rDisp = SWAP_EYES ? static_cast<EyePanel&>(eyeLeft)  : static_cast<EyePanel&>(eyeRight);
  const bool lOk = SWAP_EYES ? eyeRightOk : eyeLeftOk;
  const bool rOk = SWAP_EYES ? eyeLeftOk  : eyeRightOk;

  if (lOk) {
    lDisp.clearDisplay();
    drawEye(lDisp.gfx(), left, true);
    lDisp.display();
  }
  /* Each panel write holds the bus ~10 ms. A command that arrives during the
   * left eye should not also wait for the right one. */
  fast.pump();
  if (rOk) {
    rDisp.clearDisplay();
    drawEye(rDisp.gfx(), right, false);
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
  Serial.println("  Fast path:        UDP " + String(FastPath::UDP_PORT) +
                 (fast.started() ? "" : " (NOT listening)"));
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
  sensorCfg.pins.pir = PIN_PIR;
  sensorCfg.pins.gasAdc = PIN_GAS_ADC;
  sensorCfg.pins.gasDo = PIN_GAS_DO;
  sensorCfg.pins.ldrAdc = PIN_LDR_ADC;
  sensorCfg.pins.flame = PIN_FLAME;
  sensorCfg.pins.flameAdc = PIN_FLAME_ADC;
  sensorCfg.pins.dht = PIN_DHT;
  for (uint8_t i = 0; i < US_COUNT; i++) {
    sensorCfg.pins.usTrig[i] = PIN_US_TRIG[i];
    sensorCfg.pins.usEcho[i] = PIN_US_ECHO[i];
  }
  sensorCfg.flameActiveLow = FLAME_ACTIVE_LOW;
  sensorCfg.gasDoActiveLow = GAS_DO_ACTIVE_LOW;
  sensorCfg.gasAlarmRaw = GAS_ALARM_RAW;
  sensorCfg.motionHoldMs = 30000;
  /* One ultrasonic is fired every 60 ms, so four of them each refresh about
   * four times a second — plenty for an obstacle check, and never blocking. */
  sensorCfg.distanceSlotMs = 60;
  sensorCfg.climateEveryMs = 2500;   /* a DHT refuses to be read faster */
  sensorCfg.dhtType = DHT_KIND;
  sensors.begin(sensorCfg);
  warnAboutAdc2("gas sensor", PIN_GAS_ADC);
  warnAboutAdc2("light sensor", PIN_LDR_ADC);
  warnAboutAdc2("flame sensor AO", PIN_FLAME_ADC);
  Serial.printf("  %u ultrasonic sensor(s) fitted — fired one at a time, never blocking.\n",
                sensors.ultrasonicsFitted());
  if (PIN_DHT >= 0) Serial.printf("  DHT%s on GPIO %d\n", DHT_KIND == DHT11 ? "11" : "22", PIN_DHT);
  for (uint8_t i = 0; i < US_COUNT; i++) {
    if (PIN_US_ECHO[i] == 19 || PIN_US_ECHO[i] == 20 || PIN_US_TRIG[i] == 19 || PIN_US_TRIG[i] == 20)
      Serial.printf("  [warn] ultrasonic %s uses GPIO 19/20 — those are the USB pins.\n", US_NAMES[i]);
  }

  startEyes();
  face.begin(millis());

  /* The UDP listener binds to any address, so it can come up before the
   * network does — and must, for the banner below to report it honestly. */
  if (!fast.begin(fastCommand)) Serial.println("  [warn] UDP fast path failed to start");

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

  fast.pump();             /* the command path IRIS uses — first, and again below */
  server.handleClient();
  cloud.loop();
  sensors.tick(now);
  fast.pump();
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
  for (uint8_t i = 0; i < US_COUNT; i++) {
    if (!lastReading.hasUs[i] || lastReading.usCm[i] < 0) continue;
    if (lastUsSent[i] < 0 || labs(lastReading.usCm[i] - lastUsSent[i]) > 8) {
      lastUsSent[i] = lastReading.usCm[i];
      changed = true;
    }
  }

  /* An alert is an interruption, not a reading: sent the instant it appears,
   * once per appearance, so a sensor on its threshold cannot spam. */
  if (danger && !lastDanger) {
    if (lastReading.hasFlame && lastReading.flame) {
      Serial.println("[alert] FLAME DETECTED");
      if ((uint32_t)(now - bootMillis) < 15000UL)
        Serial.println("        (at boot with no flame? look at the module's DO LED: lit =>\n"
                       "         turn its pot until it goes off; dark => set FLAME_ACTIVE_LOW\n"
                       "         = false — this module says HIGH for fire)");
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
