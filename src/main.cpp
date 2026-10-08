#include <Arduino.h>
#include "driver/spi_master.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include <strings.h>

/*
 * Connections based on the DMHSM0012VGNA datasheet and ESP32 VSPI/SPI3.
 *
 * Display Pin  | Display Signal | ESP32 Pin | ESP32 Signal | Purpose
 * -------------|----------------|-----------|--------------|------------------------------
 * 13           | SPI_SCK/SCLK   | GPIO18    | SCLK         | Clock
 * 10           | IO0 / SDA0     | GPIO23    | DATA0/MOSI   | Register write + QSPI data 0
 * 8            | IO1 / SDA1     | GPIO19    | DATA1/MISO   | Register read + QSPI data 1
 * 14           | IO2 / SDA2     | GPIO22    | DATA2/WP     | QSPI data 2
 * 12           | IO3 / SDA3     | GPIO21    | DATA3/HD     | QSPI data 3
 * 11           | CS             | GPIO5     | CS           | Chip select
 *
 * GPIO21/GPIO22 are ordinary GPIO-matrix pins on ESP32-WROOM modules. Other
 * output-capable GPIOs can work for DATA2/DATA3, but avoid GPIO6-GPIO11 because
 * they are connected to the module's flash.
 */

#define PIN_NUM_DATA0 23
#define PIN_NUM_DATA1 19
#define PIN_NUM_DATA2 22
#define PIN_NUM_DATA3 21
#define PIN_NUM_CLK   18
#define PIN_NUM_CS     5
#define PIN_NUM_RST   17

#define CMD_WRITE_REG 0x78
#define CMD_READ_REG  0x79
#define CMD_SPI_DATA  0x02
#define CMD_QSPI_DATA 0x32

#define DISPLAY_WRITE         0x2C
#define DISPLAY_PARTIAL_WRITE 0x3C
#define DISPLAY_ROW_ADDRESS   0x2A

#define DISPLAY_WIDTH 640
#define DISPLAY_HEIGHT 480

#define PANEL_SPI_HOST VSPI_HOST
#define SPI_FREQUENCY 40e6

static const uint8_t DISPLAY_FORMAT_GRAY256 = 0x9A;
static spi_device_handle_t displaySpi = nullptr;
static uint8_t *rowBuffer = nullptr;

enum DisplayTransferMode {
  DISPLAY_MODE_SPI,
  DISPLAY_MODE_QSPI,
};

static DisplayTransferMode displayMode = DISPLAY_MODE_QSPI;

enum AnimationType {
  ANIMATION_STOPPED,
  ANIMATION_CHECKER,
  ANIMATION_BARS,
  ANIMATION_GRADIENT,
  ANIMATION_SQUARE,
  ANIMATION_JAY,
};

struct AnimationState {
  AnimationType type = ANIMATION_STOPPED;
  uint16_t fps = 10;
  uint16_t size = 32;
  uint8_t intensity = 1;
  int16_t speed = 4;
  bool vertical = false;
  uint32_t frame = 0;
  uint32_t startedAt = 0;
  uint32_t nextFrameAt = 0;
};

static AnimationState animation;

// The serial protocol is intentionally line-oriented so gui.py can wait for a
// single response per command:
//   R <reg_hex>                  -> <value_hex> or ERR ...
//   W <reg_hex> <value_hex>      -> OK or ERR ...
//   X                            -> OK
//   M [SPI|QSPI]                 -> SPI/QSPI when queried, OK when set
//   S <x> <y> <size> [intensity] [SPI|QSPI] -> OK or ERR ...
//   B [block_size] [brightness] [SPI|QSPI] -> OK or ERR ...
void printEspError(const char *operation, esp_err_t err) {
  if (err != ESP_OK) {
    Serial.printf("ERR %s: %s\n", operation, esp_err_to_name(err));
  }
}

const char *displayModeName(DisplayTransferMode mode) {
  return mode == DISPLAY_MODE_QSPI ? "QSPI" : "SPI";
}

