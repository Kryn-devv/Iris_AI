/*
 * fastlink.h — the low-latency half of the robot's command surface.
 *
 * WHY THIS EXISTS
 * The Arduino WebServer serves exactly ONE client at a time. Read its
 * handleClient(): a socket that has connected but not yet sent its request is
 * held in HC_WAIT_READ for up to HTTP_MAX_DATA_WAIT — five seconds — and while
 * it is held, no other request is even accepted. Browsers open speculative
 * pre-connect sockets that never send anything, so a calibration page sitting
 * open is enough to stall the next drive command for whole seconds. On top of
 * that every HTTP command costs a fresh TCP handshake, a request, a response
 * and a close, because the server answers Connection: close.
 *
 * None of that is fixable from outside the library, and a motor command that
 * arrives "within five seconds" is not a motor command. So the drive path gets
 * two transports that do not go through it:
 *
 *   UDP  :8267  one datagram in, motion out. No handshake, no connection
 *               state, nothing to head-of-line block. This is what IRIS uses,
 *               and it is the millisecond path.
 *   WS   :81    one persistent socket for the calibration page, which cannot
 *               speak UDP. Commands go down it with no per-command setup, and
 *               status is PUSHED back instead of polled — so the page is both
 *               faster to command and fresher to read.
 *
 * Both feed dispatchCommand() in the sketch, the same function the HTTP routes
 * and the cloud socket use. Eleven endpoints, one implementation, four ways in.
 * The HTTP server stays exactly as it was: IRIS releases, custom scripts and
 * curl all keep working, and calibration still works with JavaScript disabled.
 *
 * EXPOSURE
 * Same as the HTTP server's: anyone already on your WiFi can drive the robot.
 * These add no authentication because /motor has none either — they are a
 * faster door into the same room, not a new one. The failsafe still applies to
 * every transport, so a command from any of them stops the motors if it is not
 * renewed.
 */
#pragma once

#include <Arduino.h>
#include <WiFiUdp.h>
#include <WebSocketsServer.h>

/* Defined in the sketch. Fills `out` with the JSON the equivalent HTTP route
 * would have sent, and returns that route's status code — relayed as-is, so a
 * refusal reads the same over these transports as it does over HTTP. */
typedef int (*FastDispatch)(const String& path, const String& query, String& out);

class FastLink {
 public:
  static const uint16_t UDP_PORT = 8267;
  static const uint16_t WS_PORT  = 81;

  /* Pushed status cadence. Faster while the wheels are turning, because that
   * is when the numbers are worth watching; the old page polled at 700 ms
   * flat and still managed to look stale. */
  static const unsigned long PUSH_MOVING_MS = 100;
  static const unsigned long PUSH_IDLE_MS   = 500;

  void begin(FastDispatch dispatch) {
    dispatch_ = dispatch;
    udp_.begin(UDP_PORT);
    ws_ = new WebSocketsServer(WS_PORT);
    ws_->begin();
    ws_->onEvent([this](uint8_t num, WStype_t type, uint8_t* payload, size_t len) {
      if (type != WStype_TEXT || len == 0) return;
      /* payload is not NUL-terminated by the library on every path. */
      String text;
      text.reserve(len + 1);
      for (size_t i = 0; i < len; i++) text += (char)payload[i];
      String reply;
      if (serveWs(text, reply)) ws_->sendTXT(num, reply);
    });
    /* A laptop that drives out of WiFi range leaves a half-open socket behind:
     * TCP has no idea it is gone, connectedClients() keeps counting it, and the
     * status push keeps building JSON for nobody. Ping/pong notices within a
     * few seconds instead. Browsers answer ping frames without being asked. */
    ws_->enableHeartbeat(3000, 2000, 2);
    started_ = true;
  }

  /* Pumped from loop().
   *
   * The UDP half genuinely does not block — parsePacket() answers 0 when
   * there is nothing waiting. The WebSocket half is NOT non-blocking, and an
   * earlier version of this comment claimed it was: on ESP32 the library is
   * built in its sync flavour, so read and write busy-wait on this thread for
   * up to WEBSOCKETS_TCP_TIMEOUT. platformio.ini cuts that from the default
   * 5000 ms to 250 ms, which is what makes a viewer whose TCP window has
   * filled up a hiccup rather than a five-second freeze of the ramp. */
  void loop(bool moving) {
    if (!started_) return;
    ws_->loop();
    pumpUdp();
    pushStatus(moving);
  }

