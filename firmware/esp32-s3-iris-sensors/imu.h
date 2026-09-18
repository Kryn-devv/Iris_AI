/*
 * imu.h — MPU6050 six-axis motion sensor, at the register level.
 *
 * No library. The Arduino MPU6050 libraries all pull in dependencies, and the
 * useful part of this chip is about sixty lines: wake it, read fourteen bytes,
 * do the trigonometry. Keeping it here also means the rest of the firmware
 * builds on a machine with nothing installed, which is the rule everywhere
 * else in this project.
 *
 * WHAT IRIS ACTUALLY WANTS FROM IT
 * Not raw numbers — those are just a data feed. Four judgements:
 *   tilted   the robot is not upright. Picked up, or fallen over.
 *   bumped   a sudden shove. It hit something, or something hit it.
 *   still    not moving at all, whatever the wheels were told to do.
 *   heading  which way it is pointing, in degrees, so a turn can be
 *            commanded as "90 degrees" instead of "spin for 800 ms and hope".
 *
 * That last one is why this sensor earns its place. Timed turns are a guess
 * that a low battery or a carpet turns into a wrong guess; a heading is a
 * measurement. Gyro yaw drifts (a degree or two a minute) — fine over the ten
 * seconds a turn takes, useless as an absolute compass, so treat it as
 * relative and zero it when a manoeuvre starts.
 *
 * Wiring (ESP32-S3, sharing the aux I2C bus):
 *   VCC -> 3V3      SDA -> GPIO 38
 *   GND -> GND      SCL -> GPIO 39
 * AD0 left open puts it at 0x68, which is where this looks. Tie AD0 high for
 * 0x69 if something else has taken 0x68.
 *
 * Mount it flat, near the middle of the chassis, with the chip level. Away
 * from the motors: their magnets do not bother the accelerometer, but their
 * vibration does, and a rigid mount on a rattling plate reads as permanent
 * motion.
 */
#pragma once

#include <Arduino.h>
#include <Wire.h>
#include <math.h>

class Imu {
public:
  /* Registers we touch. The datasheet has ninety; these are the six. */
  static const uint8_t ADDR_PRIMARY = 0x68;
  static const uint8_t ADDR_ALT     = 0x69;

  bool begin(TwoWire& bus, uint8_t addr = ADDR_PRIMARY) {
    bus_ = &bus;
    addr_ = addr;

    /* WHO_AM_I. The real part answers 0x68; the clones sold as MPU6050 are
     * usually MPU6500 (0x70) or MPU9250 (0x71) dies, and every register this
     * file uses is identical on all of them, so accept the family. A 0xFF or
     * 0x00 means nothing is driving the bus — bad wiring, not a bad part. */
    uint8_t who = 0;
    if (!readRegs(REG_WHO_AM_I, &who, 1)) return fail("no answer at 0x%02X", addr_);
    if (who == 0x00 || who == 0xFF) return fail("read 0x%02X — check SDA/SCL/VCC", who);
    whoAmI_ = who;

    /* Out of sleep, and clocked from the X gyro rather than the internal
     * oscillator: the datasheet's own recommendation, and measurably more
     * stable over temperature. */
    if (!writeReg(REG_PWR_MGMT_1, 0x80)) return fail("device reset rejected");
    delay(100);                                   /* the reset takes ~50 ms   */
    if (!writeReg(REG_PWR_MGMT_1, 0x01)) return fail("wake rejected");
    delay(10);

    /* 44 Hz low-pass. Motor vibration lives well above that, and a robot's
     * own movement well below, so this one register removes most of the noise
     * that would otherwise have to be filtered in software. */
    writeReg(REG_CONFIG, 0x03);
    writeReg(REG_SMPLRT_DIV, 0x04);               /* 1 kHz / (1+4) = 200 Hz   */
    writeReg(REG_GYRO_CONFIG, 0x08);              /* +/- 500 deg/s            */
    writeReg(REG_ACCEL_CONFIG, 0x00);             /* +/- 2 g                  */
    delay(10);

    ready_ = true;
    calibrate();
    return true;
  }

  bool ready() const { return ready_; }
  uint8_t whoAmI() const { return whoAmI_; }
  const char* error() const { return err_; }

  /* Sits still for a moment and learns what "not rotating" reads as. Every
   * one of these chips reports a degree or two per second of rotation while
   * motionless, and integrating that unsubtracted is what makes a heading
   * wander off in under a minute.
   *
   * Blocks for ~300 ms, so call it in setup() or when deliberately parked. */
  void calibrate() {
    if (!ready_) return;
    double gx = 0, gy = 0, gz = 0;
    int taken = 0;
    for (int i = 0; i < 100; i++) {
      Sample s;
      if (readSample(s)) { gx += s.gx; gy += s.gy; gz += s.gz; taken++; }
      delay(3);
    }
    if (taken < 50) return;                       /* too flaky to trust       */
    gxBias_ = gx / taken;
    gyBias_ = gy / taken;
    gzBias_ = gz / taken;
    calibrated_ = true;
    heading_ = 0.0f;
  }