bool parseDisplayModeToken(const char *token, DisplayTransferMode *mode) {
  if (strcasecmp(token, "QSPI") == 0 || strcasecmp(token, "Q") == 0 || strcmp(token, "4") == 0) {
    *mode = DISPLAY_MODE_QSPI;
    return true;
  }

  if (strcasecmp(token, "SPI") == 0 || strcasecmp(token, "S") == 0 || strcmp(token, "1") == 0) {
    *mode = DISPLAY_MODE_SPI;
    return true;
  }

  return false;
}

bool parseUnsignedToken(const char *token, unsigned int *value) {
  char *end = nullptr;
  unsigned long parsed = strtoul(token, &end, 0);
  if (end == token || *end != '\0') {
    return false;
  }

  *value = static_cast<unsigned int>(parsed);
  return true;
}

esp_err_t transferPanel(uint8_t command,
                        uint32_t address,
                        uint8_t addressBits,
                        const void *txBuffer,
                        size_t txBytes,
                        void *rxBuffer,
                        size_t rxBits,
                        uint32_t flags) {
  spi_transaction_ext_t trans = {};
  trans.base.flags = flags | SPI_TRANS_VARIABLE_CMD | SPI_TRANS_VARIABLE_ADDR;
  trans.command_bits = 8;
  trans.address_bits = addressBits;
  trans.base.cmd = command;
  trans.base.addr = address;
  trans.base.length = txBytes * 8;
  trans.base.tx_buffer = txBuffer;
  trans.base.rxlength = rxBits;
  trans.base.rx_buffer = rxBuffer;

  return spi_device_polling_transmit(displaySpi, reinterpret_cast<spi_transaction_t *>(&trans));
}

esp_err_t transferDataChunk(const void *txBuffer, size_t txBytes, DisplayTransferMode mode) {
  spi_transaction_ext_t trans = {};
  trans.base.flags = SPI_TRANS_VARIABLE_CMD | SPI_TRANS_VARIABLE_ADDR;
  if (mode == DISPLAY_MODE_QSPI) {
    trans.base.flags |= SPI_TRANS_MODE_QIO;
  }
  trans.command_bits = 0;
  trans.address_bits = 0;
  trans.base.length = txBytes * 8;
  trans.base.tx_buffer = txBuffer;

  return spi_device_polling_transmit(displaySpi, reinterpret_cast<spi_transaction_t *>(&trans));
}

esp_err_t beginDisplayData(uint8_t displayCommand, const void *txBuffer, size_t txBytes, DisplayTransferMode mode) {
  const uint8_t busCommand = mode == DISPLAY_MODE_QSPI ? CMD_QSPI_DATA : CMD_SPI_DATA;
  const uint32_t address = static_cast<uint32_t>(displayCommand) << 8;
  const uint32_t flags = mode == DISPLAY_MODE_QSPI ? SPI_TRANS_MODE_QIO : 0;

  return transferPanel(busCommand, address, 24, txBuffer, txBytes, nullptr, 0, flags);
}

bool initDisplaySpi() {
  spi_bus_config_t busConfig = {};
  busConfig.mosi_io_num = PIN_NUM_DATA0;
  busConfig.miso_io_num = PIN_NUM_DATA1;
  busConfig.sclk_io_num = PIN_NUM_CLK;
  busConfig.quadwp_io_num = PIN_NUM_DATA2;
  busConfig.quadhd_io_num = PIN_NUM_DATA3;
  busConfig.max_transfer_sz = DISPLAY_WIDTH;
  busConfig.flags = SPICOMMON_BUSFLAG_MASTER | SPICOMMON_BUSFLAG_QUAD;

  esp_err_t err = spi_bus_initialize(PANEL_SPI_HOST, &busConfig, SPI_DMA_CH_AUTO);
  if (err != ESP_OK) {
    printEspError("spi_bus_initialize", err);
    return false;
  }

  spi_device_interface_config_t deviceConfig = {};
  deviceConfig.mode = 0;
  deviceConfig.clock_speed_hz = SPI_FREQUENCY;
  deviceConfig.spics_io_num = -1;
  deviceConfig.flags = SPI_DEVICE_HALFDUPLEX;
  deviceConfig.queue_size = 1;

  err = spi_bus_add_device(PANEL_SPI_HOST, &deviceConfig, &displaySpi);
  if (err != ESP_OK) {
    printEspError("spi_bus_add_device", err);
    return false;
  }

  rowBuffer = static_cast<uint8_t *>(heap_caps_malloc(DISPLAY_WIDTH, MALLOC_CAP_DMA));
  if (rowBuffer == nullptr) {
    Serial.println("ERR row buffer allocation failed");
    return false;
  }

  return true;
}

