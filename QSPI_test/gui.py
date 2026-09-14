import threading
import time
import tkinter as tk
from tkinter import scrolledtext, ttk

import json
from tkinter import filedialog

import serial
import serial.tools.list_ports


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

        grid_frame = ttk.LabelFrame(self.root, text="Display Grid of Squares")
        grid_frame.pack(padx=10, pady=5, fill="x")
        ttk.Button(grid_frame, text="Open Alignment Grid", command=self.open_alignment_grid).grid(row=0, column=7, padx=5, pady=5)

#START GPT

        self.grid_n_entry = self.add_grid_entry(grid_frame, "Grid NxN:", "2", 0, 0)
        self.grid_size_entry = self.add_grid_entry(grid_frame, "Square Size px:", "1", 0, 2)
        self.grid_intensity_entry = self.add_grid_entry(grid_frame, "Intensity:", "0xFF", 0, 4, width=10)
        ttk.Button(grid_frame, text="Draw Grid", command=self.draw_grid).grid(row=0, column=6, padx=5, pady=5)
#END GPT

        self.square_x_entry = self.add_grid_entry(pixel_frame, "X px:", "320", 0, 0)
        self.square_y_entry = self.add_grid_entry(pixel_frame, "Y px:", "240", 0, 2)
        self.square_size_entry = self.add_grid_entry(pixel_frame, "N px:", "1", 0, 4)
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
        ports = [p.device for p in serial.tools.list_ports.comports()]
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

#START GPT
    def draw_grid(self):
        def task():
            n = self.parse_pixel(self.grid_n_entry.get(), "N", 1, min(DISPLAY_WIDTH, DISPLAY_HEIGHT))
            size = self.parse_pixel(self.grid_size_entry.get(), "size", 1, max(DISPLAY_WIDTH, DISPLAY_HEIGHT))
            intensity = self.parse_byte(self.grid_intensity_entry.get(), "intensity")
            
            # Geometry Validation: Ensure square fits in perfectly square cell
            min_dim = min(DISPLAY_WIDTH, DISPLAY_HEIGHT)
            cell_size = min_dim // n
            if size > cell_size:
                raise ValueError(f"Square size ({size}) exceeds perfectly square grid cell limits ({cell_size}x{cell_size})")
            mode = self.selected_transfer_mode()

            response = self.send_cmd_with_timeout(f"G {n} {size} {intensity} {mode}", FRAME_TIMEOUT_SECONDS)
            self.expect_ok(response, "grid draw")
            self.log_from_thread(
                f"Draw {mode} grid {n}x{n} with square size {size} px "
                f"-> Intensity Hex: 0x{intensity:02X} | Bin: 0b{intensity:08b}"
            )

        self.run_serial_task("Drawing display grid", task)
    #END GPT
    
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

    def open_alignment_grid(self):
        try:
            n = self.parse_pixel(self.grid_n_entry.get(), "N", 1, min(DISPLAY_WIDTH, DISPLAY_HEIGHT))
            size = self.parse_pixel(self.grid_size_entry.get(), "size", 1, max(DISPLAY_WIDTH, DISPLAY_HEIGHT))
            intensity = self.parse_byte(self.grid_intensity_entry.get(), "intensity")
            mode = self.selected_transfer_mode()
            
            # Validation: N*N cannot exceed the ESP32's MAX_POINTS (250)
            if n * n > 250:
                raise ValueError(f"An {n}x{n} grid requires {n*n} points, exceeding the 250 hardware limit.")
                
            cell_w = DISPLAY_WIDTH // n
            cell_h = DISPLAY_HEIGHT // n
            if size > cell_w or size > cell_h:
                raise ValueError(f"Square size ({size}) exceeds base grid limits ({cell_w}x{cell_h})")
                
            AlignmentGridWindow(self, n, size, intensity, mode)
            
        except ValueError as exc:
            self.log(f"Invalid parameters: {exc}")