  /* Marks the pushed status dirty so the next loop() sends it immediately
   * rather than at the next cadence tick — called when a command lands, so the
   * page reflects a keypress in the same breath it causes motion. */
  void nudge() { pushDue_ = 0; }

  uint16_t udpPort() const { return UDP_PORT; }
  uint16_t wsPort() const { return WS_PORT; }
  int wsClients() const { return started_ ? ws_->connectedClients() : 0; }

 private:
  /* ── UDP ──────────────────────────────────────────────────────────────────
   * Payload is exactly what would follow the host in a URL:
   *     /motor?dir=forward&speed=200
   * The reply is the same JSON the HTTP route would answer, sent back to
   * whoever asked. A caller that does not want the reply simply does not read
   * it — nothing here waits for them, which is the whole point.
   *
   * One datagram per loop pass, deliberately: draining a flood in one pass
   * would let a stuck sender starve the ramp and failsafe ticks that share
   * this loop, and a motor node must always get to run those. */
  void pumpUdp() {
    const int size = udp_.parsePacket();
    if (size <= 0) return;
    if (size > (int)sizeof(buf_) - 1) {          /* nothing legitimate is this big */
      udp_.flush();
      return;
    }
    const int n = udp_.read(buf_, sizeof(buf_) - 1);
    if (n <= 0) return;
    buf_[n] = '\0';

    const IPAddress from = udp_.remoteIP();
    const uint16_t fromPort = udp_.remotePort();

    String reply;
    serveTarget(String(buf_), reply);

    /* Best effort. A caller that has already gone away must not become an
     * error on this side, and must certainly not block the loop. */
    if (udp_.beginPacket(from, fromPort)) {
      udp_.print(reply);
      udp_.endPacket();
    }
  }

  /* ── the page's control socket ────────────────────────────────────────────
   * A deliberately trivial line protocol, so neither end needs a JSON parser
   * for the envelope:
   *     page -> board   "<id> <path>[?query]"      id is any digits, 0 = no reply wanted
   *     board -> page   "R <id> <code> <json>"     answer to that id
   *     board -> page   "S <json>"                pushed /status, unprompted
   * Drive commands send id 0 and never wait for anything; calibration sends a
   * real id because a refused setting has to be shown to the user. */
  bool serveWs(const String& text, String& reply) {
    int space = text.indexOf(' ');
    long id = 0;
    String target = text;
    if (space > 0 && parseLong(text.substring(0, space), id)) {
      target = text.substring(space + 1);
    } else {
      id = 0;                                    /* bare path: fire and forget */
    }
    target.trim();
    if (!target.length()) return false;

    String body;
    const int code = serveTarget(target, body);
    if (id <= 0) return false;                   /* no reply asked for */
    reply = "R " + String(id) + " " + String(code) + " " + body;
    return true;
  }

  /* Split "path?query" and hand it to the sketch's one dispatcher. */
  int serveTarget(const String& target, String& out) {
    const int q = target.indexOf('?');
    const String path  = (q < 0) ? target : target.substring(0, q);
    const String query = (q < 0) ? String("") : target.substring(q + 1);
    if (!dispatch_) { out = "{\"error\":\"no dispatcher\"}"; return 500; }
    const int code = dispatch_(path, query, out);
    pushDue_ = 0;                                /* the page should see this at once */
    return code;
  }

  /* Pushed status. Skipped entirely with nobody listening, so an unattended
   * robot spends no time building JSON for an empty room. */
  void pushStatus(bool moving) {
    if (ws_->connectedClients() <= 0) return;
    const unsigned long now = millis();
    if (pushDue_ && (long)(now - pushDue_) < 0) return;
    pushDue_ = now + (moving ? PUSH_MOVING_MS : PUSH_IDLE_MS);
    if (!pushDue_) pushDue_ = 1;                 /* 0 is the "send now" sentinel */

    String body;
    if (!dispatch_) return;
    dispatch_("/status", "", body);
    String frame = "S " + body;
    ws_->broadcastTXT(frame);
  }

  FastDispatch dispatch_ = nullptr;
  WebSocketsServer* ws_ = nullptr;
  WiFiUDP udp_;
  bool started_ = false;
  unsigned long pushDue_ = 0;
  /* A drive command is ~40 bytes and the widest /config batch is well under
   * 200. Anything larger is not one of ours. */
  char buf_[256];
};
