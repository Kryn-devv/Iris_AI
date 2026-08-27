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
 * TWO WAYS IT REACHES IRIS
 *   A. IRIS ON YOUR LAN — leave CLOUD_HOST empty. IRIS calls this board's IP.
 *   B. IRIS ON A VPS    — set CLOUD_HOST / CLOUD_TOKEN. This board then dials
 *      OUT and holds a WebSocket open; commands come back down it. That is the
 *      only direction that works: the board is behind your router's NAT, so no
 *      address from the internet reaches it. Outbound is exactly what NAT
 *      allows, so this needs no port-forwarding, no static IP, no dynamic DNS.
 *   Both can be on at once, and the local page keeps working either way —
 *   which is what makes a wiring fault diagnosable while the link is down.
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

#include "node_args.h"
#include "cloud.h"

/* ─────────────────────────── CONFIG ─────────────────────────── */

const char* WIFI_SSID   = "YOUR_WIFI_NAME";
const char* WIFI_PASS   = "YOUR_WIFI_PASSWORD";
const char* DEVICE_NAME = "relays";           // mDNS name + shown in /status
const char* DEVICE_KIND = "relay";            // "relay" | "motor" | "generic"

/* ── IRIS in the cloud. Leave CLOUD_HOST empty for a LAN-only setup. ──
 * CLOUD_TOKEN must equal NODE_LINK_TOKEN in IRIS's .env. Without TLS the
 * token crosses the internet in clear text; the board warns at boot. */
const char* CLOUD_HOST  = "";                 // "iris.example.com" or an IP
const uint16_t CLOUD_PORT = 443;              // 443 for https/wss, else yours
const bool CLOUD_TLS    = true;               // false only on your own LAN
const char* CLOUD_TOKEN = "";                 // = NODE_LINK_TOKEN
/* Optional. With your server's CA here the certificate is actually checked,
 * which is what stops a man in the middle presenting his own and reading the
 * token. Empty means encrypted-but-unverified, and the board says so at boot.
 * Paste the PEM including the BEGIN/END lines. */
const char* CLOUD_CA_CERT = "";

/* Relays: list the GPIOs your relay module IN pins are wired to.
 *
 * CHANNEL NUMBERS COME FROM THIS ORDER, NOT FROM THE MODULE. Channel 1 is
 * RELAY_PINS[0] — whichever relay you happened to wire to GPIO 26 — and the
 * label silk-screened "IN1" on the board has no say in it. To renumber, either
 * move a wire or reorder this list; nothing else needs to change.
 *
 * Reordering moves the NUMBER, so SERVO_POWER_CH below follows the number and
 * not the pin. Put the servo's channel somewhere you will remember. */
const int  RELAY_PINS[]      = {26, 27, 32, 33};
const int  RELAY_COUNT       = sizeof(RELAY_PINS) / sizeof(RELAY_PINS[0]);

/* What is actually plugged into each channel, in the same order. Names are
 * reported by /status and printed on the board's own page, so "which relay is
 * channel 2" is answered by looking rather than by clicking each one and
 * listening. Rename freely — IRIS only ever sends numbers. */
const char* RELAY_NAMES[]    = {"light", "fan", "servo power", "spare"};

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

#define WIFI_JOIN_MS 25000UL        // then carry on and keep retrying in loop()

/* ─────────────────────────── STATE ──────────────────────────── */

WebServer server(80);
#define RELAY_MAX 16
bool relayState[RELAY_MAX] = {false};

/* Both of these would otherwise be silent memory bugs rather than build
 * errors: more pins than relayState can hold, or a names list that does not
 * line up with the pins it labels. */
static_assert(RELAY_COUNT <= RELAY_MAX,
              "RELAY_PINS has more entries than relayState can hold — raise RELAY_MAX");
static_assert(sizeof(RELAY_NAMES) / sizeof(RELAY_NAMES[0]) == RELAY_COUNT,
              "RELAY_NAMES must have exactly one entry per RELAY_PINS entry");
unsigned long motorStopAt = 0;       // millis() deadline for timed moves
unsigned long bootMillis  = 0;
int  servoAngle = SERVO_BOOT_ANGLE;
unsigned long servoPowerOffAt = 0;   // millis() deadline to drop servo power
const int SERVO_LEDC_CH = 7;         // clear of the channels analogWrite() takes
CloudLink cloud;

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

/* ───────────────────── COMMANDS (transport-agnostic) ─────────────────────
 * Each returns a CmdResult and reads its arguments through Args, so the same
 * code answers an HTTP request from a LAN-hosted IRIS and a frame from the
 * cloud socket. Writing them twice is how the two transports drift apart.  */

