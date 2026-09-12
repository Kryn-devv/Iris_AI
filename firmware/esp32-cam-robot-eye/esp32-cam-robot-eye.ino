/*
 * esp32-cam-robot-eye.ino — ESP32-CAM firmware: the robot's eye.
 *
 * A small camera server on your WiFi that the brain (IRIS / "Friday") pulls
 * from whenever it wants to look at the world.
 *
 *   GET /                     info page with a live preview and test buttons
 *   GET /capture              one fresh JPEG      <-- the brain calls this
 *       ?size=svga            switch resolution first (settles, then shoots)
 *       ?warmup=2             throw away N frames so exposure has settled
 *       ?flash=1&flash_ms=200 pulse the white LED for this shot
 *   GET /motion               "is anybody there?", and where  <-- cheap
 *   GET /status               health: camera, wifi, heap, uptime, reset cause
 *   GET /settings             read every sensor setting; set them with query
 *                             params (framesize, quality, vflip, hmirror,
 *                             brightness, contrast, saturation, awb, aec, agc,
 *                             gain, wb_mode, denoise, lenc, special_effect)
 *   GET /flash?on=1[&ms=200]  white flash LED, optionally as a timed pulse
 *   GET :81/stream            MJPEG live stream for watching in a browser
 *
 * The stream runs on its own port so someone watching the live feed can never
 * block the brain from grabbing a /capture frame on port 80.
 *
 * WHY THIS BOARD DOES NOT RECOGNISE FACES ITSELF
 * It cannot, any more. Espressif's face pipeline (ESP-WHO / ESP-DL) dropped
 * the original ESP32 — it targets the S3 and P4 — and the Arduino core removed
 * the old fd_forward.h / fr_forward.h headers after 3.0.7. Even when they
 * existed, recognition on a plain ESP32 was too slow to be pleasant. So this
 * board does the part it is good at — a clean frame, fast, plus a cheap
 * "something moved, and it moved *there*" — and the brain, which has the
 * models and the memory of who you are, does the recognising. See the README.
 *
 * Board setup (Arduino IDE):
 *   - Install the "esp32" boards package (Espressif Systems)
 *   - Tools > Board > "AI Thinker ESP32-CAM"
 *   - Tools > Partition Scheme > "Huge APP"
 *   - Tools > PSRAM > "Enabled"
 *   - Tools > Upload Speed > 115200 (the CH340 on the MB board is flaky faster)
 *   - Copy config.example.h to config.h and fill in your WiFi details
 *
 * Flashing with the ESP32-CAM-MB board (camera pushed onto it, micro-USB to
 * the PC): just click Upload. Most MB boards let the IDE reset into flash mode
 * by itself; if it sits at "Connecting....", hold IO0, tap RST, release IO0.
 *
 * Flashing a bare board (no MB, the board itself has no USB port):
 *   - USB-serial adapter: 5V->5V, GND->GND, TX->U0R, RX->U0T
 *   - Hold GPIO0 to GND while pressing reset to enter flash mode;
 *     disconnect GPIO0 and reset again to run.
 *
 * Power: 5V at ~180 mA steady, spikes past 300 mA on WiFi transmit. A power
 * bank rated 2 A per port on a short cable is fine; the S3's 3.3V pin is not.
 * If /status reports reset_reason "brownout", it is the supply, not the code.
 */

#include <strings.h>    // strcasecmp, for the framesize names

#include "esp_camera.h"
#include "esp_http_server.h"
#include "esp_timer.h"
#include "esp_system.h"
#include <WiFi.h>
#include <ESPmDNS.h>

/* Your WiFi name and password live in config.h, which git ignores. Until you
 * create it (copy config.example.h to config.h) the example is used, so the
 * sketch compiles out of the box — it just will not find your hotspot. */
#if __has_include("config.h")
#include "config.h"
#endif
#ifndef WIFI_SSID   /* no config.h of ours was found (a framework header of the same name does not count) */
#warning "No config.h - copy config.example.h to config.h and put your hotspot name and password in it"
#include "config.example.h"
#endif
#include "camera_pins.h"
#include "motion.h"

/* ───────────────────────────── state ───────────────────────────── */

static httpd_handle_t controlServer = nullptr;
static httpd_handle_t streamServer = nullptr;

static bool flashOn = false;
static unsigned long flashUntilMs = 0;      // 0 = no deadline (on until told)

static MotionDetector motion;
static unsigned long lastMotionSampleMs = 0;

static volatile int streamClients = 0;
static volatile bool captureBusy = false;   // keeps the sampler out of the way

static uint32_t framesCaptured = 0;
static uint32_t framesStreamed = 0;
static uint32_t captureErrors = 0;
static unsigned long lastCaptureMs = 0;
static uint32_t lastCaptureBytes = 0;

static bool mdnsUp = false;
static bool wifiWasDown = false;
static unsigned long wifiRetryAtMs = 0;
static unsigned long wifiBackoffMs = 2000;
static uint32_t wifiDrops = 0;

/* ───────────────────────────── small helpers ───────────────────────────── */

static void statusLed(bool on) {
#if STATUS_LED_GPIO_NUM >= 0
#if STATUS_LED_ACTIVE_LOW
  digitalWrite(STATUS_LED_GPIO_NUM, on ? LOW : HIGH);
#else
  digitalWrite(STATUS_LED_GPIO_NUM, on ? HIGH : LOW);
#endif
#else
  (void)on;
#endif
}

