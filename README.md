# dmhsm-driver
SPI/QSPI μLED drivers designed for DMHSM0012VGNA

## Functionality

This repository contains an ESP32 control driver for the **DMHSM0012VGNA** microLED display module over an SPI interface.

### Features
* **Hardware Initialization**: Uses full device sequence including pulling RST pin low. Sets the display into `GRAY256` pattern sequence out-of-the-box.
* **Register Configuration**: Configures internal options such as the checkerboard dummy self-test and PWM (brightness level 7). The driver handles the proprietary SPI command formats (`0x78` addressing system, etc.) based upon datasheet specs.
* **Frame Transmissions over SPI**: Initiates buffer stream transfer chunks based upon `0x02` commands for sending full screen data. It allows simulating dummy rendering frames up to internal display memory constraints to update arrays line-by-line natively supporting the 640x480 resolution layout.

### File Structure
- `include/DMHSM.h`: C++ Class definition describing SPI constants and display interface bindings.
- `src/DMHSM.cpp`: Class implementation of all initializations, resets, and pixel buffer transfers.
- `src/main.cpp`: An example script invoking module instantiation through Arduino standard loop/setup models.
