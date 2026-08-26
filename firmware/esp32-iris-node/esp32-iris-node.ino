/*
 * IRIS Node — universal ESP32 firmware for the IRIS desktop assistant.
 *
 * One sketch, three jobs (enable what you wired):
 *   RELAYS : lights, fans, sockets via a relay module   -> /relay?ch=1&state=on|off|toggle
 *   SERVO  : one hobby servo (curtain, door, latch)     -> /servo?angle=90
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

/* Servo. The SIGNAL wire goes to this GPIO. The servo's POWER does NOT come
 * from any ESP32 pin — the onboard regulator cannot source a servo's stall
 * current and trying it browns out the board mid-move. Feed the red wire from
 * the 5V rail, and name the relay channel that switches it below.
 *
 * SERVO_POWER_CH is why an idle servo does not buzz or cook itself holding a
 * position: after each move the channel opens and the servo goes properly
 * dead instead of fighting its own gearbox. Set it to 0 if you wired the
 * servo permanently to 5V; set PIN_SERVO to -1 if you have no servo. */
const int  PIN_SERVO        = 19;
const int  SERVO_POWER_CH   = 3;     // relay channel feeding servo +, 0 = always on
const int  SERVO_MIN_US     = 500;   // pulse width at 0 deg   (per datasheet)
const int  SERVO_MAX_US     = 2500;  // pulse width at 180 deg
const int  SERVO_TRAVEL_MS  = 700;   // time to allow for a full sweep
const int  SERVO_BOOT_ANGLE = 90;    // pulse held before the first command

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
int  servoAngle = SERVO_BOOT_ANGLE;
unsigned long servoPowerOffAt = 0;   // millis() deadline to drop servo power
const int SERVO_LEDC_CH = 7;         // clear of the channels analogWrite() takes

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

bool servoPowered() {
  if (SERVO_POWER_CH < 1 || SERVO_POWER_CH > RELAY_COUNT) return true;   // hard-wired
  return relayState[SERVO_POWER_CH - 1];
}

/* A servo reads pulse WIDTH, not duty, so the duty has to be recomputed from
 * microseconds: at 50 Hz one frame is 20000 us and 16-bit duty spans that. */
void servoWriteAngle(int angle) {
  if (PIN_SERVO < 0) return;
  angle = constrain(angle, 0, 180);
  servoAngle = angle;
  const long us = SERVO_MIN_US + (long)(SERVO_MAX_US - SERVO_MIN_US) * angle / 180;
  const uint32_t duty = (uint32_t)((us * 65535L) / 20000L);
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(PIN_SERVO, duty);
#else
  ledcWrite(SERVO_LEDC_CH, duty);
#endif
}

/* Order matters. The pulse is already correct before the relay closes, so the
 * servo wakes up knowing where to go; powering first and commanding after
 * makes it snap to whatever the last frame happened to say. */
void servoMove(int angle, bool hold) {
  servoWriteAngle(angle);
  if (SERVO_POWER_CH >= 1 && SERVO_POWER_CH <= RELAY_COUNT) {
    applyRelay(SERVO_POWER_CH - 1, true);
    servoPowerOffAt = hold ? 0 : millis() + SERVO_TRAVEL_MS;
  }
}

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
  json += "],\"motors\":" + String(MOTORS_ENABLED ? "true" : "false");
  if (PIN_SERVO >= 0) {
    json += ",\"servo\":{\"angle\":" + String(servoAngle) +
            ",\"powered\":" + String(servoPowered() ? "true" : "false") +
            ",\"power_channel\":" + String(SERVO_POWER_CH) + "}";
  }
  json += "}";
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

void handleServo() {
  if (PIN_SERVO < 0) { sendJson(400, "{\"error\":\"no servo pin configured\"}"); return; }
  if (!server.hasArg("angle")) { sendJson(400, "{\"error\":\"angle required (0-180)\"}"); return; }
  const String raw = server.arg("angle");
  /* toInt() answers 0 for "abc" and for "" — a silent slam to 0 degrees. */
  for (unsigned i = 0; i < raw.length(); i++)
    if (raw[i] < '0' || raw[i] > '9') { sendJson(400, "{\"error\":\"angle must be 0-180\"}"); return; }
  const long angle = raw.toInt();
  if (raw.length() == 0 || angle > 180) { sendJson(400, "{\"error\":\"angle must be 0-180\"}"); return; }

  const bool hold = server.hasArg("hold") && server.arg("hold") != "0";
  servoMove((int)angle, hold);
  sendJson(200, "{\"servo\":" + String(servoAngle) + ",\"hold\":" +
                String(hold ? "true" : "false") + "}");
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
  if (PIN_SERVO >= 0)
    html += "d.appendChild(document.createElement('br'));const sl=document.createElement('input');"
            "sl.type='range';sl.min=0;sl.max=180;sl.value=" + String(servoAngle) + ";sl.style.width='80%';"
            "const lb=document.createElement('div');lb.textContent='servo '+sl.value+String.fromCharCode(176);"
            "sl.oninput=()=>lb.textContent='servo '+sl.value+String.fromCharCode(176);"
            "sl.onchange=()=>fetch('/servo?angle='+sl.value);d.appendChild(lb);d.appendChild(sl);";
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

  /* The pulse is attached at boot but the power channel stays open, so the
   * servo is silent and drawing nothing until the first command — and when
   * that command arrives the signal is already valid. */
  if (PIN_SERVO >= 0) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcAttach(PIN_SERVO, 50, 16);
#else
    ledcSetup(SERVO_LEDC_CH, 50, 16);
    ledcAttachPin(PIN_SERVO, SERVO_LEDC_CH);
#endif
    servoWriteAngle(SERVO_BOOT_ANGLE);
  }
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
  server.on("/servo", handleServo);
  server.on("/motor", handleMotor);
  server.onNotFound([]() { sendJson(404, "{\"error\":\"unknown endpoint\"}"); });
  server.begin();
}

void loop() {
  server.handleClient();
  if (motorStopAt && millis() > motorStopAt) motorsStop();   // timed-move safety
  if (servoPowerOffAt && millis() > servoPowerOffAt) {       // move done: let go
    servoPowerOffAt = 0;
    if (SERVO_POWER_CH >= 1 && SERVO_POWER_CH <= RELAY_COUNT)
      applyRelay(SERVO_POWER_CH - 1, false);
  }
  if (WiFi.status() != WL_CONNECTED) {                        // WiFi self-heal
    WiFi.reconnect();
    delay(500);
  }
}
