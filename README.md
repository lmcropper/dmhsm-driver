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

## Interactive animation prototype

`QSPI_test` now contains a first-pass on-device animation engine and a desktop
preview/controller. The ESP32 renders frames locally, so serial carries only
small control messages instead of 307,200 bytes per grayscale frame.

Run the desktop controller with:

```sh
cd QSPI_test
python3 animation_controller.py
```

The preview works without connecting hardware. After flashing
`QSPI_test/src/main.cpp`, choose the ESP32 serial port and click **Connect** to
mirror Play/Stop commands to the panel. Supported commands are:

```text
A CHECKER  <fps> <block-size> <intensity> <speed> [H|V]
A BARS     <fps> <bar-width>  <intensity> <speed> [H|V]
A GRADIENT <fps> <size>       <intensity> <speed> [H|V]
A SQUARE   <fps> <side-length> <intensity> <speed> [H|V]
A JAY      <fps> <letter-height> <intensity> <speed> [H|V]
A STOP
A
```

The optional direction selects horizontal (`H`, the default) or vertical (`V`)
scrolling. The controller exposes it as a **Scroll vertically** toggle. The final
`A` form returns the current animation settings, direction, and rendered frame
counter. Its **Hardware Reset** button sends the same `X` command as the main
QSPI controller. When rendering cannot sustain the requested FPS, the firmware
skips overdue animation frames so panel motion remains synchronized with the
elapsed-time preview. The panel QSPI clock is initially raised from 1 MHz to 10 MHz; verify
signal integrity on the target hardware before trying higher rates.
