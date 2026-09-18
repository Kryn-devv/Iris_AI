/*
 * vitals.h — MAX30100 / MAX30102 pulse oximeter: heart rate and SpO2.
 *
 * ── READ THIS FIRST ──────────────────────────────────────────────────────
 * This is not a medical device and nothing it reports is a diagnosis. It is
 * a $2 optical sensor with no calibration, no certification and no clinical
 * validation. The heart rate is usually close. The SpO2 is an estimate from
 * a published curve fit, and a real pulse oximeter is calibrated per unit
 * against arterial blood gas measurements — this is not, so treat its number
 * as a trend, not a reading.
 *
 * Do not make a health decision from it. If someone seems unwell, the
 * response is a doctor, not a number off a breadboard. IRIS is told to say
 * as much whenever she reports one of these.
 * ─────────────────────────────────────────────────────────────────────────
 *
 * HOW IT WORKS, BRIEFLY
 * Two LEDs (red and infrared) shine into a fingertip and a photodiode
 * measures what comes back. Blood volume rises and falls with each beat, so
 * the reflected light does too: a large steady level (DC) with a small
 * wobble on top (AC), about 1% of it. The wobble's rate is the pulse.
 *
 * SpO2 comes from the fact that oxygenated and deoxygenated haemoglobin
 * absorb red and infrared differently. The ratio of ratios,
 *     R = (AC_red / DC_red) / (AC_ir / DC_ir)
 * maps to saturation. We use the standard linear approximation, which is
 * where the accuracy caveat above comes from.
 *
 * TWO DIFFERENT CHIPS, ONE NAME
 * Modules sold as "MAX30100" are very often MAX30102 — a different die with
 * a different register map and a different FIFO layout. Driving one as the
 * other gives silence or nonsense, so this reads the part ID and configures
 * whichever it actually found. Both are handled.
 *
 * Wiring (ESP32-S3, sharing the aux I2C bus with the MPU6050):
 *   VIN -> 3V3      SDA -> GPIO 38
 *   GND -> GND      SCL -> GPIO 39
 * It sits at 0x57, so it cannot clash with the IMU (0x68) or an OLED (0x3C).
 *
 * IF IT NEVER ANSWERS: the common purple breakout has its I2C pull-up
 * resistors tied to its own 1.8V regulator, not to VIN. On a 3.3V bus that
 * sometimes works and sometimes does not. The fix is to lift those two
 * resistors and fit 4.7k from SDA and SCL to 3V3 instead.
 */
#pragma once

#include <Arduino.h>
#include <Wire.h>
#include <math.h>

class Vitals {
public:
  static const uint8_t ADDR = 0x57;

  enum Variant { PART_UNKNOWN, PART_MAX30100, PART_MAX30102 };

  bool begin(TwoWire& bus) {
    bus_ = &bus;

    uint8_t part = 0;
    if (!readRegs(0xFF, &part, 1)) return fail("no answer at 0x57 — see the pull-up note in vitals.h");

    if (part == 0x11) {
      variant_ = PART_MAX30100;
      if (!startMax30100()) return false;
    } else if (part == 0x15) {
      variant_ = PART_MAX30102;
      if (!startMax30102()) return false;
    } else {
      return fail("part id 0x%02X is neither MAX30100 nor MAX30102", part);
    }

    ready_ = true;
    err_[0] = '\0';
    return true;
  }

  bool ready() const { return ready_; }
  Variant variant() const { return variant_; }
  const char* partName() const {
    switch (variant_) {
      case PART_MAX30100: return "MAX30100";
      case PART_MAX30102: return "MAX30102";
      default: return "unknown";
    }
  }
  const char* error() const { return err_; }

  /* Drains whatever the sensor has queued. Never blocks: the FIFO fills at
   * 100 Hz and holds 16 samples, so anything calling this more often than
   * every 100 ms keeps up comfortably. Capped per call so one slow loop
   * cannot turn into a 200-byte I2C read that stalls the eyes. */
  void tick(uint32_t now) {
    if (!ready_ || now - lastPollMs_ < POLL_MS) return;
    lastPollMs_ = now;

    uint32_t ir = 0, red = 0;
    uint8_t taken = 0;
    while (taken < MAX_PER_TICK && readFifoSample(ir, red)) {
      processSample(now, ir, red);
      taken++;
    }
    if (taken == 0) emptyPolls_++;

    /* A finger that leaves mid-measurement must not leave a stale heart rate
     * standing on the display for the next person to read as theirs. */
    if (!fingerOn_ && (bpm_ > 0 || spo2_ > 0)) reset();
  }

