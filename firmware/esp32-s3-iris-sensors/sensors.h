/*
 * ============================================================================
 *  IRIS S3 SENSORS  —  read everything, in one place, without ever blocking
 * ============================================================================
 *
 *  Every reading the board can take, gathered into one struct so the rest of
 *  the firmware never touches a pin directly. That matters because the same
 *  readings go three places and must agree: the HTTP endpoint someone polls,
 *  the telemetry pushed up to IRIS, and the alert check.
 *
 *  ── WIRING WARNINGS (ESP32-S3 pins are 3.3V, NOT 5V tolerant) ──────────────
 *    HC-SR04 ECHO outputs 5V  -> divider: ECHO --[1k]--+--[2k]-- GND, tap +
 *                                (one divider PER SENSOR — four of them)
 *    MQ-2 AO can reach ~4V    -> same 1k/2k divider on AO
 *    PIR HC-SR501 output is 3.3V — safe direct.
 *    Flame (IR) module DO is 3.3V — safe direct. AO is optional, through a divider.
 *    DHT22 DATA is 3.3V — direct, and its VCC goes to 3.3V.
 *
 *  ── ANALOG PINS: GPIO 1..10 only ───────────────────────────────────────────
 *  GPIO 11..20 are ADC2, and ADC2 stops working the moment WiFi comes up: the
 *  reading silently returns garbage rather than failing. setup() warns if a
 *  sensor is on one.
 *
 *  ── FOUR ULTRASONICS, NONE OF THEM BLOCKING ────────────────────────────────
 *  pulseIn() would hold the whole board for up to 30 ms per sensor — with four
 *  of them that is 120 ms a sweep, and the eyes would visibly stutter and a
 *  face command would wait behind it. Instead the ECHO pins raise interrupts:
 *  the board fires ONE sensor, gets on with drawing eyes and answering
 *  commands, and the echo's rising and falling edges are timestamped by the
 *  interrupt. The next loop pass reads the two stamps and moves to the next
 *  sensor. One at a time also means no sensor ever hears another's ping,
 *  which is the crosstalk that looks exactly like a broken sensor.
 *
 *  ── THE DHT ────────────────────────────────────────────────────────────────
 *  The single-wire protocol takes a few milliseconds with interrupts off, and
 *  a DHT22 cannot be read faster than about every 2 s anyway, so it runs on
 *  its own slow timer and the last good value is kept between reads.
 * ============================================================================
 */
#pragma once

#include <Arduino.h>
#include <DHT.h>

#define US_COUNT 4

/* In wiring order: TRIG/ECHO pair 0 is front-left, 1 front-right, 2 rear-left,
 * 3 rear-right. The names are what /sensors reports and what IRIS says. */
static const char* const US_NAMES[US_COUNT] = {
  "front_left", "front_right", "rear_left", "rear_right"
};

struct SensorPins {
  int pir;               /* HC-SR501 OUT             (digital)                 */
  int gasAdc;            /* MQ-2 AO through divider  (ADC1: GPIO 1..10)        */
  int gasDo;             /* MQ-2 DO, optional        (digital, -1 to skip)     */
  int ldrAdc;            /* LDR divider midpoint     (ADC1: GPIO 1..10)        */
  int flame;             /* flame module DO          (digital, often active LOW)*/
  int flameAdc;          /* flame module AO, optional (ADC1, -1 to skip)       */
  int dht;               /* DHT11/DHT22 DATA         (digital)                 */
  int usTrig[US_COUNT];  /* HC-SR04 TRIG pins, -1 for one that is not fitted   */
  int usEcho[US_COUNT];  /* HC-SR04 ECHO pins through dividers                 */
};

struct SensorConfig {
  SensorPins pins;
  bool     flameActiveLow;   /* most IR flame modules pull DO LOW on fire      */
  bool     gasDoActiveLow;   /* MQ-2 modules pull DO LOW above the pot setting */
  int      gasAlarmRaw;      /* 0..4095; watch /sensors in clean air + ~800    */
  uint32_t motionHoldMs;     /* how long motion stays "recent"                 */
  uint32_t distanceSlotMs;   /* gap between firing one ultrasonic and the next */
  uint32_t climateEveryMs;   /* DHT11 needs >= ~1000; DHT22 >= ~2000           */
  uint8_t  dhtType;          /* DHT11 or DHT22 (the library's constants)       */
};

struct SensorReading {
  bool  hasMotion = false;
  bool  motion = false;
  bool  motionRecent = false;

