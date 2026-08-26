/*
 * ============================================================================
 *  IRIS FACE ANIMATOR  —  the timing layer above eyes.h
 * ============================================================================
 *
 *  eyes.h knows what an emotion LOOKS like. This knows how a face BEHAVES:
 *  when to blink, where to glance, how to bounce while talking, and when to
 *  get sleepy because nobody has said anything for a while.
 *
 *  It owns no display. tick() hands back the two final poses and the sketch
 *  draws them, so all of the timing logic here is testable without hardware —
 *  and a motor-control-style bug ("the eyes stayed mid-blink forever") cannot
 *  hide behind an I2C bus.
 *
 *  Every deadline is rollover-safe. millis() wraps after ~49.7 days, and the
 *  naive `now >= deadline` comparison silently stops firing across the wrap:
 *  a robot left running for seven weeks would stop blinking, or freeze in a
 *  talking bounce that never expires.
 * ============================================================================
 */
#pragma once

#include <Arduino.h>
#include <math.h>

#include "eyes.h"

/* ── timing ─────────────────────────────────────────────────────── */
#define FRAME_MS            25      /* 40 fps cap; two 1 KB I2C pushes per frame */
#define EASE_NUM             3      /* per frame, close 3/16 of the remaining    */
#define EASE_DEN            16      /* distance — ~0.3 s to settle a big change  */

#define BLINK_MS           110      /* a real blink is 100-150 ms                */
#define BLINK_GAP_MIN     2600
#define BLINK_GAP_MAX     6200
#define BLINK_GAP_MIN_TALK 1500     /* people blink more while speaking          */
#define BLINK_GAP_MAX_TALK 3400
#define DOUBLE_BLINK_PCT    18      /* how often a blink comes in a pair         */

#define SACCADE_MIN_MS    1200
#define SACCADE_MAX_MS    4200
#define SACCADE_RANGE_X     11      /* px; small — a big idle glance looks manic */
#define SACCADE_RANGE_Y      6

#define SPEAK_MAX_MS     30000UL    /* hard ceiling: the face never talks forever */
#define IDLE_SLEEPY_MS  180000UL    /* 3 min of silence and it dozes off          */

/* Gaze arrives as -100..100 from the network; convert to pixels. */
#define GAZE_PX_X           26
#define GAZE_PX_Y           14

/* 0 is the "nothing pending" sentinel, so a deadline landing exactly on 0 —
 * once per millis() wrap — must be nudged rather than silently cancelled. */
static inline uint32_t faceDeadline(uint32_t now, uint32_t ms) {
  const uint32_t t = now + ms;
  return t ? t : 1;
}

static inline bool faceDue(uint32_t now, uint32_t deadline) {
  return deadline != 0 && (int32_t)(now - deadline) >= 0;
}

struct FaceAnimator {
  /* ── commanded state ── */
  uint8_t  emotion      = EMO_NEUTRAL;
  uint32_t holdUntil    = 0;        /* 0 = hold this emotion indefinitely   */
  uint8_t  revertTo     = EMO_NEUTRAL;
  uint32_t speakUntil   = 0;        /* 0 = not speaking                     */
  int16_t  gazeX        = 0;        /* -100..100                            */
  int16_t  gazeY        = 0;
  uint32_t lastCommandMs = 0;
  uint32_t commandCount  = 0;

  /* ── internal animation state ── */
  EyePose  curL, curR;
  uint32_t lastFrameMs  = 0;
  uint32_t blinkStartMs = 0;        /* 0 = eyes open                        */
  uint32_t nextBlinkAt  = 0;
  uint8_t  blinksQueued = 0;
  bool     pairSecond = false;      /* this blink is the 2nd half of a pair  */
  int16_t  sacX = 0, sacY = 0;
  uint32_t nextSaccadeAt = 0;
  bool     dozing = false;

  void begin(uint32_t now) {
    curL = poseFor(EMO_NEUTRAL, true);
    curR = poseFor(EMO_NEUTRAL, false);
    lastCommandMs = now;
    lastFrameMs = now;
    scheduleBlink(now);
    scheduleSaccade(now);
  }

  /* ── commands ─────────────────────────────────────────────────── */

  /* holdMs 0 means "stay like this"; otherwise revert to neutral afterwards,
   * so a one-off reaction cannot leave the face stuck being angry. */
  void setEmotion(uint8_t e, uint32_t holdMs, uint32_t now) {
    if (e >= EMO_COUNT) e = EMO_NEUTRAL;
    emotion = e;
    holdUntil = holdMs ? faceDeadline(now, holdMs) : 0;
    touch(now);
  }

