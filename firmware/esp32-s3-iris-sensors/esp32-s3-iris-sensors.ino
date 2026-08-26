/*
 * ============================================================================
 *  IRIS S3 NODE  —  the robot's FACE and SENSES on one ESP32-S3
 * ============================================================================
 *
 *  WHAT RUNS WHERE
 *  IRIS itself — the assistant, the agent loop, the LLM gateway — is a Python
 *  application on your PC. It cannot run on this chip and does not need to.
 *  This board is the robot's face and senses: it drives two OLED eyes and
 *  reads the sensors, and IRIS talks to it over WiFi. One brain, many bodies.
 *
 *  WHAT THIS BOARD DOES
 *    - two 128x64 SSD1306 OLEDs as expressive eyes (14 emotions, blinking,
 *      idle glances, breathing, and a syllable-paced bounce while IRIS speaks)
 *    - PIR motion, MQ-2 gas, LDR light, HC-SR04 ultrasonic distance
 *    - a web dashboard for testing all of it with no software installed
 *
 *  FIRST RUN
 *   1. Set WIFI_SSID / WIFI_PASS below. (That is the only required edit.)
 *   2. Upload, open Serial Monitor @115200, note the printed address.
 *      No router or a wrong password? The board serves its OWN WiFi instead
 *      ("iris-<name>", password below) so you can still reach the dashboard.
 *   3. Open that address in a browser to test the eyes and read the sensors.
 *   4. In IRIS:  add device face at <that-ip> as face
 *      (a "face" device also answers sensor questions — one registration.)
 *
 *  ── TWO OLEDS, ONE ADDRESS: why two I2C buses ──────────────────────────────
 *  Almost every 0.96"/0.98" SSD1306 module is hard-wired to I2C address 0x3C,
 *  and two devices cannot share an address on one bus. Rather than make you
 *  solder the address jumper, this firmware gives each eye its OWN I2C bus —
 *  the S3 has two. Wire them to different pins and it just works.
 *  If you HAVE moved one module to 0x3D, set SHARED_BUS to true and both eyes
 *  run on the left-eye pins instead.
 *
 *  ── WIRING WARNINGS (ESP32-S3 pins are 3.3V, NOT 5V tolerant) ──────────────
 *    HC-SR04 ECHO outputs 5V  -> divider: ECHO --[1k]--+--[2k]-- GND, tap +
 *    MQ-2 AO can reach ~4V    -> same 1k/2k divider on AO
 *    PIR HC-SR501 output is 3.3V — safe direct.
 *    OLEDs run happily on 3.3V (VCC 3.3V, GND, SDA, SCL).
 *    Power PIR / MQ-2 / HC-SR04 from 5V, LDR divider from 3.3V.
 *
 *  ── ANALOG PINS: use GPIO 1..10 only ───────────────────────────────────────
 *  GPIO 11..20 are ADC2, and ADC2 does not work while WiFi is running — the
 *  reading silently comes back as garbage. The sketch warns at boot if a
 *  sensor is on one of those.
 *
 *  HTTP API
 *    GET /                     dashboard
 *    GET /status               identity + face + link state (IRIS reads this)
 *    GET /sensors              fresh readings JSON
 *    GET /face?emotion=happy[&hold_ms=][&speak_ms=][&look_x=][&look_y=][&blink=1]
 *    GET /face/list            the emotions this firmware knows
 *    GET /speak?ms=2500        talking bounce for N ms (ms=0 stops)
 *    GET /look?x=-100..100&y=-100..100
 *    GET /blink[?count=2]
 *
 *  Every numeric argument is parsed strictly: a typo is answered with HTTP 400
 *  rather than silently becoming 0.
 * ============================================================================
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#include "eyes.h"
#include "face.h"
#include "page.h"

/* ══════════════════════════ CONFIG ══════════════════════════ */

const char* WIFI_SSID   = "YOUR_WIFI_NAME";
const char* WIFI_PASS   = "YOUR_WIFI_PASSWORD";
const char* DEVICE_NAME = "face";
const char* AP_PASSWORD = "iriscalib";     /* fallback network, min 8 chars */

