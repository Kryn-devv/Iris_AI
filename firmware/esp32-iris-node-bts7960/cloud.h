/*
 * ============================================================================
 *  IRIS CLOUD LINK  —  the board dials out to the brain
 * ============================================================================
 *
 *  WHY THE BOARD CALLS, NOT THE BRAIN
 *  When IRIS runs on your PC it just calls this board's IP. That stops working
 *  the moment IRIS moves to a VPS: this board sits behind your home router
 *  doing NAT, and there is no address from the outside that reaches it.
 *  Port-forwarding would make one — and would also put an ESP32's web server
 *  on the public internet, which is not a trade worth making.
 *
 *  So the direction flips. This board opens a WebSocket OUT to IRIS and keeps
 *  it open; commands come back down the same connection. Outbound connections
 *  are exactly what NAT is built to allow, so this needs no port-forwarding,
 *  no static IP and no dynamic-DNS.
 *
 *  THREE KINDS OF TRAFFIC, ONE SOCKET
 *    up    telemetry   sensor readings on a timer, and immediately when
 *                      something changes, so IRIS can answer "any motion?"
 *                      without a round trip back to this board
 *    up    alert       flame or gas, the instant it is seen
 *    down  cmd         "/face?emotion=happy", answered with a reply frame
 *
 *  RECONNECTING IS THE NORMAL CASE, NOT THE ERROR CASE
 *  Home internet drops, routers reboot, VPSes restart. The link retries
 *  forever with a backoff, and the board stays fully usable on the LAN in the
 *  meantime — the eyes keep animating and the local web page keeps working.
 * ============================================================================
 */
#pragma once

#include <Arduino.h>
#include <WebSocketsClient.h>

#ifndef FIRMWARE_VERSION
#define FIRMWARE_VERSION "iris-bts7960"
#endif

/* Telemetry cadence. Fast enough that "is anyone there?" is current, slow
 * enough that a month of uptime is not a month of chatter. */
#define TELEMETRY_EVERY_MS      5000UL
/* A change worth reporting straight away skips the timer, but never faster
 * than this — a sensor sitting on its threshold flickers. */
#define TELEMETRY_MIN_GAP_MS     700UL
/* Reconnect backoff, doubling up to the cap. */
#define LINK_RETRY_MIN_MS       2000UL
#define LINK_RETRY_MAX_MS      60000UL

/* Returns true when the command succeeded; `out` carries the JSON body
 * either way, so a refusal reaches IRIS as a refusal rather than as data. */
typedef bool (*CommandHandler)(const String& path, const String& query, String& out);

class CloudLink {
 public:
  /* host: "iris.example.com" or an IP. port: 443 for wss, else your port.
   *
   * A full URL is accepted too. CLOUD_HOST wants a bare hostname, but
   * "https://iris.example.com:7731/" is the form you copy out of a browser's
   * address bar, and the WebSocket client would take that literally: the DNS
   * lookup fails and the board retries forever with nothing in the log that
   * points at the cause. So a scheme, a port and a trailing path are parsed
   * off instead, and every correction is announced — see corrections(). */
  void begin(const char* host, uint16_t port, const char* path,
             const char* token, const char* name, const char* kind,
             bool useTls, CommandHandler handler, const char* caCert = nullptr) {
    host_ = host; port_ = port; token_ = token;
    name_ = name; kind_ = kind; useTls_ = useTls;
    handler_ = handler;
    ca_ = (caCert && *caCert) ? caCert : nullptr;
    parseHost();
    /* The token travels in the query string, which is inside the TLS tunnel
     * when useTls is on. Over plain ws:// on the open internet it is readable —
     * hence the warning setup() prints. */
    url_ = String(path) + "?token=" + urlEncode(token_) +
           "&name=" + urlEncode(name_) + "&kind=" + urlEncode(kind_);

    if (!enabled()) return;

    ws_.setReconnectInterval(LINK_RETRY_MIN_MS);
    ws_.enableHeartbeat(15000, 3000, 2);   /* library-level ping/pong */
    ws_.onEvent([this](WStype_t type, uint8_t* payload, size_t length) {
      this->onEvent(type, payload, length);
    });
    /* With a CA the certificate is actually checked, so a man in the middle
     * cannot present his own and read the token. Without one, beginSSL()
     * encrypts but authenticates nothing: the traffic is unreadable to a
     * passive listener and wide open to an active one. That is a real
     * distinction, so verified() reports which of the two you got rather than
     * letting "TLS is on" stand in for "the link is safe". */
    if (useTls_ && ca_)      ws_.beginSslWithCA(host_.c_str(), port_, url_.c_str(), ca_);
    else if (useTls_)        ws_.beginSSL(host_.c_str(), port_, url_.c_str());
    else                     ws_.begin(host_.c_str(), port_, url_.c_str());
    started_ = true;
  }

