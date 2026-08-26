/*
 * ============================================================================
 *  IRIS ROBOT EYES  —  two 0.96"/0.98" SSD1306 OLEDs, 128x64, one per eye
 * ============================================================================
 *
 *  WHY IT LOOKS ALIVE
 *  A face reads as alive because of the small involuntary things, not the big
 *  poses. Everything here is layered in that order:
 *
 *    1. POSE        the emotion's target shape (size, lids, brow angle)
 *    2. EASING      every parameter glides toward its target instead of
 *                   snapping, so a mood change is a movement, not a cut
 *    3. BREATHING   a slow ~4 s height swell, barely visible, always present
 *    4. SACCADES    tiny random glances while idle, then back to centre
 *    5. BLINKS      fast (110 ms), randomly spaced, sometimes doubled
 *    6. SPEAKING    a syllable-paced bounce layered on top while talking
 *
 *  Remove any one of those and it starts to look like a screensaver.
 *
 *  EACH DISPLAY SHOWS ONE EYE, using the whole 128x64 panel — so the eyes are
 *  big, which is most of what makes them cute. Expression comes from the
 *  SHAPE of a solid filled eye (Anki-Vector style), never from a drawn pupil:
 *  on a 1-bit panel a solid shape stays crisp while fine detail turns to mush.
 *
 *  "Inner" and "outer" below are anatomical: inner = toward the nose. The
 *  drawing code mirrors them per eye, so an angry brow slants down toward the
 *  nose on BOTH eyes rather than both slanting the same way on screen.
 * ============================================================================
 */
#pragma once

#include <Arduino.h>

/* The colour constants come from Adafruit_SSD1306.h. Defined here as a
 * fallback so this header does not silently depend on include order — and so
 * the geometry can be compiled and tested without the display driver. */
#ifndef SSD1306_WHITE
#define SSD1306_WHITE 1
#endif
#ifndef SSD1306_BLACK
#define SSD1306_BLACK 0
#endif

/* Panel geometry. One eye per panel, centred. */
#define EYE_W        128
#define EYE_H        64
#define EYE_CX       (EYE_W / 2)
#define EYE_CY       (EYE_H / 2)

/* How the eye body is drawn. */
enum EyeStyle : uint8_t {
  STYLE_RECT = 0,   /* rounded rectangle — the default, neutral eye        */
  STYLE_ARC,        /* upward crescent — the classic happy squint  ^   ^   */
  STYLE_HEART,      /* heart                                               */
  STYLE_CLOSED,     /* a flat lid bar — blink held, or a wink              */
  STYLE_CROSS,      /* two strokes — dizzy / error                         */
};

enum Emotion : uint8_t {
  EMO_NEUTRAL = 0, EMO_HAPPY, EMO_EXCITED, EMO_LOVE, EMO_SAD, EMO_ANGRY,
  EMO_SURPRISED, EMO_SLEEPY, EMO_THINKING, EMO_CONFUSED, EMO_LISTENING,
  EMO_WINK, EMO_SUSPICIOUS, EMO_DIZZY, EMO_COUNT
};

/* Spoken names, in the same order as the enum. Also what /face accepts. */
static const char* const EMOTION_NAMES[EMO_COUNT] = {
  "neutral", "happy", "excited", "love", "sad", "angry",
  "surprised", "sleepy", "thinking", "confused", "listening",
  "wink", "suspicious", "dizzy"
};

/* One eye's target geometry. Every field is interpolated, so any two poses
 * blend into a sensible in-between — which is what makes a mood change look
 * like a movement of the face rather than a slide change. */
struct EyePose {
  int16_t w, h;        /* eye size in pixels                                */
  int16_t r;           /* corner radius                                     */
  int16_t dx, dy;      /* offset from panel centre (gaze + droop)           */
  int16_t lidTop;      /* pixels the upper lid cuts in                      */
  int16_t lidBot;      /* pixels the lower lid cuts in                      */
  int16_t browIn;      /* wedge cut from the top INNER corner (angry)       */
  int16_t browOut;     /* wedge cut from the top OUTER corner (sad)         */
  int16_t arcT;        /* crescent thickness, STYLE_ARC only                */
  uint8_t style;
  uint8_t glint;       /* 0 none, 1 single shine, 2 sparkle pair            */
};

/* ─────────────────────── the emotion table ──────────────────────── */

/* Written as a function rather than an array so the asymmetric moods (a wink,
 * a confused lopsided squint) are expressible without a second table. */
