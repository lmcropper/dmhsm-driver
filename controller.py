import threading
import time
import tkinter as tk
from tkinter import scrolledtext, ttk

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None


BAUD_RATE = 115200
DISPLAY_WIDTH = 640
DISPLAY_HEIGHT = 480
DEFAULT_TIMEOUT_SECONDS = 1.0
FRAME_TIMEOUT_SECONDS = 15.0


class SPIControllerApp:
    """Small serial console for the ESP32 DMHSM0012VGNA test firmware.

    The firmware in src/main.cpp uses one newline-delimited response per
    command. Register commands are always single-line SPI; display pixels can
    be streamed as either single-line SPI or four-line QSPI.
    """

    def __init__(self, root):
        self.root = root
        self.root.title("DMHSM QSPI Controller")

        self.serial_port = None
        self.serial_lock = threading.Lock()
        self.busy = False

        self.setup_ui()
        self.refresh_ports()

    def setup_ui(self):
        self.root.columnconfigure(0, weight=1)

        conn_frame = ttk.LabelFrame(self.root, text="Connection")
        conn_frame.pack(padx=10, pady=5, fill="x")

        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(conn_frame, textvariable=self.port_var, width=24, state="readonly")
        self.port_cb.pack(side="left", padx=5, pady=5)

        self.refresh_btn = ttk.Button(conn_frame, text="Refresh", command=self.refresh_ports)
        self.refresh_btn.pack(side="left", padx=5, pady=5)

        self.connect_btn = ttk.Button(conn_frame, text="Connect", command=self.toggle_connection)
        self.connect_btn.pack(side="left", padx=5, pady=5)

        self.status_var = tk.StringVar(value="Disconnected")
        ttk.Label(conn_frame, textvariable=self.status_var).pack(side="left", padx=10, pady=5)

        read_frame = ttk.LabelFrame(self.root, text="Read Register")
        read_frame.pack(padx=10, pady=5, fill="x")

        ttk.Label(read_frame, text="Reg (hex):").pack(side="left", padx=5)
        self.read_reg_entry = ttk.Entry(read_frame, width=10)
        self.read_reg_entry.pack(side="left", padx=5)
        self.read_reg_entry.insert(0, "00")

        ttk.Button(read_frame, text="Read", command=self.read_reg).pack(side="left", padx=5)

        write_frame = ttk.LabelFrame(self.root, text="Write Register")
        write_frame.pack(padx=10, pady=5, fill="x")

        ttk.Label(write_frame, text="Reg (hex):").pack(side="left", padx=5)
        self.write_reg_entry = ttk.Entry(write_frame, width=10)
        self.write_reg_entry.pack(side="left", padx=5)
        self.write_reg_entry.insert(0, "00")

        ttk.Label(write_frame, text="Val (hex/bin):").pack(side="left", padx=5)
        self.write_val_entry = ttk.Entry(write_frame, width=15)
        self.write_val_entry.pack(side="left", padx=5)
        self.write_val_entry.insert(0, "0b00000000")

        ttk.Button(write_frame, text="Write", command=self.write_reg).pack(side="left", padx=5)

        reset_frame = ttk.LabelFrame(self.root, text="Hardware Reset")
        reset_frame.pack(padx=10, pady=5, fill="x")
        ttk.Button(reset_frame, text="Trigger Reset", command=self.trigger_reset).pack(side="left", padx=5, pady=5)

        pattern_frame = ttk.LabelFrame(self.root, text="Test Patterns")
        pattern_frame.pack(padx=10, pady=5, fill="x")

        ttk.Label(pattern_frame, text="SPI Module Pattern (Reg 0x04):").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.spi_pattern_var = tk.StringVar()
        self.spi_pattern_cb = ttk.Combobox(pattern_frame, textvariable=self.spi_pattern_var, width=30, state="readonly")
        self.spi_pattern_cb["values"] = [
            "Disabled (0x00)",
            "Black screen (0x80)",
            "White screen (0x81)",
            "2x2 checkerboard (0x88)",
            "4x4 checkerboard (0x89)",
        ]
        self.spi_pattern_cb.current(0)
        self.spi_pattern_cb.grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(pattern_frame, text="Apply", command=self.apply_spi_pattern).grid(row=0, column=2, padx=5, pady=5)

        ttk.Label(pattern_frame, text="ASIC Self-Test Image (Reg 0x1B):").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        self.asic_pattern_var = tk.StringVar()
        self.asic_pattern_cb = ttk.Combobox(pattern_frame, textvariable=self.asic_pattern_var, width=30, state="readonly")
        self.asic_pattern_cb["values"] = [
            "Disable self-test (0x00)",
            "Diag bright lines (0x10)",
            "Diag dark lines (0x20)",
            "8 grayscale bars (0x30)",
            "10 vert grayscale bars (0x40)",
            "10 horiz grayscale bars (0x50)",
            "Checkerboard pattern (0x60)",
            "Outer bright lines (0x70)",
        ]
        self.asic_pattern_cb.current(0)
        self.asic_pattern_cb.grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(pattern_frame, text="Apply", command=self.apply_asic_pattern).grid(row=1, column=2, padx=5, pady=5)

        mode_frame = ttk.LabelFrame(self.root, text="Display Transfer Mode")
        mode_frame.pack(padx=10, pady=5, fill="x")

        self.transfer_mode_var = tk.StringVar(value="QSPI 4-line")
        self.transfer_mode_cb = ttk.Combobox(mode_frame, textvariable=self.transfer_mode_var, width=18, state="readonly")
        self.transfer_mode_cb["values"] = ["QSPI 4-line", "SPI 1-line"]
        self.transfer_mode_cb.pack(side="left", padx=5, pady=5)
        ttk.Button(mode_frame, text="Apply Mode", command=self.apply_transfer_mode).pack(side="left", padx=5, pady=5)
        ttk.Button(mode_frame, text="Read Mode", command=self.read_transfer_mode).pack(side="left", padx=5, pady=5)

        pixel_frame = ttk.LabelFrame(self.root, text="Display Pixel Square")
        pixel_frame.pack(padx=10, pady=5, fill="x")

        self.square_x_entry = self.add_grid_entry(pixel_frame, "X px:", "0", 0, 0)
        self.square_y_entry = self.add_grid_entry(pixel_frame, "Y px:", "0", 0, 2)
        self.square_size_entry = self.add_grid_entry(pixel_frame, "N px:", "8", 0, 4)
        self.square_intensity_entry = self.add_grid_entry(pixel_frame, "Intensity:", "0xFF", 0, 6, width=10)
        ttk.Button(pixel_frame, text="Draw Square", command=self.draw_square).grid(row=0, column=8, padx=5, pady=5)

        checker_frame = ttk.LabelFrame(self.root, text="Display Grid / Checkerboard Frame")
        checker_frame.pack(padx=10, pady=5, fill="x")

        ttk.Label(checker_frame, text="Block size px:").pack(side="left", padx=5, pady=5)
        self.checker_size_entry = ttk.Entry(checker_frame, width=8)
        self.checker_size_entry.pack(side="left", padx=5, pady=5)
        self.checker_size_entry.insert(0, "4")

        ttk.Label(checker_frame, text="Brightness:").pack(side="left", padx=5, pady=5)
        self.checker_brightness_entry = ttk.Entry(checker_frame, width=10)
        self.checker_brightness_entry.pack(side="left", padx=5, pady=5)
        self.checker_brightness_entry.insert(0, "0xFF")

        ttk.Button(checker_frame, text="Send Full-Screen Checkerboard", command=self.send_checkerboard).pack(side="left", padx=5, pady=5)

        log_frame = ttk.LabelFrame(self.root, text="Console Output")
        log_frame.pack(padx=10, pady=5, fill="both", expand=True)

        self.log_area = scrolledtext.ScrolledText(log_frame, height=12, width=76)
        self.log_area.pack(padx=5, pady=5, fill="both", expand=True)
        self.log_area.config(state="disabled")

    def add_grid_entry(self, parent, label, default, row, column, width=8):
        ttk.Label(parent, text=label).grid(row=row, column=column, padx=5, pady=5, sticky="e")
        entry = ttk.Entry(parent, width=width)
        entry.grid(row=row, column=column + 1, padx=5, pady=5)
        entry.insert(0, default)
        return entry

    def refresh_ports(self):
        ports = [] if serial is None else [p.device for p in serial.tools.list_ports.comports()]
        self.port_cb["values"] = ports
        if ports and not self.port_var.get():
            self.port_cb.current(0)

    def log(self, message):
        self.log_area.config(state="normal")
        self.log_area.insert("end", message + "\n")
        self.log_area.see("end")
        self.log_area.config(state="disabled")

    def log_from_thread(self, message):
        self.root.after(0, self.log, message)

    def toggle_connection(self):
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            self.connect_btn.config(text="Connect")
            self.port_cb.config(state="readonly")
            self.status_var.set("Disconnected")
            self.log("Disconnected.")
            return

        port = self.port_var.get()
        if not port:
            self.log("Select a serial port first.")
            return

        try:
            self.serial_port = serial.Serial(port, BAUD_RATE, timeout=0.1)
            self.connect_btn.config(text="Disconnect")
            self.port_cb.config(state="disabled")
            self.status_var.set(f"Connected to {port}")
            self.log(f"Connected to {port} at {BAUD_RATE} baud.")
            self.wait_for_ready_async()
        except Exception as exc:
            self.log(f"Error connecting: {exc}")

    def wait_for_ready_async(self):
        def task():
            # Opening an ESP32 serial port often toggles DTR and reboots it.
            # READY confirms setup() completed and stale boot text is drained.
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                line = self.read_line(0.25)
                if line == "READY":
                    self.log_from_thread("Device ready.")
                    return
            self.log_from_thread("Connected; READY was not seen before timeout.")

        threading.Thread(target=task, daemon=True).start()

    def read_line(self, timeout_seconds):
        if not self.serial_port or not self.serial_port.is_open:
            return None

        original_timeout = self.serial_port.timeout
        try:
            self.serial_port.timeout = timeout_seconds
            raw = self.serial_port.readline()
        finally:
            self.serial_port.timeout = original_timeout

        if not raw:
            return None
        return raw.decode("ascii", errors="replace").strip()

    def send_cmd_with_timeout(self, cmd_str, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
        if not self.serial_port or not self.serial_port.is_open:
            raise RuntimeError("Not connected")

        with self.serial_lock:
            self.serial_port.reset_input_buffer()
            self.serial_port.write(f"{cmd_str}\n".encode("ascii"))

            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                line = self.read_line(0.25)
                if not line:
                    continue
                if line == "READY":
                    self.log_from_thread("Ignored startup READY while waiting for command response.")
                    continue
                return line

        raise TimeoutError(f"No response for command {cmd_str!r}")

    def run_serial_task(self, description, task):
        if self.busy:
            self.log("Serial command already in progress.")
            return

        self.busy = True
        self.status_var.set(description)

        def worker():
            try:
                task()
            except ValueError as exc:
                self.log_from_thread(f"Invalid input: {exc}")
            except Exception as exc:
                self.log_from_thread(f"Communication error: {exc}")
            finally:
                self.root.after(0, self.finish_serial_task)

        threading.Thread(target=worker, daemon=True).start()

    def finish_serial_task(self):
        self.busy = False
        if self.serial_port and self.serial_port.is_open:
            self.status_var.set("Connected")
        else:
            self.status_var.set("Disconnected")

    def parse_int(self, text, *, default_base=10, name="value"):
        value_text = text.strip().lower()
        if not value_text:
            raise ValueError(f"{name} is empty")
        if value_text.startswith("0b"):
            return int(value_text[2:], 2)
        if value_text.startswith("0x"):
            return int(value_text[2:], 16)
        return int(value_text, default_base)

    def parse_byte(self, text, name):
        value = self.parse_int(text, default_base=16, name=name)
        if not 0 <= value <= 0xFF:
            raise ValueError(f"{name} must be 0..255")
        return value

    def parse_pixel(self, text, name, minimum, maximum):
        value = self.parse_int(text, default_base=10, name=name)
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} must be {minimum}..{maximum}")
        return value

    def expect_ok(self, response, action):
        if response == "OK":
            return
        if response and response.startswith("ERR"):
            raise RuntimeError(response)
        raise RuntimeError(f"{action} failed: unexpected response {response!r}")

    def selected_transfer_mode(self):
        # Tokens match src/main.cpp's M/S/B command parser.
        return "SPI" if self.transfer_mode_var.get().startswith("SPI") else "QSPI"

    def read_reg(self):
        def task():
            reg = self.parse_byte(self.read_reg_entry.get(), "register")
            response = self.send_cmd_with_timeout(f"R {reg:X}")
            if response and not response.startswith("ERR"):
                value = int(response, 16)
                self.log_from_thread(f"READ  Reg 0x{reg:02X} -> Hex: 0x{value:02X} | Bin: 0b{value:08b}")
            else:
                raise RuntimeError(response or "read timed out")

        self.run_serial_task("Reading register", task)

    def write_reg(self):
        def task():
            reg = self.parse_byte(self.write_reg_entry.get(), "register")
            value = self.parse_byte(self.write_val_entry.get(), "value")
            response = self.send_cmd_with_timeout(f"W {reg:X} {value:X}")
            self.expect_ok(response, "write")
            self.log_from_thread(f"WRITE Reg 0x{reg:02X} <- Hex: 0x{value:02X} | Bin: 0b{value:08b}")

        self.run_serial_task("Writing register", task)

    def trigger_reset(self):
        def task():
            response = self.send_cmd_with_timeout("X")
            self.expect_ok(response, "reset")
            self.log_from_thread("Hardware reset triggered.")

        self.run_serial_task("Resetting display", task)

    def apply_transfer_mode(self):
        def task():
            mode = self.selected_transfer_mode()
            response = self.send_cmd_with_timeout(f"M {mode}")
            self.expect_ok(response, "transfer mode set")
            self.log_from_thread(f"Display transfer mode set to {mode}.")

        self.run_serial_task("Applying transfer mode", task)

    def read_transfer_mode(self):
        def task():
            response = self.send_cmd_with_timeout("M")
            if response in ("SPI", "QSPI"):
                self.root.after(0, self.transfer_mode_var.set, "SPI 1-line" if response == "SPI" else "QSPI 4-line")
                self.log_from_thread(f"Display transfer mode is {response}.")
            else:
                raise RuntimeError(response or "mode query timed out")

        self.run_serial_task("Reading transfer mode", task)

    def apply_spi_pattern(self):
        pattern_values = {
            "Disabled": 0x00,
            "Black": 0x80,
            "White screen": 0x81,
            "2x2": 0x88,
            "4x4": 0x89,
        }

        def task():
            selection = self.spi_pattern_var.get()
            value = next((val for key, val in pattern_values.items() if key in selection), None)
            if value is None:
                raise ValueError("unknown SPI pattern")
            response = self.send_cmd_with_timeout(f"W 4 {value:X}")
            self.expect_ok(response, "SPI pattern write")
            self.log_from_thread(f"SPI pattern set: {selection} -> Reg 0x04 = 0x{value:02X}")

        self.run_serial_task("Applying SPI pattern", task)

    def apply_asic_pattern(self):
        pattern_values = {
            "Disable": 0x00,
            "Diag bright": 0x10,
            "Diag dark": 0x20,
            "8 grayscale": 0x30,
            "vert grayscale": 0x40,
            "horiz grayscale": 0x50,
            "Checkerboard pattern": 0x60,
            "Outer bright lines": 0x70,
        }

        def task():
            selection = self.asic_pattern_var.get()
            value = next((val for key, val in pattern_values.items() if key in selection), None)
            if value is None:
                raise ValueError("unknown ASIC pattern")
            response = self.send_cmd_with_timeout(f"W 1B {value:X}")
            self.expect_ok(response, "ASIC pattern write")
            self.log_from_thread(f"ASIC pattern set: {selection} -> Reg 0x1B = 0x{value:02X}")

        self.run_serial_task("Applying ASIC pattern", task)

    def draw_square(self):
        def task():
            x = self.parse_pixel(self.square_x_entry.get(), "x", 0, DISPLAY_WIDTH - 1)
            y = self.parse_pixel(self.square_y_entry.get(), "y", 0, DISPLAY_HEIGHT - 1)
            size = self.parse_pixel(self.square_size_entry.get(), "size", 1, max(DISPLAY_WIDTH, DISPLAY_HEIGHT))
            intensity = self.parse_byte(self.square_intensity_entry.get(), "intensity")
            mode = self.selected_transfer_mode()

            response = self.send_cmd_with_timeout(f"S {x} {y} {size} {intensity} {mode}", FRAME_TIMEOUT_SECONDS)
            self.expect_ok(response, "square draw")
            self.log_from_thread(
                f"Draw {mode} square at ({x}, {y}) size {size} px "
                f"-> Intensity Hex: 0x{intensity:02X} | Bin: 0b{intensity:08b}"
            )

        self.run_serial_task("Drawing display square", task)

    def send_checkerboard(self):
        def task():
            block_size = self.parse_pixel(self.checker_size_entry.get(), "block size", 1, DISPLAY_WIDTH)
            brightness = self.parse_byte(self.checker_brightness_entry.get(), "grid brightness")
            mode = self.selected_transfer_mode()
            response = self.send_cmd_with_timeout(f"B {block_size} {brightness} {mode}", FRAME_TIMEOUT_SECONDS)
            self.expect_ok(response, "checkerboard frame")
            self.log_from_thread(
                f"Full-screen {mode} checkerboard sent with block size {block_size} px "
                f"and brightness 0x{brightness:02X}"
            )

        self.run_serial_task("Sending checkerboard", task)



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
        self.root.title("microLED Driver")
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

        self.disk_vars = {name: tk.StringVar(value=str(value)) for name, value in
                          (("x", 320), ("y", 240), ("diameter", 100), ("brightness", 32))}
        self.command_queue = []
        self.command_busy = False
        self.build_ui()
        self.preview_disk()
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

        ttk.Button(controls, text="Advanced register tools…", command=self.open_advanced).grid(
            row=12, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

        disk = ttk.LabelFrame(controls, text="Disk (filled circle)", padding=6)
        disk.grid(row=13, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Label(disk, text="Click or drag in the preview to position.\n↑ / ↓ brightness    ← / → diameter\nHold Shift for steps of 10.").grid(row=0, column=0, columnspan=2, sticky="w")
        for row, (name, label) in enumerate((("x", "Center X"), ("y", "Center Y"),
                                           ("diameter", "Diameter (px)"), ("brightness", "Brightness")), 1):
            ttk.Label(disk, text=label).grid(row=row, column=0, sticky="w")
            ttk.Label(disk, textvariable=self.disk_vars[name]).grid(row=row, column=1, sticky="e")
        ttk.Button(disk, text="Show disk", command=self.show_disk).grid(row=5, column=0, pady=5)
        ttk.Button(disk, text="Turn off", command=self.clear_disk).grid(row=5, column=1, pady=5)

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
        self.canvas.configure(takefocus=True, cursor="crosshair")
        self.canvas.bind("<Button-1>", self.move_disk)
        self.canvas.bind("<B1-Motion>", self.move_disk)
        for key in ("Up", "Down", "Left", "Right"):
            self.canvas.bind(f"<{key}>", self.adjust_disk)
        ttk.Label(preview, text="Click here, then use arrow keys to adjust the disk").pack(pady=(6, 0))
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
            self.command_queue.clear()
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
            self.running = False
            self.command_queue.clear()
            self.unknown_preview()
            self.status_var.set("Connected; confirming panel output…")
            self.send_command_async("D 320 240 1 0", timeout=FRAME_TIMEOUT_SECONDS)
        except Exception as exc:
            self.status_var.set(f"Connection failed: {exc}")

    def open_advanced(self):
        self.stop()
        if self.serial_port and self.serial_port.is_open:
            self.toggle_connection()
        window = tk.Toplevel(self.root)
        advanced = SPIControllerApp(window)
        def close():
            if advanced.serial_port and advanced.serial_port.is_open:
                advanced.serial_port.close()
            window.destroy()
        window.protocol("WM_DELETE_WINDOW", close)
        window.grab_set()
        self.root.wait_window(window)
        self.status_var.set("Advanced tools closed; reconnect to resume hardware control")

    def hardware_connected(self):
        return self.serial_port is not None and self.serial_port.is_open

    def unknown_preview(self):
        self.canvas.delete("all")
        self.preview_item = None
        self.preview_image = None
        self.canvas.create_text(PREVIEW_WIDTH // 2, PREVIEW_HEIGHT // 2,
                                text="Panel output unconfirmed", fill="white")

    def request_animation_frame(self):
        animation, fps, size, intensity, speed, direction = self.settings()
        frame = min(10000000, int((time.monotonic() - self.started_at) * fps))
        self.send_command_async(f"F {animation} {size} {intensity} {speed} {direction} {frame}",
                                timeout=FRAME_TIMEOUT_SECONDS)

    def move_disk(self, event):
        self.disk_vars["x"].set(str(max(0, min(PANEL_WIDTH - 1, int(event.x * PREVIEW_SCALE)))))
        self.disk_vars["y"].set(str(max(0, min(PANEL_HEIGHT - 1, int(event.y * PREVIEW_SCALE)))))
        self.show_disk()
        return "break"

    def adjust_disk(self, event):
        name = "brightness" if event.keysym in ("Up", "Down") else "diameter"
        step = 10 if event.state & 0x0001 else 1
        if event.keysym in ("Down", "Left"):
            step = -step
        low, high = (0, 255) if name == "brightness" else (1, PANEL_WIDTH)
        value = max(low, min(high, int(self.disk_vars[name].get()) + step))
        self.disk_vars[name].set(str(value))
        self.show_disk()
        return "break"

    def disk_settings(self):
        values = tuple(int(self.disk_vars[name].get()) for name in ("x", "y", "diameter", "brightness"))
        for value, low, high in zip(values, (0, 0, 1, 0), (639, 479, 640, 255)):
            if not low <= value <= high:
                raise ValueError("Disk: X 0–639, Y 0–479, diameter 1–640, brightness 0–255")
        return values

    @staticmethod
    def render_disk(x, y, diameter, brightness):
        # Render every panel pixel, then retain the brightest pixel in each
        # 2x2 preview cell so even a one-pixel disk remains visible.
        output = bytearray(PREVIEW_WIDTH * PREVIEW_HEIGHT)
        offset = 1 if diameter % 2 == 0 else 0
        for py in range(max(0, y - diameter), min(PANEL_HEIGHT, y + diameter + 1)):
            dy = 2 * (py - y) - offset
            for px in range(max(0, x - diameter), min(PANEL_WIDTH, x + diameter + 1)):
                dx = 2 * (px - x) - offset
                if dx * dx + dy * dy < diameter * diameter:
                    output[(py // PREVIEW_SCALE) * PREVIEW_WIDTH + px // PREVIEW_SCALE] = brightness
        return output

    def display_preview(self, pixels):
        pixels = bytes(pixels).translate(PREVIEW_BRIGHTNESS_LUT)
        rgb = bytearray(len(pixels) * 3)
        rgb[0::3] = rgb[1::3] = rgb[2::3] = pixels
        ppm = f"P6\n{PREVIEW_WIDTH} {PREVIEW_HEIGHT}\n255\n".encode("ascii") + rgb
        image = tk.PhotoImage(data=ppm, format="PPM")
        if self.preview_item is None:
            self.canvas.delete("all")
        if self.preview_item is None:
            self.preview_item = self.canvas.create_image(0, 0, image=image, anchor="nw")
        else:
            self.canvas.itemconfigure(self.preview_item, image=image)
        self.preview_image = image

    def preview_disk(self):
        try:
            settings = self.disk_settings()
        except ValueError as exc:
            self.status_var.set(str(exc) or "Enter whole numbers for disk settings")
            return
        # Editing previews a draft without changing the connected panel.
        self.running = False
        if self.hardware_connected():
            return
        self.display_preview(self.render_disk(*settings))
        self.status_var.set("Click or drag the preview; arrow keys change brightness and diameter")

    def show_disk(self):
        self.canvas.focus_set()
        try:
            x, y, diameter, brightness = self.disk_settings()
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        self.running = False
        if not self.hardware_connected():
            self.display_preview(self.render_disk(x, y, diameter, brightness))
        self.send_command_async(f"D {x} {y} {diameter} {brightness}", timeout=FRAME_TIMEOUT_SECONDS)

    def clear_disk(self):
        self.running = False
        if not self.hardware_connected():
            self.display_preview(bytearray(PREVIEW_WIDTH * PREVIEW_HEIGHT))
        self.send_command_async("D 320 240 1 0", timeout=FRAME_TIMEOUT_SECONDS)

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
            if self.hardware_connected():
                self.request_animation_frame()
            else:
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
            if self.hardware_connected():
                self.request_animation_frame()
            else:
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
        if self.hardware_connected():
            self.request_animation_frame()
        else:
            self.send_command_async(f"A {animation} {fps} {size} {intensity} {speed} {direction}")

    def stop(self):
        self.running = False
        self.send_command_async("A STOP")

    def trigger_reset(self):
        self.running = False
        self.send_command_async("X")

    def send_command_async(self, command, timeout=1.0):
        if not self.serial_port or not self.serial_port.is_open:
            self.status_var.set(f"Previewing locally: {command}")
            return
        # Keep only the latest pending disk position during dragging or key repeat.
        # Other commands retain their order, including Play and Turn off.
        if command.startswith(("D ", "F ")):
            while self.command_queue and self.command_queue[-1][0].startswith(("D ", "F ")):
                self.command_queue.pop()
        self.command_queue.append((command, timeout, self.serial_port))
        self.dispatch_command()

    def dispatch_command(self):
        if self.command_busy or not self.command_queue:
            return
        command, timeout, port = self.command_queue.pop(0)
        if port is not self.serial_port or not port.is_open:
            self.dispatch_command()
            return
        self.command_busy = True

        def worker():
            try:
                with self.serial_lock:
                    port.reset_input_buffer()
                    port.write((command + "\n").encode("ascii"))
                    original_timeout = port.timeout
                    try:
                        port.timeout = timeout
                        deadline = time.monotonic() + timeout
                        response = ""
                        while time.monotonic() < deadline:
                            port.timeout = max(0.01, deadline - time.monotonic())
                            line = port.readline().decode("ascii", errors="replace").strip()
                            if line == "OK" or line.startswith("ERR"):
                                response = line
                                break
                    finally:
                        port.timeout = original_timeout
                message = f"Device: {response or 'no response'} | {command}"
            except Exception as exc:
                response = ""
                message = f"Communication error: {exc}"
            self.root.after(0, self.command_finished, command, port, response, message)

        threading.Thread(target=worker, daemon=True).start()

    def command_finished(self, command, port, response, message):
        self.command_busy = False
        if port is self.serial_port and port.is_open:
            if response == "OK":
                parts = command.split()
                if parts[0] == "D":
                    self.display_preview(self.render_disk(*map(int, parts[1:])))
                elif parts[0] == "F":
                    animation, size, intensity, speed, direction, frame = parts[1:]
                    self.display_preview(self.render_preview(animation, int(size), int(intensity),
                                         int(speed), direction, int(frame)))
                elif parts[0] == "X":
                    self.unknown_preview()
            else:
                # A failed transfer may have changed only part of the panel.
                self.unknown_preview()
                self.running = False
                self.command_queue.clear()
                if not response:
                    # Never let a late acknowledgement confirm a later command.
                    port.close()
                    self.serial_port = None
                    self.connect_button.config(text="Connect")
                    message += " — disconnected; reconnect to confirm output"
            self.status_var.set(message)
        self.dispatch_command()

    def preview_tick(self):
        try:
            if self.running and self.hardware_connected():
                if not self.command_busy and not self.command_queue:
                    self.request_animation_frame()
            elif self.running:
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
                    jay_left = int((PANEL_WIDTH - word_width) / 2) if vertical else jay_position
                    jay_top = jay_position if vertical else int((PANEL_HEIGHT - word_height) / 2)
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
    app = AnimationController(window)
    def close():
        if app.serial_port and app.serial_port.is_open:
            app.serial_port.close()
        window.destroy()
    window.protocol("WM_DELETE_WINDOW", close)
    window.mainloop()