static void setFlash(bool on) {
#if ENABLE_FLASH_LED && FLASH_LED_GPIO_NUM >= 0
  flashOn = on;
  digitalWrite(FLASH_LED_GPIO_NUM, on ? HIGH : LOW);
  // The lamp changes the whole picture, so the motion baseline is now stale.
  motion.resetBaseline();
#else
  (void)on;
#endif
}

static bool tokenRequired() {
  static const char* token = ACCESS_TOKEN;
  return token[0] != '\0';
}

// Length-checked comparison that always walks the whole expected token, so a
// wrong guess takes the same time as a right one.
static bool tokenMatches(const char* candidate) {
  static const char* expected = ACCESS_TOKEN;
  const size_t n = strlen(expected);
  if (strlen(candidate) != n) return false;
  uint8_t diff = 0;
  for (size_t i = 0; i < n; i++) diff |= (uint8_t)(expected[i] ^ candidate[i]);
  return diff == 0;
}

// Every response carries this: the brain's own dashboard is a web page, and
// without it the browser refuses to read frames from a different origin.
static void sendCors(httpd_req_t* req) {
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Headers", "X-Auth-Token");
}

// Reads the query string into `buf`. False means it did not fit, which the
// caller must refuse rather than act on: a truncated query silently drops
// whichever parameters fell off the end.
static bool queryString(httpd_req_t* req, char* buf, size_t len) {
  buf[0] = '\0';
  const esp_err_t err = httpd_req_get_url_query_str(req, buf, len);
  if (err == ESP_ERR_NOT_FOUND) return true;      // no query at all is fine
  if (err != ESP_OK) {
    buf[0] = '\0';
    return false;
  }
  return true;
}

static esp_err_t rejectLongQuery(httpd_req_t* req) {
  sendCors(req);
  httpd_resp_send_err(req, HTTPD_414_URI_TOO_LONG, "Query string too long");
  return ESP_FAIL;
}

// True when the request may proceed. Answers 401 itself when it may not.
static bool authOk(httpd_req_t* req, const char* query) {
  if (!tokenRequired()) return true;

  char value[65] = {0};
  if (query != nullptr && query[0] != '\0' &&
      httpd_query_key_value(query, "token", value, sizeof(value)) == ESP_OK &&
      tokenMatches(value)) {
    return true;
  }
  if (httpd_req_get_hdr_value_str(req, "X-Auth-Token", value, sizeof(value)) == ESP_OK &&
      tokenMatches(value)) {
    return true;
  }
  sendCors(req);
  httpd_resp_send_err(req, HTTPD_401_UNAUTHORIZED, "Missing or wrong token");
  return false;
}

// Strict integer parse: a typo is an error, never a silent 0.
static bool parseInt(const char* query, const char* key, long* out) {
  char value[16] = {0};
  if (query == nullptr || query[0] == '\0') return false;
  if (httpd_query_key_value(query, key, value, sizeof(value)) != ESP_OK) return false;
  if (value[0] == '\0') return false;
  char* end = nullptr;
  const long parsed = strtol(value, &end, 10);
  if (end == value || (end != nullptr && *end != '\0')) return false;
  *out = parsed;
  return true;
}

static long clampLong(long v, long lo, long hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

static esp_err_t sendJson(httpd_req_t* req, const char* json) {
  sendCors(req);
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_send(req, json, HTTPD_RESP_USE_STRLEN);
}

/* ───────────────────────────── framesize names ───────────────────────────── */

struct FrameSizeName {
  const char* name;
  framesize_t size;
  uint16_t w, h;
};

// Only the 4:3 sizes the OV2640 does well, plus 16:9 HD. The motion grid is
// 4:3, so HD is offered but noted as the odd one out.
static const FrameSizeName FRAME_SIZES[] = {
  {"qqvga", FRAMESIZE_QQVGA, 160, 120},
  {"qvga",  FRAMESIZE_QVGA,  320, 240},
  {"cif",   FRAMESIZE_CIF,   400, 296},
  {"hvga",  FRAMESIZE_HVGA,  480, 320},
  {"vga",   FRAMESIZE_VGA,   640, 480},
  {"svga",  FRAMESIZE_SVGA,  800, 600},
  {"xga",   FRAMESIZE_XGA,  1024, 768},
  {"hd",    FRAMESIZE_HD,   1280, 720},
  {"sxga",  FRAMESIZE_SXGA, 1280,1024},
  {"uxga",  FRAMESIZE_UXGA, 1600,1200},
};
static const size_t FRAME_SIZE_COUNT = sizeof(FRAME_SIZES) / sizeof(FRAME_SIZES[0]);

static const FrameSizeName* frameSizeByName(const char* name) {
  for (size_t i = 0; i < FRAME_SIZE_COUNT; i++) {
    if (strcasecmp(name, FRAME_SIZES[i].name) == 0) return &FRAME_SIZES[i];
  }
  return nullptr;
}

static const char* frameSizeName() {
  sensor_t* s = esp_camera_sensor_get();
  if (s == nullptr) return "unknown";
  for (size_t i = 0; i < FRAME_SIZE_COUNT; i++) {
    if (FRAME_SIZES[i].size == s->status.framesize) return FRAME_SIZES[i].name;
  }
  return "other";
}

// Switch resolution and let the sensor settle. Changing framesize resizes the
// frame buffers and restarts the auto-exposure hunt, so the first frames after
// it are the wrong brightness — those get thrown away here rather than handed
// to a face recogniser as evidence.
static bool applyFrameSize(framesize_t size) {
  sensor_t* s = esp_camera_sensor_get();
  if (s == nullptr || s->set_framesize == nullptr) return false;
  if (s->status.framesize == size) return true;
  if (s->set_framesize(s, size) != 0) return false;

  motion.resetBaseline();
  for (int i = 0; i < 2; i++) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb != nullptr) esp_camera_fb_return(fb);
  }
  return true;
}