/* ── the eyes ── */
const bool SHARED_BUS   = false;  /* true only if you moved one OLED to 0x3D */
const int  PIN_L_SDA    = 9;      /* left eye  */
const int  PIN_L_SCL    = 10;
const int  PIN_R_SDA    = 11;     /* right eye (ignored when SHARED_BUS)     */
const int  PIN_R_SCL    = 12;
const uint8_t OLED_ADDR_L = 0x3C;
const uint8_t OLED_ADDR_R = 0x3C;  /* set to 0x3D when SHARED_BUS is true    */
/* 400 kHz is the SSD1306 spec. 800 kHz works on virtually every module and
 * buys the animation ~15 extra frames per second; drop it if an eye glitches. */
const uint32_t I2C_HZ   = 800000;
const bool SWAP_EYES    = false;  /* true if left/right came out reversed    */

/* ── the sensors ──  set a pin to -1 to disable one you have not wired ── */
const int PIN_PIR       = 4;      /* HC-SR501 OUT (digital)                  */
const int PIN_GAS_ADC   = 5;      /* MQ-2 AO through divider  (ADC1: 1..10)  */
const int PIN_LDR_ADC   = 6;      /* LDR divider midpoint     (ADC1: 1..10)  */
const int PIN_US_TRIG   = 7;      /* HC-SR04 TRIG                            */
const int PIN_US_ECHO   = 8;      /* HC-SR04 ECHO through divider            */

/* Raw ADC value (0..4095) above which "gas detected" is reported.
 * Calibrate: watch /sensors in clean air, add ~800 headroom. */
const int GAS_ALARM_RAW = 1800;

/* Motion stays "recent" this long after the last PIR trigger, so a quick
 * "is there motion?" question does not miss a short blip. */
const unsigned long MOTION_HOLD_S = 30;

#define WIFI_JOIN_MS 25000UL      /* then fall back to our own network */

/* ══════════════════════════ STATE ══════════════════════════ */

WebServer server(80);
Adafruit_SSD1306 eyeLeft(EYE_W, EYE_H, &Wire, -1);
Adafruit_SSD1306 eyeRight(EYE_W, EYE_H, SHARED_BUS ? &Wire : &Wire1, -1);
FaceAnimator face;

unsigned long bootMillis = 0;
unsigned long lastMotionMs = 0;
bool eyeLeftOk = false, eyeRightOk = false;
bool apMode = false, staAnnounced = false;
uint16_t framesLastSecond = 0, fps = 0;
unsigned long fpsWindowMs = 0;

/* ═════════════════════ strict argument parsing ═════════════════════ */

/* String::toInt() answers 0 for "", "abc" and "twelve". A silent 0 here means
 * "look dead ahead" or "stop speaking" — the opposite of what was asked — so
 * a malformed value is an error, never a default. */
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

static const char* argFail = NULL;   /* requests are handled one at a time */

/* Present but malformed => false. Absent => keep the caller's default.
 * Out of range is clamped: look_x=500 plainly means "hard right". */
static bool argClamp(const char* name, long lo, long hi, long& out) {
  if (!server.hasArg(name)) return true;
  long v;
  if (!parseLong(server.arg(name), v)) { argFail = name; return false; }
  out = (v < lo) ? lo : (v > hi ? hi : v);
  return true;
}

static void sendJson(int code, const String& body) {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(code, "application/json", body);
}

static void sendBadArg() {
  sendJson(400, String("{\"error\":\"bad value for '") + (argFail ? argFail : "?") +
                "'\",\"hint\":\"must be a whole number\"}");
}

/* ═════════════════════════ readings ═════════════════════════ */

static bool readMotionNow() {
  return PIN_PIR >= 0 && digitalRead(PIN_PIR) == HIGH;
}

static long readDistanceCm() {
  if (PIN_US_TRIG < 0 || PIN_US_ECHO < 0) return -1;
  digitalWrite(PIN_US_TRIG, LOW);  delayMicroseconds(3);
  digitalWrite(PIN_US_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_US_TRIG, LOW);
  const long duration = pulseIn(PIN_US_ECHO, HIGH, 30000);   /* 30ms ~ 5m */
  if (duration <= 0) return -1;
  return (long)(duration * 0.0343 / 2.0);
}

