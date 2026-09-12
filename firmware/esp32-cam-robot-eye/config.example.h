// Copy this file to config.h and fill in your own values.
// config.h is gitignored so your WiFi password never gets committed.
#pragma once

// --- WiFi ---
#define WIFI_SSID "your-wifi-name"
#define WIFI_PASSWORD "your-wifi-password"

// --- Camera identity ---
// A short name for this camera, in case the robot grows more than one eye.
#define CAMERA_ID "front-eye"

// mDNS hostname: the brain can reach the camera at http://robot-eye.local
// instead of a hardcoded IP address.
#define MDNS_NAME "robot-eye"

// --- Ports ---
// Control server: /, /capture, /status, /motion, /settings, /flash
#define HTTP_PORT 80
// MJPEG live stream runs on its own port so a viewer watching /stream
// can never block the brain from grabbing a /capture frame.
#define STREAM_PORT 81

// --- Access token (optional) ---
// Leave empty for no check. Set it and every endpoint requires the token,
// as ?token=... or an X-Auth-Token header. This is a lock on the LAN, not
// real internet-facing security: keep the camera off the public internet.
// The same value goes in the brain's device registration.
#define ACCESS_TOKEN ""

// --- Flash LED ---
// Set to 1 to allow the /flash endpoint to switch on the white flash LED
// (very bright, useful in the dark, eats power). Note: on the AI-Thinker
// board this LED sits on GPIO 4, which is also the SD card's DATA1 line —
// harmless while the SD card is unused, which it is here.
#define ENABLE_FLASH_LED 1
// A /flash request with no ms= turns the LED on until told otherwise. Any
// pulse is capped to this, so a dropped "off" can never cook the LED.
#define FLASH_MAX_MS 5000

// --- Image defaults ---
// SVGA (800x600) is a good balance: enough face detail for recognition to
// work at arm's length, small enough (~40 KB) to stream smoothly.
// Options: FRAMESIZE_QVGA, VGA, SVGA, XGA, SXGA, UXGA.
#define DEFAULT_FRAMESIZE FRAMESIZE_SVGA
// 0-63, lower means better quality and bigger frames. 10-14 is the sweet spot.
#define DEFAULT_JPEG_QUALITY 12
// If the camera ends up mounted upside down or mirrored on the robot's head,
// flip it here — or live, without reflashing, via /settings?vflip=1&hmirror=1.
#define DEFAULT_VFLIP 0
#define DEFAULT_HMIRROR 0

// --- Motion / presence detection ---
// The camera watches for change on its own so the brain does not have to
// poll frames just to learn whether anybody is there. See motion.h.
#define ENABLE_MOTION 1
// How often a frame is sampled for the motion check. Every sample costs one
// JPEG decode at 1/8 scale (roughly 40 ms at SVGA), so this is the main
// idle CPU cost of the firmware.
#define MOTION_INTERVAL_MS 400
// A cell counts as changed when its brightness moves by more than this
// (0-255). Raise it if a flickering lamp or sensor noise triggers motion.
#define MOTION_CELL_DELTA 18
// Fraction of the grid (as a percentage) that must change to call it motion.
// About 2% is a hand entering the frame; 6% is a person walking in.
#define MOTION_MIN_PERCENT 2
// How long motion stays "recent" after the last change, so the brain asking
// half a second later still hears about it.
#define MOTION_HOLD_MS 3000

// --- Stream ---
// One viewer at a time. A second one is refused rather than left to fight
// over the two frame buffers and halve everyone's frame rate.
#define STREAM_MAX_CLIENTS 1
// Upper bound on stream frame rate. The sensor will not always reach it.
#define STREAM_TARGET_FPS 12
