/*
 * motion.h — "is anybody there?", answered on the camera itself.
 *
 * WHY THIS EXISTS
 * The brain wants to know when to bother looking. Without this, finding out
 * whether anyone is in front of the robot means pulling a full JPEG over WiFi
 * and running a face detector on it — several hundred milliseconds and a real
 * chunk of the brain's CPU, repeated forever, mostly to learn that the room is
 * still empty. So the camera answers the cheap question by itself and the
 * brain only pays for a frame once something has actually changed.
 *
 * HOW IT WORKS
 * Every MOTION_INTERVAL_MS the current JPEG is decoded at 1/8 scale — the
 * decoder can skip most of its work at that scale, so an SVGA frame costs
 * roughly 40 ms instead of the ~300 ms a full decode would — then squashed
 * into a fixed GRID_W x GRID_H grid of average brightness. Each cell is
 * compared with the same cell last time round. Enough cells moving far enough
 * counts as motion.
 *
 * The grid is a fixed size no matter what resolution the sensor is set to,
 * which is what lets /settings change framesize mid-flight without making the
 * stored baseline meaningless.
 *
 * WHAT THE BRAIN GETS BACK
 * Not just a yes/no: also *where*. The bounding box and centroid of the cells
 * that changed are reported in normalized coordinates, and the centroid is
 * additionally given in the -100..100 range the S3 node's /look endpoint
 * expects — so "turn the eyes toward whoever just walked in" is a straight
 * copy of two numbers, with no coordinate maths in between.
 *
 * WHAT THIS IS NOT
 * This is a change detector, not a person detector. A curtain in a draught
 * moves it. Deciding whether the thing that moved is a face — and whose face —
 * happens in the brain, which is the only place with the memory and the models
 * for it. See the repo README for why that split is forced by the hardware.
 */
#pragma once

#include <Arduino.h>    // ps_malloc, and the Arduino types used below
#include <stdlib.h>
#include <string.h>

#include "esp_camera.h"
#include "img_converters.h"

// Coarse enough that sensor noise averages out, fine enough to locate a
// person in the frame. 32x24 keeps the aspect ratio of every 4:3 framesize.
#define MOTION_GRID_W 32
#define MOTION_GRID_H 24
#define MOTION_CELLS (MOTION_GRID_W * MOTION_GRID_H)

struct MotionState {
  bool  moved = false;         // motion in the most recent sample
  bool  recent = false;        // motion within MOTION_HOLD_MS
  uint16_t changedCells = 0;   // how many grid cells moved
  uint8_t  percent = 0;        // changedCells as a percentage of the grid
  // Bounding box of the changed cells, normalized 0..1000 (per-mille, so the
  // whole thing stays integer and the JSON has no floats to round badly).
  uint16_t boxX = 0, boxY = 0, boxW = 0, boxH = 0;
  // Centroid of change, in the -100..100 range the S3 node's /look wants.
  int16_t  lookX = 0, lookY = 0;
  bool     haveBox = false;
  unsigned long lastMotionMs = 0;
  unsigned long lastSampleMs = 0;
  uint32_t samples = 0;
  uint32_t events = 0;         // transitions from still to moving
};

class MotionDetector {
 public:
  // Forget the baseline. Call this after anything that legitimately changes
  // the whole picture — a framesize change, a flip, the flash coming on —
  // so the next sample re-baselines instead of reporting a false event.
  void resetBaseline() {
    havePrev_ = false;
    state_.moved = false;
    state_.changedCells = 0;
    state_.percent = 0;
    state_.haveBox = false;
  }

  const MotionState& state() const { return state_; }

  // Expire the "recent" flag. Cheap; safe to call from a request handler.
  void tick(unsigned long now, unsigned long holdMs) {
    state_.recent = state_.lastMotionMs != 0 && (now - state_.lastMotionMs) < holdMs;
  }

