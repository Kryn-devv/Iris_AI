/*
 * ============================================================================
 *  IRIS ROBOT EYES — the panel behind each eye, whatever chip is inside it
 * ============================================================================
 *
 *  Two OLED modules that look identical can carry different controller chips:
 *  the 0.96" is an SSD1306, the slightly bigger 1.02"/1.3" is nearly always an
 *  SH1106. They need different libraries and different start-up commands, and
 *  an SH1106 fed SSD1306 commands lights up solid and flickers instead of
 *  drawing. This header hides the difference behind one tiny interface so the
 *  rest of the firmware never cares — and so a robot can have one of each,
 *  which is exactly what happens the day one eye dies and the shop only has
 *  the other size.
 *
 *  The sketch decides which of the two implementations to compile in by
 *  defining IRIS_USE_SSD1306 / IRIS_USE_SH1106 before including this file, so
 *  a build with two SH1106 panels does not need the SSD1306 library installed
 *  and vice versa.
 */
#pragma once

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>

/* One eye's display. Everything the face code needs, nothing more. */
struct EyePanel {
  virtual ~EyePanel() {}
  /* Bring the panel up at this I2C address. The bus is already running on our
   * pins; the implementation must not move it. */
  virtual bool begin(uint8_t addr) = 0;
  virtual void clearDisplay() = 0;
  virtual void display() = 0;
  /* The drawing surface — eyes.h draws through Adafruit_GFX only. */
  virtual Adafruit_GFX& gfx() = 0;
  virtual const char* chip() const = 0;
};

#if IRIS_USE_SSD1306
#include <Adafruit_SSD1306.h>
struct Ssd1306Panel : EyePanel {
  Adafruit_SSD1306 d;
  Ssd1306Panel(uint16_t w, uint16_t h, TwoWire* bus) : d(w, h, bus, -1) {}
  /* periphBegin=false: the bus is up on OUR pins, and letting the library call
   * Wire.begin() again would reset it to the board's default pins. */
  bool begin(uint8_t addr) override { return d.begin(SSD1306_SWITCHCAPVCC, addr, true, false); }
  void clearDisplay() override { d.clearDisplay(); }
  void display() override { d.display(); }
  Adafruit_GFX& gfx() override { return d; }
  const char* chip() const override { return "SSD1306"; }
};
#endif

#if IRIS_USE_SH1106
#include <Adafruit_SH110X.h>
struct Sh1106Panel : EyePanel {
  Adafruit_SH1106G d;
  /* The SH110X driver sets the bus clock itself around every frame (the last
   * two constructor arguments: during the transfer, and afterwards). Both at
   * our bus speed, so it never drops to the library's 100 kHz default. */
  Sh1106Panel(uint16_t w, uint16_t h, TwoWire* bus, uint32_t hz) : d(w, h, bus, -1, hz, hz) {}
  /* This library does call Wire.begin() — with no pins, which on the ESP32
   * core keeps the pins the bus was last given. Harmless. */
  bool begin(uint8_t addr) override { return d.begin(addr, true); }
  void clearDisplay() override { d.clearDisplay(); }
  void display() override { d.display(); }
  Adafruit_GFX& gfx() override { return d; }
  const char* chip() const override { return "SH1106"; }
};
#endif
