/*
 * ============================================================================
 *  IRIS NODE VOICE  —  an I2S microphone and speaker on the robot
 * ============================================================================
 *
 *  WHAT HAPPENS WHEN YOU SPEAK
 *    1. the mic is read continuously into a tiny ring buffer
 *    2. when the level rises, an HTTP POST is opened and the buffered audio
 *       (including a moment from BEFORE you started, so the first syllable is
 *       not clipped) begins uploading while you are still talking
 *    3. when you stop, the upload closes
 *    4. IRIS transcribes, thinks, speaks, and answers with a WAV
 *    5. the WAV streams straight to the speaker as it arrives
 *
 *  WHY THE DETECTION IS DONE HERE
 *  Streaming the microphone to the cloud all day would cost bandwidth, cost
 *  privacy, and cost money. So the board decides locally whether anyone is
 *  actually speaking and only then opens a connection. That is a level
 *  threshold, not speech recognition — it will trigger on a door slam, and
 *  IRIS simply finds nothing to transcribe.
 *
 *  WHY THE REPLY IS A WAV
 *  An ESP32 has no mp3 decoder. IRIS answers with plain PCM in a WAV
 *  container, and the sample rate is read out of the header — so whatever rate
 *  the server's voice happens to use is played correctly with no resampling
 *  at either end.
 *
 *  ── WIRING ─────────────────────────────────────────────────────────────────
 *   Microphone — INMP441 / ICS-43434 (I2S, NOT the analog KY-038)
 *     VDD 3.3V · GND · SD -> MIC_DATA · WS -> MIC_WS · SCK -> MIC_SCK
 *     L/R -> GND (selects the left channel, which is what is read here)
 *
 *   Speaker — MAX98357A (I2S amplifier, 3W)
 *     VIN 5V · GND · DIN -> AMP_DATA · BCLK -> AMP_BCLK · LRC -> AMP_LRC
 *     SD  -> leave floating (unmuted) · speaker to the + / - screw terminals
 *
 *  A 3W amplifier driving a real speaker is the largest current draw on the
 *  board. Powering it from the same weak USB supply as everything else is the
 *  usual cause of "it reboots whenever it talks".
 * ============================================================================
 */
#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <driver/i2s.h>

/* ── capture format ── */
#define VOICE_RATE            16000    /* what speech recognition wants      */
#define VOICE_FRAME_SAMPLES     256    /* 16 ms per frame                    */
#define VOICE_PREROLL_FRAMES      6    /* ~96 ms kept before the trigger     */

/* ── how "someone is speaking" is decided ── */
#define VOICE_START_LEVEL      1400    /* mean |sample| that opens a capture */
#define VOICE_STOP_LEVEL        700    /* below this counts as silence       */
#define VOICE_START_FRAMES        2    /* consecutive loud frames to start   */
#define VOICE_SILENCE_MS        800    /* quiet for this long ends the phrase*/
#define VOICE_MIN_MS            350    /* shorter than this is a noise, not a word */
#define VOICE_MAX_MS          10000    /* hard ceiling on one utterance      */
#define VOICE_COOLDOWN_MS      1200    /* after a reply, ignore its own echo */

/* ── playback ── */
#define VOICE_PLAY_CHUNK        512    /* bytes per i2s_write                */
#define VOICE_HTTP_TIMEOUT_MS 20000UL  /* the brain has to think, then speak */

struct VoiceConfig {
  /* microphone (I2S port 0, receive) */
  int micSck = -1;
  int micWs  = -1;
  int micData = -1;
  /* speaker amplifier (I2S port 1, transmit) */
  int ampBclk = -1;
  int ampLrc  = -1;
  int ampData = -1;
  /* optional push-to-talk button to ground; -1 to rely on level detection */
  int buttonPin = -1;
  /* INMP441 gives 24-bit samples left-justified in 32-bit slots. Taking the
   * top 16 bits is the true value; most electret I2S mics then want a little
   * software gain. Raise if IRIS mishears you, lower if it clips. */
  uint8_t gain = 4;
  /* where to send it */
  String host;
  uint16_t port = 443;
  bool tls = true;
  bool tlsVerify = false;
  String path = "/api/v1/nodes/voice";
  String token;
  String node = "face";
};