  bool calibrated() const { return calibrated_; }

  /* Called every loop. Reads at SAMPLE_MS and returns immediately otherwise;
   * one read is 14 bytes at 400 kHz, about 400 microseconds. */
  void tick(uint32_t now) {
    if (!ready_ || now - lastReadMs_ < SAMPLE_MS) return;
    const float dt = lastReadMs_ == 0 ? (SAMPLE_MS / 1000.0f)
                                      : (now - lastReadMs_) / 1000.0f;
    lastReadMs_ = now;

    Sample s;
    if (!readSample(s)) { readFailures_++; return; }

    /* Raw counts to real units, at the ranges set in begin(). */
    const float ax = s.ax / 16384.0f;             /* g, at +/- 2 g            */
    const float ay = s.ay / 16384.0f;
    const float az = s.az / 16384.0f;
    const float gx = (s.gx - gxBias_) / 65.5f;    /* deg/s, at +/- 500 deg/s  */
    const float gy = (s.gy - gyBias_) / 65.5f;
    const float gz = (s.gz - gzBias_) / 65.5f;

    accelG_ = sqrtf(ax * ax + ay * ay + az * az);
    tempC_ = s.temp / 340.0f + 36.53f;
    gyroXdps_ = gx; gyroYdps_ = gy; gyroZdps_ = gz;

    /* Pitch and roll from gravity. Exact when still, useless while
     * accelerating; the gyro is the other way round. A complementary filter
     * takes the long-term truth from one and the short-term truth from the
     * other, and costs two multiplies. */
    const float accelPitch = atan2f(-ax, sqrtf(ay * ay + az * az)) * RAD_TO_DEG;
    const float accelRoll  = atan2f(ay, az) * RAD_TO_DEG;
    if (!fused_) {
      pitch_ = accelPitch; roll_ = accelRoll; fused_ = true;
    } else {
      pitch_ = COMP_ALPHA * (pitch_ + gy * dt) + (1.0f - COMP_ALPHA) * accelPitch;
      roll_  = COMP_ALPHA * (roll_  + gx * dt) + (1.0f - COMP_ALPHA) * accelRoll;
    }

    /* Yaw has no gravity reference, so it is pure integration. The deadband
     * matters more than it looks: without it the bias that calibrate() could
     * not quite remove accumulates every single sample, and the heading walks
     * away while the robot sits still. */
    if (fabsf(gz) > YAW_DEADBAND_DPS) {
      heading_ += gz * dt;
      while (heading_ >= 180.0f) heading_ -= 360.0f;
      while (heading_ < -180.0f) heading_ += 360.0f;
    }

    /* A shove is a spike in total acceleration away from 1 g. Sustained
     * tilt does not trigger it, because gravity stays 1 g however the board
     * is turned — only a real jolt adds to it. */
    const float jolt = fabsf(accelG_ - 1.0f);
    if (jolt > BUMP_G) { lastBumpMs_ = now; bumps_++; }

    /* "Still" is about the gyro, not the accelerometer: a robot being carried
     * smoothly reads 1 g just like one parked on the floor, but it is never
     * rotationally quiet. */
    const bool quiet = jolt < STILL_G &&
                       fabsf(gx) < STILL_DPS && fabsf(gy) < STILL_DPS &&
                       fabsf(gz) < STILL_DPS;
    if (!quiet) lastMotionMs_ = now;

    samples_++;
  }

  /* ── the four judgements ─────────────────────────────────────────────── */

  /* Not upright. Past ~45 degrees the robot is on its side or in someone's
   * hand, and driving the motors is the wrong response to both. */
  bool tilted(float limitDeg = 45.0f) const {
    return ready_ && fused_ &&
           (fabsf(pitch_) > limitDeg || fabsf(roll_) > limitDeg);
  }

  /* Hit something in the last window. Edge-triggered by design: ask once. */
  bool bumped(uint32_t now, uint32_t windowMs = 500) const {
    return ready_ && lastBumpMs_ != 0 && (now - lastBumpMs_) < windowMs;
  }

  /* Genuinely not moving. The window has to outlast a wheel's stick-slip on
   * carpet, or a robot inching forward reads as stuck. */
  bool still(uint32_t now, uint32_t windowMs = 1200) const {
    return ready_ && lastMotionMs_ != 0 && (now - lastMotionMs_) > windowMs;
  }

  float heading() const { return heading_; }      /* degrees, relative, +/-180 */
  void  zeroHeading() { heading_ = 0.0f; }

