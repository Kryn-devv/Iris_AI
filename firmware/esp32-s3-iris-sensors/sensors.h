/*
 * ============================================================================
 *  IRIS S3 SENSORS  —  read everything, in one place
 * ============================================================================
 *
 *  Every reading the board can take, gathered into one struct so the rest of
 *  the firmware never touches a pin directly. That matters because the same
 *  readings go three places and must agree: the HTTP endpoint someone polls,
 *  the telemetry pushed up to IRIS, and the alert check.
 *
 *  ── WIRING WARNINGS (ESP32-S3 pins are 3.3V, NOT 5V tolerant) ──────────────
 *    HC-SR04 ECHO outputs 5V  -> divider: ECHO --[1k]--+--[2k]-- GND, tap +
 *    MQ-2 AO can reach ~4V    -> same 1k/2k divider on AO
 *    PIR HC-SR501 output is 3.3V — safe direct.
 *    Flame (IR) module DO is 3.3V — safe direct. Use DO, not AO.
 *
 *  ── ANALOG PINS: GPIO 1..10 only ───────────────────────────────────────────
 *  GPIO 11..20 are ADC2, and ADC2 stops working the moment WiFi comes up: the
 *  reading silently returns garbage rather than failing. setup() warns if a
 *  sensor is on one.
 *
 *  ── THE SLOW SENSORS ───────────────────────────────────────────────────────
 *  Two sensors here block, and both are measured on a timer and cached rather
 *  than on every request — otherwise the eyes visibly stutter at 40 fps.
 *
 *    ultrasonic  pulseIn() blocks up to its timeout (25 ms with nothing in
 *                range). With TWO of them the readings are also STAGGERED:
 *                fired together, each hears the other's ping and both report
 *                nonsense that looks exactly like a broken sensor.
 *    DHT         the single-wire protocol takes ~25 ms and a DHT11 cannot be
 *                read faster than about once a second anyway.
 * ============================================================================
 */
#pragma once

#include <Arduino.h>
#include <DHT.h>

struct SensorPins {
  int pir;         /* HC-SR501 OUT            (digital)          */
  int gasAdc;      /* MQ-2 AO through divider (ADC1: GPIO 1..10) */
  int ldrAdc;      /* LDR divider midpoint    (ADC1: GPIO 1..10) */
  int flame;       /* flame module DO         (digital, often active LOW) */
  int usTrig;      /* HC-SR04 #1 (front) TRIG                    */
  int usEcho;      /* HC-SR04 #1 (front) ECHO through divider     */
  int usTrig2;     /* HC-SR04 #2 (rear) TRIG,  -1 if not fitted   */
  int usEcho2;     /* HC-SR04 #2 (rear) ECHO through divider      */
  int dht;         /* DHT11/DHT22 DATA        (digital)           */
};

struct SensorConfig {
  SensorPins pins;
  bool     flameActiveLow;   /* most IR flame modules pull DO LOW on fire */
  int      gasAlarmRaw;      /* 0..4095; watch /sensors in clean air + ~800 */
  uint32_t motionHoldMs;     /* how long motion stays "recent"             */
  uint32_t distanceEveryMs;  /* how often to re-measure ONE ultrasonic      */
  uint32_t climateEveryMs;   /* DHT11 needs >= ~1000; DHT22 >= ~2000        */
  uint8_t  dhtType;          /* DHT11 or DHT22 (the library's constants)    */
};

struct SensorReading {
  bool  hasMotion = false;
  bool  motion = false;
  bool  motionRecent = false;

  bool  hasGas = false;
  int   gasRaw = -1;
  bool  gasAlarm = false;

  bool  hasLight = false;
  int   lightRaw = -1;
  int   lightPercent = -1;

  bool  hasFlame = false;
  bool  flame = false;

  bool  hasDistance = false;
  long  distanceCm = -1;

  bool  hasDistance2 = false;
  long  distanceCm2 = -1;

  bool  hasClimate = false;
  float temperatureC = NAN;
  float humidityPct = NAN;
};

class Sensors {
 public:
  void begin(const SensorConfig& cfg) {
    cfg_ = cfg;
    if (cfg_.pins.pir >= 0) pinMode(cfg_.pins.pir, INPUT);
    if (cfg_.pins.flame >= 0) pinMode(cfg_.pins.flame, INPUT);
    if (cfg_.pins.usTrig >= 0) pinMode(cfg_.pins.usTrig, OUTPUT);
    if (cfg_.pins.usEcho >= 0) pinMode(cfg_.pins.usEcho, INPUT);
    if (cfg_.pins.usTrig2 >= 0) pinMode(cfg_.pins.usTrig2, OUTPUT);
    if (cfg_.pins.usEcho2 >= 0) pinMode(cfg_.pins.usEcho2, INPUT);
    analogReadResolution(12);
    lastDistanceAt_ = 0;
    lastClimateAt_ = 0;
    lastMotionMs_ = 0;
    whichUs_ = 0;

    if (cfg_.pins.dht >= 0) {
      dht_ = new DHT(cfg_.pins.dht, cfg_.dhtType);
      dht_->begin();
    }
  }