typedef void (*VoiceTickCallback)();
typedef void (*VoiceSpeakingCallback)(uint32_t ms);

class NodeVoice {
 public:
  bool begin(const VoiceConfig& cfg) {
    cfg_ = cfg;
    if (cfg_.buttonPin >= 0) pinMode(cfg_.buttonPin, INPUT_PULLUP);

    micOk_ = cfg_.micSck >= 0 && cfg_.micWs >= 0 && cfg_.micData >= 0 && startMic();
    ampOk_ = cfg_.ampBclk >= 0 && cfg_.ampLrc >= 0 && cfg_.ampData >= 0 && startAmp();

    if (micOk_ && cfg_.host.length() == 0) {
      Serial.println("[voice] a microphone is wired but CLOUD_HOST is empty —");
      Serial.println("        set it so speech has somewhere to go.");
    }
    return micOk_;
  }

  bool micReady() const { return micOk_; }
  bool speakerReady() const { return ampOk_; }
  bool capturing() const { return state_ == CAPTURING; }
  bool busy() const { return state_ != IDLE; }
  const String& lastHeard() const { return heard_; }
  const String& lastReply() const { return reply_; }
  uint32_t exchanges() const { return exchanges_; }
  uint32_t failures() const { return failures_; }

  void onTick(VoiceTickCallback cb) { tickCb_ = cb; }
  void onSpeaking(VoiceSpeakingCallback cb) { speakingCb_ = cb; }

  /* Call every loop. Never blocks for long except while a reply is playing,
   * and the tick callback is pumped throughout so the eyes keep moving. */
  void loop(uint32_t now) {
    if (!micOk_ || cfg_.host.length() == 0 || cfg_.token.length() == 0) return;
    if (WiFi.status() != WL_CONNECTED) return;
    if ((uint32_t)(now - quietUntil_) > (uint32_t)0x7FFFFFFF) return;  /* cooldown */

    static int16_t frame[VOICE_FRAME_SAMPLES];
    if (!readFrame(frame)) return;
    const uint32_t level = meanLevel(frame, VOICE_FRAME_SAMPLES);

    if (state_ == IDLE) {
      pushPreroll(frame);
      const bool pressed = cfg_.buttonPin >= 0 && digitalRead(cfg_.buttonPin) == LOW;
      if (pressed) { loudFrames_ = VOICE_START_FRAMES; }
      else if (level >= VOICE_START_LEVEL) { loudFrames_++; }
      else { loudFrames_ = 0; }

      if (loudFrames_ >= VOICE_START_FRAMES) {
        loudFrames_ = 0;
        beginExchange(now);
      }
      return;
    }

    /* CAPTURING */
    if (!writeChunk((const uint8_t*)frame, sizeof(frame))) {
      Serial.println("[voice] upload broke mid-phrase");
      abortExchange(now);
      return;
    }
    sentBytes_ += sizeof(frame);

    if (level < VOICE_STOP_LEVEL) {
      if (silenceStart_ == 0) silenceStart_ = now ? now : 1;
    } else {
      silenceStart_ = 0;
    }

    const uint32_t spoken = (uint32_t)(now - captureStart_);
    const bool quietLongEnough =
        silenceStart_ != 0 && (uint32_t)(now - silenceStart_) >= VOICE_SILENCE_MS;
    if (quietLongEnough || spoken >= VOICE_MAX_MS) {
      if (spoken < VOICE_MIN_MS) {         /* a cough, not a sentence */
        abortExchange(now);
        return;
      }
      finishExchange(now);
    }
  }

