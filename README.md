# microLED Driver

**Double-click `Start.command` to open the app.** Choose an animation and click **Play**. The preview works without hardware.

To drive the panel:
1. Connect the ESP32 by USB.
2. Flash the firmware once: open Terminal in this folder and run `./Start.command --flash`.
3. Open the app, choose the ESP32 serial port, click **Connect**, then **Play**.

Set animation, FPS, size, brightness (0–255), speed, and scrolling direction in the app. Click Play to apply changes, or press Enter after editing a numeric field. **Advanced register tools** opens the register console, static patterns, and SPI/QSPI controls. It disconnects the animation controller first; close the tools and reconnect to resume animations.

## Disk of pixels

Use **Disk (filled circle)** in the main app. **Click or drag on the preview** to place the disk center. With the preview focused, **Up/Down** increases/decreases brightness and **Right/Left** increases/decreases diameter. Each key press changes the value by 1; hold **Shift** for steps of 10. Mouse and key changes request updates to the connected panel. The connected preview changes only after firmware confirms a successful full-frame transfer; while an update is pending it retains the previous confirmed image. Pending changes are combined during rapid dragging so the panel receives the latest setting.

**Show disk** also activates the disk and focuses the preview for arrow keys. **Turn off** clears the screen. Current coordinates, diameter, and brightness are displayed beside the preview. X ranges from 0–639, Y from 0–479, diameter from 1–640 pixels, and brightness from 0–255. The origin is the upper-left; X increases rightward and Y downward. A disk crossing an edge is clipped. Even diameters place the center half a pixel right and down for a symmetric raster.

Reflash with `./Start.command --flash` to enable disk control and confirmed animation frames. Connecting clears the panel and confirms that black frame before displaying it. Connected animations send one frame at a time and update the preview after each acknowledgement; offline previews run independently. Failed transfers show “Panel output unconfirmed”; a timeout disconnects the controller to prevent a late reply from confirming the wrong frame. Firmware acknowledges successful transmission, not optical measurements from the LEDs. Serial protocol: `D <center-x> <center-y> <diameter> <brightness>`; success returns `OK` and stops any animation. Preview brightness is boosted for visibility and is not a calibrated measure of panel luminance. The preview combines each 2×2 panel block so a one-pixel disk remains visible.

## Setup

The launcher uses an installed Python with Tkinter. For hardware connection that Python also needs `pyserial` (`python3 -m pip install pyserial`). Flashing needs PlatformIO (`python3 -m pip install platformio`). Existing local environments are supported. If Tkinter is missing, install a Python distribution with Tk support; on Homebrew use the `python-tk` package matching your Python version.

Build without flashing: `./Start.command --build`. Alternatively, run `pio run -t upload`. If multiple ESP32 boards are connected, specify one with `pio run -t upload --upload-port PORT`.

## Wiring

Firmware targets an ESP32 Dev Module and the DMHSM0012VGNA panel.

| Panel signal | ESP32 GPIO |
|---|---:|
| SCLK | 18 |
| IO0 / MOSI | 23 |
| IO1 / MISO | 19 |
| IO2 | 22 |
| IO3 | 21 |
| CS | 5 |
| RST | 17 |

Use the datasheet for power, ground, and electrical requirements. The current firmware uses a 40 MHz SPI clock; actual panel operation requires hardware verification.

## Files

Everything needed for daily use is here:
- `Start.command` — opens the app; also builds or flashes firmware.
- `controller.py` — animation preview and advanced controls.
- `src/main.cpp` — the single active firmware.
- `platformio.ini` — board/build settings.
- `DMHSM0012VGNA_Datasheet.pdf` — hardware reference.

Previous SPI example, controller files, datasheet text, and research report are preserved in `.archive` (Finder: Command–Shift–Period shows hidden files). The driver retains its existing Git repository and local environments. The archive is local only; previously tracked source files remain recoverable from Git history.

Connected animation protocol: `F <type> <size> <brightness> <speed> <H|V> <frame>` renders one frame, stops autonomous animation, and returns `OK` only after the transfer succeeds.