  /* ── what IRIS asks for ──────────────────────────────────────────────── */

  bool fingerPresent() const { return fingerOn_; }

  /* 0 until enough beats agree. Better to say "keep still" than to publish a
   * number made from two noisy intervals. */
  int heartRate() const { return (beatsSeen_ >= MIN_BEATS_FOR_BPM) ? (int)lroundf(bpm_) : 0; }

  /* 0 until a heart rate is settled, since the ratio is only meaningful over
   * whole beats. Clamped to a plausible range — outside it the reading is
   * noise wearing a number's clothes, and should not be shown at all. */
  int spo2() const {
    if (heartRate() == 0 || spo2_ < 70.0f || spo2_ > 100.0f) return 0;
    return (int)lroundf(spo2_);
  }

  /* True when both numbers are settled enough to say out loud. */
  bool settled() const { return heartRate() > 0 && spo2() > 0; }

  uint32_t irDc() const { return (uint32_t)irDc_; }
  uint32_t redDc() const { return (uint32_t)redDc_; }
  uint32_t beats() const { return beatsSeen_; }

  /* Forgets the current measurement. Called when a finger lifts, and worth
   * calling before a new person puts theirs down. */
  void reset() {
    bpm_ = 0; spo2_ = 0; beatsSeen_ = 0; lastBeatMs_ = 0;
    intervalCount_ = 0; beatState_ = WAIT_HIGH;
    acAmplitude_ = 0;
    irAcSum_ = redAcSum_ = 0; acWindow_ = 0;
  }

  String toJson() const {
    if (!ready_) return String("{\"fitted\":false}");
    String j = "{\"fitted\":true";
    j += ",\"part\":\"" + String(partName()) + "\"";
    j += ",\"finger\":" + String(fingerOn_ ? "true" : "false");
    j += ",\"bpm\":" + String(heartRate());
    j += ",\"spo2\":" + String(spo2());
    j += ",\"settled\":" + String(settled() ? "true" : "false");
    j += ",\"ir_dc\":" + String(irDc());
    j += ",\"beats\":" + String(beatsSeen_);
    /* Said in the data as well as the docs, because the number travels
     * further than the file it came from. */
    j += ",\"medical_grade\":false";
    j += "}";
    return j;
  }

private:
  enum { WAIT_HIGH, WAIT_LOW };

  static const uint32_t POLL_MS = 40;
  static const uint8_t  MAX_PER_TICK = 8;
  static const uint8_t  MIN_BEATS_FOR_BPM = 4;
  static const uint32_t BEAT_MIN_MS = 300;    /* 200 bpm ceiling */
  static const uint32_t BEAT_MAX_MS = 2000;   /*  30 bpm floor   */
  static const uint8_t  INTERVALS = 5;

  /* Below this the photodiode is looking at the room, not at anybody. */
  static const uint32_t FINGER_ON_DC  = 30000;
  static const uint32_t FINGER_OFF_DC = 20000;  /* hysteresis: no flicker */

  /* ── part-specific setup ─────────────────────────────────────────────── */

  bool startMax30100() {
    if (!writeReg(0x06, 0x40)) return fail("reset rejected");   /* MODE: RESET */
    delay(100);
    writeReg(0x06, 0x03);          /* MODE: SpO2 (both LEDs)                  */
    writeReg(0x07, 0x47);          /* hi-res, 100 Hz, 1600us pulse (16 bit)   */
    writeReg(0x09, 0x77);          /* RED and IR both at ~24 mA               */
    writeReg(0x02, 0x00);          /* FIFO write ptr                          */
    writeReg(0x03, 0x00);          /* overflow counter                        */
    writeReg(0x04, 0x00);          /* FIFO read ptr                           */
    return true;
  }