  bool  hasGas = false;
  int   gasRaw = -1;
  bool  hasGasDo = false;
  bool  gasDo = false;
  bool  gasAlarm = false;

  bool  hasLight = false;
  int   lightRaw = -1;
  int   lightPercent = -1;

  bool  hasFlame = false;
  bool  flame = false;
  int   flameRaw = -1;        /* -1 when no AO is wired */

  /* Per sensor, then the two summaries IRIS has always understood: the
   * nearest thing ahead and the nearest thing behind. */
  bool  hasUs[US_COUNT] = {false, false, false, false};
  long  usCm[US_COUNT] = {-1, -1, -1, -1};
  bool  hasDistance = false;   /* any front sensor fitted and answering */
  long  distanceCm = -1;
  bool  hasDistance2 = false;  /* any rear sensor fitted and answering  */
  long  distanceCm2 = -1;

  bool  hasClimate = false;
  float temperatureC = NAN;
  float humidityPct = NAN;
};

/* ── echo timing, done by interrupts so nothing waits ─────────────────────── */
static volatile uint32_t s_usRise[US_COUNT];
static volatile uint32_t s_usFall[US_COUNT];
static volatile bool     s_usDone[US_COUNT];
static int               s_usEchoPin[US_COUNT] = {-1, -1, -1, -1};

#define IRIS_US_ISR(I)                                              \
  static void IRAM_ATTR usIsr##I() {                                \
    if (digitalRead(s_usEchoPin[I])) { s_usRise[I] = micros(); }    \
    else { s_usFall[I] = micros(); s_usDone[I] = true; }            \
  }
IRIS_US_ISR(0)
IRIS_US_ISR(1)
IRIS_US_ISR(2)
IRIS_US_ISR(3)
static void (*const s_usIsr[US_COUNT])() = {usIsr0, usIsr1, usIsr2, usIsr3};

class Sensors {
 public:
  void begin(const SensorConfig& cfg) {
    cfg_ = cfg;
    /* Alarm inputs are biased to their QUIET level, so a wire that has come
     * loose reads "no fire" and "no gas" instead of a floating pin's random
     * alarm at boot. A module that is present overrides the weak pull. */
    if (cfg_.pins.pir >= 0) pinMode(cfg_.pins.pir, INPUT_PULLDOWN);
    if (cfg_.pins.flame >= 0) pinMode(cfg_.pins.flame, cfg_.flameActiveLow ? INPUT_PULLUP : INPUT_PULLDOWN);
    if (cfg_.pins.gasDo >= 0) pinMode(cfg_.pins.gasDo, cfg_.gasDoActiveLow ? INPUT_PULLUP : INPUT_PULLDOWN);
    for (uint8_t i = 0; i < US_COUNT; i++) {
      if (!usFitted(i)) continue;
      pinMode(cfg_.pins.usTrig[i], OUTPUT);
      digitalWrite(cfg_.pins.usTrig[i], LOW);
      pinMode(cfg_.pins.usEcho[i], INPUT);
      s_usEchoPin[i] = cfg_.pins.usEcho[i];
      s_usDone[i] = false;
      attachInterrupt(digitalPinToInterrupt(cfg_.pins.usEcho[i]), s_usIsr[i], CHANGE);
    }
    analogReadResolution(12);
    lastFireMs_ = 0;
    lastClimateAt_ = 0;
    lastMotionMs_ = 0;
    usCur_ = 0;
    usBusy_ = false;

    if (cfg_.pins.dht >= 0) {
      dht_ = new DHT(cfg_.pins.dht, cfg_.dhtType);
      dht_->begin();
    }
  }

  uint8_t ultrasonicsFitted() const {
    uint8_t n = 0;
    for (uint8_t i = 0; i < US_COUNT; i++) if (usFitted(i)) n++;
    return n;
  }