/* ───────────────────────────── camera ───────────────────────────── */

static bool initCameraOnce() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    // Allocate for UXGA even though we will run smaller: the frame buffers are
    // sized once, at init, from this value. Ask for SVGA here and a later
    // /settings?framesize=uxga has nowhere to put the picture. Asking for the
    // maximum costs PSRAM we are not otherwise using, and buys the brain the
    // freedom to ask for a big frame when a face is far away.
    config.frame_size = FRAMESIZE_UXGA;
    config.jpeg_quality = 10;
    config.fb_count = 2;
    config.grab_mode = CAMERA_GRAB_LATEST;
    config.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    // No PSRAM: one small buffer in internal RAM is all that fits.
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 15;
    config.fb_count = 1;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  const esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("  camera init failed: 0x%x\n", err);
    return false;
  }

  sensor_t* s = esp_camera_sensor_get();
  if (s != nullptr) {
    // Down to the working resolution now that the buffers exist.
    if (psramFound()) s->set_framesize(s, DEFAULT_FRAMESIZE);
    if (s->set_quality) s->set_quality(s, DEFAULT_JPEG_QUALITY);

    // Colour accuracy matters here: the brain judges ripeness and reads
    // labels off these frames, and a blue cast makes a red apple green.
    if (s->set_whitebal) s->set_whitebal(s, 1);
    if (s->set_awb_gain) s->set_awb_gain(s, 1);
    if (s->set_exposure_ctrl) s->set_exposure_ctrl(s, 1);
    if (s->set_gain_ctrl) s->set_gain_ctrl(s, 1);
    if (s->set_saturation) s->set_saturation(s, 0);
    if (s->set_vflip) s->set_vflip(s, DEFAULT_VFLIP ? 1 : 0);
    if (s->set_hmirror) s->set_hmirror(s, DEFAULT_HMIRROR ? 1 : 0);

    // The OV3660 some boards ship instead of the OV2640 comes out upside
    // down and washed out with the settings above.
    if (s->id.PID == OV3660_PID) {
      if (s->set_vflip) s->set_vflip(s, DEFAULT_VFLIP ? 0 : 1);
      if (s->set_brightness) s->set_brightness(s, 1);
      if (s->set_saturation) s->set_saturation(s, -2);
    }
  }
  return true;
}

// Camera init fails for real reasons — usually a marginal 5V supply or a
// ribbon that has worked loose. Retry a few times before giving up, because a
// brownout at boot often clears on the second attempt.
static bool initCamera() {
  for (int attempt = 1; attempt <= 3; attempt++) {
    Serial.printf("Camera init, attempt %d/3...\n", attempt);
    if (initCameraOnce()) {
      Serial.printf("Camera ready (%s, %s)\n", CAMERA_BOARD_NAME, frameSizeName());
      return true;
    }
    esp_camera_deinit();
    delay(500);
  }
  return false;
}

/* ───────────────────────────── handlers ───────────────────────────── */

// One fresh JPEG. This is the endpoint the brain calls when it wants to look.
static esp_err_t captureHandler(httpd_req_t* req) {
  char query[128];
  if (!queryString(req, query, sizeof(query))) return rejectLongQuery(req);
  if (!authOk(req, query)) return ESP_FAIL;

  captureBusy = true;

  // ?size=svga — the brain asks for more pixels when a face is far away.
  char sizeName[12] = {0};
  if (httpd_query_key_value(query, "size", sizeName, sizeof(sizeName)) == ESP_OK) {
    const FrameSizeName* wanted = frameSizeByName(sizeName);
    if (wanted == nullptr) {
      captureBusy = false;
      sendCors(req);
      httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Unknown size (try qvga, vga, svga, xga, uxga)");
      return ESP_FAIL;
    }
    applyFrameSize(wanted->size);
  }

  // ?flash=1 — light the scene for this one shot. Always released below, so a
  // failed capture cannot leave the lamp burning.
  bool pulsed = false;
  long flashMs = 150;
  long flashWanted = 0;
  if (parseInt(query, "flash", &flashWanted) && flashWanted != 0) {
    long requested = 0;
    if (parseInt(query, "flash_ms", &requested)) flashMs = clampLong(requested, 10, FLASH_MAX_MS);
    setFlash(true);
    pulsed = true;
    delay((uint32_t)flashMs);
  }

  // ?warmup=N — drop N frames first. Auto-exposure needs a few frames after
  // the light changes, and a face recogniser fed an under-exposed frame
  // reports "nobody there" rather than "too dark to tell".
  long warmup = 0;
  if (parseInt(query, "warmup", &warmup)) {
    warmup = clampLong(warmup, 0, 8);
    for (long i = 0; i < warmup; i++) {
      camera_fb_t* drop = esp_camera_fb_get();
      if (drop != nullptr) esp_camera_fb_return(drop);
    }
  }

  camera_fb_t* fb = esp_camera_fb_get();

  if (pulsed) setFlash(false);

  if (fb == nullptr) {
    captureErrors++;
    captureBusy = false;
    sendCors(req);
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Frame capture failed");
    return ESP_FAIL;
  }

  esp_err_t res;
  if (fb->format != PIXFORMAT_JPEG) {
    // The sensor is only ever configured for JPEG, so this should not happen.
    // Converting rather than trusting it keeps the endpoint's promise that
    // what comes back is always a JPEG.
    uint8_t* jpg = nullptr;
    size_t jpgLen = 0;
    const bool ok = frame2jpg(fb, 80, &jpg, &jpgLen);
    esp_camera_fb_return(fb);
    if (!ok) {
      captureErrors++;
      captureBusy = false;
      sendCors(req);
      httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Frame was not JPEG and would not convert");
      return ESP_FAIL;
    }
    sendCors(req);
    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "X-Camera-Id", CAMERA_ID);
    res = httpd_resp_send(req, (const char*)jpg, jpgLen);
    lastCaptureBytes = (uint32_t)jpgLen;
    free(jpg);
  } else {
    char dims[24];
    snprintf(dims, sizeof(dims), "%ux%u", (unsigned)fb->width, (unsigned)fb->height);
    sendCors(req);
    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
    httpd_resp_set_hdr(req, "X-Camera-Id", CAMERA_ID);
    httpd_resp_set_hdr(req, "X-Frame-Size", dims);
    res = httpd_resp_send(req, (const char*)fb->buf, fb->len);
    lastCaptureBytes = (uint32_t)fb->len;
    esp_camera_fb_return(fb);
  }

  framesCaptured++;
  lastCaptureMs = millis();
  captureBusy = false;
  return res;
}