  bool enabled() const { return host_.length() > 0 && token_.length() > 0; }

  /* What begin() had to fix in CLOUD_HOST, or "" when it was already clean.
   * Reported at boot rather than silently applied: a board dialling a port the
   * sketch does not appear to name is worse than a board that will not dial. */
  const String& corrections() const { return fixes_; }

  const String& host() const { return host_; }
  uint16_t port() const { return port_; }
  bool tls() const { return useTls_; }
  /* True only when the server's certificate is checked against a CA. TLS
   * without this is encrypted but unauthenticated. */
  bool verified() const { return useTls_ && ca_ != nullptr; }
  /* Not const: the library's isConnected() is not. */
  bool connected() { return started_ && ws_.isConnected(); }
  uint32_t commandsHandled() const { return commands_; }
  uint32_t telemetrySent() const { return telemetry_; }

  void loop() {
    if (!started_) return;
    ws_.loop();
  }

  /* Push readings. `changed` marks something that should not wait for the
   * timer — motion appearing, or a reading crossing into danger. */
  void sendTelemetry(const String& sensorsJson, uint32_t now, bool changed) {
    if (!connected()) return;
    const uint32_t gap = (uint32_t)(now - lastTelemetryAt_);
    if (lastTelemetryAt_ != 0) {
      if (changed ? (gap < TELEMETRY_MIN_GAP_MS) : (gap < TELEMETRY_EVERY_MS)) return;
    }
    lastTelemetryAt_ = now ? now : 1;
    telemetry_++;
    ws_.sendTXT("{\"type\":\"telemetry\",\"sensors\":" + sensorsJson + "}");
  }

  /* Something is wrong right now. Sent regardless of the telemetry cadence:
   * a fire is not a reading, it is an interruption. */
  void sendAlert(const char* kind, const char* message) {
    if (!connected()) return;
    ws_.sendTXT(String("{\"type\":\"alert\",\"kind\":\"") + kind +
                "\",\"message\":\"" + escape(message) + "\"}");
  }