esp_err_t writeRegister(uint8_t reg_addr, uint8_t value) {
  digitalWrite(PIN_NUM_CS, LOW);
  esp_err_t err = transferPanel(CMD_WRITE_REG, (reg_addr << 8) | value, 16, nullptr, 0, nullptr, 0, 0);
  digitalWrite(PIN_NUM_CS, HIGH);
  return err;
}

esp_err_t readRegister(uint8_t reg_addr, uint8_t *result) {
  spi_transaction_ext_t trans = {};
  trans.base.flags = SPI_TRANS_VARIABLE_CMD | SPI_TRANS_VARIABLE_ADDR | SPI_TRANS_USE_RXDATA;
  trans.command_bits = 8;
  trans.address_bits = 8;
  trans.base.cmd = CMD_READ_REG;
  trans.base.addr = reg_addr;
  trans.base.rxlength = 8;

  digitalWrite(PIN_NUM_CS, LOW);
  esp_err_t err = spi_device_polling_transmit(displaySpi, reinterpret_cast<spi_transaction_t *>(&trans));
  digitalWrite(PIN_NUM_CS, HIGH);

  if (err == ESP_OK) {
    *result = trans.base.rx_data[0];
  }
  return err;
}

esp_err_t ensureGray256Mode() {
  esp_err_t err = writeRegister(0x00, DISPLAY_FORMAT_GRAY256);
  if (err != ESP_OK) return err;
  err = writeRegister(0x04, 0x00);
  if (err != ESP_OK) return err;
  return writeRegister(0x1B, 0x00);
}

esp_err_t writeRowAddressWindow(uint16_t row_start, uint16_t row_end) {
  uint8_t addressBytes[4] = {
    static_cast<uint8_t>((row_start >> 8) & 0xFF),
    static_cast<uint8_t>(row_start & 0xFF),
    static_cast<uint8_t>((row_end >> 8) & 0xFF),
    static_cast<uint8_t>(row_end & 0xFF),
  };

  digitalWrite(PIN_NUM_CS, LOW);
  esp_err_t err = beginDisplayData(DISPLAY_ROW_ADDRESS, addressBytes, sizeof(addressBytes), DISPLAY_MODE_SPI);
  digitalWrite(PIN_NUM_CS, HIGH);
  return err;
}

esp_err_t writeCheckerboardFrame(uint16_t blockSize, uint8_t highIntensity, uint8_t lowIntensity, DisplayTransferMode mode) {
  if (blockSize == 0) {
    blockSize = 4;
  }

  if (rowBuffer == nullptr) {
    return ESP_ERR_NO_MEM;
  }

  digitalWrite(PIN_NUM_CS, LOW);
  esp_err_t err = ESP_OK;

  for (uint16_t row = 0; row < DISPLAY_HEIGHT; ++row) {
    for (uint16_t col = 0; col < DISPLAY_WIDTH; ++col) {
      const bool highBlock = ((((col / blockSize) + (row / blockSize)) & 0x01) == 0);
      rowBuffer[col] = highBlock ? highIntensity : lowIntensity;
    }

    // The display-data header changes with the transfer mode:
    //   SPI  mode: 0x02 + 0x002C00, then one data bit lane
    //   QSPI mode: 0x32 + 0x002C00, then four data bit lanes
    // Subsequent chunks keep CS low and send only more pixel bytes.
    if (row == 0) {
      err = beginDisplayData(DISPLAY_WRITE, rowBuffer, DISPLAY_WIDTH, mode);
    } else {
      err = transferDataChunk(rowBuffer, DISPLAY_WIDTH, mode);
    }

    if (err != ESP_OK) {
      break;
    }
  }

  digitalWrite(PIN_NUM_CS, HIGH);
  return err;
}