CmdResult cmdStatus(const Args&) {
  String json = "{\"name\":\"" + String(DEVICE_NAME) + "\",\"kind\":\"" + DEVICE_KIND +
                "\",\"ip\":\"" + WiFi.localIP().toString() +
                "\",\"rssi\":" + String(WiFi.RSSI()) +
                ",\"uptime_s\":" + String((millis() - bootMillis) / 1000) +
                ",\"link\":\"" + String(cloud.enabled() ? (cloud.connected() ? "cloud" : "dialling")
                                                        : "lan") +
                "\",\"relays\":[";
  for (int i = 0; i < RELAY_COUNT; i++) {
    json += relayState[i] ? "\"on\"" : "\"off\"";
    if (i < RELAY_COUNT - 1) json += ",";
  }
  json += "],\"channels\":[";
  for (int i = 0; i < RELAY_COUNT; i++) {
    json += "{\"ch\":" + String(i + 1) + ",\"name\":\"" + RELAY_NAMES[i] +
            "\",\"gpio\":" + String(RELAY_PINS[i]) +
            ",\"state\":\"" + (relayState[i] ? "on" : "off") + "\"}";
    if (i < RELAY_COUNT - 1) json += ",";
  }
  json += "],\"motors\":" + String(MOTORS_ENABLED ? "true" : "false");
  if (PIN_SERVO >= 0) {
    json += ",\"servo\":{\"angle\":" + String(servoAngle) +
            ",\"powered\":" + String(servoPowered() ? "true" : "false") +
            ",\"power_channel\":" + String(SERVO_POWER_CH) + "}";
  }
  json += "}";
  return cmdOk(json);
}

CmdResult cmdRelay(const Args& args) {
  long ch = 1;
  bool given = false;
  if (!args.number("ch", ch, 1, given)) return cmdErr(400, "ch must be a number");
  if (ch < 1 || ch > RELAY_COUNT) return cmdErr(400, "bad channel");

  const String state = args.has("state") ? args.get("state") : "toggle";
  if (state != "on" && state != "off" && state != "toggle")
    return cmdErr(400, "state must be on, off or toggle");

  const int idx = (int)ch - 1;
  const bool on = (state == "toggle") ? !relayState[idx] : (state == "on");
  applyRelay(idx, on);
  return cmdOk("{\"ch\":" + String(ch) + ",\"state\":\"" + (on ? "on" : "off") + "\"}");
}

CmdResult cmdServo(const Args& args) {
  if (PIN_SERVO < 0) return cmdErr(400, "no servo pin configured");
  if (!args.has("angle")) return cmdErr(400, "angle required (0-180)");

  long angle = 0;
  /* Refused rather than clamped: clamping parks the horn against an end stop
   * and leaves the servo stalling there, which is how gears strip. */
  if (!parseLong(args.get("angle"), angle) || angle < 0 || angle > 180)
    return cmdErr(400, "angle must be 0-180");

  const bool hold = args.has("hold") && args.get("hold") != "0";
  servoMove((int)angle, hold);
  return cmdOk("{\"servo\":" + String(servoAngle) + ",\"hold\":" +
               String(hold ? "true" : "false") + "}");
}

CmdResult cmdMotor(const Args& args) {
  if (!MOTORS_ENABLED) return cmdErr(400, "motors disabled in firmware");

  const String dir = args.has("dir") ? args.get("dir") : "stop";

  long speed = DEFAULT_SPEED, ms = 0;
  bool given = false;
  if (!args.number("speed", speed, DEFAULT_SPEED, given))
    return cmdErr(400, "speed must be a number");
  speed = constrain(speed, 0L, 255L);
  if (!args.number("ms", ms, 0, given)) return cmdErr(400, "ms must be a number");
  /* Clamping a negative duration to 0 inverts the intent — it would mean "run
   * until something else stops you" when the caller asked for a bounded move. */
  if (ms < 0) return cmdErr(400, "ms cannot be negative");

  /* Validation before any state change: a typo used to cancel the pending
   * auto-stop and THEN return 400, leaving the motors running with nothing
   * scheduled to stop them. */
  if      (dir == "forward")  motorsWrite(HIGH, LOW,  HIGH, LOW,  speed);
  else if (dir == "backward") motorsWrite(LOW,  HIGH, LOW,  HIGH, speed);
  else if (dir == "left")     motorsWrite(LOW,  HIGH, HIGH, LOW,  speed);
  else if (dir == "right")    motorsWrite(HIGH, LOW,  LOW,  HIGH, speed);
  else if (dir == "stop")     { motorsStop(); return cmdOk("{\"motor\":\"stop\"}"); }
  else                        return cmdErr(400, "dir must be forward, backward, left, right or stop");

  motorStopAt = (ms > 0) ? millis() + ms : 0;
  return cmdOk("{\"motor\":\"" + dir + "\",\"speed\":" + String(speed) +
               ",\"ms\":" + String(ms) + "}");
}

CmdResult dispatch(const String& path, const Args& args) {
  if (path == "/status" || path == "status") return cmdStatus(args);
  if (path == "/relay"  || path == "relay")  return cmdRelay(args);
  if (path == "/servo"  || path == "servo")  return cmdServo(args);
  if (path == "/motor"  || path == "motor")  return cmdMotor(args);
  return cmdErr(404, "unknown endpoint");
}