// "Is anybody there?" — answered from the detector's cached state, so this is
// a few microseconds of work and the brain may poll it as often as it likes.
static esp_err_t motionHandler(httpd_req_t* req) {
  char query[128];
  if (!queryString(req, query, sizeof(query))) return rejectLongQuery(req);
  if (!authOk(req, query)) return ESP_FAIL;

#if ENABLE_MOTION
  long reset = 0;
  if (parseInt(query, "reset", &reset) && reset != 0) motion.resetBaseline();

  const unsigned long now = millis();
  motion.tick(now, MOTION_HOLD_MS);
  const MotionState& m = motion.state();

  // Where the change was, when there was any. Built separately because these
  // are objects-or-null, which one format string cannot express.
  char box[128] = "null";
  char look[64] = "null";
  if (m.haveBox) {
    snprintf(box, sizeof(box),
             "{\"x\":%u,\"y\":%u,\"w\":%u,\"h\":%u,\"units\":\"per-mille\"}",
             (unsigned)m.boxX, (unsigned)m.boxY, (unsigned)m.boxW, (unsigned)m.boxH);
    // Already in the -100..100 range the S3 node's /look?x=&y= expects, so
    // "turn the eyes toward whoever moved" is a copy of two numbers.
    snprintf(look, sizeof(look), "{\"x\":%d,\"y\":%d}", (int)m.lookX, (int)m.lookY);
  }

  char json[512];
  const int n = snprintf(
      json, sizeof(json),
      "{\"enabled\":true,\"ready\":%s,\"moved\":%s,\"recent\":%s,"
      "\"percent\":%u,\"changed_cells\":%u,\"grid\":{\"w\":%d,\"h\":%d},"
      "\"box\":%s,\"look\":%s,"
      "\"since_motion_ms\":%lu,\"samples\":%lu,\"events\":%lu}",
      m.samples > 0 ? "true" : "false",
      m.moved ? "true" : "false",
      m.recent ? "true" : "false",
      (unsigned)m.percent,
      (unsigned)m.changedCells,
      MOTION_GRID_W, MOTION_GRID_H,
      box, look,
      m.lastMotionMs ? (unsigned long)(now - m.lastMotionMs) : 0UL,
      (unsigned long)m.samples,
      (unsigned long)m.events);

  if (n < 0 || n >= (int)sizeof(json)) {
    sendCors(req);
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Motion JSON did not fit");
    return ESP_FAIL;
  }
  return sendJson(req, json);
#else
  return sendJson(req, "{\"enabled\":false,\"reason\":\"ENABLE_MOTION is 0 in config.h\"}");
#endif
}

static const char* resetReasonName() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:  return "power-on";
    case ESP_RST_SW:       return "software";
    case ESP_RST_PANIC:    return "panic";
    case ESP_RST_INT_WDT:  return "interrupt-watchdog";
    case ESP_RST_TASK_WDT: return "task-watchdog";
    case ESP_RST_WDT:      return "watchdog";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_DEEPSLEEP:return "deep-sleep";
    case ESP_RST_EXT:      return "external";
    default:               return "unknown";
  }
}

