/*
 * IRIS Node — universal ESP32 firmware for the IRIS desktop assistant.
 *
 * One sketch, three jobs (enable what you wired):
 *   RELAYS : lights, fans, sockets via a relay module   -> /relay?ch=1&state=on|off|toggle
 *   MOTORS : robot base via an L298N dual H-bridge      -> /motor?dir=forward&speed=200&ms=1500
 *   STATUS : JSON self-description                      -> /status
 * Plus a built-in control page at /  so you can drive it from any browser.
 *
 * SETUP
 *   1. Arduino IDE -> File > Preferences > Additional boards manager URLs:
 *        https://espressif.github.io/arduino-esp32/package_esp32_index.json
 *   2. Tools > Board > Boards Manager -> install "esp32 by Espressif Systems".
 *   3. Fill in WIFI_SSID / WIFI_PASS and DEVICE_NAME below.
 *   4. Pick your pins in the CONFIG section (defaults suit common modules).
 *   5. Flash, open Serial Monitor at 115200 — the board prints its IP.
 *   6. In IRIS say:  add device kitchen light at <that IP> as relay
 *
 * The board also announces itself as  http://<DEVICE_NAME>.local  via mDNS.
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>

/* ─────────────────────────── CONFIG ─────────────────────────── */

const char* WIFI_SSID   = "YOUR_WIFI_NAME";
const char* WIFI_PASS   = "YOUR_WIFI_PASSWORD";
const char* DEVICE_NAME = "iris-node";        // mDNS name + shown in /status
const char* DEVICE_KIND = "relay";            // "relay" | "motor" | "generic"

/* Relays: list the GPIOs your relay module IN pins are wired to.
 * Channel numbers are 1-based in the API (ch=1 is RELAY_PINS[0]). */
const int  RELAY_PINS[]      = {26, 27, 32, 33};
const int  RELAY_COUNT       = sizeof(RELAY_PINS) / sizeof(RELAY_PINS[0]);
/* Most relay boards are ACTIVE-LOW: the relay closes when the pin is LOW. */
const bool RELAY_ACTIVE_LOW  = true;

/* Motors (L298N):   ENA IN1 IN2  = left,   ENB IN3 IN4 = right  */
const bool MOTORS_ENABLED = false;   // set true for the robot base build
const int  PIN_ENA = 25, PIN_IN1 = 13, PIN_IN2 = 12;
const int  PIN_ENB = 14, PIN_IN3 = 21, PIN_IN4 = 22;
const int  DEFAULT_SPEED = 200;      // 0..255

/* ─────────────────────────── STATE ──────────────────────────── */

WebServer server(80);
bool relayState[16] = {false};
unsigned long motorStopAt = 0;       // millis() deadline for timed moves
unsigned long bootMillis  = 0;

/* ─────────────────────────── HELPERS ────────────────────────── */

void applyRelay(int idx, bool on) {
  relayState[idx] = on;
  digitalWrite(RELAY_PINS[idx], (on != RELAY_ACTIVE_LOW) ? HIGH : LOW);
}

void motorsWrite(int in1, int in2, int in3, int in4, int speed) {
  digitalWrite(PIN_IN1, in1); digitalWrite(PIN_IN2, in2);
  digitalWrite(PIN_IN3, in3); digitalWrite(PIN_IN4, in4);
  analogWrite(PIN_ENA, speed); analogWrite(PIN_ENB, speed);
}

void motorsStop() { motorsWrite(LOW, LOW, LOW, LOW, 0); motorStopAt = 0; }

void sendJson(int code, const String& body) {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(code, "application/json", body);
}

/* ─────────────────────────── HANDLERS ───────────────────────── */

void handleStatus() {
  String json = "{\"name\":\"" + String(DEVICE_NAME) + "\",\"kind\":\"" + DEVICE_KIND +
                "\",\"ip\":\"" + WiFi.localIP().toString() +
                "\",\"rssi\":" + String(WiFi.RSSI()) +
                ",\"uptime_s\":" + String((millis() - bootMillis) / 1000) +
                ",\"relays\":[";
  for (int i = 0; i < RELAY_COUNT; i++) {
    json += relayState[i] ? "\"on\"" : "\"off\"";
    if (i < RELAY_COUNT - 1) json += ",";
  }
  json += "],\"motors\":" + String(MOTORS_ENABLED ? "true" : "false") + "}";
  sendJson(200, json);
}

