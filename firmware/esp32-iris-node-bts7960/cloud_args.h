/*
 * Arguments parsed out of a command arriving over the cloud socket.
 *
 * The robot's handlers read arguments through argHas()/argGet() in the sketch,
 * which pick between the live HTTP request and one of these. That indirection
 * is why all eleven endpoints — calibration included — work over the cloud link
 * without being written a second time.
 *
 * Purely a parsed query bag: the HTTP side is served straight from WebServer,
 * so there is no "from the server" mode here to keep in sync. Nor a parseLong —
 * robot_config.h already has one, and two copies of a parser is how they drift.
 */
#pragma once

#include <Arduino.h>

class Args {
 public:
  static Args fromQuery(const String& query) {
    Args a;
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
    for (uint8_t i = 0; i < count_; i++) if (keys_[i] == name) return true;
    return false;
  }

  String get(const char* name) const {
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

  /* Enough for the widest endpoint here: /config takes six pin arguments plus
   * the calibration flags, and they are sent in batches rather than all at once.
   */
  static const uint8_t MAX_ARGS = 10;
  uint8_t count_ = 0;
  String keys_[MAX_ARGS];
  String values_[MAX_ARGS];
};
