/*
 * Command arguments, whichever transport the command arrived on.
 *
 * A command now reaches this board four ways — an HTTP request, a UDP datagram,
 * a frame on the page's control socket, and a frame on the cloud socket — and
 * every handler reads its arguments through one of these. That is why all
 * eleven endpoints, calibration included, are written once and work over all
 * four.
 *
 * WHY THERE IS A fromServer() MODE HERE
 * An earlier version left HTTP out of this class, on the theory that WebServer
 * already holds the parameters so the sketch could just branch:
 *
 *     static bool argHas(const char* n) { return cloudArgs ? cloudArgs->has(n)
 *                                                          : argHas(n); }
 *
 * That is a typo for server.hasArg(n), it compiles without a warning on GCC 8,
 * and it is an infinite self-call: the board locked up on the FIRST drive
 * command and never wrote a single duty cycle, which read exactly like dead
 * wiring. Routing HTTP through this class instead removes the branch that the
 * typo lived in — Args::has() names server.hasArg() explicitly and nothing
 * called argHas() can reach itself. A shape that cannot express the bug beats
 * a comment asking the next reader not to write it.
 *
 * No parseLong here — robot_config.h already has one, and two copies of a
 * parser is how they drift.
 */
#pragma once

#include <Arduino.h>
#include <WebServer.h>

/* Defined in the sketch. Read directly for the HTTP case rather than copying
 * every parameter, because WebServer already holds them. */
extern WebServer server;

class Args {
 public:
  /* The live HTTP request. */
  static Args fromServer() {
    Args a;
    a.fromServer_ = true;
    return a;
  }

  static Args fromQuery(const String& query) {
    Args a;
    a.fromServer_ = false;
    int at = 0;
    while (at < (int)query.length()) {
      if (a.count_ >= MAX_ARGS) { a.truncated_ = true; break; }
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

  /* True when the query carried more arguments than this can hold. The
   * dispatcher refuses such a request outright: dropping the tail silently
   * meant a whole-calibration push applied the first ten settings, ignored
   * the rest, and answered 200 — the caller believing it had saved something
   * the board never saw. */
  bool truncated() const { return truncated_; }

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

  /* Wider than the widest endpoint, which is /config: six pin arguments plus
   * eleven calibration fields. Ten was chosen on the assumption that the page
   * always sends them in batches — true of the page, not true of anything
   * else that might push a whole calibration in one request. */
  static const uint8_t MAX_ARGS = 20;
  bool fromServer_ = true;
  bool truncated_ = false;
  uint8_t count_ = 0;
  String keys_[MAX_ARGS];
  String values_[MAX_ARGS];
};