  /* Called every loop. Cheap: the only slow sensor is rate-limited. */
  void tick(uint32_t now) {
    if (cfg_.pins.pir >= 0 && digitalRead(cfg_.pins.pir) == HIGH) {
      lastMotionMs_ = now ? now : 1;      /* 0 doubles as "never seen" */
    }
    /* One ultrasonic per slot, alternating. Firing both at once means each
     * hears the other's ping, and the false echoes look exactly like a broken
     * sensor rather than like interference. */
    if ((uint32_t)(now - lastDistanceAt_) >= cfg_.distanceEveryMs) {
      lastDistanceAt_ = now;
      const bool haveFront = cfg_.pins.usTrig >= 0 && cfg_.pins.usEcho >= 0;
      const bool haveRear = cfg_.pins.usTrig2 >= 0 && cfg_.pins.usEcho2 >= 0;
      if (haveFront && (whichUs_ == 0 || !haveRear)) {
        cachedDistance_ = measureDistance(cfg_.pins.usTrig, cfg_.pins.usEcho);
      } else if (haveRear) {
        cachedDistance2_ = measureDistance(cfg_.pins.usTrig2, cfg_.pins.usEcho2);
      }
      whichUs_ ^= 1;
    }

    /* The DHT blocks for ~25 ms and refuses to be read quickly, so it gets its
     * own slow timer. A failed read keeps the last good value rather than
     * flapping to nothing — one dropped sample is noise, not news. */
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
    if (cfg_.pins.usTrig >= 0 && cfg_.pins.usEcho >= 0) {
      r.hasDistance = cachedDistance_ >= 0;
      r.distanceCm = cachedDistance_;
    }
    if (cfg_.pins.usTrig2 >= 0 && cfg_.pins.usEcho2 >= 0) {
      r.hasDistance2 = cachedDistance2_ >= 0;
      r.distanceCm2 = cachedDistance2_;
    }
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
      add("\"gas_raw\":" + String(r.gasRaw));
      add("\"gas_alarm\":" + String(r.gasAlarm ? "true" : "false"));
    }
    if (r.hasLight) {
      add("\"light_raw\":" + String(r.lightRaw));
      add("\"light_percent\":" + String(r.lightPercent));
    }
    if (r.hasFlame) add("\"flame\":" + String(r.flame ? "true" : "false"));
    if (r.hasDistance) add("\"distance_cm\":" + String(r.distanceCm));
    if (r.hasDistance2) add("\"distance_rear_cm\":" + String(r.distanceCm2));
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
    add("gas", cfg_.pins.gasAdc >= 0);
    add("light", cfg_.pins.ldrAdc >= 0);
    add("flame", cfg_.pins.flame >= 0);
    add("ultrasonic", cfg_.pins.usTrig >= 0 && cfg_.pins.usEcho >= 0);
    add("ultrasonic_rear", cfg_.pins.usTrig2 >= 0 && cfg_.pins.usEcho2 >= 0);
    add("temperature", cfg_.pins.dht >= 0);
    add("humidity", cfg_.pins.dht >= 0);
    j += "]";
    return j;
  }

  const SensorConfig& config() const { return cfg_; }

 private:
  static long measureDistance(int trig, int echo) {
    digitalWrite(trig, LOW);  delayMicroseconds(3);
    digitalWrite(trig, HIGH); delayMicroseconds(10);
    digitalWrite(trig, LOW);
    /* 25 ms ~ 4 m. Shorter than the classic 30 ms on purpose: this blocks the
     * animation, and nothing useful lives past 4 m for a desk robot. */
    const long duration = pulseIn(echo, HIGH, 25000);
    if (duration <= 0) return -1;
    const long cm = (long)(duration * 0.0343 / 2.0);
    return (cm > 0 && cm < 500) ? cm : -1;
  }

  SensorConfig cfg_{};
  uint32_t lastMotionMs_ = 0;
  uint32_t lastDistanceAt_ = 0;
  uint32_t lastClimateAt_ = 0;
  uint8_t  whichUs_ = 0;
  long     cachedDistance_ = -1;
  long     cachedDistance2_ = -1;
  float    cachedTempC_ = NAN;
  float    cachedHumidity_ = NAN;
  DHT*     dht_ = nullptr;
};