  bool startMax30102() {
    if (!writeReg(0x09, 0x40)) return fail("reset rejected");   /* MODE: RESET */
    delay(100);
    writeReg(0x08, 0x0F);          /* FIFO: average 1, rollover on            */
    writeReg(0x09, 0x03);          /* MODE: SpO2                              */
    writeReg(0x0A, 0x27);          /* 4096nA range, 100 Hz, 411us (18 bit)    */
    writeReg(0x0C, 0x24);          /* LED1 (RED) ~7 mA                        */
    writeReg(0x0D, 0x24);          /* LED2 (IR)  ~7 mA                        */
    writeReg(0x04, 0x00);
    writeReg(0x05, 0x00);
    writeReg(0x06, 0x00);
    return true;
  }

  /* One sample out of the FIFO, or false when it is empty. The two parts
   * disagree about everything here: which registers hold the pointers, how
   * wide a sample is, and which colour comes first. */
  bool readFifoSample(uint32_t& ir, uint32_t& red) {
    if (variant_ == PART_MAX30100) {
      uint8_t wr = 0, rd = 0;
      if (!readRegs(0x02, &wr, 1) || !readRegs(0x04, &rd, 1)) return false;
      if (wr == rd) return false;
      uint8_t b[4];
      if (!readRegs(0x05, b, 4)) return false;
      ir  = ((uint32_t)b[0] << 8) | b[1];
      red = ((uint32_t)b[2] << 8) | b[3];
      return true;
    }
    if (variant_ == PART_MAX30102) {
      uint8_t wr = 0, rd = 0;
      if (!readRegs(0x04, &wr, 1) || !readRegs(0x06, &rd, 1)) return false;
      if (wr == rd) return false;
      uint8_t b[6];
      if (!readRegs(0x07, b, 6)) return false;
      /* 18 bits, left-justified in three bytes, RED before IR here. */
      red = (((uint32_t)b[0] << 16) | ((uint32_t)b[1] << 8) | b[2]) & 0x03FFFF;
      ir  = (((uint32_t)b[3] << 16) | ((uint32_t)b[4] << 8) | b[5]) & 0x03FFFF;
      return true;
    }
    return false;
  }

  /* ── the signal processing ───────────────────────────────────────────── */

  void processSample(uint32_t now, uint32_t ir, uint32_t red) {
    /* Track the steady level with a slow one-pole filter. What is left over
     * is the pulse. A ~1 second time constant is long enough not to eat the
     * beat and short enough to follow a finger settling into place. */
    irDc_  = irDc_  == 0 ? ir  : irDc_  + (ir  - irDc_)  * DC_ALPHA;
    redDc_ = redDc_ == 0 ? red : redDc_ + (red - redDc_) * DC_ALPHA;

    const float irAc  = (float)ir  - irDc_;
    const float redAc = (float)red - redDc_;

    /* Hysteresis on both edges, so a finger resting lightly does not make
     * the reading appear and vanish several times a second. */
    if (!fingerOn_ && irDc_ > FINGER_ON_DC) { fingerOn_ = true; reset(); }
    else if (fingerOn_ && irDc_ < FINGER_OFF_DC) { fingerOn_ = false; }
    if (!fingerOn_) return;

    /* A short moving average. The beat is around 1 Hz and the sampling is
     * 100 Hz, so this costs nothing in signal and removes a lot of jitter
     * from the peak detector below. */
    smooth_ = smooth_ * 0.75f + irAc * 0.25f;

    /* Peak-to-peak, adapting downward slowly so one cough does not raise the
     * detection threshold for the next ten seconds. */
    const float mag = fabsf(smooth_);
    if (mag > acAmplitude_) acAmplitude_ = mag;
    else acAmplitude_ *= 0.999f;

    detectBeat(now);
    accumulateForSpo2(irAc, redAc);
  }

