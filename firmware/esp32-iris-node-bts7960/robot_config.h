/*
 * Calibration record and pin validation for the IRIS robot node.
 *
 * Kept in its own header for two reasons. The Arduino .ino preprocessor hoists
 * auto-generated prototypes ABOVE the sketch body, so any top-level function
 * taking a `Config&` would be declared before `struct Config` exists and fail
 * to compile. Headers are not scanned, so the type and everything that operates
 * on it live here. It also puts all the pure, testable validation logic in one
 * place, away from the hardware.
 */
#pragma once

#include <Arduino.h>

#define PWM_DUTY_MAX   255

/* A deadband above about half scale stops being a deadband and turns every
 * gentle request into near-full throttle, so min_duty is capped well below
 * full range. Accepting 255 here made any nonzero request full speed. */
#define MIN_DUTY_MAX   120

struct Config {
  uint8_t  aR, aL, aEn;      /* side A pins */
  uint8_t  bR, bL, bEn;      /* side B pins */
  bool     swapSides;        /* true: config side A is physically the RIGHT side */
  bool     invA, invB;       /* true: positive speed spins that side backwards */
  uint8_t  trimA, trimB;     /* 0..100 % scaling, to make it drive straight */
  uint16_t pwmFreq;          /* Hz (BTS7960 tolerates <= 25 kHz) */
  uint16_t failsafeMs;       /* auto-stop when no command; 0 disables */
  uint16_t rampMs;           /* 0..255 duty ramp time; 0 = instant */
  uint8_t  defaultSpeed;
  uint8_t  minDuty;          /* below this a motor only whines; 0 = off */
  bool     brakeOnStop;      /* true = active brake, false = coast */
};

inline void configFillDefaults(Config& c) {
  c.aR = 25; c.aL = 26; c.aEn = 27;
  c.bR = 32; c.bL = 33; c.bEn = 14;
  c.swapSides = false;
  c.invA = false; c.invB = false;
  c.trimA = 100; c.trimB = 100;
  c.pwmFreq = 20000;          /* above hearing: no motor whine */
  c.failsafeMs = 10000;
  c.rampMs = 180;
  c.defaultSpeed = 200;
  c.minDuty = 0;
  c.brakeOnStop = false;
}

/* Which GPIOs may drive a motor input. Rejecting a pin that cannot work is not
 * pedantry: ledcAttach() on an absent or input-only pin fails silently, and the
 * symptom is "half my driver does nothing" — precisely the bug this firmware
 * exists to eliminate.
 *    6..11         wired to the SPI flash; driving them crashes the board
 *    20,24,28..31  do not exist on the ESP32 package
 *    34..39        input only, physically incapable of driving anything
 *    1,3           UART0 — the serial monitor, and actively driven by it     */
inline bool pinUsable(int p) {
  if (p < 0 || p > 33) return false;
  if (p >= 6 && p <= 11) return false;
  if (p == 20 || p == 24) return false;
  if (p >= 28 && p <= 31) return false;
  if (p == 1 || p == 3) return false;
#ifdef GPIO_IS_VALID_OUTPUT_GPIO
  if (!GPIO_IS_VALID_OUTPUT_GPIO(p)) return false;    /* authoritative per chip */
#endif
  return true;
}

/* Usable, but sampled by the bootloader while the chip starts. A motor driver
 * holding one of these the wrong way can put the board into download mode (0)
 * or tell the flash to run at 1.8 V (12) on the NEXT reset — a fault that looks
 * like a dead board rather than a bad pin choice. Allowed, but reported. */
inline bool pinRisky(int p) { return p == 0 || p == 2 || p == 12 || p == 15; }

inline bool pinsAllUsable(const Config& c) {
  return pinUsable(c.aR) && pinUsable(c.aL) && pinUsable(c.aEn) &&
         pinUsable(c.bR) && pinUsable(c.bL) && pinUsable(c.bEn);
}

inline bool configHasRiskyPin(const Config& c) {
  return pinRisky(c.aR) || pinRisky(c.aL) || pinRisky(c.aEn) ||
         pinRisky(c.bR) || pinRisky(c.bL) || pinRisky(c.bEn);
}

/* One GPIO cannot carry two signals: two LEDC channels fighting over a pin give
 * garbage duty, and RPWM == LPWM makes direction meaningless. The two ENABLE
 * pins are the one legitimate exception — tying every R_EN/L_EN of both modules
 * to a single GPIO is a normal, sensible way to wire this. Returns the
 * offending GPIO, or -1 when the set is fine. */
inline int pinConflict(const Config& c) {
  const uint8_t pwm[4] = { c.aR, c.aL, c.bR, c.bL };
  for (int i = 0; i < 4; i++)
    for (int j = i + 1; j < 4; j++)
      if (pwm[i] == pwm[j]) return pwm[i];
  for (int i = 0; i < 4; i++)
    if (pwm[i] == c.aEn || pwm[i] == c.bEn) return pwm[i];
  return -1;
}

/* NVS can hold anything — a hand-edited partition, a value written by an older
 * build, a half-finished write. A stored ramp_ms of 60000 or min_duty of 255
 * makes a perfectly wired robot look broken, so every field is clamped on load
 * as well as on entry. */
inline void configApplyClamps(Config& c) {
  if (c.trimA > 100) c.trimA = 100;
  if (c.trimB > 100) c.trimB = 100;
  if (c.pwmFreq < 100 || c.pwmFreq > 25000) c.pwmFreq = 20000;
  if (c.failsafeMs > 60000) c.failsafeMs = 60000;
  if (c.rampMs > 3000) c.rampMs = 3000;
  if (c.minDuty > MIN_DUTY_MAX) c.minDuty = MIN_DUTY_MAX;
}

/* server.arg().toInt() answers 0 for "", "abc" and "twelve". For a GPIO number
 * that silently means GPIO 0 — a strapping pin — and for a direction it means
 * "guess". Every numeric argument therefore goes through this strict parser and
 * a malformed one is an error, not a default. */
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