static esp_err_t statusHandler(httpd_req_t* req) {
  char query[128];
  if (!queryString(req, query, sizeof(query))) return rejectLongQuery(req);
  if (!authOk(req, query)) return ESP_FAIL;

  sensor_t* s = esp_camera_sensor_get();
  const unsigned long now = millis();
#if ENABLE_MOTION
  motion.tick(now, MOTION_HOLD_MS);
  const bool motionRecent = motion.state().recent;
#else
  const bool motionRecent = false;
#endif

  char json[768];
  const int n = snprintf(
      json, sizeof(json),
      "{\"camera\":\"%s\",\"board\":\"%s\",\"firmware\":\"robot-eye-2.0\","
      "\"sensor_pid\":%u,\"resolution\":\"%s\",\"quality\":%d,"
      "\"psram\":%s,\"motion_recent\":%s,"
      "\"wifi\":{\"ssid\":\"%s\",\"ip\":\"%s\",\"rssi\":%d,\"drops\":%lu},"
      "\"mdns\":%s,\"stream_clients\":%d,"
      "\"frames_captured\":%lu,\"frames_streamed\":%lu,\"capture_errors\":%lu,"
      "\"last_capture_bytes\":%lu,\"last_capture_age_ms\":%lu,"
      "\"free_heap\":%u,\"min_free_heap\":%u,\"free_psram\":%u,"
      "\"uptime_s\":%llu,\"reset_reason\":\"%s\",\"flash_on\":%s,\"auth\":%s}",
      CAMERA_ID, CAMERA_BOARD_NAME,
      s ? (unsigned)s->id.PID : 0u,
      frameSizeName(), s ? (int)s->status.quality : -1,
      psramFound() ? "true" : "false",
      motionRecent ? "true" : "false",
      WiFi.SSID().c_str(), WiFi.localIP().toString().c_str(), (int)WiFi.RSSI(),
      (unsigned long)wifiDrops,
      mdnsUp ? "true" : "false",
      streamClients,
      (unsigned long)framesCaptured, (unsigned long)framesStreamed,
      (unsigned long)captureErrors,
      (unsigned long)lastCaptureBytes,
      lastCaptureMs ? (unsigned long)(now - lastCaptureMs) : 0UL,
      (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap(),
      (unsigned)ESP.getFreePsram(),
      (unsigned long long)(esp_timer_get_time() / 1000000ULL),
      resetReasonName(),
      flashOn ? "true" : "false",
      tokenRequired() ? "true" : "false");

  if (n < 0 || n >= (int)sizeof(json)) {
    sendCors(req);
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Status JSON did not fit");
    return ESP_FAIL;
  }
  return sendJson(req, json);
}

// Live sensor tuning. This is what stops a camera mounted upside down on the
// robot's head from being a reflashing job, and lets the brain darken the
// exposure when it is looking at a bright window.
static esp_err_t settingsHandler(httpd_req_t* req) {
  char query[512];
  if (!queryString(req, query, sizeof(query))) return rejectLongQuery(req);
  if (!authOk(req, query)) return ESP_FAIL;

  sensor_t* s = esp_camera_sensor_get();
  if (s == nullptr) {
    sendCors(req);
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "No camera sensor");
    return ESP_FAIL;
  }

  int applied = 0;
  char rejected[64] = {0};

  char sizeName[12] = {0};
  if (httpd_query_key_value(query, "framesize", sizeName, sizeof(sizeName)) == ESP_OK) {
    const FrameSizeName* wanted = frameSizeByName(sizeName);
    if (wanted == nullptr) {
      snprintf(rejected, sizeof(rejected), "framesize");
    } else if (applyFrameSize(wanted->size)) {
      applied++;
    }
  }

  long v = 0;
  #define APPLY(key, setter, lo, hi)                     \
    if (parseInt(query, key, &v)) {                      \
      if (s->setter) {                                   \
        s->setter(s, (int)clampLong(v, lo, hi));         \
        applied++;                                       \
      }                                                  \
    }

  APPLY("quality",        set_quality,        4, 63)
  APPLY("brightness",     set_brightness,    -2,  2)
  APPLY("contrast",       set_contrast,      -2,  2)
  APPLY("saturation",     set_saturation,    -2,  2)
  APPLY("sharpness",      set_sharpness,     -2,  2)
  APPLY("special_effect", set_special_effect, 0,  6)
  APPLY("wb_mode",        set_wb_mode,        0,  4)
  APPLY("awb",            set_whitebal,       0,  1)
  APPLY("awb_gain",       set_awb_gain,       0,  1)
  APPLY("aec",            set_exposure_ctrl,  0,  1)
  APPLY("aec2",           set_aec2,           0,  1)
  APPLY("ae_level",       set_ae_level,      -2,  2)
  APPLY("aec_value",      set_aec_value,      0, 1200)
  APPLY("agc",            set_gain_ctrl,      0,  1)
  APPLY("agc_gain",       set_agc_gain,       0, 30)
  /* set_gainceiling takes an enum, not an int, on newer cores. */
  if (parseInt(query, "gainceiling", &v)) {
    if (s->set_gainceiling) {
      s->set_gainceiling(s, (gainceiling_t)clampLong(v, 0, 6));
      applied++;
    }
  }
  APPLY("bpc",            set_bpc,            0,  1)
  APPLY("wpc",            set_wpc,            0,  1)
  APPLY("raw_gma",        set_raw_gma,        0,  1)
  APPLY("lenc",           set_lenc,           0,  1)
  APPLY("denoise",        set_denoise,        0,  8)
  APPLY("colorbar",       set_colorbar,       0,  1)
  #undef APPLY

  // A flip changes every pixel, so the motion baseline has to go with it.
  if (parseInt(query, "hmirror", &v)) {
    if (s->set_hmirror) { s->set_hmirror(s, (int)clampLong(v, 0, 1)); applied++; motion.resetBaseline(); }
  }
  if (parseInt(query, "vflip", &v)) {
    if (s->set_vflip) { s->set_vflip(s, (int)clampLong(v, 0, 1)); applied++; motion.resetBaseline(); }
  }

  char json[640];
  const int n = snprintf(
      json, sizeof(json),
      "{\"applied\":%d,%s%s%s"
      "\"framesize\":\"%s\",\"quality\":%d,\"brightness\":%d,\"contrast\":%d,"
      "\"saturation\":%d,\"sharpness\":%d,\"special_effect\":%d,\"wb_mode\":%d,"
      "\"awb\":%d,\"awb_gain\":%d,\"aec\":%d,\"aec2\":%d,\"ae_level\":%d,"
      "\"aec_value\":%d,\"agc\":%d,\"agc_gain\":%d,\"gainceiling\":%d,"
      "\"bpc\":%d,\"wpc\":%d,\"raw_gma\":%d,\"lenc\":%d,\"denoise\":%d,"
      "\"hmirror\":%d,\"vflip\":%d,\"colorbar\":%d}",
      applied,
      rejected[0] ? "\"rejected\":\"" : "",
      rejected[0] ? rejected : "",
      rejected[0] ? "\"," : "",
      frameSizeName(),
      (int)s->status.quality, (int)s->status.brightness, (int)s->status.contrast,
      (int)s->status.saturation, (int)s->status.sharpness,
      (int)s->status.special_effect, (int)s->status.wb_mode,
      (int)s->status.awb, (int)s->status.awb_gain,
      (int)s->status.aec, (int)s->status.aec2, (int)s->status.ae_level,
      (int)s->status.aec_value, (int)s->status.agc, (int)s->status.agc_gain,
      (int)s->status.gainceiling,
      (int)s->status.bpc, (int)s->status.wpc, (int)s->status.raw_gma,
      (int)s->status.lenc, (int)s->status.denoise,
      (int)s->status.hmirror, (int)s->status.vflip, (int)s->status.colorbar);

  if (n < 0 || n >= (int)sizeof(json)) {
    sendCors(req);
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Settings JSON did not fit");
    return ESP_FAIL;
  }
  return sendJson(req, json);
}

