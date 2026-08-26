/*
 * IRIS Node — BTS7960 4-wheel-drive variant, for the movement/robot ESP32.
 *
 * Use this sketch (not esp32-iris-node.ino) when your robot uses TWO BTS7960
 * driver boards wired as a skid-steer 4WD: one BTS7960 drives BOTH left
 * motors in parallel, the other drives BOTH right motors in parallel.
 *
 * Same network API as the other IRIS firmware, so IRIS's tools work
 * unchanged: /status and /motor?dir=forward&speed=200&ms=1500.
 *
 * ── WIRING ──────────────────────────────────────────────────────────────
 * Each BTS7960 module has 6 signal pins. Per side:
 *   RPWM  -> an ESP32 GPIO (forward speed, PWM)
 *   LPWM  -> an ESP32 GPIO (reverse speed, PWM)
 *   R_EN  -> tie together WITH L_EN on the same board, to one ESP32 GPIO
 *   L_EN  -> (tied to R_EN, see above)
 *   B+/B- -> your motor battery (NOT the ESP32) — BTS7960 handles high
 *            current; never power motors from the ESP32's 5V pin.
 *   GND   -> common ground between ESP32, both BTS7960 boards and the
 *            motor battery (grounds MUST be joined or nothing will work).
 *
 * Default pins below (classic ESP32 Dev Module — safe general I/O, no
 * strapping pins, no input-only pins):
 *   LEFT  BTS7960:  RPWM=25  LPWM=26  EN=27
 *   RIGHT BTS7960:  RPWM=32  LPWM=33  EN=14
 * Change them in CONFIG if your wiring differs.
 *
 * If a motor doesn't spin: swap that side's RPWM/LPWM pins (its wired
 * backwards) rather than changing anything in software.
 *
 * SETUP: Arduino IDE -> install "esp32 by Espressif Systems" board package
 * (see firmware/esp32-iris-node for the one-time setup steps) -> Board:
 * "ESP32 Dev Module" -> fill in WiFi + pins below -> Upload -> open Serial
 * Monitor at 115200 to read the IP it gets from your WiFi router -> in IRIS:
 *   add device robot at <that IP> as motor
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>

/* ─────────────────────────── CONFIG ─────────────────────────── */

const char* WIFI_SSID   = "YOUR_WIFI_NAME";
const char* WIFI_PASS   = "YOUR_WIFI_PASSWORD";
const char* DEVICE_NAME = "robot";

const int PIN_L_RPWM = 25, PIN_L_LPWM = 26, PIN_L_EN = 27;   // left side
const int PIN_R_RPWM = 32, PIN_R_LPWM = 33, PIN_R_EN = 14;   // right side

const int DEFAULT_SPEED = 200;   // 0..255, used when the command omits "speed"

/* ─────────────────────────── STATE ──────────────────────────── */

WebServer server(80);
unsigned long motorStopAt = 0;
unsigned long bootMillis  = 0;
String lastDir = "stop";

/* ─────────────────────────── MOTOR CONTROL ──────────────────── */

/* One side: exactly one of forward/backward is non-zero at a time. */
void driveSide(int rpwmPin, int lpwmPin, int forwardSpeed, int backwardSpeed) {
  analogWrite(rpwmPin, forwardSpeed);
  analogWrite(lpwmPin, backwardSpeed);
}

void motorsStop() {
  driveSide(PIN_L_RPWM, PIN_L_LPWM, 0, 0);
  driveSide(PIN_R_RPWM, PIN_R_LPWM, 0, 0);
  motorStopAt = 0;
  lastDir = "stop";
}

/* Tank-steer: turning spins one side forward and the other backward,
 * matching the plain L298N sketch's turn behaviour so "robot left"/"right"
 * feels the same regardless of which driver hardware is fitted. */
void motorsDrive(const String& dir, int speed) {
  speed = constrain(speed, 0, 255);
  if (dir == "forward") {
    driveSide(PIN_L_RPWM, PIN_L_LPWM, speed, 0);
    driveSide(PIN_R_RPWM, PIN_R_LPWM, speed, 0);
  } else if (dir == "backward") {
    driveSide(PIN_L_RPWM, PIN_L_LPWM, 0, speed);
    driveSide(PIN_R_RPWM, PIN_R_LPWM, 0, speed);
  } else if (dir == "left") {
    driveSide(PIN_L_RPWM, PIN_L_LPWM, 0, speed);   // left side reverse
    driveSide(PIN_R_RPWM, PIN_R_LPWM, speed, 0);   // right side forward
  } else if (dir == "right") {
    driveSide(PIN_L_RPWM, PIN_L_LPWM, speed, 0);   // left side forward
    driveSide(PIN_R_RPWM, PIN_R_LPWM, 0, speed);   // right side reverse
  } else {
    motorsStop();
    return;
  }
  lastDir = dir;
}