/* ═════════════════════════ endpoints ═════════════════════════ */

static void handleSensors() {
  const bool motionNow = readMotionNow();
  if (motionNow) lastMotionMs = millis();
  const bool motionRecent = lastMotionMs > 0 &&
                            (millis() - lastMotionMs) < MOTION_HOLD_S * 1000UL;

  const int gasRaw = PIN_GAS_ADC >= 0 ? analogRead(PIN_GAS_ADC) : -1;
  const int lightRaw = PIN_LDR_ADC >= 0 ? analogRead(PIN_LDR_ADC) : -1;
  const long distance = readDistanceCm();

  String json = "{";
  json += "\"motion\":" + String(motionNow ? "true" : "false");
  json += ",\"motion_recent\":" + String(motionRecent ? "true" : "false");
  if (gasRaw >= 0) {
    json += ",\"gas_raw\":" + String(gasRaw);
    json += ",\"gas_alarm\":" + String(gasRaw >= GAS_ALARM_RAW ? "true" : "false");
  }
  if (lightRaw >= 0) {
    json += ",\"light_raw\":" + String(lightRaw);
    json += ",\"light_percent\":" + String((int)(lightRaw * 100L / 4095));
  }
  if (distance >= 0) json += ",\"distance_cm\":" + String(distance);
  json += ",\"uptime_s\":" + String((millis() - bootMillis) / 1000);
  json += "}";
  sendJson(200, json);
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

static void handleStatus() {
  /* kind "face" so IRIS can find this node for expressions; the sensor list is
   * still advertised, because one board does both jobs. */
  String j = "{\"name\":\"" + String(DEVICE_NAME) + "\",\"kind\":\"face\"";
  j += ",\"firmware\":\"iris-s3-face-1.0\"";
  j += ",\"ip\":\"" + (apMode && WiFi.status() != WL_CONNECTED
                        ? WiFi.softAPIP().toString() : WiFi.localIP().toString()) + "\"";
  j += ",\"link\":\"" + String(WiFi.status() == WL_CONNECTED ? "sta" : (apMode ? "ap" : "down")) + "\"";
  j += ",\"ap_mode\":" + String(apMode ? "true" : "false");
  j += ",\"rssi\":" + String(WiFi.RSSI());
  j += ",\"uptime_s\":" + String((millis() - bootMillis) / 1000);
  j += ",\"free_heap\":" + String((uint32_t)ESP.getFreeHeap());
  j += ",\"face\":" + faceJson();
  j += ",\"sensors\":[";
  bool first = true;
  auto add = [&](const char* n, bool enabled) {
    if (!enabled) return;
    if (!first) j += ",";
    j += "\"" + String(n) + "\"";
    first = false;
  };
  add("motion", PIN_PIR >= 0);
  add("gas", PIN_GAS_ADC >= 0);
  add("light", PIN_LDR_ADC >= 0);
  add("ultrasonic", PIN_US_TRIG >= 0 && PIN_US_ECHO >= 0);
  j += "]}";
  sendJson(200, j);
}

static void handleFaceList() {
  String j = "{\"emotions\":[";
  for (uint8_t i = 0; i < EMO_COUNT; i++) {
    if (i) j += ",";
    j += "\"" + String(EMOTION_NAMES[i]) + "\"";
  }
  j += "],\"count\":" + String(EMO_COUNT) + "}";
  sendJson(200, j);
}

/* One call can set the mood, aim the gaze, start the talking bounce and blink,
 * so IRIS reacts to a sentence in a single round trip instead of four. */
static void handleFace() {
  const unsigned long now = millis();
  String wanted = server.hasArg("emotion") ? server.arg("emotion")
                : (server.hasArg("e") ? server.arg("e") : "");

  long holdMs = 0, speakMs = -1, lookX = face.gazeX, lookY = face.gazeY, blink = 0;
  if (!argClamp("hold_ms", 0, 600000, holdMs) ||
      !argClamp("speak_ms", 0, (long)SPEAK_MAX_MS, speakMs) ||
      !argClamp("look_x", -100, 100, lookX) ||
      !argClamp("look_y", -100, 100, lookY) ||
      !argClamp("blink", 0, 5, blink)) { sendBadArg(); return; }

  /* Validate the emotion name BEFORE anything is applied, so a typo leaves the
   * face exactly as it was instead of half-changing it. */
  bool known = false;
  uint8_t emo = EMO_NEUTRAL;
  if (wanted.length()) {
    emo = emotionFromName(wanted, &known);
    if (!known) {
      sendJson(400, "{\"error\":\"unknown emotion '" + wanted +
                    "'\",\"hint\":\"GET /face/list for the full set\"}");
      return;
    }
  }

  if (known) face.setEmotion(emo, (uint32_t)holdMs, now);
  if (server.hasArg("look_x") || server.hasArg("look_y"))
    face.look((int16_t)lookX, (int16_t)lookY, now);
  if (speakMs >= 0) face.setSpeaking((uint32_t)speakMs, now);
  if (blink > 0) face.blinkNow(now, (uint8_t)blink);

  sendJson(200, "{\"ok\":true,\"face\":" + faceJson() + "}");
}

static void handleSpeak() {
  long ms = 2000;
  if (!argClamp("ms", 0, (long)SPEAK_MAX_MS, ms)) { sendBadArg(); return; }
  face.setSpeaking((uint32_t)ms, millis());
  sendJson(200, "{\"ok\":true,\"face\":" + faceJson() + "}");
}

static void handleLook() {
  long x = face.gazeX, y = face.gazeY;
  if (!argClamp("x", -100, 100, x) || !argClamp("y", -100, 100, y)) { sendBadArg(); return; }
  face.look((int16_t)x, (int16_t)y, millis());
  sendJson(200, "{\"ok\":true,\"face\":" + faceJson() + "}");
}

static void handleBlink() {
  long count = 1;
  if (!argClamp("count", 1, 5, count)) { sendBadArg(); return; }
  face.blinkNow(millis(), (uint8_t)count);
  sendJson(200, "{\"ok\":true}");
}

static void handleRoot() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send_P(200, "text/html", FACE_PAGE);
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

/* ═══════════════════════════ WiFi ═══════════════════════════ */

static void announceSta() {
  Serial.println();
  Serial.println("=================================");
  Serial.print  ("  IRIS S3 node online:  http://");
  Serial.println(WiFi.localIP());
  Serial.println("  Test the eyes:    open that address in a browser");
  Serial.println("  Register in IRIS: add device " + String(DEVICE_NAME) +
                 " at " + WiFi.localIP().toString() + " as face");
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

  if (PIN_PIR >= 0) pinMode(PIN_PIR, INPUT);
  if (PIN_US_TRIG >= 0) pinMode(PIN_US_TRIG, OUTPUT);
  if (PIN_US_ECHO >= 0) pinMode(PIN_US_ECHO, INPUT);
  analogReadResolution(12);
  warnAboutAdc2("gas sensor", PIN_GAS_ADC);
  warnAboutAdc2("light sensor", PIN_LDR_ADC);

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
    EyePose l, r;
    if (face.tick(millis(), l, r)) drawFace(l, r);
    delay(5);
  }
  if (WiFi.status() == WL_CONNECTED) announceSta();
  else startFallbackAp();

  server.on("/",           handleRoot);
  server.on("/status",     handleStatus);
  server.on("/sensors",    handleSensors);
  server.on("/face",       handleFace);
  server.on("/face/list",  handleFaceList);
  server.on("/speak",      handleSpeak);
  server.on("/look",       handleLook);
  server.on("/blink",      handleBlink);
  server.onNotFound([]() { sendJson(404, "{\"error\":\"unknown endpoint\"}"); });
  server.begin();      /* unconditional: the dashboard must exist even with no
                        * router, or a wiring fault cannot be diagnosed */

  /* A wave hello, so you know it booted. */
  face.setEmotion(EMO_EXCITED, 1800, millis());
  face.blinkNow(millis(), 2);
}

void loop() {
  server.handleClient();

  const unsigned long now = millis();
  if (readMotionNow()) lastMotionMs = now;   /* catch blips between requests */

  EyePose left, right;
  if (face.tick(now, left, right)) {
    drawFace(left, right);
    framesLastSecond++;
  }
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
