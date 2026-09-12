/*
 * camera_pins.h — which physical pins the camera sensor is wired to.
 *
 * Pick your board by setting CAMERA_MODEL_* in config.h (or leave it alone
 * for the AI-Thinker ESP32-CAM, which is the default and by far the most
 * common). Everything else in the firmware is board-independent.
 *
 * The two ESP32-S3 entries are here for later: the S3 is the chip Espressif's
 * current vision work actually targets, so if this robot ever grows a
 * seeing-in-the-dark upgrade it will be one of those boards. See the README
 * for why that matters and why it changes nothing today.
 */
#pragma once

// Default when config.h names no board.
#if !defined(CAMERA_MODEL_AI_THINKER) && !defined(CAMERA_MODEL_ESP32S3_EYE) && \
    !defined(CAMERA_MODEL_XIAO_ESP32S3) && !defined(CAMERA_MODEL_WROVER_KIT) && \
    !defined(CAMERA_MODEL_ESP_EYE)
#define CAMERA_MODEL_AI_THINKER
#endif

#if defined(CAMERA_MODEL_AI_THINKER)
// AI-Thinker ESP32-CAM (plain ESP32-S module, OV2640, 4 MB PSRAM).
#define CAMERA_BOARD_NAME "ai-thinker-esp32-cam"
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22
// On-board white flash LED, and the red status LED next to the antenna.
#define FLASH_LED_GPIO_NUM 4
#define STATUS_LED_GPIO_NUM 33
#define STATUS_LED_ACTIVE_LOW 1

#elif defined(CAMERA_MODEL_ESP32S3_EYE)
// Espressif ESP32-S3-EYE devkit.
#define CAMERA_BOARD_NAME "esp32-s3-eye"
#define PWDN_GPIO_NUM -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 15
#define SIOD_GPIO_NUM 4
#define SIOC_GPIO_NUM 5
#define Y9_GPIO_NUM 16
#define Y8_GPIO_NUM 17
#define Y7_GPIO_NUM 18
#define Y6_GPIO_NUM 12
#define Y5_GPIO_NUM 10
#define Y4_GPIO_NUM 8
#define Y3_GPIO_NUM 9
#define Y2_GPIO_NUM 11
#define VSYNC_GPIO_NUM 6
#define HREF_GPIO_NUM 7
#define PCLK_GPIO_NUM 13
#define FLASH_LED_GPIO_NUM -1
#define STATUS_LED_GPIO_NUM -1
#define STATUS_LED_ACTIVE_LOW 0

#elif defined(CAMERA_MODEL_XIAO_ESP32S3)
// Seeed Studio XIAO ESP32S3 Sense.
#define CAMERA_BOARD_NAME "xiao-esp32s3-sense"
#define PWDN_GPIO_NUM -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 10
#define SIOD_GPIO_NUM 40
#define SIOC_GPIO_NUM 39
#define Y9_GPIO_NUM 48
#define Y8_GPIO_NUM 11
#define Y7_GPIO_NUM 12
#define Y6_GPIO_NUM 14
#define Y5_GPIO_NUM 16
#define Y4_GPIO_NUM 18
#define Y3_GPIO_NUM 17
#define Y2_GPIO_NUM 15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM 47
#define PCLK_GPIO_NUM 13
#define FLASH_LED_GPIO_NUM -1
#define STATUS_LED_GPIO_NUM 21
#define STATUS_LED_ACTIVE_LOW 1

#elif defined(CAMERA_MODEL_WROVER_KIT)
// ESP32 WROVER-KIT.
#define CAMERA_BOARD_NAME "wrover-kit"
#define PWDN_GPIO_NUM -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 21
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 19
#define Y4_GPIO_NUM 18
#define Y3_GPIO_NUM 5
#define Y2_GPIO_NUM 4
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22
#define FLASH_LED_GPIO_NUM -1
#define STATUS_LED_GPIO_NUM -1
#define STATUS_LED_ACTIVE_LOW 0

#elif defined(CAMERA_MODEL_ESP_EYE)
// Espressif ESP-EYE (original, plain ESP32).
#define CAMERA_BOARD_NAME "esp-eye"
#define PWDN_GPIO_NUM -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 4
#define SIOD_GPIO_NUM 18
#define SIOC_GPIO_NUM 23
#define Y9_GPIO_NUM 36
#define Y8_GPIO_NUM 37
#define Y7_GPIO_NUM 38
#define Y6_GPIO_NUM 39
#define Y5_GPIO_NUM 35
#define Y4_GPIO_NUM 14
#define Y3_GPIO_NUM 13
#define Y2_GPIO_NUM 34
#define VSYNC_GPIO_NUM 5
#define HREF_GPIO_NUM 27
#define PCLK_GPIO_NUM 25
#define FLASH_LED_GPIO_NUM 22
#define STATUS_LED_GPIO_NUM 21
#define STATUS_LED_ACTIVE_LOW 1

#else
#error "No camera board selected in camera_pins.h"
#endif