esp_err_t writeSquarePixels(uint16_t x, uint16_t y, uint16_t size, uint8_t intensity, DisplayTransferMode mode) {
  if (size == 0) {
    return ESP_ERR_INVALID_ARG;
  }

  if (x >= DISPLAY_WIDTH || y >= DISPLAY_HEIGHT) {
    return ESP_ERR_INVALID_ARG;
  }

  if (rowBuffer == nullptr) {
    return ESP_ERR_NO_MEM;
  }

  uint16_t width = size;
  uint16_t height = size;
  if (x + width > DISPLAY_WIDTH) {
    width = DISPLAY_WIDTH - x;
  }
  if (y + height > DISPLAY_HEIGHT) {
    height = DISPLAY_HEIGHT - y;
  }

  digitalWrite(PIN_NUM_CS, LOW);
  esp_err_t err = ESP_OK;

  for (uint16_t row = 0; row < DISPLAY_HEIGHT; ++row) {
    for (uint16_t col = 0; col < DISPLAY_WIDTH; ++col) {
      uint8_t pixel = 0x00;
      if (row >= y && row < (y + height) && col >= x && col < (x + width)) {
        pixel = intensity;
      }
      rowBuffer[col] = pixel;
    }

    if (row == 0) {
      err = beginDisplayData(DISPLAY_WRITE, rowBuffer, DISPLAY_WIDTH, mode);
    } else {
      err = transferDataChunk(rowBuffer, DISPLAY_WIDTH, mode);
    }

    if (err != ESP_OK) {
      break;
    }
  }

  digitalWrite(PIN_NUM_CS, HIGH);
  return err;
}

// Center is a pixel index; even diameters use a half-pixel center offset.
esp_err_t writeDiskPixels(int cx, int cy, int diameter, uint8_t intensity) {
  if (rowBuffer == nullptr) return ESP_ERR_NO_MEM;
  const int offset = diameter % 2 == 0 ? 1 : 0;
  const int radiusSquared = diameter * diameter;
  digitalWrite(PIN_NUM_CS, LOW);
  esp_err_t err = ESP_OK;
  for (int y = 0; y < DISPLAY_HEIGHT; ++y) {
    const int dy = 2 * (y - cy) - offset;
    for (int x = 0; x < DISPLAY_WIDTH; ++x) {
      const int dx = 2 * (x - cx) - offset;
      rowBuffer[x] = dx * dx + dy * dy < radiusSquared ? intensity : 0;
    }
    err = y == 0 ? beginDisplayData(DISPLAY_WRITE, rowBuffer, DISPLAY_WIDTH, displayMode)
                 : transferDataChunk(rowBuffer, DISPLAY_WIDTH, displayMode);
    if (err != ESP_OK) break;
  }
  digitalWrite(PIN_NUM_CS, HIGH);
  return err;
}

const char *animationTypeName(AnimationType type) {
  switch (type) {
    case ANIMATION_CHECKER: return "CHECKER";
    case ANIMATION_BARS: return "BARS";
    case ANIMATION_GRADIENT: return "GRADIENT";
    case ANIMATION_SQUARE: return "SQUARE";
    case ANIMATION_JAY: return "JAY";
    default: return "STOPPED";
  }
}