static esp_err_t flashHandler(httpd_req_t* req) {
  char query[128];
  if (!queryString(req, query, sizeof(query))) return rejectLongQuery(req);
  if (!authOk(req, query)) return ESP_FAIL;

#if ENABLE_FLASH_LED && FLASH_LED_GPIO_NUM >= 0
  long on = 0;
  if (parseInt(query, "on", &on)) {
    setFlash(on != 0);
    long ms = 0;
    if (on != 0 && parseInt(query, "ms", &ms)) {
      // A timed pulse: the board turns it off itself, so a lost "off" request
      // cannot leave a 500 mA lamp on until someone notices the smell.
      flashUntilMs = millis() + (unsigned long)clampLong(ms, 10, FLASH_MAX_MS);
    } else {
      flashUntilMs = 0;
    }
  }
  char json[96];
  snprintf(json, sizeof(json), "{\"flash_on\":%s,\"off_in_ms\":%lu}",
           flashOn ? "true" : "false",
           flashUntilMs ? (unsigned long)(flashUntilMs - millis()) : 0UL);
  return sendJson(req, json);
#else
  sendCors(req);
  httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "Flash LED disabled or absent on this board");
  return ESP_FAIL;
#endif
}

// MJPEG stream: multipart/x-mixed-replace, one JPEG per part.
static esp_err_t streamHandler(httpd_req_t* req) {
  char query[128];
  if (!queryString(req, query, sizeof(query))) return rejectLongQuery(req);
  if (!authOk(req, query)) return ESP_FAIL;

  if (streamClients >= STREAM_MAX_CLIENTS) {
    // Refused rather than admitted: two viewers sharing two frame buffers
    // halve each other's frame rate and starve /capture as well.
    // httpd_err_code_t has no 503, so the status line is set by hand.
    sendCors(req);
    httpd_resp_set_status(req, "503 Service Unavailable");
    httpd_resp_set_type(req, "text/plain");
    httpd_resp_sendstr(req, "Camera already has a viewer");
    return ESP_FAIL;
  }
  streamClients++;

  sendCors(req);
  esp_err_t res = httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=frame");
  if (res != ESP_OK) {
    streamClients--;
    return res;
  }

  const unsigned long minFrameMs = 1000UL / (STREAM_TARGET_FPS > 0 ? STREAM_TARGET_FPS : 1);
  char partHeader[96];

  while (true) {
    const unsigned long frameStart = millis();

    camera_fb_t* fb = esp_camera_fb_get();
    if (fb == nullptr) {
      captureErrors++;
      break;   // fall through to the clean termination below
    }

    if (fb->format == PIXFORMAT_JPEG) {
      const int headerLen = snprintf(
          partHeader, sizeof(partHeader),
          "\r\n--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
          (unsigned)fb->len);
      res = httpd_resp_send_chunk(req, partHeader, headerLen);
      if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len);
      if (res == ESP_OK) framesStreamed++;

#if ENABLE_MOTION
      // The stream already paid for this frame, so folding it into the motion
      // detector costs one decode instead of a second frame grab.
      if (frameStart - lastMotionSampleMs >= MOTION_INTERVAL_MS) {
        lastMotionSampleMs = frameStart;
        motion.sample(fb, frameStart, MOTION_CELL_DELTA, MOTION_MIN_PERCENT);
      }
#endif
    } else {
      res = ESP_FAIL;   // never configured; treat as a fault rather than send garbage
    }

    esp_camera_fb_return(fb);

    if (res != ESP_OK) break;   // viewer closed the tab, or the send failed

    const unsigned long spent = millis() - frameStart;
    if (spent < minFrameMs) delay(minFrameMs - spent);
  }

  // Always close the multipart body. A stream that just stops leaves some
  // clients waiting on a part that never arrives.
  httpd_resp_send_chunk(req, nullptr, 0);
  streamClients--;
  return ESP_OK;
}