static EyePose poseFor(uint8_t emotion, bool isLeft) {
  EyePose p;
  /* Neutral is the base every other pose edits, so a new emotion only has to
   * state what makes it different. */
  p.w = 88; p.h = 56; p.r = 20;
  p.dx = 0; p.dy = 0;
  p.lidTop = 0; p.lidBot = 0;
  p.browIn = 0; p.browOut = 0;
  p.arcT = 14;
  p.style = STYLE_RECT;
  p.glint = 1;

  switch (emotion) {
    case EMO_HAPPY:
      /* Squinting with pleasure: the eye closes into an upward arc. */
      p.w = 96; p.h = 52; p.arcT = 16; p.dy = 3;
      p.style = STYLE_ARC; p.glint = 0;
      break;

    case EMO_EXCITED:
      /* Wide open, lifted, extra sparkle. Height leaves room for the lift:
       * at h=62 the clamp allowed only 1px of dy, so the declared pose was
       * not the pose that could ever be drawn. */
      p.w = 98; p.h = 60; p.r = 24; p.dy = -2; p.glint = 2;
      break;

    case EMO_LOVE:
      p.w = 96; p.h = 58; p.style = STYLE_HEART; p.glint = 0;
      break;

    case EMO_SAD:
      /* Drooped and small, brows lifted at the OUTER corners — the single
       * strongest sadness cue there is. */
      p.w = 82; p.h = 42; p.r = 16; p.dy = 9;
      p.lidTop = 8; p.browOut = 20;
      break;

    case EMO_ANGRY:
      /* Brows driven down toward the nose, eye narrowed. */
      p.w = 92; p.h = 40; p.r = 12;
      p.lidTop = 3; p.browIn = 26;
      break;

    case EMO_SURPRISED:
      /* Small and very round: a startled eye gets taller, not wider. */
      p.w = 74; p.h = 64; p.r = 32; p.glint = 2;
      break;

    case EMO_SLEEPY:
      p.w = 86; p.h = 30; p.r = 13; p.dy = 11;
      p.lidTop = 16; p.glint = 0;
      break;

    case EMO_THINKING:
      /* Gaze up and away, one eye slightly narrower than the other. */
      p.w = 82; p.h = 48; p.r = 18;
      p.dx = -12; p.dy = -7;
      p.lidTop = 7; p.browOut = 7;
      if (!isLeft) { p.h -= 8; p.lidTop += 4; }
      break;

    case EMO_CONFUSED:
      /* Lopsided on purpose: matched eyes never read as confused. */
      if (isLeft) { p.w = 92; p.h = 58; p.r = 22; p.dy = -3; }
      else        { p.w = 74; p.h = 40; p.r = 16; p.dy = 5; p.browOut = 12; }
      break;

    case EMO_LISTENING:
      /* Attentive: a touch wider than neutral, centred, bright. */
      p.w = 94; p.h = 60; p.r = 22; p.glint = 1;
      break;

    case EMO_WINK:
      if (isLeft) { p.style = STYLE_CLOSED; p.h = 10; p.glint = 0; }
      else        { p.w = 96; p.h = 52; p.arcT = 16; p.style = STYLE_ARC; p.glint = 0; }
      break;

    case EMO_SUSPICIOUS:
      p.w = 90; p.h = 32; p.r = 12;
      p.lidTop = 20; p.browIn = 10; p.glint = 0;
      break;

    case EMO_DIZZY:
      /* Square, and inside the 64px panel — 66 was taller than the screen. */
      p.w = 62; p.h = 62; p.style = STYLE_CROSS; p.glint = 0;
      break;

    case EMO_NEUTRAL:
    default:
      break;
  }
  return p;
}

static uint8_t emotionFromName(const String& name, bool* found = nullptr) {
  String wanted = name;
  wanted.trim();
  wanted.toLowerCase();
  for (uint8_t i = 0; i < EMO_COUNT; i++) {
    if (wanted == EMOTION_NAMES[i]) { if (found) *found = true; return i; }
  }
  /* A few natural synonyms, so a spoken word does not need to be the enum. */
  if (wanted == "sleep" || wanted == "tired")            { if (found) *found = true; return EMO_SLEEPY; }
  if (wanted == "idle" || wanted == "normal" || wanted == "calm")
                                                          { if (found) *found = true; return EMO_NEUTRAL; }
  if (wanted == "smile" || wanted == "glad")              { if (found) *found = true; return EMO_HAPPY; }
  if (wanted == "mad" || wanted == "cross")               { if (found) *found = true; return EMO_ANGRY; }
  if (wanted == "shock" || wanted == "shocked" || wanted == "wow")
                                                          { if (found) *found = true; return EMO_SURPRISED; }
  if (wanted == "think" || wanted == "thoughtful")        { if (found) *found = true; return EMO_THINKING; }
  if (wanted == "listen")                                 { if (found) *found = true; return EMO_LISTENING; }
  if (found) *found = false;
  return EMO_NEUTRAL;
}

/* ───────────────────────── easing ──────────────────────────────── */