class AlignmentGridWindow:
    def __init__(self, parent_app, n, size, intensity, mode):
        self.app = parent_app
        self.n = n
        self.default_size = size
        self.intensity = intensity
        self.mode = mode
        
        self.window = tk.Toplevel(parent_app.root)
        self.window.title(f"Optical Alignment Grid ({n}x{n})")
        
        # File IO Toolbar
        toolbar = ttk.Frame(self.window)
        toolbar.pack(fill="x", padx=10, pady=5)
        ttk.Button(toolbar, text="Load Config", command=self.load_config).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Save Config", command=self.save_config).pack(side="left", padx=5)
        ttk.Label(toolbar, text="Arrows: Nudge | +/-: Resize | T: Toggle | Space: Push").pack(side="right", padx=5)
        
        # Canvas
        self.canvas = tk.Canvas(self.window, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT, bg="black")
        self.canvas.pack(padx=10, pady=5)
        
        # Control Panel - Row 1 (Inputs)
        control_frame = ttk.Frame(self.window)
        control_frame.pack(fill="x", padx=10, pady=2)
        
        ttk.Label(control_frame, text="X:").pack(side="left", padx=2)
        self.x_var = tk.StringVar()
        self.x_entry = ttk.Entry(control_frame, textvariable=self.x_var, width=5)
        self.x_entry.pack(side="left", padx=2)
        
        ttk.Label(control_frame, text="Y:").pack(side="left", padx=2)
        self.y_var = tk.StringVar()
        self.y_entry = ttk.Entry(control_frame, textvariable=self.y_var, width=5)
        self.y_entry.pack(side="left", padx=2)
        
        ttk.Label(control_frame, text="Size:").pack(side="left", padx=2)
        self.size_var = tk.StringVar()
        self.size_entry = ttk.Entry(control_frame, textvariable=self.size_var, width=5)
        self.size_entry.pack(side="left", padx=2)
        
        ttk.Button(control_frame, text="Apply", command=self.apply_manual_inputs).pack(side="left", padx=2)
        ttk.Button(control_frame, text="Apply Size to All", command=self.apply_size_to_all).pack(side="left", padx=10)
        ttk.Button(control_frame, text="Toggle On/Off", command=self.toggle_active).pack(side="left", padx=2)
        
        # Control Panel - Row 2 (Actions)
        action_frame = ttk.Frame(self.window)
        action_frame.pack(fill="x", padx=10, pady=5)
        ttk.Button(action_frame, text="Push to Display", command=self.send_payload).pack(side="right", padx=2)
        ttk.Button(action_frame, text="Reset Grid", command=self.reset_grid).pack(side="right", padx=10)
        
        self.squares = []
        self.selected_idx = None
        
        self.reset_grid()
        
        # Event Bindings
        self.canvas.bind("<Button-1>", self.on_click)
        
        # Movement & Toggles
        self.window.bind("<Up>", lambda e: self.nudge(0, -1))
        self.window.bind("<Down>", lambda e: self.nudge(0, 1))
        self.window.bind("<Left>", lambda e: self.nudge(-1, 0))
        self.window.bind("<Right>", lambda e: self.nudge(1, 0))
        self.window.bind("t", self.toggle_active)
        self.window.bind("T", self.toggle_active)
        self.window.bind("<space>", self.send_payload)
        
        # Resize keys
        self.window.bind("<plus>", lambda e: self.adjust_size(1))
        self.window.bind("<equal>", lambda e: self.adjust_size(1))
        self.window.bind("<minus>", lambda e: self.adjust_size(-1))
        
        # Bind enter key in entries to apply
        self.x_entry.bind("<Return>", self.apply_manual_inputs)
        self.y_entry.bind("<Return>", self.apply_manual_inputs)
        self.size_entry.bind("<Return>", self.apply_manual_inputs)
        
        self.window.focus_set()

    def update_square_visual(self, idx):
        """Helper to consistently style squares based on selection and active state."""
        sq = self.squares[idx]
        
        # If disabled, make it hollow so the user knows it won't render
        is_active = sq.get('active', True)
        fill_color = f"#{self.intensity:02X}{self.intensity:02X}{self.intensity:02X}" if is_active else ""
        
        outline_color = "red" if idx == self.selected_idx else ("gray" if is_active else "#444444")
        line_width = 2 if idx == self.selected_idx else 1
        
        self.canvas.itemconfig(sq['id'], fill=fill_color, outline=outline_color, width=line_width)
        self.canvas.coords(sq['id'], sq['x'], sq['y'], sq['x'] + sq['size'], sq['y'] + sq['size'])

    def reset_grid(self):
        self.canvas.delete("all")
        self.squares.clear()
        self.selected_idx = None
        self.x_var.set("")
        self.y_var.set("")
        self.size_var.set("")
        
        cell_w = DISPLAY_WIDTH // self.n
        cell_h = DISPLAY_HEIGHT // self.n
        pad_x = (cell_w - self.default_size) // 2
        pad_y = (cell_h - self.default_size) // 2
        
        for row in range(self.n):
            for col in range(self.n):
                x = col * cell_w + pad_x
                y = row * cell_h + pad_y
                rect_id = self.canvas.create_rectangle(0, 0, 0, 0) # Coords set by visual updater
                self.squares.append({'id': rect_id, 'x': x, 'y': y, 'size': self.default_size, 'active': True})
                self.update_square_visual(len(self.squares) - 1)

    def on_click(self, event):
        prev_idx = self.selected_idx
        
        # Find which square was clicked (manual coordinate check so hollow squares are still clickable)
        clicked_idx = None
        for idx, sq in enumerate(self.squares):
            if sq['x'] <= event.x <= sq['x'] + sq['size'] and sq['y'] <= event.y <= sq['y'] + sq['size']:
                clicked_idx = idx
                break
                
        if clicked_idx is not None:
            self.selected_idx = clicked_idx
            if prev_idx is not None and prev_idx != clicked_idx:
                self.update_square_visual(prev_idx) # Deselect previous
                
            self.update_square_visual(self.selected_idx)
            
            sq = self.squares[self.selected_idx]
            self.x_var.set(str(sq['x']))
            self.y_var.set(str(sq['y']))
            self.size_var.set(str(sq['size']))
            self.window.focus_set() 

    def toggle_active(self, event=None):
        if self.selected_idx is None: return
        sq = self.squares[self.selected_idx]
        sq['active'] = not sq.get('active', True)
        self.update_square_visual(self.selected_idx)

    def apply_manual_inputs(self, event=None):
        if self.selected_idx is None: return
        try:
            new_x = int(self.x_var.get())
            new_y = int(self.y_var.get())
            new_size = int(self.size_var.get())
            
            new_size = max(1, min(DISPLAY_WIDTH, DISPLAY_HEIGHT, new_size))
            new_x = max(0, min(DISPLAY_WIDTH - new_size, new_x))
            new_y = max(0, min(DISPLAY_HEIGHT - new_size, new_y))
            
            sq = self.squares[self.selected_idx]
            sq['x'] = new_x
            sq['y'] = new_y
            sq['size'] = new_size
            
            self.update_square_visual(self.selected_idx)
            
            self.x_var.set(str(new_x))
            self.y_var.set(str(new_y))
            self.size_var.set(str(new_size))
            self.window.focus_set()
        except ValueError:
            pass 

    def apply_size_to_all(self):
        try:
            new_size = int(self.size_var.get())
            new_size = max(1, min(DISPLAY_WIDTH, DISPLAY_HEIGHT, new_size))
            
            for idx, sq in enumerate(self.squares):
                # Calculate current center point
                cx = sq['x'] + (sq['size'] // 2)
                cy = sq['y'] + (sq['size'] // 2)
                
                # Shift top-left corner to keep the center in the same place
                new_x = cx - (new_size // 2)
                new_y = cy - (new_size // 2)
                
                # Enforce hardware boundaries
                sq['x'] = max(0, min(DISPLAY_WIDTH - new_size, new_x))
                sq['y'] = max(0, min(DISPLAY_HEIGHT - new_size, new_y))
                sq['size'] = new_size
                
                self.update_square_visual(idx)
                
            # If a square is currently selected, update the manual entry boxes
            if self.selected_idx is not None:
                self.x_var.set(str(self.squares[self.selected_idx]['x']))
                self.y_var.set(str(self.squares[self.selected_idx]['y']))
                
            self.size_var.set(str(new_size))
            self.window.focus_set()
        except ValueError:
            pass

    def adjust_size(self, d_size):
        if self.selected_idx is None: return
        sq = self.squares[self.selected_idx]
        
        new_size = sq['size'] + d_size
        new_size = max(1, min(DISPLAY_WIDTH, DISPLAY_HEIGHT, new_size))
        
        if new_size == sq['size']: return # Hit min/max limit
        
        # Calculate current center point
        cx = sq['x'] + (sq['size'] // 2)
        cy = sq['y'] + (sq['size'] // 2)
        
        # Shift top-left corner to keep the center in the same place
        new_x = cx - (new_size // 2)
        new_y = cy - (new_size // 2)
        
        # Enforce hardware boundaries
        sq['x'] = max(0, min(DISPLAY_WIDTH - new_size, new_x))
        sq['y'] = max(0, min(DISPLAY_HEIGHT - new_size, new_y))
        sq['size'] = new_size
        
        self.update_square_visual(self.selected_idx)
        self.size_var.set(str(new_size))
        self.x_var.set(str(sq['x']))
        self.y_var.set(str(sq['y']))

    def nudge(self, dx, dy):
        if self.selected_idx is None: return
        sq = self.squares[self.selected_idx]
        
        sq['x'] = max(0, min(DISPLAY_WIDTH - sq['size'], sq['x'] + dx))
        sq['y'] = max(0, min(DISPLAY_HEIGHT - sq['size'], sq['y'] + dy))
        
        self.update_square_visual(self.selected_idx)
        self.x_var.set(str(sq['x']))
        self.y_var.set(str(sq['y']))

    def adjust_size(self, d_size):
        if self.selected_idx is None: return
        sq = self.squares[self.selected_idx]
        
        new_size = max(1, min(DISPLAY_WIDTH - sq['x'], DISPLAY_HEIGHT - sq['y'], sq['size'] + d_size))
        sq['size'] = new_size
        
        self.update_square_visual(self.selected_idx)
        self.size_var.set(str(new_size))

    def save_config(self):
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON Files", "*.json")], title="Save Alignment Configuration"
        )
        if not filepath: return
        
        data = {
            "n": self.n,
            "intensity": self.intensity,
            "coordinates": [{"x": sq['x'], "y": sq['y'], "size": sq['size'], "active": sq.get('active', True)} for sq in self.squares]
        }
        try:
            with open(filepath, 'w') as f: json.dump(data, f, indent=4)
            self.app.log(f"Saved alignment config to {filepath}")
        except Exception as e:
            self.app.log(f"Failed to save config: {e}")

    def load_config(self):
        filepath = filedialog.askopenfilename(
            filetypes=[("JSON Files", "*.json")], title="Load Alignment Configuration"
        )
        if not filepath: return
        
        try:
            with open(filepath, 'r') as f: data = json.load(f)
            self.n = data.get("n", self.n)
            self.intensity = data.get("intensity", self.intensity)
            
            self.canvas.delete("all")
            self.squares.clear()
            self.selected_idx = None
            self.x_var.set("")
            self.y_var.set("")
            self.size_var.set("")
            
            for pt in data.get("coordinates", []):
                rect_id = self.canvas.create_rectangle(0,0,0,0)
                self.squares.append({
                    'id': rect_id, 'x': pt['x'], 'y': pt['y'], 
                    'size': pt.get('size', self.default_size), 'active': pt.get('active', True)
                })
                self.update_square_visual(len(self.squares) - 1)
                
            self.app.log(f"Loaded alignment config: {filepath}")
            self.window.title(f"Optical Alignment Grid ({self.n}x{self.n}) - Loaded")
        except Exception as e:
            self.app.log(f"Failed to load config: {e}")

    def send_payload(self, event=None):
        def task():
            self.app.expect_ok(self.app.send_cmd_with_timeout("C"), "clear list")
            
            # Filter out inactive squares before sending
            active_squares = [sq for sq in self.squares if sq.get('active', True)]
            
            for sq in active_squares:
                cmd = f"P {sq['x']} {sq['y']} {sq['size']} {self.intensity:X}"
                self.app.expect_ok(self.app.send_cmd_with_timeout(cmd), f"place point {sq['x']},{sq['y']}")
                
            response = self.app.send_cmd_with_timeout(f"D {self.mode}", FRAME_TIMEOUT_SECONDS)
            self.app.expect_ok(response, "draw frame")
            self.app.log_from_thread(f"Pushed {len(active_squares)} active squares to display.")
            
        self.app.run_serial_task("Pushing alignment grid", task)


if __name__ == "__main__":
    root = tk.Tk()
    app = SPIControllerApp(root)
    root.mainloop()
