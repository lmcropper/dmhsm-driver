#include <Arduino.h>
#include <SPI.h>
#include "DMHSM.h"

// Display Pins
#define DISP_CS   5
#define DISP_RST  17

// SPI Pins
#define SPI_SCK   18
#define SPI_MISO  19
#define SPI_MOSI  23

DMHSM display(DISP_CS, DISP_RST, &SPI);

// Pointer for our screen buffer
// A 640x480 8-bit buffer requires 307,200 bytes.
// Using an ESP32 w/ PSRAM is highly recommended!
uint8_t *frameBuffer = nullptr;
uint8_t frameCount = 0;

void setup() {
  Serial.begin(115200);

  // Initialize display arrays (using PSRAM if available)
  // Check if your board is compiled with PSRAM available
  if (psramFound()) {
      frameBuffer = (uint8_t *)ps_malloc(640 * 480);
      Serial.println("Allocated frame buffer in PSRAM");
  } else {
      frameBuffer = (uint8_t *)malloc(640 * 480);
      Serial.println("Allocated frame buffer in internal RAM (May fail depending on ESP32 variant)");
  }

  if (frameBuffer == nullptr) {
      Serial.println("ERROR: Could not allocate frame buffer! Device will likely reboot or crash.");
      while(true) delay(100);
  }

  // Set up custom SPI pins on the default SPI object
  SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI, DISP_CS);
  
  display.begin();
  Serial.println("Display initialized with custom SPI pins.");
}

void loop() {
  if (frameBuffer != nullptr) {
      // Create a simple animated sweeping gradient or pattern
      for (int y = 0; y < 480; y++) {
          for (int x = 0; x < 640; x++) {
              frameBuffer[y * 640 + x] = (x + y + frameCount) % 256;
          }
      }
      frameCount += 5;

      // Push buffer to display
      display.sendFrameBuffer(frameBuffer);
  }

  // Cap framerate
  delay(30);
}