esp_err_t writeAnimationFrame() {
  if (rowBuffer == nullptr) return ESP_ERR_NO_MEM;

  const int32_t phase = static_cast<int32_t>(animation.frame) * animation.speed;
  digitalWrite(PIN_NUM_CS, LOW);
  esp_err_t err = ESP_OK;

  for (uint16_t y = 0; y < DISPLAY_HEIGHT; ++y) {
    for (uint16_t x = 0; x < DISPLAY_WIDTH; ++x) {
      uint8_t pixel = 0;
      switch (animation.type) {
        case ANIMATION_CHECKER: {
          const uint16_t block = animation.size == 0 ? 1 : animation.size;
          const int32_t moving = animation.vertical ? y : x;
          const int32_t fixed = animation.vertical ? x : y;
          int32_t shifted = (moving + phase) % (2 * block);
          if (shifted < 0) shifted += 2 * block;
          pixel = (((shifted / block) + (fixed / block)) & 1) ? 0 : animation.intensity;
          break;
        }
        case ANIMATION_BARS: {
          const uint16_t width = animation.size == 0 ? 1 : animation.size;
          int32_t shifted = (static_cast<int32_t>(animation.vertical ? y : x) + phase) % (2 * width);
          if (shifted < 0) shifted += 2 * width;
          pixel = shifted < width ? animation.intensity : 0;
          break;
        }
        case ANIMATION_GRADIENT:
          pixel = static_cast<uint8_t>((static_cast<uint16_t>((x + y + phase) & 0xFF) * animation.intensity) / 255);
          break;
        case ANIMATION_SQUARE: {
          const uint16_t side = animation.size < DISPLAY_HEIGHT ? animation.size : DISPLAY_HEIGHT;
          const int32_t travel = (animation.vertical ? DISPLAY_HEIGHT : DISPLAY_WIDTH) > side
                                   ? (animation.vertical ? DISPLAY_HEIGHT : DISPLAY_WIDTH) - side : 1;
          int32_t position = phase % (2 * travel);
          if (position < 0) position += 2 * travel;
          if (position > travel) position = 2 * travel - position;
          const int32_t left = animation.vertical ? (DISPLAY_WIDTH - side) / 2 : position;
          const int32_t top = animation.vertical ? position : (DISPLAY_HEIGHT - side) / 2;
          pixel = (x >= left && x < left + side && y >= top && y < top + side)
                    ? animation.intensity : 0;
          break;
        }
        case ANIMATION_JAY: {
          static const char *glyphs[] = {
            "11111" "00100" "00100" "00100" "10100" "10100" "01100",
            "01110" "10001" "10001" "11111" "10001" "10001" "10001",
            "10001" "10001" "01010" "00100" "00100" "00100" "00100",
          };
          const uint16_t scale = animation.size / 7 > 0 ? animation.size / 7 : 1;
          const uint16_t wordWidth = 17 * scale;
          const uint16_t wordHeight = 7 * scale;
          const int32_t travel = animation.vertical
                                   ? (DISPLAY_HEIGHT > wordHeight ? DISPLAY_HEIGHT - wordHeight : 1)
                                   : (DISPLAY_WIDTH > wordWidth ? DISPLAY_WIDTH - wordWidth : 1);
          int32_t position = phase % (2 * travel);
          if (position < 0) position += 2 * travel;
          if (position > travel) position = 2 * travel - position;
          const int32_t left = animation.vertical ? (DISPLAY_WIDTH - wordWidth) / 2 : position;
          const int32_t top = animation.vertical ? position : (DISPLAY_HEIGHT - wordHeight) / 2;
          const int32_t localX = static_cast<int32_t>(x) - left;
          const int32_t localY = static_cast<int32_t>(y) - top;
          const int32_t letter = localX >= 0 ? localX / (6 * scale) : -1;
          const int32_t column = localX >= 0 ? (localX % (6 * scale)) / scale : -1;
          const int32_t row = localY >= 0 ? localY / scale : -1;
          pixel = letter >= 0 && letter < 3 && column >= 0 && column < 5 && row >= 0 && row < 7 &&
                  glyphs[letter][row * 5 + column] == '1' ? animation.intensity : 0;
          break;
        }
        default:
          pixel = 0;
      }
      rowBuffer[x] = pixel;
    }

    err = y == 0
      ? beginDisplayData(DISPLAY_WRITE, rowBuffer, DISPLAY_WIDTH, displayMode)
      : transferDataChunk(rowBuffer, DISPLAY_WIDTH, displayMode);
    if (err != ESP_OK) break;
  }

  digitalWrite(PIN_NUM_CS, HIGH);
  return err;
}