  /* Threshold crossing with hysteresis and a refractory period. Simpler than
   * a derivative peak finder and far harder to fool: a single noisy sample
   * cannot produce a beat, because the signal has to go all the way back down
   * before another one counts. */
  void detectBeat(uint32_t now) {
    if (acAmplitude_ < MIN_AMPLITUDE) return;
    const float high = acAmplitude_ * 0.35f;
    const float low  = acAmplitude_ * 0.15f;

    if (beatState_ == WAIT_HIGH) {
      if (smooth_ > high) {
        beatState_ = WAIT_LOW;
        if (lastBeatMs_ != 0) {
          const uint32_t gap = now - lastBeatMs_;
          if (gap >= BEAT_MIN_MS && gap <= BEAT_MAX_MS) {
            intervals_[intervalHead_] = gap;
            intervalHead_ = (intervalHead_ + 1) % INTERVALS;
            if (intervalCount_ < INTERVALS) intervalCount_++;
            beatsSeen_++;
            updateBpm();
          } else {
            /* Out of range: a movement artefact, not a beat. Start the
             * timing again rather than averaging nonsense into the result. */
            intervalCount_ = 0;
            beatsSeen_ = 0;
          }
        }
        lastBeatMs_ = now;
      }
    } else if (smooth_ < low) {
      beatState_ = WAIT_HIGH;
    }
  }

  void updateBpm() {
    if (intervalCount_ == 0) return;
    uint32_t sum = 0;
    for (uint8_t i = 0; i < intervalCount_; i++) sum += intervals_[i];
    const float mean = (float)sum / intervalCount_;
    if (mean > 0) bpm_ = 60000.0f / mean;
  }

  /* SpO2 wants the AC amplitude over whole beats, not per sample, so the
   * squares are summed across a window and turned into an RMS at the end. */
  void accumulateForSpo2(float irAc, float redAc) {
    irAcSum_  += irAc * irAc;
    redAcSum_ += redAc * redAc;
    if (++acWindow_ < SPO2_WINDOW) return;

    const float irRms  = sqrtf(irAcSum_  / acWindow_);
    const float redRms = sqrtf(redAcSum_ / acWindow_);
    irAcSum_ = redAcSum_ = 0;
    acWindow_ = 0;

    if (irDc_ <= 0 || redDc_ <= 0 || irRms <= 0) return;
    const float ratio = (redRms / redDc_) / (irRms / irDc_);

    /* The standard empirical fit. Every caveat at the top of this file is
     * about this one line. */
    const float estimate = 110.0f - 25.0f * ratio;
    spo2_ = (spo2_ == 0) ? estimate : spo2_ * 0.8f + estimate * 0.2f;
  }

  /* ── I2C ─────────────────────────────────────────────────────────────── */

  bool writeReg(uint8_t reg, uint8_t val) {
    bus_->beginTransmission(ADDR);
    bus_->write(reg);
    bus_->write(val);
    return bus_->endTransmission() == 0;
  }

  bool readRegs(uint8_t reg, uint8_t* out, uint8_t n) {
    bus_->beginTransmission(ADDR);
    bus_->write(reg);
    if (bus_->endTransmission(false) != 0) return false;
    if (bus_->requestFrom((int)ADDR, (int)n) != n) return false;
    for (uint8_t i = 0; i < n; i++) out[i] = bus_->read();
    return true;
  }

  bool fail(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(err_, sizeof(err_), fmt, ap);
    va_end(ap);
    ready_ = false;
    return false;
  }

  static constexpr float DC_ALPHA      = 0.01f;
  static constexpr float MIN_AMPLITUDE = 20.0f;
  static const uint16_t  SPO2_WINDOW   = 100;   /* ~1 s at 100 Hz */

  TwoWire* bus_ = nullptr;
  Variant  variant_ = PART_UNKNOWN;
  bool     ready_ = false;
  char     err_[80] = "not started";

  bool  fingerOn_ = false;
  float irDc_ = 0, redDc_ = 0;
  float smooth_ = 0, acAmplitude_ = 0;

  uint8_t  beatState_ = WAIT_HIGH;
  uint32_t lastBeatMs_ = 0;
  uint32_t intervals_[INTERVALS] = {0};
  uint8_t  intervalHead_ = 0, intervalCount_ = 0;
  uint32_t beatsSeen_ = 0;
  float    bpm_ = 0;

  float    irAcSum_ = 0, redAcSum_ = 0;
  uint16_t acWindow_ = 0;
  float    spo2_ = 0;

  uint32_t lastPollMs_ = 0, emptyPolls_ = 0;
};
