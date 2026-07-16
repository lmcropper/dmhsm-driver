"""Live controller and computer-side preview for the DMHSM animation engine."""

import threading
import time
import tkinter as tk
from tkinter import ttk

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None


PANEL_WIDTH = 640
PANEL_HEIGHT = 480
PREVIEW_SCALE = 2
PREVIEW_WIDTH = PANEL_WIDTH // PREVIEW_SCALE
PREVIEW_HEIGHT = PANEL_HEIGHT // PREVIEW_SCALE
PREVIEW_FPS = 15
PREVIEW_VISIBLE_MIN = 48
PREVIEW_BRIGHTNESS_LUT = bytes(
    0 if value == 0 else PREVIEW_VISIBLE_MIN + (value * (255 - PREVIEW_VISIBLE_MIN) // 255)
    for value in range(256)
)
BAUD_RATE = 115200


class AnimationController:
    def __init__(self, root):
        self.root = root
        self.root.title("DMHSM Animation Controller")
        self.serial_port = None
        self.serial_lock = threading.Lock()
        self.running = False
        self.frame = 0
        self.started_at = time.monotonic()
        self.preview_image = None
        self.preview_item = None
        self.preview_rendered_frame = None
        self.preview_rendered_settings = None

        self.port_var = tk.StringVar()
        self.animation_var = tk.StringVar(value="CHECKER")
        self.fps_var = tk.StringVar(value="10")
        self.size_var = tk.StringVar(value="32")
        self.intensity_var = tk.StringVar(value="1")
        self.speed_var = tk.StringVar(value="4")
        self.vertical_var = tk.BooleanVar(value=False)
        self.committed = {"fps": 10, "size": 32, "intensity": 1, "speed": 4}
        self.status_var = tk.StringVar(value="Preview ready; hardware disconnected")

        self.build_ui()
        self.refresh_ports()
        self.preview_tick()

    def build_ui(self):
        controls = ttk.Frame(self.root, padding=10)
        controls.grid(row=0, column=0, sticky="ns")

        ttk.Label(controls, text="Serial port").grid(row=0, column=0, sticky="w")
        self.port_box = ttk.Combobox(controls, textvariable=self.port_var, width=20, state="readonly")
        self.port_box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 8))
        ttk.Button(controls, text="Refresh", command=self.refresh_ports).grid(row=2, column=0, sticky="ew")
        self.connect_button = ttk.Button(controls, text="Connect", command=self.toggle_connection)
        self.connect_button.grid(row=2, column=1, sticky="ew")

        ttk.Separator(controls).grid(row=3, column=0, columnspan=2, sticky="ew", pady=10)
        self.add_field(controls, 4, "Animation", ttk.Combobox(
            controls,
            textvariable=self.animation_var,
            values=("CHECKER", "BARS", "GRADIENT", "SQUARE", "JAY"),
            width=12,
            state="readonly",
        ))
        fields = (
            (5, "FPS", "fps", self.fps_var, 1, 120),
            (6, "Size", "size", self.size_var, 1, 640),
            (7, "Brightness", "intensity", self.intensity_var, 0, 255),
            (8, "Speed", "speed", self.speed_var, -100, 100),
        )
        for row, label, name, variable, low, high in fields:
            widget = ttk.Spinbox(controls, from_=low, to=high, textvariable=variable, width=10)
            widget.bind("<Return>", lambda _event, n=name: self.commit_field(n))
            self.add_field(controls, row, label, widget)

        ttk.Checkbutton(
            controls,
            text="Scroll vertically",
            variable=self.vertical_var,
            command=self.direction_changed,
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Button(controls, text="Play", command=self.play).grid(row=10, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(controls, text="Stop", command=self.stop).grid(row=10, column=1, sticky="ew", pady=(12, 0))
        ttk.Button(controls, text="Hardware Reset", command=self.trigger_reset).grid(
            row=11, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

        preview = ttk.LabelFrame(self.root, text="Expected microLED output", padding=8)
        preview.grid(row=0, column=1, padx=(0, 10), pady=10)
        self.canvas = tk.Canvas(
            preview,
            width=PREVIEW_WIDTH,
            height=PREVIEW_HEIGHT,
            background="black",
            highlightthickness=0,
        )
        self.canvas.pack()
        ttk.Label(self.root, textvariable=self.status_var, anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10)
        )

    @staticmethod
    def add_field(parent, row, label, widget):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        widget.grid(row=row, column=1, sticky="ew", pady=3)

    def refresh_ports(self):
        ports = [] if serial is None else [port.device for port in serial.tools.list_ports.comports()]
        self.port_box["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def toggle_connection(self):
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            self.serial_port = None
            self.connect_button.config(text="Connect")
            self.status_var.set("Hardware disconnected; preview still available")
            return
        if serial is None:
            self.status_var.set("Install pyserial to connect hardware; offline preview is available")
            return
        try:
            self.serial_port = serial.Serial(self.port_var.get(), BAUD_RATE, timeout=1.0)
            self.connect_button.config(text="Disconnect")
            self.status_var.set(f"Connected to {self.port_var.get()}")
        except Exception as exc:
            self.status_var.set(f"Connection failed: {exc}")

    def settings(self):
        return (self.animation_var.get(), self.committed["fps"], self.committed["size"],
                self.committed["intensity"], self.committed["speed"],
                "V" if self.vertical_var.get() else "H")

    def direction_changed(self):
        self.preview_rendered_settings = None
        if self.running:
            self.frame = 0
            self.started_at = time.monotonic()
            animation, fps, size, intensity, speed, direction = self.settings()
            self.send_command_async(f"A {animation} {fps} {size} {intensity} {speed} {direction}")

    def commit_field(self, name, apply_running=True):
        variable = getattr(self, f"{name}_var") if name != "intensity" else self.intensity_var
        limits = {"fps": (1, 120), "size": (1, PANEL_WIDTH),
                  "intensity": (0, 255), "speed": (-100, 100)}
        try:
            value = int(variable.get())
        except ValueError:
            variable.set(str(self.committed[name]))
            self.status_var.set(f"Invalid {name}; restored {self.committed[name]}")
            return "break"
        low, high = limits[name]
        value = max(low, min(high, value))
        variable.set(str(value))
        self.committed[name] = value
        if self.running and apply_running:
            self.frame = 0
            self.started_at = time.monotonic()
            animation, fps, size, intensity, speed, direction = self.settings()
            self.send_command_async(f"A {animation} {fps} {size} {intensity} {speed} {direction}")
        return "break"

    def commit_all_fields(self):
        for name in self.committed:
            self.commit_field(name, apply_running=False)

    def play(self):
        self.commit_all_fields()
        animation, fps, size, intensity, speed, direction = self.settings()
        self.running = True
        self.frame = 0
        self.started_at = time.monotonic()
        self.preview_rendered_frame = None
        self.preview_rendered_settings = None
        self.send_command_async(f"A {animation} {fps} {size} {intensity} {speed} {direction}")

    def stop(self):
        self.running = False
        self.send_command_async("A STOP")

    def trigger_reset(self):
        self.send_command_async("X")

    def send_command_async(self, command):
        if not self.serial_port or not self.serial_port.is_open:
            self.status_var.set(f"Previewing locally: {command}")
            return

        def worker():
            try:
                with self.serial_lock:
                    self.serial_port.reset_input_buffer()
                    self.serial_port.write((command + "\n").encode("ascii"))
                    response = self.serial_port.readline().decode("ascii", errors="replace").strip()
                self.root.after(0, self.status_var.set, f"Device: {response or 'no response'} | {command}")
            except Exception as exc:
                self.root.after(0, self.status_var.set, f"Communication error: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def preview_tick(self):
        try:
            if self.running:
                animation, fps, size, intensity, speed, direction = self.settings()
                self.frame = int((time.monotonic() - self.started_at) * fps)
                settings = (animation, size, intensity, speed, direction)

                # A low hardware FPS can map several preview ticks to the same
                # animation frame. Avoid recreating an identical Tk image.
                if self.frame != self.preview_rendered_frame or settings != self.preview_rendered_settings:
                    pixels = self.render_preview(animation, size, intensity, speed, direction, self.frame)
                    pixels = pixels.translate(PREVIEW_BRIGHTNESS_LUT)
                    rgb_pixels = bytearray(len(pixels) * 3)
                    rgb_pixels[0::3] = pixels
                    rgb_pixels[1::3] = pixels
                    rgb_pixels[2::3] = pixels
                    ppm = f"P6\n{PREVIEW_WIDTH} {PREVIEW_HEIGHT}\n255\n".encode("ascii") + rgb_pixels

                    # Tk 9 accepts PPM as raw bytes but no longer recognizes
                    # the base64 representation supported by some Tk builds.
                    next_image = tk.PhotoImage(data=ppm, format="PPM")
                    if self.preview_item is None:
                        self.preview_item = self.canvas.create_image(0, 0, image=next_image, anchor="nw")
                    else:
                        self.canvas.itemconfigure(self.preview_item, image=next_image)
                    self.preview_image = next_image
                    self.preview_rendered_frame = self.frame
                    self.preview_rendered_settings = settings
        except (tk.TclError, ValueError) as exc:
            self.running = False
            self.status_var.set(f"Preview stopped after a rendering error: {exc}")
        finally:
            self.root.after(1000 // PREVIEW_FPS, self.preview_tick)

    @staticmethod
    def render_preview(animation, size, intensity, speed, direction, frame):
        output = bytearray(PREVIEW_WIDTH * PREVIEW_HEIGHT)
        phase = frame * speed
        vertical = direction == "V"
        side = min(size, PANEL_HEIGHT if vertical else min(PANEL_WIDTH, PANEL_HEIGHT))
        travel = max(1, (PANEL_HEIGHT if vertical else PANEL_WIDTH) - side)
        position = phase % (2 * travel)
        if position > travel:
            position = 2 * travel - position
        left = (PANEL_WIDTH - side) // 2 if vertical else position
        top = position if vertical else (PANEL_HEIGHT - side) // 2

        index = 0
        for py in range(PREVIEW_HEIGHT):
            y = py * PREVIEW_SCALE
            for px in range(PREVIEW_WIDTH):
                x = px * PREVIEW_SCALE
                shifted_axis = (y if vertical else x) + phase
                if animation == "CHECKER":
                    value = 0 if (((shifted_axis // size) + ((x if vertical else y) // size)) & 1) else intensity
                elif animation == "BARS":
                    value = intensity if shifted_axis % (2 * size) < size else 0
                elif animation == "GRADIENT":
                    value = (((x + y + phase) & 0xFF) * intensity) // 255
                elif animation == "SQUARE":
                    value = intensity if left <= x < left + side and top <= y < top + side else 0
                else:
                    scale = max(1, size // 7)
                    word_width = 17 * scale
                    word_height = 7 * scale
                    jay_travel = max(1, (PANEL_HEIGHT - word_height) if vertical else (PANEL_WIDTH - word_width))
                    jay_position = phase % (2 * jay_travel)
                    if jay_position > jay_travel:
                        jay_position = 2 * jay_travel - jay_position
                    jay_left = (PANEL_WIDTH - word_width) // 2 if vertical else jay_position
                    jay_top = jay_position if vertical else (PANEL_HEIGHT - word_height) // 2
                    local_x = x - jay_left
                    local_y = y - jay_top
                    letter = local_x // (6 * scale) if local_x >= 0 else -1
                    column = (local_x % (6 * scale)) // scale if local_x >= 0 else -1
                    row = local_y // scale if local_y >= 0 else -1
                    if 0 <= letter < 3 and column < 5 and 0 <= row < 7:
                        # Packed row-major 5x7 glyphs are clearer expressed as strings below.
                        glyphs = (
                            "11111" "00100" "00100" "00100" "10100" "10100" "01100",
                            "01110" "10001" "10001" "11111" "10001" "10001" "10001",
                            "10001" "10001" "01010" "00100" "00100" "00100" "00100",
                        )
                        value = intensity if glyphs[letter][row * 5 + column] == "1" else 0
                    else:
                        value = 0
                output[index] = value
                index += 1
        return bytes(output)


if __name__ == "__main__":
    window = tk.Tk()
    AnimationController(window)
    window.mainloop()
