/*
 * Transport-agnostic command arguments for the IRIS relay/servo node.
 *
 * The same command now arrives two ways: as HTTP query parameters when IRIS is
 * on your LAN, and as a params object over the cloud socket when IRIS is on a
 * VPS. Presenting both through one interface means every endpoint is written
 * once and works over either transport — the alternative is two copies of each
 * handler that drift apart, and the one that drifts is always the one you are
 * not testing.
 *
 * Kept in a header because the Arduino .ino preprocessor hoists auto-generated
 * prototypes ABOVE the sketch body: a top-level function taking an `Args&`
 * would be declared before `class Args` exists and fail to compile. Headers
 * are not scanned.
 */
#pragma once

#include <Arduino.h>
#include <WebServer.h>

/* Defined in the sketch. Args reads it directly for the HTTP case rather than
 * copying every parameter, because WebServer already holds them. */
extern WebServer server;

struct CmdResult {
  int code;
  String body;
};

inline CmdResult cmdOk(const String& body) { return {200, body}; }
inline CmdResult cmdErr(int code, const String& message) {
  return {code, "{\"error\":\"" + message + "\"}"};
}

/* String::toInt() answers 0 for "", "abc" and "twelve". For a relay channel
 * that is a silent channel 0; for a duration it silently means "no timed
 * stop", which on a motor node is the difference between a move that ends and
 * one that does not. Every numeric argument goes through this instead, and a
 * malformed one is an error rather than a default. */
inline bool parseLong(const String& s, long& out) {
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
    if (v > 2000000L) return false;          /* nothing we accept is this big */
  }
  out = neg ? -v : v;
  return true;
}

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

  /* Reads a number, or reports why it could not. `missing` is returned when the
   * argument is absent, so "not given" and "given as nonsense" stay distinct —
   * an absent `ms` means no timed stop, a malformed one is a mistake. */
  bool number(const char* name, long& out, long missing, bool& present) const {
    present = has(name);
    if (!present) { out = missing; return true; }
    return parseLong(get(name), out);
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