  /* Called every loop. Never blocks for more than the 10 µs trigger pulse. */
  void tick(uint32_t now) {
    if (cfg_.pins.pir >= 0 && digitalRead(cfg_.pins.pir) == HIGH) {
      lastMotionMs_ = now ? now : 1;      /* 0 doubles as "never seen" */
    }

    if (usBusy_) {
      const uint8_t i = usCur_;
      if (s_usDone[i]) {
        const uint32_t rise = s_usRise[i], fall = s_usFall[i];
        long cm = -1;
        if (rise != 0 && fall > rise) {
          const uint32_t us = fall - rise;
          cm = (long)(us / 58);            /* 58 µs per cm, there and back */
          if (cm <= 0 || cm >= 500) cm = -1;
        }
        cachedUs_[i] = cm;
        usBusy_ = false;
        usCur_ = nextFitted(i);
      } else if ((uint32_t)(micros() - usFiredUs_) > 30000UL) {
        cachedUs_[i] = -1;                 /* nothing in range, or unplugged */
        usBusy_ = false;
        usCur_ = nextFitted(i);
      }
    } else if (ultrasonicsFitted() > 0 &&
               (uint32_t)(now - lastFireMs_) >= cfg_.distanceSlotMs) {
      lastFireMs_ = now;
      fire(usCur_);
    }

    /* The DHT refuses to be read quickly, so it gets its own slow timer. A
     * failed read keeps the last good value rather than flapping to nothing —
     * one dropped sample is noise, not news. */
    if (dht_ != nullptr && (uint32_t)(now - lastClimateAt_) >= cfg_.climateEveryMs) {
      lastClimateAt_ = now;
      const float t = dht_->readTemperature();
      const float h = dht_->readHumidity();
      if (!isnan(t) && t > -40.0f && t < 85.0f) cachedTempC_ = t;
      if (!isnan(h) && h >= 0.0f && h <= 100.0f) cachedHumidity_ = h;
    }
  }

  SensorReading read(uint32_t now) const {
    SensorReading r;

    if (cfg_.pins.pir >= 0) {
      r.hasMotion = true;
      r.motion = digitalRead(cfg_.pins.pir) == HIGH;
      r.motionRecent = lastMotionMs_ != 0 &&
                       (uint32_t)(now - lastMotionMs_) < cfg_.motionHoldMs;
    }
    if (cfg_.pins.gasAdc >= 0) {
      r.hasGas = true;
      r.gasRaw = analogRead(cfg_.pins.gasAdc);
      r.gasAlarm = r.gasRaw >= cfg_.gasAlarmRaw;
    }
    if (cfg_.pins.gasDo >= 0) {
      r.hasGasDo = true;
      const int level = digitalRead(cfg_.pins.gasDo);
      r.gasDo = cfg_.gasDoActiveLow ? (level == LOW) : (level == HIGH);
      /* The module's own comparator counts too: either source raises the alarm. */
      r.hasGas = true;
      r.gasAlarm = r.gasAlarm || r.gasDo;
    }
    if (cfg_.pins.ldrAdc >= 0) {
      r.hasLight = true;
      r.lightRaw = analogRead(cfg_.pins.ldrAdc);
      r.lightPercent = (int)((long)r.lightRaw * 100L / 4095L);
    }
    if (cfg_.pins.flame >= 0) {
      r.hasFlame = true;
      const int level = digitalRead(cfg_.pins.flame);
      /* Nearly every IR flame module pulls DO LOW when it sees fire, which is
       * the opposite of what "HIGH means yes" intuition suggests — reading it
       * the wrong way round means the alarm is permanently on, or never. */
      r.flame = cfg_.flameActiveLow ? (level == LOW) : (level == HIGH);
    }
    if (cfg_.pins.flameAdc >= 0) {
      r.hasFlame = true;
      r.flameRaw = analogRead(cfg_.pins.flameAdc);
    }

    long front = -1, rear = -1;
    for (uint8_t i = 0; i < US_COUNT; i++) {
      if (!usFitted(i)) continue;
      r.hasUs[i] = true;
      r.usCm[i] = cachedUs_[i];
      if (cachedUs_[i] < 0) continue;
      long& side = (i < 2) ? front : rear;
      if (side < 0 || cachedUs_[i] < side) side = cachedUs_[i];
    }
    r.hasDistance = front >= 0;
    r.distanceCm = front;
    r.hasDistance2 = rear >= 0;
    r.distanceCm2 = rear;

    if (dht_ != nullptr) {
      r.hasClimate = !isnan(cachedTempC_) || !isnan(cachedHumidity_);
      r.temperatureC = cachedTempC_;
      r.humidityPct = cachedHumidity_;
    }
    return r;
  }

  /* Anything that means "act now, do not wait to be asked". */
  static bool isDangerous(const SensorReading& r) {
    return (r.hasFlame && r.flame) || (r.hasGas && r.gasAlarm);
  }