 private:
  enum State { IDLE, CAPTURING };

  /* ---------------------------------------------------------------- I2S ---*/

  bool startMic() {
    i2s_config_t config = {};
    config.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
    config.sample_rate = VOICE_RATE;
    config.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
    config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
    config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    config.dma_buf_count = 6;
    config.dma_buf_len = VOICE_FRAME_SAMPLES;
    config.use_apll = false;
    config.tx_desc_auto_clear = false;

    if (i2s_driver_install(I2S_NUM_0, &config, 0, NULL) != ESP_OK) {
      Serial.println("[voice] could not start the microphone I2S port");
      return false;
    }
    i2s_pin_config_t pins = {};
    pins.mck_io_num = I2S_PIN_NO_CHANGE;
    pins.bck_io_num = cfg_.micSck;
    pins.ws_io_num = cfg_.micWs;
    pins.data_out_num = I2S_PIN_NO_CHANGE;
    pins.data_in_num = cfg_.micData;
    if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK) {
      Serial.println("[voice] bad microphone pins");
      i2s_driver_uninstall(I2S_NUM_0);
      return false;
    }
    i2s_zero_dma_buffer(I2S_NUM_0);
    return true;
  }

  bool startAmp() {
    i2s_config_t config = {};
    config.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX);
    config.sample_rate = VOICE_RATE;          /* re-set per reply from the WAV */
    config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
    config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
    config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    config.dma_buf_count = 8;
    config.dma_buf_len = 256;
    config.use_apll = false;
    config.tx_desc_auto_clear = true;         /* silence, not a buzz, on underrun */

    if (i2s_driver_install(I2S_NUM_1, &config, 0, NULL) != ESP_OK) {
      Serial.println("[voice] could not start the speaker I2S port");
      return false;
    }
    i2s_pin_config_t pins = {};
    pins.mck_io_num = I2S_PIN_NO_CHANGE;
    pins.bck_io_num = cfg_.ampBclk;
    pins.ws_io_num = cfg_.ampLrc;
    pins.data_out_num = cfg_.ampData;
    pins.data_in_num = I2S_PIN_NO_CHANGE;
    if (i2s_set_pin(I2S_NUM_1, &pins) != ESP_OK) {
      Serial.println("[voice] bad speaker pins");
      i2s_driver_uninstall(I2S_NUM_1);
      return false;
    }
    i2s_zero_dma_buffer(I2S_NUM_1);
    return true;
  }

  /* One frame of 16-bit mono, converted from the mic's 32-bit slots. */
  bool readFrame(int16_t* out) {
    static int32_t raw[VOICE_FRAME_SAMPLES];
    size_t got = 0;
    if (i2s_read(I2S_NUM_0, raw, sizeof(raw), &got, 0) != ESP_OK) return false;
    const size_t samples = got / sizeof(int32_t);
    if (samples < VOICE_FRAME_SAMPLES) return false;
    for (size_t i = 0; i < VOICE_FRAME_SAMPLES; i++) {
      /* Bits 31..16 are the top 16 of the mic's 24-bit sample. */
      long value = (long)(raw[i] >> 16) * (long)cfg_.gain;
      if (value > 32767) value = 32767;
      if (value < -32768) value = -32768;
      out[i] = (int16_t)value;
    }
    return true;
  }

  static uint32_t meanLevel(const int16_t* samples, size_t count) {
    uint32_t total = 0;
    for (size_t i = 0; i < count; i++) {
      total += (uint32_t)abs((int)samples[i]);
    }
    return count ? (total / count) : 0;
  }

  void pushPreroll(const int16_t* frame) {
    memcpy(preroll_[prerollAt_], frame, sizeof(preroll_[0]));
    prerollAt_ = (prerollAt_ + 1) % VOICE_PREROLL_FRAMES;
    if (prerollFilled_ < VOICE_PREROLL_FRAMES) prerollFilled_++;
  }

  /* ---------------------------------------------------------- the exchange -*/

  void beginExchange(uint32_t now) {
    if (!openUpload()) {
      failures_++;
      quietUntil_ = now + VOICE_COOLDOWN_MS;
      return;
    }
    state_ = CAPTURING;
    captureStart_ = now ? now : 1;
    silenceStart_ = 0;
    sentBytes_ = 0;
    Serial.println("[voice] listening...");

    /* The moment before the trigger, so the first syllable survives. */
    for (uint8_t i = 0; i < prerollFilled_; i++) {
      const uint8_t index = (prerollAt_ + i) % VOICE_PREROLL_FRAMES;
      if (!writeChunk((const uint8_t*)preroll_[index], sizeof(preroll_[0]))) break;
      sentBytes_ += sizeof(preroll_[0]);
    }
    prerollFilled_ = 0;
  }

  void abortExchange(uint32_t now) {
    closeUpload();
    state_ = IDLE;
    silenceStart_ = 0;
    quietUntil_ = now + VOICE_COOLDOWN_MS;
  }

  void finishExchange(uint32_t now) {
    /* Terminating chunk, then the reply. */
    client_->print("0\r\n\r\n");
    state_ = IDLE;
    silenceStart_ = 0;

    const bool played = readAndPlayReply();
    closeUpload();
    if (played) exchanges_++;
    else failures_++;

    /* Ignore the tail of our own speech, and give the mic's DMA a moment to
     * refill with room audio rather than the reply that just played. */
    i2s_zero_dma_buffer(I2S_NUM_0);
    quietUntil_ = millis() + VOICE_COOLDOWN_MS;
  }

  /* ------------------------------------------------------------ HTTP ------*/

  bool openUpload() {
    if (cfg_.tls) {
      WiFiClientSecure* secure = new WiFiClientSecure();
      /* Without verification a network attacker could impersonate the server
       * and collect the token. Fine on a LAN, a real (if unlikely) risk over
       * the internet — hence the boot warning and the tlsVerify option. */
      if (!cfg_.tlsVerify) secure->setInsecure();
      client_ = secure;
    } else {
      client_ = new WiFiClient();
    }
    client_->setTimeout(VOICE_HTTP_TIMEOUT_MS / 1000);
    if (!client_->connect(cfg_.host.c_str(), cfg_.port)) {
      Serial.println("[voice] could not reach IRIS");
      closeUpload();
      return false;
    }

    /* Chunked, because the length is unknown while still recording — which is
     * the whole point: uploading during the sentence rather than after it. */
    String head = "POST " + cfg_.path + "?token=" + cfg_.token +
                  "&node=" + cfg_.node + "&rate=" + String(VOICE_RATE) +
                  " HTTP/1.1\r\n";
    head += "Host: " + cfg_.host + "\r\n";
    head += "Content-Type: application/octet-stream\r\n";
    head += "Transfer-Encoding: chunked\r\n";
    head += "Connection: close\r\n\r\n";
    client_->print(head);
    return true;
  }

  bool writeChunk(const uint8_t* data, size_t length) {
    if (client_ == nullptr || !client_->connected()) return false;
    char header[12];
    snprintf(header, sizeof(header), "%X\r\n", (unsigned)length);
    if (client_->print(header) == 0) return false;
    if (client_->write(data, length) != length) return false;
    return client_->print("\r\n") == 2;
  }

  void closeUpload() {
    if (client_ != nullptr) {
      client_->stop();
      delete client_;
      client_ = nullptr;
    }
  }

  /* Read the response, then stream its PCM to the amplifier as it arrives. */
  bool readAndPlayReply() {
    if (client_ == nullptr) return false;

    const uint32_t deadline = millis() + VOICE_HTTP_TIMEOUT_MS;
    String status = readLine(deadline);
    if (status.length() == 0) {
      Serial.println("[voice] IRIS did not answer");
      return false;
    }
    const int code = statusCode(status);

    heard_ = ""; reply_ = "";
    long declaredLength = -1;
    bool chunked = false;
    while (true) {
      const String line = readLine(deadline);
      if (line.length() == 0) break;              /* blank line = end of headers */
      String lower = line;
      lower.toLowerCase();
      /* Arduino's String::trim() mutates in place and returns void. */
      if (lower.startsWith("x-iris-heard:")) {
        heard_ = line.substring(13);
        heard_.trim();
      } else if (lower.startsWith("x-iris-reply:")) {
        reply_ = line.substring(13);
        reply_.trim();
      }
      else if (lower.startsWith("content-length:")) declaredLength = line.substring(15).toInt();
      else if (lower.startsWith("transfer-encoding:") && lower.indexOf("chunked") >= 0) chunked = true;
    }

    if (code != 200) {
      Serial.printf("[voice] IRIS said %d: %s\n", code, readBody(deadline, 200).c_str());
      return false;
    }
    if (heard_.length()) Serial.println("[voice] heard: " + heard_);
    if (reply_.length()) Serial.println("[voice] reply: " + reply_);
    if (!ampOk_) {
      Serial.println("[voice] no speaker wired — reply not played");
      return true;
    }
    /* Chunked replies are not produced by this endpoint, and decoding them
     * would double the size of this function for a case that cannot happen. */
    if (chunked) {
      Serial.println("[voice] unexpected chunked reply — not played");
      return false;
    }
    return playWav(deadline, declaredLength);
  }

  /* Streaming WAV parse: find the format and the data chunk, then play. */
  bool playWav(uint32_t deadline, long declaredLength) {
    uint8_t header[12];
    if (!readExactly(header, sizeof(header), deadline)) return false;
    if (memcmp(header, "RIFF", 4) != 0 || memcmp(header + 8, "WAVE", 4) != 0) {
      Serial.println("[voice] the reply was not a WAV");
      return false;
    }

    uint32_t rate = VOICE_RATE;
    uint16_t channels = 1, bits = 16;
    long dataBytes = -1;

    /* Walk the chunk list. A WAV may carry LIST/fact chunks before the audio,
     * so assuming a flat 44-byte header would play metadata as sound. */
    for (int guard = 0; guard < 16; guard++) {
      uint8_t chunk[8];
      if (!readExactly(chunk, sizeof(chunk), deadline)) return false;
      const uint32_t size = (uint32_t)chunk[4] | ((uint32_t)chunk[5] << 8) |
                            ((uint32_t)chunk[6] << 16) | ((uint32_t)chunk[7] << 24);
      if (memcmp(chunk, "fmt ", 4) == 0) {
        uint8_t fmt[16];
        const uint32_t want = size < sizeof(fmt) ? size : sizeof(fmt);
        if (!readExactly(fmt, want, deadline)) return false;
        channels = (uint16_t)fmt[2] | ((uint16_t)fmt[3] << 8);
        rate = (uint32_t)fmt[4] | ((uint32_t)fmt[5] << 8) |
               ((uint32_t)fmt[6] << 16) | ((uint32_t)fmt[7] << 24);
        bits = (uint16_t)fmt[14] | ((uint16_t)fmt[15] << 8);
        if (!skipBytes(size - want, deadline)) return false;
      } else if (memcmp(chunk, "data", 4) == 0) {
        dataBytes = (long)size;
        break;
      } else {
        if (!skipBytes(size + (size & 1), deadline)) return false;   /* pad byte */
      }
    }
    if (dataBytes < 0) {
      Serial.println("[voice] the WAV had no audio in it");
      return false;
    }
    if (bits != 16) {
      Serial.printf("[voice] %u-bit audio is not supported (need 16)\n", bits);
      return false;
    }
    if (rate < 8000 || rate > 48000) rate = VOICE_RATE;

    /* Match the amplifier to the file rather than resampling at either end. */
    i2s_set_clk(I2S_NUM_1, rate,
                I2S_BITS_PER_SAMPLE_16BIT,
                channels >= 2 ? I2S_CHANNEL_STEREO : I2S_CHANNEL_MONO);

    const uint32_t ms = (uint32_t)((dataBytes * 1000L) / (long)(rate * 2 * channels));
    if (speakingCb_) speakingCb_(ms);
    Serial.printf("[voice] playing %ld bytes @ %u Hz (%.1fs)\n",
                  dataBytes, rate, ms / 1000.0);

    static uint8_t buffer[VOICE_PLAY_CHUNK];
    long remaining = dataBytes;
    while (remaining > 0 && millis() < deadline) {
      const size_t want = remaining < (long)sizeof(buffer) ? (size_t)remaining : sizeof(buffer);
      const int got = client_->read(buffer, want);
      if (got <= 0) {
        if (!client_->connected() && client_->available() == 0) break;
        pump();
        continue;
      }
      size_t written = 0;
      i2s_write(I2S_NUM_1, buffer, (size_t)got, &written, portMAX_DELAY);
      remaining -= got;
      pump();                       /* keep the eyes moving while it talks */
    }
    /* Let the DMA drain before muting, or the last word is clipped. */
    const uint32_t drain = millis() + 120;
    while (millis() < drain) pump();
    i2s_zero_dma_buffer(I2S_NUM_1);
    (void)declaredLength;
    return remaining <= 0;
  }

  void pump() {
    if (tickCb_) tickCb_();
  }

  String readLine(uint32_t deadline) {
    String line;
    while (millis() < deadline) {
      if (client_->available() == 0) {
        if (!client_->connected()) break;
        pump();
        continue;
      }
      const char c = (char)client_->read();
      if (c == '\n') break;
      if (c != '\r' && line.length() < 300) line += c;
      if (c == '\r') continue;
    }
    return line;
  }

  String readBody(uint32_t deadline, size_t limit) {
    String body;
    while (millis() < deadline && body.length() < limit) {
      if (client_->available() == 0) {
        if (!client_->connected()) break;
        pump();
        continue;
      }
      body += (char)client_->read();
    }
    return body;
  }

  bool readExactly(uint8_t* out, size_t length, uint32_t deadline) {
    size_t have = 0;
    while (have < length && millis() < deadline) {
      const int got = client_->read(out + have, length - have);
      if (got > 0) { have += got; continue; }
      if (!client_->connected() && client_->available() == 0) break;
      pump();
    }
    return have == length;
  }

  bool skipBytes(uint32_t count, uint32_t deadline) {
    uint8_t scratch[64];
    while (count > 0 && millis() < deadline) {
      const size_t want = count < sizeof(scratch) ? count : sizeof(scratch);
      if (!readExactly(scratch, want, deadline)) return false;
      count -= want;
    }
    return count == 0;
  }

  static int statusCode(const String& statusLine) {
    const int space = statusLine.indexOf(' ');
    if (space < 0) return 0;
    return statusLine.substring(space + 1, space + 4).toInt();
  }

  VoiceConfig cfg_{};
  bool micOk_ = false, ampOk_ = false;
  State state_ = IDLE;
  WiFiClient* client_ = nullptr;
  uint32_t captureStart_ = 0, silenceStart_ = 0, quietUntil_ = 0;
  uint32_t sentBytes_ = 0, exchanges_ = 0, failures_ = 0;
  uint8_t loudFrames_ = 0;
  int16_t preroll_[VOICE_PREROLL_FRAMES][VOICE_FRAME_SAMPLES];
  uint8_t prerollAt_ = 0, prerollFilled_ = 0;
  String heard_, reply_;
  VoiceTickCallback tickCb_ = nullptr;
  VoiceSpeakingCallback speakingCb_ = nullptr;
};