 private:
  /* Turns whatever was pasted into CLOUD_HOST into a bare hostname, adopting
   * the scheme and port it carried. Adopting rather than ignoring is the safer
   * half of the trade: "https://host:7731" with CLOUD_PORT left at 443 states
   * its intent unambiguously, and dialling 443 anyway would fail for a reason
   * the user already told us about. */
  void parseHost() {
    String h = host_;
    h.trim();
    fixes_ = "";

    const struct { const char* prefix; bool tls; } schemes[] = {
      {"https://", true}, {"wss://", true}, {"http://", false}, {"ws://", false},
    };
    for (auto& s : schemes) {
      if (h.startsWith(s.prefix)) {
        h = h.substring(strlen(s.prefix));
        if (useTls_ != s.tls) {
          fixes_ += String("scheme in CLOUD_HOST implies TLS ") +
                    (s.tls ? "ON" : "OFF") + "; using that. ";
          useTls_ = s.tls;
        }
        fixes_ += "dropped the scheme. ";
        break;
      }
    }

    /* A path or query has no meaning here — the link's own path is passed
     * separately — so anything from the first slash is dropped. */
    const int slash = h.indexOf('/');
    if (slash >= 0) { h = h.substring(0, slash); fixes_ += "dropped a path. "; }

    /* An IPv6 literal is [::1]:port, so only split on a colon that follows the
     * closing bracket — or on the sole colon when there are no brackets. */
    const int rbracket = h.lastIndexOf(']');
    const int colon = h.indexOf(':', rbracket >= 0 ? rbracket : 0);
    if (colon >= 0) {
      const String tail = h.substring(colon + 1);
      long p = 0;
      bool digits = tail.length() > 0;
      for (unsigned i = 0; i < tail.length(); i++)
        if (tail[i] < '0' || tail[i] > '9') { digits = false; break; }
      if (digits) p = tail.toInt();
      if (digits && p > 0 && p <= 65535) {
        if ((uint16_t)p != port_) {
          fixes_ += "took port " + String(p) + " from CLOUD_HOST. ";
          port_ = (uint16_t)p;
        } else {
          fixes_ += "dropped a duplicate port. ";
        }
        h = h.substring(0, colon);
      }
      /* A non-numeric tail is left alone: it is not a port, and guessing what
       * it was would be worse than dialling the name as written and failing
       * with a DNS error that names it. */
    }

    host_ = h;
  }

  void onEvent(WStype_t type, uint8_t* payload, size_t length) {
    switch (type) {
      case WStype_CONNECTED:
        Serial.println("[cloud] linked to IRIS");
        lastTelemetryAt_ = 0;         /* report immediately on reconnect */
        sendHello();
        break;
      case WStype_DISCONNECTED:
        Serial.println("[cloud] link lost — retrying in the background");
        break;
      case WStype_TEXT:
        handleFrame((const char*)payload, length);
        break;
      case WStype_ERROR:
        Serial.println("[cloud] link error");
        break;
      default:
        break;
    }
  }

  void sendHello() {
    ws_.sendTXT("{\"type\":\"hello\",\"name\":\"" + name_ +
                "\",\"kind\":\"" + kind_ +
                "\",\"firmware\":\"" + String(FIRMWARE_VERSION) +
                "\",\"sensors\":" + helloSensors_ + "}");
  }

  /* Frames arrive from a server this board was configured to trust, but a
   * malformed one must still not be able to wedge the loop, so everything is
   * parsed with plain string scanning and bounded lengths — no JSON library,
   * no allocation surprises, nothing recursive. */
  void handleFrame(const char* data, size_t length) {
    if (length == 0 || length > 2048) return;
    String frame(data, length);

    const String type = jsonString(frame, "type");
    if (type == "ping") { ws_.sendTXT("{\"type\":\"pong\"}"); return; }
    if (type == "pong" || type == "welcome") return;
    if (type != "cmd") return;

    const long id = jsonNumber(frame, "id", -1);
    String path = jsonString(frame, "path");
    if (id < 0 || path.length() == 0 || path[0] != '/') {
      if (id >= 0) reply(id, false, "{\"error\":\"bad command\"}");
      return;
    }

    /* params is a flat object; flatten it into the query string the HTTP
     * handlers already parse, so one code path serves both transports. */
    const String query = paramsToQuery(frame);
    commands_++;
    String result;
    const bool ok = handler_ ? handler_(path, query, result) : false;
    if (result.length() == 0) result = ok ? "{}" : "{\"error\":\"no handler\"}";
    reply(id, ok, result);
  }

  void reply(long id, bool ok, const String& data) {
    ws_.sendTXT("{\"type\":\"reply\",\"id\":" + String(id) +
                ",\"ok\":" + (ok ? "true" : "false") +
                ",\"data\":" + data + "}");
  }

  /* ---- tiny JSON readers: enough for the flat frames above ---- */

