import threading
import time
import tkinter as tk
from tkinter import scrolledtext, ttk

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

        self.square_x_entry = self.add_grid_entry(pixel_frame, "X px:", "0", 0, 0)
        self.square_y_entry = self.add_grid_entry(pixel_frame, "Y px:", "0", 0, 2)
        self.square_size_entry = self.add_grid_entry(pixel_frame, "N px:", "8", 0, 4)
        self.square_intensity_entry = self.add_grid_entry(pixel_frame, "Intensity:", "0xFF", 0, 6, width=10)
        ttk.Button(pixel_frame, text="Draw Square", command=self.draw_square).grid(row=0, column=8, padx=5, pady=5)

        checker_frame = ttk.LabelFrame(self.root, text="Display Checkerboard Frame")
        checker_frame.pack(padx=10, pady=5, fill="x")

        ttk.Label(checker_frame, text="Block size px:").pack(side="left", padx=5, pady=5)
        self.checker_size_entry = ttk.Entry(checker_frame, width=8)
        self.checker_size_entry.pack(side="left", padx=5, pady=5)
        self.checker_size_entry.insert(0, "4")
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

    def send_checkerboard(self):
        def task():
            block_size = self.parse_pixel(self.checker_size_entry.get(), "block size", 1, DISPLAY_WIDTH)
            mode = self.selected_transfer_mode()
            response = self.send_cmd_with_timeout(f"B {block_size} {mode}", FRAME_TIMEOUT_SECONDS)
            self.expect_ok(response, "checkerboard frame")
            self.log_from_thread(f"Full-screen {mode} checkerboard sent with block size {block_size} px")

        self.run_serial_task("Sending checkerboard", task)


if __name__ == "__main__":
    root = tk.Tk()
    app = SPIControllerApp(root)
    root.mainloop()