/* ─────────────────────────── HTTP HANDLERS ──────────────────── */

void sendJson(int code, const String& body) {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(code, "application/json", body);
}

void handleStatus() {
  String json = "{\"name\":\"" + String(DEVICE_NAME) + "\",\"kind\":\"motor\",\"driver\":\"bts7960x2\"" +
                ",\"ip\":\"" + WiFi.localIP().toString() + "\",\"rssi\":" + String(WiFi.RSSI()) +
                ",\"uptime_s\":" + String((millis() - bootMillis) / 1000) +
                ",\"last_direction\":\"" + lastDir + "\",\"motors\":true}";
  sendJson(200, json);
}

void handleMotor() {
  String dir = server.hasArg("dir") ? server.arg("dir") : "stop";
  int speed   = server.hasArg("speed") ? server.arg("speed").toInt() : DEFAULT_SPEED;
  long ms     = server.hasArg("ms") ? server.arg("ms").toInt() : 0;

  motorsDrive(dir, speed);
  motorStopAt = (dir != "stop" && ms > 0) ? millis() + ms : 0;

  sendJson(200, "{\"motor\":\"" + dir + "\",\"speed\":" + String(speed) + ",\"ms\":" + String(ms) + "}");
}

void handleRoot() {
  String html =
    "<!DOCTYPE html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>" + String(DEVICE_NAME) + "</title><style>"
    "body{font-family:system-ui;background:#05070f;color:#e6edf7;text-align:center;padding:24px}"
    "h1{font-size:16px;letter-spacing:.2em;color:#5eead4}"
    "button{margin:6px;padding:16px 22px;font-size:16px;border-radius:12px;border:1px solid #2dd4bf55;"
    "background:#0d1224;color:#e6edf7;cursor:pointer} button:active{background:#5eead4;color:#05070f}"
    "</style></head><body><h1>" + String(DEVICE_NAME) + " (BTS7960 x2)</h1><div>";
  const char* dirs[] = {"forward", "left", "stop", "right", "backward"};
  for (auto d : dirs) {
    html += "<button onclick=\"fetch('/motor?dir=" + String(d) + "')\">" + String(d) + "</button>";
  }
  html += "</div></body></html>";
  server.send(200, "text/html", html);
}

/* ─────────────────────────── SETUP/LOOP ─────────────────────── */

void setup() {
  Serial.begin(115200);
  bootMillis = millis();

  pinMode(PIN_L_EN, OUTPUT); pinMode(PIN_R_EN, OUTPUT);
  digitalWrite(PIN_L_EN, HIGH);   // enable both BTS7960 boards permanently
  digitalWrite(PIN_R_EN, HIGH);
  pinMode(PIN_L_RPWM, OUTPUT); pinMode(PIN_L_LPWM, OUTPUT);
  pinMode(PIN_R_RPWM, OUTPUT); pinMode(PIN_R_LPWM, OUTPUT);
  motorsStop();

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) { delay(400); Serial.print("."); }
  Serial.println();
  Serial.println("=================================");
  Serial.print  ("  IRIS robot (BTS7960 x2) online:  http://");
  Serial.println(WiFi.localIP());
  Serial.println("  Register in IRIS:  add device " + String(DEVICE_NAME) + " at " + WiFi.localIP().toString() + " as motor");
  Serial.println("=================================");

  if (MDNS.begin(DEVICE_NAME)) MDNS.addService("http", "tcp", 80);

  server.on("/", handleRoot);
  server.on("/status", handleStatus);
  server.on("/motor", handleMotor);
  server.onNotFound([]() { sendJson(404, "{\"error\":\"unknown endpoint\"}"); });
  server.begin();
}

void loop() {
  server.handleClient();
  if (motorStopAt && millis() > motorStopAt) motorsStop();   // timed-move safety
  if (WiFi.status() != WL_CONNECTED) { WiFi.reconnect(); delay(500); }
}