  /* Shortest signed angle from here to a target heading: what a turn should
   * be commanded with, and what tells it when to stop. */
  float headingErrorTo(float targetDeg) const {
    float e = targetDeg - heading_;
    while (e > 180.0f) e -= 360.0f;
    while (e < -180.0f) e += 360.0f;
    return e;
  }

  float pitch() const { return pitch_; }
  float roll() const { return roll_; }
  float accelG() const { return accelG_; }
  float gyroZ() const { return gyroZdps_; }
  float temperatureC() const { return tempC_; }
  uint32_t bumps() const { return bumps_; }
  uint32_t readFailures() const { return readFailures_; }

  String toJson(uint32_t now) const {
    if (!ready_) return String("{\"fitted\":false}");
    String j = "{\"fitted\":true";
    j += ",\"pitch\":" + String(pitch_, 1);
    j += ",\"roll\":" + String(roll_, 1);
    j += ",\"heading\":" + String(heading_, 1);
    j += ",\"accel_g\":" + String(accelG_, 2);
    j += ",\"gyro_z\":" + String(gyroZdps_, 1);
    j += ",\"tilted\":" + String(tilted() ? "true" : "false");
    j += ",\"bumped\":" + String(bumped(now) ? "true" : "false");
    j += ",\"still\":" + String(still(now) ? "true" : "false");
    j += ",\"temp_c\":" + String(tempC_, 1);
    j += ",\"calibrated\":" + String(calibrated_ ? "true" : "false");
    j += ",\"bumps\":" + String(bumps_);
    j += ",\"read_failures\":" + String(readFailures_);
    j += "}";
    return j;
  }

private:
  static const uint8_t REG_SMPLRT_DIV   = 0x19;
  static const uint8_t REG_CONFIG       = 0x1A;
  static const uint8_t REG_GYRO_CONFIG  = 0x1B;
  static const uint8_t REG_ACCEL_CONFIG = 0x1C;
  static const uint8_t REG_DATA         = 0x3B;
  static const uint8_t REG_PWR_MGMT_1   = 0x6B;
  static const uint8_t REG_WHO_AM_I     = 0x75;

  static const uint32_t SAMPLE_MS = 20;           /* 50 Hz: plenty for a robot */
  static constexpr float COMP_ALPHA       = 0.98f;
  static constexpr float YAW_DEADBAND_DPS = 1.5f;
  static constexpr float BUMP_G           = 0.35f;
  static constexpr float STILL_G          = 0.06f;
  static constexpr float STILL_DPS        = 3.0f;

  struct Sample { int16_t ax, ay, az, temp, gx, gy, gz; };

  bool writeReg(uint8_t reg, uint8_t val) {
    bus_->beginTransmission(addr_);
    bus_->write(reg);
    bus_->write(val);
    return bus_->endTransmission() == 0;
  }

  bool readRegs(uint8_t reg, uint8_t* out, uint8_t n) {
    bus_->beginTransmission(addr_);
    bus_->write(reg);
    if (bus_->endTransmission(false) != 0) return false;
    if (bus_->requestFrom((int)addr_, (int)n) != n) return false;
    for (uint8_t i = 0; i < n; i++) out[i] = bus_->read();
    return true;
  }

  bool readSample(Sample& s) {
    uint8_t b[14];
    if (!readRegs(REG_DATA, b, 14)) return false;
    s.ax   = (int16_t)((b[0]  << 8) | b[1]);
    s.ay   = (int16_t)((b[2]  << 8) | b[3]);
    s.az   = (int16_t)((b[4]  << 8) | b[5]);
    s.temp = (int16_t)((b[6]  << 8) | b[7]);
    s.gx   = (int16_t)((b[8]  << 8) | b[9]);
    s.gy   = (int16_t)((b[10] << 8) | b[11]);
    s.gz   = (int16_t)((b[12] << 8) | b[13]);
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

  TwoWire* bus_ = nullptr;
  uint8_t  addr_ = ADDR_PRIMARY;
  uint8_t  whoAmI_ = 0;
  bool     ready_ = false;
  bool     fused_ = false;
  bool     calibrated_ = false;
  char     err_[64] = "not started";

  double gxBias_ = 0, gyBias_ = 0, gzBias_ = 0;
  float  pitch_ = 0, roll_ = 0, heading_ = 0;
  float  accelG_ = 1.0f, gyroXdps_ = 0, gyroYdps_ = 0, gyroZdps_ = 0;
  float  tempC_ = NAN;

  uint32_t lastReadMs_ = 0, lastBumpMs_ = 0, lastMotionMs_ = 0;
  uint32_t samples_ = 0, bumps_ = 0, readFailures_ = 0;
};
