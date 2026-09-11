/*
 * ============================================================================
 *  IRIS S3 FAST PATH  —  commands over UDP, answered in the same millisecond
 * ============================================================================
 *
 *  Arduino's WebServer serves one client at a time and will sit on a socket
 *  that has connected but not spoken yet, so an open browser tab can queue a
 *  face command behind it for whole seconds. The robot node solved that with
 *  a UDP listener, and IRIS already speaks it: it reads the port out of
 *  /status ("fast":{"udp":8267}) and sends every command as one datagram,
 *  falling back to HTTP if no answer comes back. This is the same listener
 *  for the face board, so an expression lands as fast as the WiFi carries it.
 *
 *  Protocol — deliberately the same as fastlink.h on the robot:
 *      IRIS -> board   "/face?emotion=happy"        exactly the URL's tail
 *      board -> IRIS   the JSON the HTTP handler would have sent
 *
 *  The datagram is trimmed first, so `echo "/blink" | nc -u <ip> 8267` works
 *  from a shell — the newline nc adds must not land inside the last argument.
 * ============================================================================
 */
#pragma once

#include <Arduino.h>
#include <WiFiUdp.h>

/* The board's side of the command: split path from query, answer with JSON. */
typedef void (*FastHandler)(const String& target, String& replyOut);

class FastPath {
 public:
  static const uint16_t UDP_PORT = 8267;

  bool begin(FastHandler handler) {
    handler_ = handler;
    started_ = udp_.begin(UDP_PORT);
    return started_;
  }

  bool started() const { return started_; }
  uint32_t handled() const { return handled_; }

  /* Call this as often as possible — between the two eye redraws too. Each
   * call costs a few microseconds when nothing is waiting. */
  void pump() {
    if (!started_ || handler_ == nullptr) return;
    const int size = udp_.parsePacket();
    if (size <= 0) return;
    if (size > (int)sizeof(buf_) - 1) {           /* nothing legitimate is this big */
      udp_.flush();
      return;
    }
    const int n = udp_.read(buf_, sizeof(buf_) - 1);
    if (n <= 0) return;
    buf_[n] = '\0';

    const IPAddress from = udp_.remoteIP();
    const uint16_t fromPort = udp_.remotePort();

    String target(buf_);
    target.trim();
    String reply;
    handler_(target, reply);
    handled_++;

    /* Best effort: a caller that has already given up must not become an
     * error here, and must certainly not stall the animation. */
    if (udp_.beginPacket(from, fromPort)) {
      udp_.print(reply);
      udp_.endPacket();
    }
  }

 private:
  WiFiUDP udp_;
  FastHandler handler_ = nullptr;
  bool started_ = false;
  uint32_t handled_ = 0;
  char buf_[512];
};