void handleRelay() {
  int ch = server.hasArg("ch") ? server.arg("ch").toInt() : 1;
  String state = server.hasArg("state") ? server.arg("state") : "toggle";
  if (ch < 1 || ch > RELAY_COUNT) { sendJson(400, "{\"error\":\"bad channel\"}"); return; }
  int idx = ch - 1;
  bool on = (state == "toggle") ? !relayState[idx] : (state == "on");
  applyRelay(idx, on);
  sendJson(200, "{\"ch\":" + String(ch) + ",\"state\":\"" + (on ? "on" : "off") + "\"}");
}

void handleMotor() {
  if (!MOTORS_ENABLED) { sendJson(400, "{\"error\":\"motors disabled in firmware\"}"); return; }
  String dir = server.hasArg("dir") ? server.arg("dir") : "stop";
  int speed  = server.hasArg("speed") ? constrain(server.arg("speed").toInt(), 0, 255) : DEFAULT_SPEED;
  long ms    = server.hasArg("ms") ? server.arg("ms").toInt() : 0;

  if      (dir == "forward")  motorsWrite(HIGH, LOW,  HIGH, LOW,  speed);
  else if (dir == "backward") motorsWrite(LOW,  HIGH, LOW,  HIGH, speed);
  else if (dir == "left")     motorsWrite(LOW,  HIGH, HIGH, LOW,  speed);
  else if (dir == "right")    motorsWrite(HIGH, LOW,  LOW,  HIGH, speed);
  else                        { motorsStop(); sendJson(200, "{\"motor\":\"stop\"}"); return; }

  motorStopAt = (ms > 0) ? millis() + ms : 0;
  sendJson(200, "{\"motor\":\"" + dir + "\",\"speed\":" + String(speed) + ",\"ms\":" + String(ms) + "}");
}

void handleRoot() {
  String html =
    "<!DOCTYPE html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>" + String(DEVICE_NAME) + "</title><style>"
    "body{font-family:system-ui;background:#05070f;color:#e6edf7;text-align:center;padding:24px}"
    "h1{font-size:18px;letter-spacing:.2em;color:#5eead4}"
    "button{margin:6px;padding:14px 22px;font-size:15px;border-radius:12px;border:1px solid #2dd4bf55;"
    "background:#0d1224;color:#e6edf7;cursor:pointer} button:active{background:#5eead4;color:#05070f}"
    "</style></head><body><h1>" + String(DEVICE_NAME) + "</h1><div id=r></div>";
  html += "<script>const R=" + String(RELAY_COUNT) + ";const d=document.getElementById('r');";
  html += "for(let i=1;i<=R;i++){const b=document.createElement('button');b.textContent='Relay '+i;"
          "b.onclick=()=>fetch('/relay?ch='+i+'&state=toggle');d.appendChild(b);}";
  if (MOTORS_ENABLED)
    html += "['forward','left','stop','right','backward'].forEach(k=>{const b=document.createElement('button');"
            "b.textContent=k;b.onclick=()=>fetch('/motor?dir='+k);d.appendChild(b);});";
  html += "</script></body></html>";
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "text/html", html);
}

/* ─────────────────────────── SETUP/LOOP ─────────────────────── */

void setup() {
  Serial.begin(115200);
  bootMillis = millis();

  for (int i = 0; i < RELAY_COUNT; i++) { pinMode(RELAY_PINS[i], OUTPUT); applyRelay(i, false); }
  if (MOTORS_ENABLED) {
    pinMode(PIN_ENA, OUTPUT); pinMode(PIN_IN1, OUTPUT); pinMode(PIN_IN2, OUTPUT);
    pinMode(PIN_ENB, OUTPUT); pinMode(PIN_IN3, OUTPUT); pinMode(PIN_IN4, OUTPUT);
    motorsStop();
  }

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) { delay(400); Serial.print("."); }
  Serial.println();
  Serial.println("=================================");
  Serial.print  ("  IRIS node online:  http://");
  Serial.println(WiFi.localIP());
  Serial.println("  Register in IRIS:  add device " + String(DEVICE_NAME) + " at " + WiFi.localIP().toString());
  Serial.println("=================================");

  if (MDNS.begin(DEVICE_NAME)) MDNS.addService("http", "tcp", 80);

  server.on("/", handleRoot);
  server.on("/status", handleStatus);
  server.on("/relay", handleRelay);
  server.on("/motor", handleMotor);
  server.onNotFound([]() { sendJson(404, "{\"error\":\"unknown endpoint\"}"); });
  server.begin();
}

void loop() {
  server.handleClient();
  if (motorStopAt && millis() > motorStopAt) motorsStop();   // timed-move safety
  if (WiFi.status() != WL_CONNECTED) {                        // WiFi self-heal
    WiFi.reconnect();
    delay(500);
  }
}
