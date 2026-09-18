/*
 * ============================================================================
 *  MPU6050 CHECK  —  is it wired right, and is it actually working?
 * ============================================================================
 *
 *  Flash this, open the serial monitor at 115200, and it answers both
 *  questions in order. No libraries to install: the MPU6050 is talked to
 *  through four raw registers, so there is nothing to go wrong in a
 *  dependency and nothing to install before you can test a £2 sensor.
 *
 *  It checks, in this order, because each step only makes sense if the one
 *  before it passed:
 *
 *    1. Does anything answer on the bus at all?      (I2C scan)
 *    2. Is the thing that answered really an MPU?    (WHO_AM_I = 0x68)
 *    3. Does it produce numbers?                      (wake it, read)
 *    4. Are the numbers RIGHT?                        (gravity is 1g, down)
 *
 *  Step 4 is the one people skip, and it is the one that matters. A sensor
 *  mounted on its side answers every register perfectly and reports turns
 *  that never happened.
 *
 *  Wiring (default, the right eye's bus on the IRIS S3 board):
 *      VCC -> 3.3V     SDA -> GPIO 38
 *      GND -> GND      SCL -> GPIO 39      AD0 -> GND
 *
 *  Using the LEFT eye's bus instead? Change SDA_PIN/SCL_PIN to 20 and 21.
 * ============================================================================
 */
#include <Arduino.h>
#include <Wire.h>

/* ── change these two if you wired it somewhere else ───────────────────── */
static const int SDA_PIN = 38;
static const int SCL_PIN = 39;

/* ── MPU6050 registers. Four is all this needs. ────────────────────────── */
static const uint8_t MPU_ADDR     = 0x68;  /* 0x69 if AD0 is tied high     */
static const uint8_t REG_WHO_AM_I = 0x75;
static const uint8_t REG_PWR_MGMT = 0x6B;
static const uint8_t REG_DATA     = 0x3B;  /* 14 bytes: accel, temp, gyro  */

/* Sensitivity at the power-on defaults: +/-2g and +/-250 deg/s. */
static const float ACCEL_LSB_PER_G   = 16384.0f;
static const float GYRO_LSB_PER_DPS  = 131.0f;

static bool present = false;

static bool writeReg(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

static bool readRegs(uint8_t reg, uint8_t* out, uint8_t count) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;   /* repeated start */
  if (Wire.requestFrom((int)MPU_ADDR, (int)count) != count) return false;
  for (uint8_t i = 0; i < count; i++) out[i] = Wire.read();
  return true;
}

static void scanBus() {
  Serial.printf("\n1. Scanning I2C on SDA %d / SCL %d...\n", SDA_PIN, SCL_PIN);
  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() != 0) continue;
    found++;
    const char* what = "";
    if (addr == 0x68 || addr == 0x69) what = "  <- MPU6050";
    else if (addr == 0x3C || addr == 0x3D) what = "  <- an OLED";
    else if (addr == 0x57) what = "  <- MAX30100/30102";
    Serial.printf("   found 0x%02X%s\n", addr, what);
  }
  if (found == 0) {
    Serial.println("   NOTHING answered.");
    Serial.println("   -> SDA and SCL swapped? No power to the module?");
    Serial.println("      Measure 3.3V at the module's VCC pin before anything else.");
  }
}

static bool identify() {
  Serial.println("\n2. Asking who it is (WHO_AM_I)...");
  uint8_t who = 0;
  if (!readRegs(REG_WHO_AM_I, &who, 1)) {
    Serial.println("   No answer at 0x68.");
    Serial.println("   -> If the scan above found 0x69, AD0 is picking up 3.3V:");
    Serial.println("      tie AD0 firmly to GND, or change MPU_ADDR to 0x69.");
    return false;
  }
  Serial.printf("   answered 0x%02X\n", who);
  /* 0x68 is the MPU6050. Clones answer 0x70 (MPU6500), 0x71/0x73 (MPU9250),
   * 0x98 — all register-compatible for what this test does, so they pass. */
  if (who == 0x68) Serial.println("   That is a genuine MPU6050.");
  else Serial.println("   Not the classic 0x68, but register-compatible. Fine.");
  return true;
}

static bool wake() {
  Serial.println("\n3. Waking it up...");
  if (!writeReg(REG_PWR_MGMT, 0x00)) {
    Serial.println("   Could not write to it — the bus is flaky.");
    Serial.println("   -> Shorten the wires, or keep them away from the motor wires.");
    return false;
  }
  delay(100);
  Serial.println("   awake.");
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(600);
  Serial.println("\n=============== MPU6050 CHECK ===============");

  Wire.begin(SDA_PIN, SCL_PIN, 400000);
  scanBus();
  present = identify() && wake();

  if (!present) {
    Serial.println("\nStopping here — fix the above and reflash.");
    return;
  }
  Serial.println("\n4. Live readings. Now do these three things:");
  Serial.println("   a) Put it FLAT on the table and leave it alone.");
  Serial.println("      -> accel Z should sit near +1.00 g, X and Y near 0.00");
  Serial.println("      -> all three gyro numbers should sit near 0");
  Serial.println("   b) Tilt it on its side.");
  Serial.println("      -> the 1.00 g should move from Z to X or Y");
  Serial.println("   c) Spin it flat, like the robot turning.");
  Serial.println("      -> gyro Z should jump, and return to 0 when you stop");
  Serial.println("\n   If Z does NOT read ~1g when flat, it is mounted wrong,");
  Serial.println("   and every turn angle will be wrong in a way that looks");
  Serial.println("   like a software bug.\n");
}

void loop() {
  if (!present) { delay(2000); return; }

  uint8_t raw[14];
  if (!readRegs(REG_DATA, raw, 14)) {
    Serial.println("read failed — check the wiring is still seated");
    delay(1000);
    return;
  }

  const int16_t ax = (raw[0]  << 8) | raw[1];
  const int16_t ay = (raw[2]  << 8) | raw[3];
  const int16_t az = (raw[4]  << 8) | raw[5];
  const int16_t t  = (raw[6]  << 8) | raw[7];
  const int16_t gx = (raw[8]  << 8) | raw[9];
  const int16_t gy = (raw[10] << 8) | raw[11];
  const int16_t gz = (raw[12] << 8) | raw[13];

  const float axg = ax / ACCEL_LSB_PER_G;
  const float ayg = ay / ACCEL_LSB_PER_G;
  const float azg = az / ACCEL_LSB_PER_G;
  const float total = sqrtf(axg * axg + ayg * ayg + azg * azg);

  Serial.printf("accel g  X%+6.2f Y%+6.2f Z%+6.2f  |total|%5.2f   "
                "gyro d/s  X%+7.1f Y%+7.1f Z%+7.1f   %4.1fC",
                axg, ayg, azg, total,
                gx / GYRO_LSB_PER_DPS, gy / GYRO_LSB_PER_DPS, gz / GYRO_LSB_PER_DPS,
                t / 340.0f + 36.53f);

  /* The one check worth making automatically: at rest the three accelerometer
   * axes must add up to exactly one gravity. If they do not, the sensor is
   * either moving, or not working, and no amount of calibration fixes it. */
  if (total < 0.85f || total > 1.15f) Serial.print("   <- NOT 1g: moving, or faulty");
  Serial.println();
  delay(250);
}