// Info page. Sent as chunks from string literals rather than snprintf'd into
// one buffer — the old version did the latter and was one hostname away from
// silently truncating the page.
static esp_err_t indexHandler(httpd_req_t* req) {
  char query[128];
  if (!queryString(req, query, sizeof(query))) return rejectLongQuery(req);
  if (!authOk(req, query)) return ESP_FAIL;

  sendCors(req);
  httpd_resp_set_type(req, "text/html");

  httpd_resp_sendstr_chunk(req,
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>robot eye</title><style>"
    "body{font-family:system-ui,sans-serif;background:#111;color:#eee;text-align:center;margin:0;padding:16px}"
    "img{max-width:100%;border-radius:8px;background:#000}"
    "a,button{color:#8cf;background:#222;border:1px solid #345;border-radius:6px;"
    "padding:6px 10px;margin:3px;display:inline-block;text-decoration:none;font-size:14px;cursor:pointer}"
    "pre{text-align:left;background:#181818;padding:10px;border-radius:6px;overflow-x:auto;font-size:12px}"
    "</style></head><body>");

  char line[192];
  snprintf(line, sizeof(line), "<h2>robot eye &mdash; %s</h2>", CAMERA_ID);
  httpd_resp_sendstr_chunk(req, line);

  snprintf(line, sizeof(line), "<img id=\"v\" src=\"http://%s:%d/stream\" alt=\"live view\">",
           WiFi.localIP().toString().c_str(), STREAM_PORT);
  httpd_resp_sendstr_chunk(req, line);

  httpd_resp_sendstr_chunk(req,
    "<p><a href=\"/capture\">/capture</a>"
    "<a href=\"/status\">/status</a>"
    "<a href=\"/motion\">/motion</a>"
    "<a href=\"/settings\">/settings</a>"
    "<a href=\"/flash?on=1&ms=400\">flash</a>"
    "<a href=\"/settings?vflip=1\">flip</a>"
    "<a href=\"/settings?hmirror=1\">mirror</a></p>"
    "<pre id=\"s\">loading status...</pre>"
    "<script>"
    "async function t(){try{const r=await fetch('/status');"
    "document.getElementById('s').textContent=JSON.stringify(await r.json(),null,1);}"
    "catch(e){document.getElementById('s').textContent='status unreachable';}}"
    "t();setInterval(t,3000);"
    "</script></body></html>");

  return httpd_resp_sendstr_chunk(req, nullptr);
}

/* ───────────────────────────── servers ───────────────────────────── */

static void startServers() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = HTTP_PORT;
  config.ctrl_port = 32768;
  config.max_uri_handlers = 8;
  // Reclaim the least-recently-used socket instead of refusing new requests:
  // a browser tab that died without closing its socket used to be able to
  // wedge the server until a reboot.
  config.lru_purge_enable = true;

  httpd_uri_t indexUri    = {.uri = "/",         .method = HTTP_GET, .handler = indexHandler,    .user_ctx = nullptr};
  httpd_uri_t captureUri  = {.uri = "/capture",  .method = HTTP_GET, .handler = captureHandler,  .user_ctx = nullptr};
  httpd_uri_t statusUri   = {.uri = "/status",   .method = HTTP_GET, .handler = statusHandler,   .user_ctx = nullptr};
  httpd_uri_t motionUri   = {.uri = "/motion",   .method = HTTP_GET, .handler = motionHandler,   .user_ctx = nullptr};
  httpd_uri_t settingsUri = {.uri = "/settings", .method = HTTP_GET, .handler = settingsHandler, .user_ctx = nullptr};
  httpd_uri_t flashUri    = {.uri = "/flash",    .method = HTTP_GET, .handler = flashHandler,    .user_ctx = nullptr};

  if (httpd_start(&controlServer, &config) == ESP_OK) {
    httpd_register_uri_handler(controlServer, &indexUri);
    httpd_register_uri_handler(controlServer, &captureUri);
    httpd_register_uri_handler(controlServer, &statusUri);
    httpd_register_uri_handler(controlServer, &motionUri);
    httpd_register_uri_handler(controlServer, &settingsUri);
    httpd_register_uri_handler(controlServer, &flashUri);
    Serial.printf("Control server on port %d\n", HTTP_PORT);
  } else {
    Serial.println("Control server failed to start");
  }

  // Separate httpd instance for the stream: its handler holds its worker
  // thread for as long as someone is watching, so it must not be the same
  // thread pool the brain's /capture needs.
  httpd_config_t streamConfig = HTTPD_DEFAULT_CONFIG();
  streamConfig.server_port = STREAM_PORT;
  streamConfig.ctrl_port = 32769;
  streamConfig.max_uri_handlers = 2;
  streamConfig.max_open_sockets = 2;
  streamConfig.lru_purge_enable = true;

  httpd_uri_t streamUri = {.uri = "/stream", .method = HTTP_GET, .handler = streamHandler, .user_ctx = nullptr};

  if (httpd_start(&streamServer, &streamConfig) == ESP_OK) {
    httpd_register_uri_handler(streamServer, &streamUri);
    Serial.printf("Stream server on port %d\n", STREAM_PORT);
  } else {
    Serial.println("Stream server failed to start");
  }
}

/* ───────────────────────────── wifi ───────────────────────────── */

static void announceMdns() {
  if (mdnsUp) MDNS.end();
  mdnsUp = MDNS.begin(MDNS_NAME);
  if (mdnsUp) {
    MDNS.addService("http", "tcp", HTTP_PORT);
    Serial.printf("mDNS: http://%s.local\n", MDNS_NAME);
  } else {
    Serial.println("mDNS start failed (camera still reachable by IP)");
  }
}

