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

        self.port_var = tk.StringVar()
        self.animation_var = tk.StringVar(value="CHECKER")
        self.fps_var = tk.IntVar(value=10)
        self.size_var = tk.IntVar(value=32)
        self.intensity_var = tk.IntVar(value=255)
        self.speed_var = tk.IntVar(value=4)
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
            values=("CHECKER", "BARS", "GRADIENT", "SQUARE"),
            width=12,
            state="readonly",
        ))
        self.add_field(controls, 5, "FPS", ttk.Spinbox(controls, from_=1, to=120, textvariable=self.fps_var, width=10))
        self.add_field(controls, 6, "Size", ttk.Spinbox(controls, from_=1, to=640, textvariable=self.size_var, width=10))
        self.add_field(controls, 7, "Intensity", ttk.Spinbox(controls, from_=0, to=255, textvariable=self.intensity_var, width=10))
        self.add_field(controls, 8, "Speed", ttk.Spinbox(controls, from_=-100, to=100, textvariable=self.speed_var, width=10))

        ttk.Button(controls, text="Play", command=self.play).grid(row=9, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(controls, text="Stop", command=self.stop).grid(row=9, column=1, sticky="ew", pady=(12, 0))

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
        fps = max(1, min(120, self.fps_var.get()))
        size = max(1, min(PANEL_WIDTH, self.size_var.get()))
        intensity = max(0, min(255, self.intensity_var.get()))
        speed = max(-100, min(100, self.speed_var.get()))
        return self.animation_var.get(), fps, size, intensity, speed

    def play(self):
        animation, fps, size, intensity, speed = self.settings()
        self.running = True
        self.frame = 0
        self.started_at = time.monotonic()
        self.send_command_async(f"A {animation} {fps} {size} {intensity} {speed}")

    def stop(self):
        self.running = False
        self.send_command_async("A STOP")

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
        if self.running:
            animation, fps, size, intensity, speed = self.settings()
            self.frame = int((time.monotonic() - self.started_at) * fps)
            pixels = self.render_preview(animation, size, intensity, speed, self.frame)
            pgm = f"P5\n{PREVIEW_WIDTH} {PREVIEW_HEIGHT}\n255\n".encode("ascii") + pixels
            self.preview_image = tk.PhotoImage(data=pgm, format="PGM")
            if self.preview_item is None:
                self.preview_item = self.canvas.create_image(0, 0, image=self.preview_image, anchor="nw")
            else:
                self.canvas.itemconfigure(self.preview_item, image=self.preview_image)
        self.root.after(33, self.preview_tick)

    @staticmethod
    def render_preview(animation, size, intensity, speed, frame):
        output = bytearray(PREVIEW_WIDTH * PREVIEW_HEIGHT)
        phase = frame * speed
        side = min(size, PANEL_HEIGHT)
        travel = max(1, PANEL_WIDTH - side)
        left = phase % (2 * travel)
        if left > travel:
            left = 2 * travel - left
        top = (PANEL_HEIGHT - side) // 2

        index = 0
        for py in range(PREVIEW_HEIGHT):
            y = py * PREVIEW_SCALE
            for px in range(PREVIEW_WIDTH):
                x = px * PREVIEW_SCALE
                if animation == "CHECKER":
                    value = 0 if (((x + phase) // size + y // size) & 1) else intensity
                elif animation == "BARS":
                    value = intensity if (x + phase) % (2 * size) < size else 0
                elif animation == "GRADIENT":
                    value = (x + y + phase) & 0xFF
                else:
                    value = intensity if left <= x < left + side and top <= y < top + side else 0
                output[index] = value
                index += 1
        return bytes(output)


if __name__ == "__main__":
    window = tk.Tk()
    AnimationController(window)
    window.mainloop()