  String toJson(const SensorReading& r, uint32_t uptimeS) const {
    String j = "{";
    bool first = true;
    auto add = [&](const String& piece) {
      if (!first) j += ",";
      j += piece;
      first = false;
    };
    if (r.hasMotion) {
      add("\"motion\":" + String(r.motion ? "true" : "false"));
      add("\"motion_recent\":" + String(r.motionRecent ? "true" : "false"));
    }
    if (r.hasGas) {
      if (r.gasRaw >= 0) add("\"gas_raw\":" + String(r.gasRaw));
      if (r.hasGasDo) add("\"gas_do\":" + String(r.gasDo ? "true" : "false"));
      add("\"gas_alarm\":" + String(r.gasAlarm ? "true" : "false"));
    }
    if (r.hasLight) {
      add("\"light_raw\":" + String(r.lightRaw));
      add("\"light_percent\":" + String(r.lightPercent));
    }
    if (r.hasFlame) {
      add("\"flame\":" + String(r.flame ? "true" : "false"));
      if (r.flameRaw >= 0) add("\"flame_raw\":" + String(r.flameRaw));
    }
    if (r.hasDistance) add("\"distance_cm\":" + String(r.distanceCm));
    if (r.hasDistance2) add("\"distance_rear_cm\":" + String(r.distanceCm2));
    if (ultrasonicsFitted() > 0) {
      String d = "\"distances\":{";
      bool firstD = true;
      for (uint8_t i = 0; i < US_COUNT; i++) {
        if (!r.hasUs[i]) continue;
        if (!firstD) d += ",";
        d += "\"" + String(US_NAMES[i]) + "\":" + (r.usCm[i] >= 0 ? String(r.usCm[i]) : String("null"));
        firstD = false;
      }
      d += "}";
      add(d);
    }
    if (r.hasClimate) {
      if (!isnan(r.temperatureC)) add("\"temperature_c\":" + String(r.temperatureC, 1));
      if (!isnan(r.humidityPct)) add("\"humidity_pct\":" + String(r.humidityPct, 1));
    }
    add("\"uptime_s\":" + String(uptimeS));
    j += "}";
    return j;
  }

  String namesJson() const {
    String j = "[";
    bool first = true;
    auto add = [&](const char* name, bool present) {
      if (!present) return;
      if (!first) j += ",";
      j += "\"" + String(name) + "\"";
      first = false;
    };
    add("motion", cfg_.pins.pir >= 0);
    add("gas", cfg_.pins.gasAdc >= 0 || cfg_.pins.gasDo >= 0);
    add("light", cfg_.pins.ldrAdc >= 0);
    add("flame", cfg_.pins.flame >= 0 || cfg_.pins.flameAdc >= 0);
    add("ultrasonic", usFitted(0) || usFitted(1));
    add("ultrasonic_rear", usFitted(2) || usFitted(3));
    for (uint8_t i = 0; i < US_COUNT; i++) {
      if (usFitted(i)) add((String("ultrasonic_") + US_NAMES[i]).c_str(), true);
    }
    add("temperature", cfg_.pins.dht >= 0);
    add("humidity", cfg_.pins.dht >= 0);
    j += "]";
    return j;
  }

  const SensorConfig& config() const { return cfg_; }

 private:
  bool usFitted(uint8_t i) const {
    return i < US_COUNT && cfg_.pins.usTrig[i] >= 0 && cfg_.pins.usEcho[i] >= 0;
  }

  uint8_t nextFitted(uint8_t from) const {
    for (uint8_t step = 1; step <= US_COUNT; step++) {
      const uint8_t i = (from + step) % US_COUNT;
      if (usFitted(i)) return i;
    }
    return from;
  }

  void fire(uint8_t i) {
    if (!usFitted(i)) { usCur_ = nextFitted(i); return; }
    s_usRise[i] = 0;
    s_usFall[i] = 0;
    s_usDone[i] = false;
    digitalWrite(cfg_.pins.usTrig[i], HIGH);
    delayMicroseconds(10);
    digitalWrite(cfg_.pins.usTrig[i], LOW);
    usFiredUs_ = micros();
    usBusy_ = true;
  }

  SensorConfig cfg_{};
  uint32_t lastMotionMs_ = 0;
  uint32_t lastFireMs_ = 0;
  uint32_t usFiredUs_ = 0;
  uint32_t lastClimateAt_ = 0;
  uint8_t  usCur_ = 0;
  bool     usBusy_ = false;
  long     cachedUs_[US_COUNT] = {-1, -1, -1, -1};
  float    cachedTempC_ = NAN;
  float    cachedHumidity_ = NAN;
  DHT*     dht_ = nullptr;
};
