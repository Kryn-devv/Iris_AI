/*
 * IRIS Sensor Node — ESP32-S3 firmware for the IRIS desktop assistant.
 *
 * Turns an ESP32-S3 with sensors into a node IRIS can question:
 *   "is there any motion?"  "what's the gas level?"  "how far is the object?"
 *
 * Sensors (enable what you wired in CONFIG):
 *   PIR motion (HC-SR501)        -> digital pin
 *   Gas (MQ-2 / MQ-135, AO pin)  -> ADC pin        [through a voltage divider!]
 *   Light (LDR divider)          -> ADC pin
 *   Ultrasonic (HC-SR04)         -> TRIG out, ECHO in [ECHO through a divider!]
 *
 * Endpoints:
 *   GET /          tiny live dashboard
 *   GET /status    node identity JSON (IRIS uses this to detect the node)
 *   GET /sensors   fresh readings JSON
 *
 * ── WIRING WARNINGS (ESP32-S3 pins are 3.3V, NOT 5V tolerant!) ─────────────
 *   HC-SR04 ECHO outputs 5V  -> divider: ECHO --[1k]--+--[2k]-- GND, tap the +
 *   MQ-2 AO can reach ~4V    -> same 1k/2k divider on AO
 *   PIR HC-SR501 output is 3.3V — safe to connect directly.
 *   Power the PIR / MQ-2 / HC-SR04 from the 5V pin, LDR from 3.3V.
 *
 * SETUP: same as esp32-iris-node — install the esp32 board package, pick
 * "ESP32S3 Dev Module", fill in WiFi + pins, flash, read the IP from Serial
 * Monitor (115200), then tell IRIS:  add device room sensor at <IP> as sensor
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>

/* ─────────────────────────── CONFIG ─────────────────────────── */

const char* WIFI_SSID   = "YOUR_WIFI_NAME";
const char* WIFI_PASS   = "YOUR_WIFI_PASSWORD";
const char* DEVICE_NAME = "room-sensor";

/* Set a pin to -1 to disable a sensor you have not wired. */
const int PIN_PIR        = 4;    // HC-SR501 OUT (digital)
const int PIN_GAS_ADC    = 5;    // MQ-2 AO through divider (ADC1: GPIO1..10 on S3)
const int PIN_LDR_ADC    = 6;    // LDR divider midpoint
const int PIN_US_TRIG    = 7;    // HC-SR04 TRIG
const int PIN_US_ECHO    = 8;    // HC-SR04 ECHO through divider

/* Raw ADC value (0..4095) above which "gas detected" is reported.
 * Calibrate: watch /sensors in clean air, add ~800 headroom. */
const int GAS_ALARM_RAW  = 1800;

/* Motion is "recent" for this many seconds after the last PIR trigger,
 * so a quick "is there motion?" question doesn't miss a short blip. */
const unsigned long MOTION_HOLD_S = 30;

/* ─────────────────────────── STATE ──────────────────────────── */

WebServer server(80);
unsigned long bootMillis = 0;
unsigned long lastMotionMs = 0;

/* ─────────────────────────── READINGS ───────────────────────── */

bool readMotionNow() {
  return PIN_PIR >= 0 && digitalRead(PIN_PIR) == HIGH;
}

long readDistanceCm() {
  if (PIN_US_TRIG < 0 || PIN_US_ECHO < 0) return -1;
  digitalWrite(PIN_US_TRIG, LOW);  delayMicroseconds(3);
  digitalWrite(PIN_US_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_US_TRIG, LOW);
  long duration = pulseIn(PIN_US_ECHO, HIGH, 30000);   // 30ms ≈ 5m ceiling
  if (duration <= 0) return -1;
  return (long)(duration * 0.0343 / 2.0);
}

void handleSensors() {
  bool motionNow = readMotionNow();
  if (motionNow) lastMotionMs = millis();
  bool motionRecent = lastMotionMs > 0 && (millis() - lastMotionMs) < MOTION_HOLD_S * 1000UL;

  int gasRaw = PIN_GAS_ADC >= 0 ? analogRead(PIN_GAS_ADC) : -1;
  int lightRaw = PIN_LDR_ADC >= 0 ? analogRead(PIN_LDR_ADC) : -1;
  long distance = readDistanceCm();

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
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "application/json", json);
}

void handleStatus() {
  String json = "{\"name\":\"" + String(DEVICE_NAME) +
                "\",\"kind\":\"sensor\",\"ip\":\"" + WiFi.localIP().toString() +
                "\",\"rssi\":" + String(WiFi.RSSI()) +
                ",\"uptime_s\":" + String((millis() - bootMillis) / 1000) +
                ",\"sensors\":[";
  bool first = true;
  auto add = [&](const char* n, bool enabled) {
    if (!enabled) return;
    if (!first) json += ",";
    json += "\"" + String(n) + "\"";
    first = false;
  };
  add("motion", PIN_PIR >= 0);
  add("gas", PIN_GAS_ADC >= 0);
  add("light", PIN_LDR_ADC >= 0);
  add("ultrasonic", PIN_US_TRIG >= 0 && PIN_US_ECHO >= 0);
  json += "]}";
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "application/json", json);
}

void handleRoot() {
  String html =
    "<!DOCTYPE html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<meta http-equiv=refresh content=2><title>" + String(DEVICE_NAME) + "</title><style>"
    "body{font-family:system-ui;background:#05070f;color:#e6edf7;text-align:center;padding:24px}"
    "h1{font-size:16px;letter-spacing:.2em;color:#5eead4}pre{color:#8b96ad;font-size:14px}"
    "</style></head><body><h1>" + String(DEVICE_NAME) + "</h1>"
    "<pre id=o>loading…</pre>"
    "<script>fetch('/sensors').then(r=>r.json()).then(j=>document.getElementById('o').textContent=JSON.stringify(j,null,2));</script>"
    "</body></html>";
  server.send(200, "text/html", html);
}

/* ─────────────────────────── SETUP/LOOP ─────────────────────── */

void setup() {
  Serial.begin(115200);
  bootMillis = millis();

  if (PIN_PIR >= 0) pinMode(PIN_PIR, INPUT);
  if (PIN_US_TRIG >= 0) pinMode(PIN_US_TRIG, OUTPUT);
  if (PIN_US_ECHO >= 0) pinMode(PIN_US_ECHO, INPUT);
  if (PIN_GAS_ADC >= 0) analogReadResolution(12);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) { delay(400); Serial.print("."); }
  Serial.println();
  Serial.println("=================================");
  Serial.print  ("  IRIS sensor node online:  http://");
  Serial.println(WiFi.localIP());
  Serial.println("  Register in IRIS:  add device " + String(DEVICE_NAME) + " at " + WiFi.localIP().toString() + " as sensor");
  Serial.println("=================================");

  if (MDNS.begin(DEVICE_NAME)) MDNS.addService("http", "tcp", 80);

  server.on("/", handleRoot);
  server.on("/status", handleStatus);
  server.on("/sensors", handleSensors);
  server.onNotFound([]() { server.send(404, "application/json", "{\"error\":\"unknown endpoint\"}"); });
  server.begin();
}

void loop() {
  server.handleClient();
  if (readMotionNow()) lastMotionMs = millis();     // track blips between requests
  if (WiFi.status() != WL_CONNECTED) { WiFi.reconnect(); delay(500); }
}