// Bounded attempt. Returns whether we got on. Never loops forever: a camera
// that spins in setup() with no WiFi is a camera with no serial output and no
// way to tell whether it is even alive.
static bool wifiConnect(unsigned long timeoutMs) {
  Serial.printf("Connecting to WiFi \"%s\"", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false);              // keeps frame latency low
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  const unsigned long deadline = millis() + timeoutMs;
  bool led = false;
  while (WiFi.status() != WL_CONNECTED && millis() < deadline) {
    led = !led;
    statusLed(led);
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() != WL_CONNECTED) {
    statusLed(false);
    Serial.printf("WiFi not connected after %lus — will keep retrying in the background.\n",
                  timeoutMs / 1000);
    return false;
  }

  Serial.printf("WiFi connected, IP: %s (RSSI %d)\n",
                WiFi.localIP().toString().c_str(), (int)WiFi.RSSI());
  statusLed(true);
  return true;
}

// Called from loop(). Reconnects with a doubling backoff and re-announces
// mDNS, which the old firmware forgot — after a router reboot the name went
// away and only the raw IP still worked.
//
// Nothing here blocks. An earlier version sat in an 8-second wait for the
// association to come up, which also held up everything else loop() is
// responsible for: a timed flash pulse could overstay by those 8 seconds, and
// the motion sampler skipped its cadence entirely. Instead the attempt is
// kicked off and the next pass through loop() reads the result.
static void wifiMaintain() {
  const unsigned long now = millis();

  if (WiFi.status() == WL_CONNECTED) {
    if (wifiWasDown) {
      Serial.printf("WiFi back, IP: %s\n", WiFi.localIP().toString().c_str());
      statusLed(true);
      announceMdns();
      // The room has had time to change while we were off the air, so the
      // stored reference frame is stale — comparing against it would report
      // a person walking in when nothing of the sort happened.
      motion.resetBaseline();
      wifiWasDown = false;
      wifiRetryAtMs = 0;
      wifiBackoffMs = 2000;
    }
    return;
  }

  if (!wifiWasDown) {
    wifiWasDown = true;
    wifiDrops++;
    Serial.println("WiFi dropped.");
    statusLed(false);
    wifiRetryAtMs = now;      // first retry immediately
  }

  if (now < wifiRetryAtMs) return;

  Serial.printf("Reconnecting (next attempt in %lus if this one fails)...\n",
                wifiBackoffMs / 1000);
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  wifiRetryAtMs = now + wifiBackoffMs;
  wifiBackoffMs = wifiBackoffMs < 60000 ? wifiBackoffMs * 2 : 60000;
}

/* ───────────────────────────── setup / loop ───────────────────────────── */

void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(false);
  Serial.printf("\n== robot_eye starting (%s, reset: %s) ==\n",
                CAMERA_BOARD_NAME, resetReasonName());

#if STATUS_LED_GPIO_NUM >= 0
  pinMode(STATUS_LED_GPIO_NUM, OUTPUT);
#endif
#if ENABLE_FLASH_LED && FLASH_LED_GPIO_NUM >= 0
  pinMode(FLASH_LED_GPIO_NUM, OUTPUT);
  digitalWrite(FLASH_LED_GPIO_NUM, LOW);
#endif
  statusLed(false);

  if (!psramFound()) {
    Serial.println("WARNING: no PSRAM found. Resolution is capped at VGA and");
    Serial.println("         the stream will be slow. Enable Tools > PSRAM,");
    Serial.println("         and check this is really a PSRAM-equipped board.");
  }

  if (!initCamera()) {
    // Out of retries. Blinking forever tells you nothing and recovers from
    // nothing, so make the failure visible for a few seconds and then reboot:
    // the usual cause is a marginal 5V rail, and a reboot often clears it.
    Serial.println("Camera would not start. Rebooting in 5s.");
    Serial.println("Check: 5V supply able to give ~500mA, and the ribbon seated.");
    for (int i = 0; i < 25; i++) {
      statusLed(true);
      delay(100);
      statusLed(false);
      delay(100);
    }
    ESP.restart();
  }

  wifiConnect(30000);

  if (WiFi.status() == WL_CONNECTED) announceMdns();
  startServers();

  if (WiFi.status() == WL_CONNECTED) {
    const String ip = WiFi.localIP().toString();
    Serial.printf("Live view:  http://%s:%d/\n", ip.c_str(), HTTP_PORT);
    Serial.printf("Brain pulls frames from:  http://%s:%d/capture\n", ip.c_str(), HTTP_PORT);
    Serial.printf("Presence check (cheap):   http://%s:%d/motion\n", ip.c_str(), HTTP_PORT);
    Serial.printf("Register it with the brain:  add device %s at %s as camera\n",
                  CAMERA_ID, ip.c_str());
  }
  if (tokenRequired()) Serial.println("Access token is set: every endpoint needs ?token=...");
}

void loop() {
  const unsigned long now = millis();

  wifiMaintain();

  // Expire a timed flash pulse.
  if (flashOn && flashUntilMs != 0 && now >= flashUntilMs) {
    setFlash(false);
    flashUntilMs = 0;
  }

#if ENABLE_MOTION
  // Sample for motion when nothing else is using the camera. An active stream
  // feeds the detector from its own frames, so this only does the work when
  // nobody is watching.
  motion.tick(now, MOTION_HOLD_MS);
  if (streamClients == 0 && !captureBusy &&
      now - lastMotionSampleMs >= MOTION_INTERVAL_MS) {
    lastMotionSampleMs = now;
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb != nullptr) {
      motion.sample(fb, now, MOTION_CELL_DELTA, MOTION_MIN_PERCENT);
      esp_camera_fb_return(fb);
    }
  }
#endif

  // Yield to the WiFi and httpd tasks. Short enough that a flash pulse ends
  // on time and motion keeps its cadence.
  delay(20);
}