  // Fold one frame into the detector. Returns true when this sample was
  // usable (decoded and compared); false means it was skipped, which is not
  // an error — a dropped sample just means the next one does the work.
  bool sample(camera_fb_t* fb, unsigned long now, uint8_t cellDelta, uint8_t minPercent) {
    if (fb == nullptr || fb->format != PIXFORMAT_JPEG) return false;
    if (fb->width < 8 || fb->height < 8) return false;

    const uint16_t sw = fb->width / 8;
    const uint16_t sh = fb->height / 8;
    if (sw < MOTION_GRID_W || sh < MOTION_GRID_H) {
      // Below QVGA there are fewer decoded pixels than grid cells; the grid
      // would be interpolating noise. Motion is simply not offered there.
      return false;
    }
    if (!ensureBuffer(sw, sh)) return false;

    if (!jpg2rgb565(fb->buf, fb->len, rgb_, JPG_SCALE_8X)) return false;

    uint8_t grid[MOTION_CELLS];
    reduce(sw, sh, grid);

    state_.lastSampleMs = now;
    state_.samples++;

    if (!havePrev_) {
      memcpy(prev_, grid, sizeof(grid));
      havePrev_ = true;
      return true;   // first frame is the baseline, never an event
    }

    uint16_t changed = 0;
    uint16_t minX = MOTION_GRID_W, minY = MOTION_GRID_H, maxX = 0, maxY = 0;
    uint32_t sumX = 0, sumY = 0;

    for (uint16_t y = 0; y < MOTION_GRID_H; y++) {
      for (uint16_t x = 0; x < MOTION_GRID_W; x++) {
        const uint16_t i = y * MOTION_GRID_W + x;
        const int diff = (int)grid[i] - (int)prev_[i];
        if (diff >= cellDelta || -diff >= cellDelta) {
          changed++;
          if (x < minX) minX = x;
          if (y < minY) minY = y;
          if (x > maxX) maxX = x;
          if (y > maxY) maxY = y;
          sumX += x;
          sumY += y;
        }
      }
    }

    memcpy(prev_, grid, sizeof(grid));

    const uint16_t percent = (uint16_t)((changed * 100UL) / MOTION_CELLS);
    const bool wasMoving = state_.moved;

    state_.changedCells = changed;
    state_.percent = (uint8_t)(percent > 255 ? 255 : percent);
    state_.moved = percent >= minPercent;

    if (state_.moved && changed > 0) {
      state_.haveBox = true;
      // Per-mille of the frame. maxX is inclusive, hence the +1 on the span.
      state_.boxX = (uint16_t)((minX * 1000UL) / MOTION_GRID_W);
      state_.boxY = (uint16_t)((minY * 1000UL) / MOTION_GRID_H);
      state_.boxW = (uint16_t)(((maxX - minX + 1) * 1000UL) / MOTION_GRID_W);
      state_.boxH = (uint16_t)(((maxY - minY + 1) * 1000UL) / MOTION_GRID_H);

      // Centroid, mapped so the middle of the frame is 0 and the edges are
      // -100 and +100 — the S3 node's /look?x=&y= coordinate system.
      //
      // The mean cell index is deliberately NOT worked out first. Truncating
      // it to a whole cell before the mapping throws away up to half a cell,
      // which is 4 units of look on a 24-row grid — a standing bias up and to
      // the left, so the eyes would sit slightly off whoever they are meant
      // to be facing. Scaling first and dividing once keeps the precision.
      // Bounds: sumX <= 768*31, so the numerator stays well inside 32 bits.
      state_.lookX = (int16_t)(((long)sumX * 200) / ((long)changed * (MOTION_GRID_W - 1)) - 100);
      state_.lookY = (int16_t)(((long)sumY * 200) / ((long)changed * (MOTION_GRID_H - 1)) - 100);

      state_.lastMotionMs = now;
      if (!wasMoving) state_.events++;
    } else {
      state_.haveBox = false;
    }
    return true;
  }

 private:
  bool ensureBuffer(uint16_t sw, uint16_t sh) {
    // The decoder writes sw*sh RGB565 pixels; the margin absorbs any MCU
    // rounding it does at the right and bottom edges.
    const size_t need = ((size_t)sw + 8) * ((size_t)sh + 8) * 2;
    if (rgb_ != nullptr && rgbSize_ >= need) return true;
    if (rgb_ != nullptr) {
      free(rgb_);
      rgb_ = nullptr;
      rgbSize_ = 0;
    }
    // Prefer PSRAM: on a board that has it this never competes with the
    // frame buffers or the WiFi stack for internal RAM.
    rgb_ = (uint8_t*)ps_malloc(need);
    if (rgb_ == nullptr) rgb_ = (uint8_t*)malloc(need);
    if (rgb_ == nullptr) return false;
    rgbSize_ = need;
    return true;
  }

  // Average each grid cell's brightness out of the decoded RGB565 image.
  //
  // The channel order the decoder packs is not checked here on purpose: this
  // feeds a frame-to-frame difference, and any consistent weighting of the
  // three channels detects change equally well. Getting red and blue the
  // wrong way round would cost nothing that matters.
  void reduce(uint16_t sw, uint16_t sh, uint8_t* grid) const {
    const uint16_t* px = (const uint16_t*)rgb_;
    for (uint16_t gy = 0; gy < MOTION_GRID_H; gy++) {
      const uint32_t y0 = ((uint32_t)gy * sh) / MOTION_GRID_H;
      uint32_t y1 = ((uint32_t)(gy + 1) * sh) / MOTION_GRID_H;
      if (y1 <= y0) y1 = y0 + 1;

      for (uint16_t gx = 0; gx < MOTION_GRID_W; gx++) {
        const uint32_t x0 = ((uint32_t)gx * sw) / MOTION_GRID_W;
        uint32_t x1 = ((uint32_t)(gx + 1) * sw) / MOTION_GRID_W;
        if (x1 <= x0) x1 = x0 + 1;

        uint32_t sum = 0, count = 0;
        for (uint32_t y = y0; y < y1; y++) {
          const uint16_t* row = px + (size_t)y * sw;
          for (uint32_t x = x0; x < x1; x++) {
            const uint16_t p = row[x];
            const uint8_t r = (uint8_t)((p >> 11) & 0x1F);
            const uint8_t g = (uint8_t)((p >> 5) & 0x3F);
            const uint8_t b = (uint8_t)(p & 0x1F);
            // 5- and 6-bit channels widened to 8 bits, then ITU-R BT.601
            // luma weights (77/151/28 out of 256).
            sum += ((uint32_t)(r << 3) * 77 + (uint32_t)(g << 2) * 151 + (uint32_t)(b << 3) * 28) >> 8;
            count++;
          }
        }
        grid[gy * MOTION_GRID_W + gx] = (uint8_t)(count ? (sum / count) : 0);
      }
    }
  }

  MotionState state_;
  uint8_t  prev_[MOTION_CELLS] = {0};
  bool     havePrev_ = false;
  uint8_t* rgb_ = nullptr;
  size_t   rgbSize_ = 0;
};