/* Exponential smoothing toward the target: fast at first, gentle at the end.
 * `num/den` is the fraction of the remaining distance covered per frame, so
 * the motion is frame-rate shaped rather than frame-rate counted.
 *
 * The +/-1 nudge matters: integer easing alone stalls one pixel short forever,
 * which shows up as an eye that never quite finishes closing. */
static int16_t ease(int16_t current, int16_t target, int16_t num, int16_t den) {
  const int32_t delta = (int32_t)target - current;
  if (delta == 0) return current;
  int32_t stepv = (delta * num) / den;
  if (stepv == 0) stepv = (delta > 0) ? 1 : -1;
  return (int16_t)(current + stepv);
}

static void easePose(EyePose& cur, const EyePose& want, int16_t num, int16_t den) {
  cur.w       = ease(cur.w,       want.w,       num, den);
  cur.h       = ease(cur.h,       want.h,       num, den);
  cur.r       = ease(cur.r,       want.r,       num, den);
  cur.dx      = ease(cur.dx,      want.dx,      num, den);
  cur.dy      = ease(cur.dy,      want.dy,      num, den);
  cur.lidTop  = ease(cur.lidTop,  want.lidTop,  num, den);
  cur.lidBot  = ease(cur.lidBot,  want.lidBot,  num, den);
  cur.browIn  = ease(cur.browIn,  want.browIn,  num, den);
  cur.browOut = ease(cur.browOut, want.browOut, num, den);
  cur.arcT    = ease(cur.arcT,    want.arcT,    num, den);
  /* Style and glint are categorical — there is no halfway between a heart and
   * a rectangle, so they switch once the size has mostly caught up. Blending
   * them numerically produced a visible glitch frame. */
  if (abs((int)cur.h - (int)want.h) < 8 && abs((int)cur.w - (int)want.w) < 8) {
    cur.style = want.style;
    cur.glint = want.glint;
  }
}

/* ───────────────────── clamping to the panel ────────────────────── */

/* Nothing may be asked to draw outside 128x64, and Adafruit_GFX's
 * fillRoundRect() corrupts its own arcs when the radius exceeds half the
 * shorter side — so both are enforced here rather than trusted per-pose. */
static void clampPose(EyePose& p) {
  if (p.w < 8) p.w = 8;
  if (p.h < 2) p.h = 2;
  if (p.w > EYE_W) p.w = EYE_W;
  if (p.h > EYE_H) p.h = EYE_H;

  const int16_t maxR = (p.w < p.h ? p.w : p.h) / 2;
  if (p.r < 0) p.r = 0;
  if (p.r > maxR) p.r = maxR;

  /* Lids may meet, never cross. Splitting the overflow between them was not
   * enough: when one lid was already smaller than its half of the overflow it
   * clamped at 0 and the remainder stayed on the other, so the two still
   * summed past the eye height — and since both are drawn as BLACK rectangles
   * over the white body, that erased the eye entirely. A sleepy face
   * (lidTop 16) mid-blink (h ~3) vanished instead of becoming a thin line.
   * Clamping sequentially makes the bound hold by construction. */
  if (p.lidTop < 0) p.lidTop = 0;
  if (p.lidBot < 0) p.lidBot = 0;
  if (p.lidTop > p.h) p.lidTop = p.h;
  if (p.lidBot > p.h - p.lidTop) p.lidBot = p.h - p.lidTop;
  if (p.browIn < 0) p.browIn = 0;
  if (p.browOut < 0) p.browOut = 0;
  if (p.browIn > p.h) p.browIn = p.h;
  if (p.browOut > p.h) p.browOut = p.h;
  if (p.arcT < 2) p.arcT = 2;
  if (p.arcT > p.h) p.arcT = p.h;

  /* Keep the whole eye on screen no matter what gaze was requested. */
  const int16_t slackX = (EYE_W - p.w) / 2;
  const int16_t slackY = (EYE_H - p.h) / 2;
  if (p.dx >  slackX) p.dx =  slackX;
  if (p.dx < -slackX) p.dx = -slackX;
  if (p.dy >  slackY) p.dy =  slackY;
  if (p.dy < -slackY) p.dy = -slackY;
}

/* ───────────────────────── drawing ─────────────────────────────── */

/* Draws one eye onto an Adafruit_GFX-compatible display, already cleared.
 * Templated on the display type so this header needs no Adafruit include —
 * it keeps the geometry testable in isolation from the driver. */