  /* Bounded on purpose. IRIS estimates how long a sentence takes and sends
   * that; if the "finished speaking" call is lost to a dropped packet the
   * mouth-bounce still stops on its own. */
  void setSpeaking(uint32_t ms, uint32_t now) {
    if (ms == 0) { speakUntil = 0; touch(now); return; }
    if (ms > SPEAK_MAX_MS) ms = SPEAK_MAX_MS;
    speakUntil = faceDeadline(now, ms);
    /* Reset the blink clock to the talking cadence straight away rather than
     * waiting out a long idle gap first. */
    if (!faceDue(now, nextBlinkAt)) scheduleBlink(now);
    touch(now);
  }

  void look(int16_t x100, int16_t y100, uint32_t now) {
    gazeX = clamp100(x100);
    gazeY = clamp100(y100);
    sacX = sacY = 0;                /* an explicit look overrides a glance  */
    scheduleSaccade(now);
    touch(now);
  }

  void blinkNow(uint32_t now, uint8_t count = 1) {
    if (blinkStartMs == 0) blinkStartMs = now ? now : 1;
    blinksQueued = count > 1 ? (uint8_t)(count - 1) : 0;
    pairSecond = false;            /* an explicit burst is not a random pair */
    touch(now);
  }

  bool speaking(uint32_t now) const { return speakUntil != 0 && !faceDue(now, speakUntil); }

  const char* emotionName() const {
    return emotion < EMO_COUNT ? EMOTION_NAMES[emotion] : "neutral";
  }

  /* ── the frame ────────────────────────────────────────────────── */

  /* Returns false when it is not yet time for a new frame, so the caller can
   * spend the rest of the loop serving HTTP instead of pushing pixels. */
  bool tick(uint32_t now, EyePose& outL, EyePose& outR) {
    if ((uint32_t)(now - lastFrameMs) < FRAME_MS) return false;
    lastFrameMs = now;

    expire(now);

    /* 1. the emotion's target shape */
    EyePose wantL = poseFor(emotion, true);
    EyePose wantR = poseFor(emotion, false);

    /* 2. gaze: commanded direction plus whatever idle glance is in progress */
    const int16_t gx = (int16_t)((int32_t)gazeX * GAZE_PX_X / 100) + sacX;
    const int16_t gy = (int16_t)((int32_t)gazeY * GAZE_PX_Y / 100) + sacY;
    wantL.dx += gx; wantL.dy += gy;
    wantR.dx += gx; wantR.dy += gy;

    /* 3. glide toward it */
    easePose(curL, wantL, EASE_NUM, EASE_DEN);
    easePose(curR, wantR, EASE_NUM, EASE_DEN);

    /* 4. the involuntary layers, applied to a copy — they are per-frame
     *    modulations, not targets, so easing them would smear them away */
    outL = curL;
    outR = curR;
    modulate(now, outL, true);
    modulate(now, outR, false);

    advanceBlink(now);
    advanceSaccade(now);
    return true;
  }

 private:
  static int16_t clamp100(int16_t v) { return v < -100 ? -100 : (v > 100 ? 100 : v); }

  void touch(uint32_t now) {
    lastCommandMs = now ? now : 1;
    commandCount++;
    dozing = false;         /* any contact at all wakes the face up */
  }

  void expire(uint32_t now) {
    if (faceDue(now, speakUntil)) speakUntil = 0;
    if (faceDue(now, holdUntil)) { emotion = revertTo; holdUntil = 0; }
    /* Nobody has spoken to it in a long time: doze, but wake on any command.
     * Deliberately not a "sleepy" emotion assignment — that would be reported
     * back to IRIS as the commanded mood and confuse the next transition. */
    if (!dozing && !speaking(now) &&
        (uint32_t)(now - lastCommandMs) > IDLE_SLEEPY_MS) {
      dozing = true;
    }
  }