void serviceAnimation() {
  if (animation.type == ANIMATION_STOPPED) return;
  const uint32_t now = millis();
  if (static_cast<int32_t>(now - animation.nextFrameAt) < 0) return;

  // Derive the frame from elapsed time so slow panel transfers skip overdue
  // frames instead of making the physical animation run slower than preview.
  const uint32_t elapsed = now - animation.startedAt;
  animation.frame = static_cast<uint32_t>(
    (static_cast<uint64_t>(elapsed) * animation.fps) / 1000UL
  );
  esp_err_t err = writeAnimationFrame();
  if (err != ESP_OK) {
    animation.type = ANIMATION_STOPPED;
    printEspError("animation", err);
    return;
  }

  const uint32_t nextFrame = animation.frame + 1;
  animation.nextFrameAt = animation.startedAt + static_cast<uint32_t>(
    (static_cast<uint64_t>(nextFrame) * 1000UL + animation.fps - 1) / animation.fps
  );
  const uint32_t finishedAt = millis();
  if (static_cast<int32_t>(finishedAt - animation.nextFrameAt) >= 0) {
    // Do not build an ever-growing backlog when the requested FPS exceeds the
    // measured panel throughput. Render the next frame as soon as possible.
    animation.nextFrameAt = finishedAt;
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(PIN_NUM_CS, OUTPUT);
  digitalWrite(PIN_NUM_CS, HIGH);
  pinMode(PIN_NUM_RST, OUTPUT);
  digitalWrite(PIN_NUM_RST, HIGH);

  if (!initDisplaySpi()) {
    Serial.println("SPI INIT FAILED");
    while (true) {
      delay(1000);
    }
  }

  Serial.println("READY");
}

void loop() {
  serviceAnimation();
  if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');
    input.trim();
    if (input.length() == 0) return;

    char cmd = input.charAt(0);
    if (cmd == 'A' || cmd == 'a') {
      char typeToken[16] = {};
      unsigned int fps = 10, size = 32, intensity = 1;
      int speed = 4;
      char directionToken[4] = "H";
      int parsed = sscanf(input.c_str() + 1, "%15s %u %u %u %d %3s", typeToken, &fps, &size, &intensity, &speed, directionToken);

      if (parsed < 1) {
        Serial.printf("%s %u %u %u %d %s %lu\n", animationTypeName(animation.type), animation.fps,
                      animation.size, animation.intensity, animation.speed,
                      animation.vertical ? "V" : "H",
                      static_cast<unsigned long>(animation.frame));
      } else if (strcasecmp(typeToken, "STOP") == 0) {
        animation.type = ANIMATION_STOPPED;
        Serial.println("OK");
      } else {
        AnimationType requested = ANIMATION_STOPPED;
        if (strcasecmp(typeToken, "CHECKER") == 0) requested = ANIMATION_CHECKER;
        else if (strcasecmp(typeToken, "BARS") == 0) requested = ANIMATION_BARS;
        else if (strcasecmp(typeToken, "GRADIENT") == 0) requested = ANIMATION_GRADIENT;
        else if (strcasecmp(typeToken, "SQUARE") == 0) requested = ANIMATION_SQUARE;
        else if (strcasecmp(typeToken, "JAY") == 0) requested = ANIMATION_JAY;

        if (requested == ANIMATION_STOPPED || fps < 1 || fps > 120 || size < 1 ||
            size > DISPLAY_WIDTH || intensity > 255 ||
            (parsed >= 6 && strcasecmp(directionToken, "H") != 0 && strcasecmp(directionToken, "V") != 0)) {
          Serial.println("ERR animation arguments");
        } else {
          animation.type = requested;
          animation.fps = static_cast<uint16_t>(fps);
          animation.size = static_cast<uint16_t>(size);
          animation.intensity = static_cast<uint8_t>(intensity);
          animation.speed = static_cast<int16_t>(speed);
          animation.vertical = parsed >= 6 && strcasecmp(directionToken, "V") == 0;
          animation.frame = 0;
          animation.startedAt = millis();
          animation.nextFrameAt = animation.startedAt;
          Serial.println("OK");
        }
      }
    } else if (cmd == 'F' || cmd == 'f') {
      char type[16], direction[4], extra;
      int size, intensity, speed;
      unsigned long frame;
      AnimationType requested = ANIMATION_STOPPED;
      int parsed = sscanf(input.c_str() + 1, "%15s %d %d %d %3s %lu %c",
                          type, &size, &intensity, &speed, direction, &frame, &extra);
      if (parsed >= 1) {
        if (strcasecmp(type, "CHECKER") == 0) requested = ANIMATION_CHECKER;
        else if (strcasecmp(type, "BARS") == 0) requested = ANIMATION_BARS;
        else if (strcasecmp(type, "GRADIENT") == 0) requested = ANIMATION_GRADIENT;
        else if (strcasecmp(type, "SQUARE") == 0) requested = ANIMATION_SQUARE;
        else if (strcasecmp(type, "JAY") == 0) requested = ANIMATION_JAY;
      }
      if (parsed != 6 || requested == ANIMATION_STOPPED || size < 1 || size > DISPLAY_WIDTH ||
          intensity < 0 || intensity > 255 || speed < -100 || speed > 100 || frame > 10000000UL ||
          (strcasecmp(direction, "H") != 0 && strcasecmp(direction, "V") != 0)) {
        Serial.println("ERR frame arguments");
      } else {
        animation.type = requested;
        animation.size = size;
        animation.intensity = intensity;
        animation.speed = speed;
        animation.vertical = strcasecmp(direction, "V") == 0;
        animation.frame = frame;
        esp_err_t err = ensureGray256Mode();
        if (err == ESP_OK) err = writeAnimationFrame();
        animation.type = ANIMATION_STOPPED;
        if (err == ESP_OK) Serial.println("OK");
        else printEspError("frame", err);
      }
    } else if (cmd == 'D' || cmd == 'd') {
      int x, y, diameter, intensity;
      char extra;
      if (sscanf(input.c_str() + 1, "%d %d %d %d %c", &x, &y, &diameter, &intensity, &extra) != 4 ||
          x < 0 || x >= DISPLAY_WIDTH || y < 0 || y >= DISPLAY_HEIGHT ||
          diameter < 1 || diameter > DISPLAY_WIDTH || intensity < 0 || intensity > 255) {
        Serial.println("ERR disk arguments");
      } else {
        animation.type = ANIMATION_STOPPED;
        esp_err_t err = ensureGray256Mode();
        if (err == ESP_OK) err = writeDiskPixels(x, y, diameter, static_cast<uint8_t>(intensity));
        if (err == ESP_OK) Serial.println("OK");
        else printEspError("writeDiskPixels", err);
      }
    } else if (cmd == 'X' || cmd == 'x') {
      animation.type = ANIMATION_STOPPED;
      digitalWrite(PIN_NUM_RST, LOW);
      delay(5);
      digitalWrite(PIN_NUM_RST, HIGH);
      Serial.println("OK");
    } else if (cmd == 'R' || cmd == 'r') {
      int reg;
      if (sscanf(input.c_str() + 1, "%x", &reg) == 1) {
        uint8_t result = 0;
        esp_err_t err = readRegister(static_cast<uint8_t>(reg), &result);
        if (err == ESP_OK) {
          Serial.printf("%02X\n", result);
        } else {
          printEspError("readRegister", err);
        }
      } else {
        Serial.println("ERR");
      }
    } else if (cmd == 'W' || cmd == 'w') {
      int reg, val;
      if (sscanf(input.c_str() + 1, "%x %x", &reg, &val) == 2) {
        esp_err_t err = writeRegister(static_cast<uint8_t>(reg), static_cast<uint8_t>(val));
        if (err == ESP_OK) {
          Serial.println("OK");
        } else {
          printEspError("writeRegister", err);
        }
      } else {
        Serial.println("ERR");
      }
    } else if (cmd == 'M' || cmd == 'm') {
      char modeToken[16] = {};
      int parsed = sscanf(input.c_str() + 1, "%15s", modeToken);
      if (parsed == 0) {
        Serial.println(displayModeName(displayMode));
      } else if (parseDisplayModeToken(modeToken, &displayMode)) {
        Serial.println("OK");
      } else {
        Serial.println("ERR");
      }
    } else if (cmd == 'S' || cmd == 's') {
      unsigned int x, y, size, intensity = 0xFF;
      char arg4[16] = {};
      char arg5[16] = {};
      DisplayTransferMode requestedMode = displayMode;
      int parsed = sscanf(input.c_str() + 1, "%u %u %u %15s %15s", &x, &y, &size, arg4, arg5);
      if (parsed >= 3) {
        bool argsOk = true;
        if (parsed >= 4) {
          if (parseDisplayModeToken(arg4, &requestedMode)) {
            // Intensity omitted; arg4 selected the transfer mode.
          } else {
            argsOk = parseUnsignedToken(arg4, &intensity);
            argsOk = argsOk && intensity <= 0xFF;
          }
        }
        if (parsed >= 5) {
          argsOk = argsOk && parseDisplayModeToken(arg5, &requestedMode);
        }

        if (argsOk) {
          esp_err_t err = ensureGray256Mode();
          if (err == ESP_OK) {
            err = writeSquarePixels(static_cast<uint16_t>(x), static_cast<uint16_t>(y), static_cast<uint16_t>(size), static_cast<uint8_t>(intensity), requestedMode);
          }

          if (err == ESP_OK) {
            displayMode = requestedMode;
            Serial.println("OK");
          } else {
            printEspError("writeSquarePixels", err);
          }
        } else {
          Serial.println("ERR");
        }
      } else {
        Serial.println("ERR");
      }
    } else if (cmd == 'B' || cmd == 'b') {
      unsigned int blockSize = 4;
      unsigned int brightness = 0xFF;
      char arg1[16] = {};
      char arg2[16] = {};
      char arg3[16] = {};
      DisplayTransferMode requestedMode = displayMode;
      int parsed = sscanf(input.c_str() + 1, "%15s %15s %15s", arg1, arg2, arg3);
      bool argsOk = true;

      if (parsed >= 1) {
        if (parseDisplayModeToken(arg1, &requestedMode)) {
          // Block size omitted; arg1 selected the transfer mode.
        } else {
          argsOk = parseUnsignedToken(arg1, &blockSize);
          argsOk = argsOk && blockSize > 0;
        }
      }
      if (parsed >= 2) {
        if (parseDisplayModeToken(arg2, &requestedMode)) {
          // Brightness omitted; arg2 selected the transfer mode.
        } else {
          argsOk = argsOk && parseUnsignedToken(arg2, &brightness);
          argsOk = argsOk && brightness <= 0xFF;
        }
      }
      if (parsed >= 3) {
        argsOk = argsOk && parseDisplayModeToken(arg3, &requestedMode);
      }

      if (argsOk) {
        esp_err_t err = ensureGray256Mode();
        if (err == ESP_OK) {
          err = writeCheckerboardFrame(static_cast<uint16_t>(blockSize), static_cast<uint8_t>(brightness), 0x00, requestedMode);
        }

        if (err == ESP_OK) {
          displayMode = requestedMode;
          Serial.println("OK");
        } else {
          printEspError("writeCheckerboardFrame", err);
        }
      } else {
        Serial.println("ERR");
      }
    } else {
      Serial.println("ERR");
    }
  }
}