template <typename GFX>
void drawEye(GFX& d, EyePose p, bool isLeft) {
  clampPose(p);

  const int16_t cx = EYE_CX + p.dx;
  const int16_t cy = EYE_CY + p.dy;
  const int16_t x0 = cx - p.w / 2;
  const int16_t y0 = cy - p.h / 2;

  if (p.style == STYLE_CROSS) {
    /* Two thick strokes. Drawn as stacked lines because a 1-bit panel makes a
     * single-pixel diagonal look like dashes. */
    for (int8_t t = -2; t <= 2; t++) {
      d.drawLine(x0 + t, y0, x0 + p.w + t, y0 + p.h, SSD1306_WHITE);
      d.drawLine(x0 + p.w + t, y0, x0 + t, y0 + p.h, SSD1306_WHITE);
    }
    return;
  }

  if (p.style == STYLE_CLOSED) {
    /* A closed lid still has width, and still moves with gaze — a flat bar
     * parked at centre is what makes a wink look broken. */
    const int16_t t = p.h < 4 ? 4 : p.h;
    d.fillRoundRect(x0, cy - t / 2, p.w, t, t / 2, SSD1306_WHITE);
    return;
  }

  if (p.style == STYLE_HEART) {
    const int16_t lobe = p.w / 4;
    const int16_t ly = y0 + p.h / 3;
    d.fillCircle(cx - lobe, ly, lobe, SSD1306_WHITE);
    d.fillCircle(cx + lobe, ly, lobe, SSD1306_WHITE);
    d.fillTriangle(cx - 2 * lobe, ly, cx + 2 * lobe, ly, cx, y0 + p.h, SSD1306_WHITE);
    return;
  }

  if (p.style == STYLE_ARC) {
    /* The happy squint. A filled circle with a second circle punched out just
     * below it leaves an upward crescent — the "^" that reads as delight.
     * Doing it with two circles rather than an arc primitive keeps the ends
     * tapered, which is the whole charm of the shape. */
    const int16_t rad = p.w / 2;
    int16_t arcCy = cy + rad / 2;
    /* Keep the crescent's centre on the panel. The eye box is clamped, but the
     * arc hangs rad/2 BELOW that box, and a blink shrinks h — which widens the
     * allowed dy and pushes the arc off the bottom. That mattered because the
     * trim rectangle below would then get a negative height, and Adafruit_GFX
     * draws a negative-height rect UPWARD: it erased the very crescent it was
     * there to tidy, so a happy face looking down flickered as it blinked. */
    if (arcCy > EYE_H - 2) arcCy = EYE_H - 2;
    d.fillCircle(cx, arcCy, rad, SSD1306_WHITE);
    d.fillCircle(cx, arcCy + p.arcT, rad, SSD1306_BLACK);
    const int16_t trimY = arcCy + 1;
    if (trimY < EYE_H) d.fillRect(0, trimY, EYE_W, EYE_H - trimY, SSD1306_BLACK);
    return;
  }

  /* STYLE_RECT — the workhorse. Solid body, then black shapes carve the
   * expression out of it. Subtractive drawing keeps every edge crisp, which
   * outlining on a 1-bit panel does not. */
  d.fillRoundRect(x0, y0, p.w, p.h, p.r, SSD1306_WHITE);

  if (p.lidTop > 0) d.fillRect(x0 - 2, y0 - 2, p.w + 4, p.lidTop + 2, SSD1306_BLACK);
  if (p.lidBot > 0) d.fillRect(x0 - 2, y0 + p.h - p.lidBot, p.w + 4, p.lidBot + 4, SSD1306_BLACK);

  /* Brow wedges. `inner` is the nose side, which is the RIGHT edge of the left
   * eye's panel and the LEFT edge of the right eye's. */
  const int16_t innerX = isLeft ? (x0 + p.w) : x0;
  const int16_t outerX = isLeft ? x0 : (x0 + p.w);
  if (p.browIn > 0)
    d.fillTriangle(innerX, y0 - 1, innerX, y0 + p.browIn, outerX, y0 - 1, SSD1306_BLACK);
  if (p.browOut > 0)
    d.fillTriangle(outerX, y0 - 1, outerX, y0 + p.browOut, innerX, y0 - 1, SSD1306_BLACK);

  /* Shine. A hole in a solid eye reads as gloss, and gloss reads as friendly —
   * this one small circle does a surprising amount of the "cute". */
  if (p.glint >= 1 && p.h > 16 && p.w > 24) {
    const int16_t gx = isLeft ? (x0 + p.w / 4) : (x0 + p.w - p.w / 4);
    const int16_t gy = y0 + p.h / 4 + p.lidTop / 2;
    const int16_t gr = p.w / 11;
    if (gy - gr > y0 + p.lidTop) d.fillCircle(gx, gy, gr, SSD1306_BLACK);
    if (p.glint >= 2) {
      const int16_t sr = gr / 2 > 1 ? gr / 2 : 2;
      const int16_t sx = isLeft ? (gx + p.w / 3) : (gx - p.w / 3);
      const int16_t sy = gy + p.h / 5;
      if (sy + sr < y0 + p.h - p.lidBot) d.fillCircle(sx, sy, sr, SSD1306_BLACK);
    }
  }
}