  static String jsonString(const String& src, const char* key) {
    const String needle = String("\"") + key + "\":\"";
    int at = src.indexOf(needle);
    if (at < 0) return "";
    at += needle.length();
    String out;
    for (int i = at; i < (int)src.length() && out.length() < 128; i++) {
      const char c = src[i];
      if (c == '\\' && i + 1 < (int)src.length()) { out += src[++i]; continue; }
      if (c == '"') break;
      out += c;
    }
    return out;
  }

  static long jsonNumber(const String& src, const char* key, long fallback) {
    const String needle = String("\"") + key + "\":";
    int at = src.indexOf(needle);
    if (at < 0) return fallback;
    at += needle.length();
    while (at < (int)src.length() && src[at] == ' ') at++;
    bool neg = false;
    if (at < (int)src.length() && (src[at] == '-' || src[at] == '+')) {
      neg = src[at] == '-';
      at++;
    }
    if (at >= (int)src.length() || !isDigit(src[at])) return fallback;
    long value = 0;
    while (at < (int)src.length() && isDigit(src[at])) {
      value = value * 10 + (src[at++] - '0');
      if (value > 100000000L) return fallback;
    }
    return neg ? -value : value;
  }

  /* {"params":{"emotion":"happy","speak_ms":2500}} -> emotion=happy&speak_ms=2500 */
  static String paramsToQuery(const String& frame) {
    int at = frame.indexOf("\"params\":{");
    if (at < 0) return "";
    at += 10;
    String query;
    while (at < (int)frame.length() && query.length() < 256) {
      while (at < (int)frame.length() && (frame[at] == ' ' || frame[at] == ',')) at++;
      if (at >= (int)frame.length() || frame[at] == '}') break;
      if (frame[at] != '"') break;
      at++;
      String key;
      while (at < (int)frame.length() && frame[at] != '"' && key.length() < 32) key += frame[at++];
      at++;                                     /* closing quote */
      while (at < (int)frame.length() && (frame[at] == ' ' || frame[at] == ':')) at++;
      String value;
      if (at < (int)frame.length() && frame[at] == '"') {
        at++;
        while (at < (int)frame.length() && frame[at] != '"' && value.length() < 48) {
          if (frame[at] == '\\' && at + 1 < (int)frame.length()) at++;
          value += frame[at++];
        }
        at++;
      } else {
        while (at < (int)frame.length() && frame[at] != ',' && frame[at] != '}' &&
               value.length() < 48) {
          value += frame[at++];
        }
        value.trim();
      }
      if (key.length() == 0) break;
      if (query.length()) query += "&";
      query += urlEncode(key) + "=" + urlEncode(value);
    }
    return query;
  }

  static String urlEncode(const String& text) {
    String out;
    for (size_t i = 0; i < text.length(); i++) {
      const char c = text[i];
      if (isAlphaNumeric(c) || c == '-' || c == '_' || c == '.' || c == '~') {
        out += c;
      } else {
        char buf[4];
        snprintf(buf, sizeof(buf), "%%%02X", (uint8_t)c);
        out += buf;
      }
    }
    return out;
  }

  static String escape(const String& text) {
    String out;
    for (size_t i = 0; i < text.length() && out.length() < 180; i++) {
      const char c = text[i];
      if (c == '"' || c == '\\') { out += '\\'; out += c; }
      else if (c >= 32) out += c;
    }
    return out;
  }

 public:
  /* Set before begin() so the hello frame can advertise what is wired up. */
  String helloSensors_ = "[]";

 private:
  WebSocketsClient ws_;
  String host_, token_, name_, kind_, url_;
  uint16_t port_ = 443;
  bool useTls_ = true;
  bool started_ = false;
  const char* ca_ = nullptr;
  String fixes_;
  CommandHandler handler_ = nullptr;
  uint32_t lastTelemetryAt_ = 0;
  uint32_t commands_ = 0;
  uint32_t telemetry_ = 0;
};