/* ─────────────────────────── HANDLERS ───────────────────────── */

void serveDispatch(const char* path) {
  const CmdResult r = dispatch(path, Args::fromServer());
  sendJson(r.code, r.body);
}

/* The cloud socket's side of the same dispatch. A refusal reaches IRIS as a
 * refusal rather than as data, so a bad command is not reported as success. */
bool cloudCommand(const String& path, const String& query, String& out) {
  const CmdResult r = dispatch(path, Args::fromQuery(query));
  out = r.body;
  return r.code < 400;
}

void handleRoot() {
  String html =
    "<!DOCTYPE html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>" + String(DEVICE_NAME) + "</title><style>"
    "body{font-family:system-ui;background:#05070f;color:#e6edf7;text-align:center;padding:24px}"
    "h1{font-size:18px;letter-spacing:.2em;color:#5eead4}"
    "button{margin:6px;padding:14px 22px;font-size:15px;border-radius:12px;border:1px solid #2dd4bf55;"
    "background:#0d1224;color:#e6edf7;cursor:pointer;min-width:118px;line-height:1.5}"
    "button small{color:#8b96ad;font-size:11px} button:active{background:#5eead4;color:#05070f}"
    "button:active small{color:#05070f}"
    "</style></head><body><h1>" + String(DEVICE_NAME) + "</h1><div id=r></div>";
  /* Each button carries its channel number, its name and its GPIO, because
     "Relay 3" alone does not tell you which thing in the room it switches. */
  html += "<script>const C=[";
  for (int i = 0; i < RELAY_COUNT; i++) {
    html += "{n:" + String(i + 1) + ",s:'" + String(RELAY_NAMES[i]) +
            "',g:" + String(RELAY_PINS[i]) + "}";
    if (i < RELAY_COUNT - 1) html += ",";
  }
  html += "];const d=document.getElementById('r');";
  html += "C.forEach(c=>{const b=document.createElement('button');"
          "b.innerHTML='<b>'+c.n+'</b> &middot; '+c.s+'<br><small>GPIO '+c.g+'</small>';"
          "b.onclick=()=>fetch('/relay?ch='+c.n+'&state=toggle');d.appendChild(b);});";
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
  /* Time-boxed. Waiting forever meant a wrong password produced a board that
   * printed dots and nothing else — indistinguishable from dead hardware.
   * loop() keeps retrying, so a router that comes up late still connects. */
  const unsigned long joinUntil = millis() + WIFI_JOIN_MS;
  while (WiFi.status() != WL_CONNECTED && millis() < joinUntil) {
    delay(400);
    Serial.print(".");
  }
  Serial.println();
  Serial.println("=================================");
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print  ("  IRIS node online:  http://");
    Serial.println(WiFi.localIP());
    Serial.println("  Register in IRIS:  add device " + String(DEVICE_NAME) + " at " + WiFi.localIP().toString());
    if (MDNS.begin(DEVICE_NAME)) MDNS.addService("http", "tcp", 80);
    Serial.println("  Relay channels:");
    for (int i = 0; i < RELAY_COUNT; i++)
      Serial.printf("    ch%d  GPIO %-2d  %s\n", i + 1, RELAY_PINS[i], RELAY_NAMES[i]);
    if (PIN_SERVO >= 0)
      Serial.printf("    servo signal GPIO %d, powered by ch%d\n",
                    PIN_SERVO, SERVO_POWER_CH);
  } else {
    Serial.println("  WiFi did NOT join. Check WIFI_SSID / WIFI_PASS.");
    Serial.println("  Still retrying in the background; relays are safe (all off).");
  }
  Serial.println("=================================");

  server.on("/", handleRoot);
  server.on("/status", []() { serveDispatch("/status"); });
  server.on("/relay",  []() { serveDispatch("/relay");  });
  server.on("/servo",  []() { serveDispatch("/servo");  });
  server.on("/motor",  []() { serveDispatch("/motor");  });
  server.onNotFound([]() { sendJson(404, "{\"error\":\"unknown endpoint\"}"); });
  server.begin();

  cloud.begin(CLOUD_HOST, CLOUD_PORT, "/api/v1/nodes/link", CLOUD_TOKEN,
              DEVICE_NAME, DEVICE_KIND, CLOUD_TLS, cloudCommand, CLOUD_CA_CERT);
  if (cloud.enabled()) {
    Serial.println("  Cloud link:       dialling " + cloud.host() + ":" +
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
  } else {
    Serial.println("  Cloud link:       off (LAN only). Set CLOUD_HOST and "
                   "CLOUD_TOKEN to reach a VPS-hosted IRIS.");
  }
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
    return;                        /* no link to pump until WiFi is back */
  }
  cloud.loop();                    /* dials out, reconnects, handles commands */
}