  /* Breathing, the talking bounce, per-emotion life, and the blink. */
  void modulate(uint32_t now, EyePose& p, bool isLeft) {
    const float t = (float)now * 0.001f;
    float hMul = 1.0f, wMul = 1.0f;
    int16_t dyAdd = 0, dxAdd = 0;

    /* Always breathing: ~4 s period, barely perceptible, and the single
     * cheapest thing that stops a face looking like a static bitmap. */
    hMul *= 1.0f + 0.035f * sinf(t * 1.6f);

    if (speaking(now)) {
      /* Three incommensurate rates so the bounce never settles into an
       * obvious loop — it reads as syllables rather than a metronome. */
      const float env = 0.55f * sinf(t * 13.0f)
                      + 0.30f * sinf(t * 21.7f)
                      + 0.15f * sinf(t * 7.3f);
      hMul *= 1.0f + 0.13f * env;
      dyAdd += (int16_t)(3.0f * env);
    }

    switch (emotion) {
      case EMO_LISTENING:
        wMul *= 1.0f + 0.030f * sinf(t * 6.0f);      /* attentive pulse      */
        break;
      case EMO_EXCITED:
        hMul *= 1.0f + 0.045f * sinf(t * 9.5f);      /* can barely sit still */
        break;
      case EMO_LOVE:
        { const float beat = sinf(t * 5.0f);          /* a heartbeat          */
          hMul *= 1.0f + 0.07f * beat; wMul *= 1.0f + 0.07f * beat; }
        break;
      case EMO_DIZZY:
        dxAdd += (int16_t)(6.0f * sinf(t * 8.0f));
        dyAdd += (int16_t)(4.0f * cosf(t * 11.0f));
        break;
      case EMO_SAD:
        dyAdd += (int16_t)(1.5f + 1.5f * sinf(t * 1.1f));  /* a slow sigh    */
        break;
      default:
        break;
    }

    if (dozing) {                    /* heavy lids, slow drift              */
      hMul *= 0.45f;
      dyAdd += 8 + (int16_t)(2.0f * sinf(t * 0.9f));
    }

    /* The blink multiplies height last, so it closes whatever shape is
     * showing — including a heart or a happy arc. */
    hMul *= blinkScale(now);

    p.h = (int16_t)((float)p.h * hMul);
    p.w = (int16_t)((float)p.w * wMul);
    p.dy += dyAdd;
    p.dx += dxAdd;
    (void)isLeft;
  }

  float blinkScale(uint32_t now) const {
    if (blinkStartMs == 0) return 1.0f;
    const uint32_t elapsed = now - blinkStartMs;
    if (elapsed >= BLINK_MS) return 1.0f;
    const float half = BLINK_MS * 0.5f;
    const float k = (elapsed < half) ? (elapsed / half)          /* closing */
                                     : ((BLINK_MS - elapsed) / half); /* opening */
    return 1.0f - 0.94f * k;         /* never fully 0: a 1 px line still reads */
  }

  void advanceBlink(uint32_t now) {
    if (blinkStartMs != 0) {
      if ((uint32_t)(now - blinkStartMs) >= BLINK_MS) {
        blinkStartMs = 0;
        /* A queued blink is scheduled as a DUE TIME, never by pre-setting
         * blinkStartMs into the future: blinkScale() measures now-start as
         * unsigned, so a future start reads as an enormous elapsed time and
         * the blink would be over before it began. */
        if (blinksQueued > 0) {
          blinksQueued--;
          pairSecond = true;
          nextBlinkAt = faceDeadline(now, 70);   /* the pair's second blink */
        } else {
          scheduleBlink(now);
        }
      }
      return;
    }
    if (faceDue(now, nextBlinkAt)) {
      blinkStartMs = now ? now : 1;
      nextBlinkAt = 0;
      /* Only a FRESH blink may become a pair. Testing blinksQueued alone was
       * not enough: by the time the pair's second blink starts the counter is
       * already back at 0, so it rolled again and pairs chained into a flutter
       * roughly 18% of the time. An explicit flag is the only thing that can
       * tell "first of a pair" from "second of a pair" apart. */
      if (pairSecond) {
        pairSecond = false;
      } else if (blinksQueued == 0 && random(100) < DOUBLE_BLINK_PCT) {
        blinksQueued = 1;
      }
    }
  }

  void scheduleBlink(uint32_t now) {
    const bool talking = speaking(now);
    const long lo = talking ? BLINK_GAP_MIN_TALK : BLINK_GAP_MIN;
    const long hi = talking ? BLINK_GAP_MAX_TALK : BLINK_GAP_MAX;
    nextBlinkAt = faceDeadline(now, (uint32_t)random(lo, hi));
  }

  void advanceSaccade(uint32_t now) {
    if (!faceDue(now, nextSaccadeAt)) return;
    /* Alternate between a small glance and returning to centre, so the eyes
     * wander rather than drifting further and further off. */
    if (sacX != 0 || sacY != 0) {
      sacX = sacY = 0;
    } else if (!speaking(now)) {
      sacX = (int16_t)random(-SACCADE_RANGE_X, SACCADE_RANGE_X + 1);
      sacY = (int16_t)random(-SACCADE_RANGE_Y, SACCADE_RANGE_Y + 1);
    }
    scheduleSaccade(now);
  }

  void scheduleSaccade(uint32_t now) {
    nextSaccadeAt = faceDeadline(now, (uint32_t)random(SACCADE_MIN_MS, SACCADE_MAX_MS));
  }
};
