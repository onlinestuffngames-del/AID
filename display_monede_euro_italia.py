from __future__ import annotations

from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import math
from pathlib import Path
import site
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

user_site = site.getusersitepackages()
if user_site and user_site not in sys.path:
    sys.path.append(user_site)

import cv2  # type: ignore
import numpy as np  # type: ignore

from arduino_controller import ArduinoController
from camera_measurement import CameraMeasurement, MEASUREMENT_FRAMES
from coin_database import CoinDatabase, REQUIRED_HEADERS


CSV_PATH = Path(__file__).with_name("monede_euro_italia.csv")
IMAGE_FOLDER = Path(__file__).with_name("imagini_monede")
CAMERA_REFRESH_MS = 60
SITE_PORT_START = 8000
SITE_PORT_END = 8010


class NoCacheRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def compute_coin_volume_mm3(diameter_mm: float, thickness_mm: float | None) -> float | None:
    """Volume of a coin approximated as a cylinder: V = pi * r^2 * h."""
    if thickness_mm is None:
        return None
    if diameter_mm <= 0 or thickness_mm <= 0:
        return None
    radius_mm = diameter_mm / 2.0
    return math.pi * (radius_mm ** 2) * thickness_mm


class DeviceInterface:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Python UI - Camera + Catalog")
        self.root.geometry("980x740")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.dark_mode_enabled = False
        self.light_bg = "#f0f0f0"
        self.light_fg = "#000000"
        self.light_btn = "#e0e0e0"
        self.light_slider_bg = "#d9d9d9"
        self.dark_bg = "#1a1a1a"
        self.dark_fg = "#ffffff"
        self.dark_btn = "#333333"
        self.dark_slider_bg = "#404040"

        self.led_on = False
        self.camera = CameraMeasurement(reference_diameter_mm=23.75)
        self.arduino = ArduinoController(lambda line: self.root.after(0, lambda value=line: self.handle_arduino_line(value)))
        self.camera_after_id: str | None = None
        self.processing_capture = False
        self.manual_window: tk.Toplevel | None = None
        self.database_window: tk.Toplevel | None = None
        self.mode = "AUTO"
        self.site_server: ThreadingHTTPServer | None = None
        self.site_url: str | None = None

        IMAGE_FOLDER.mkdir(exist_ok=True)
        self.database = CoinDatabase(CSV_PATH)
        self.headers = self.database.headers
        self.rows = self.database.rows

        self.root.configure(bg=self.light_bg)
        self.create_interface()

    def load_database(self) -> tuple[list[str], list[dict[str, str]]]:
        database = CoinDatabase(CSV_PATH)
        return database.headers, database.rows

    def save_database(self) -> None:
        self.database.headers = self.headers
        self.database.rows = self.rows
        self.database.save()

    def create_interface(self) -> None:
        self.main = tk.Frame(self.root, bg=self.light_bg)
        self.main.pack(fill=tk.BOTH, expand=True, padx=30, pady=20)

        self.left_col = tk.Frame(self.main, bg=self.light_bg)
        self.left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 30))

        self.label_init = tk.Label(self.left_col, text="INIT", font=("Arial", 14, "bold"), bg=self.light_bg, fg=self.light_fg)
        self.label_init.pack(anchor="w", pady=2)

        self.port_btn = tk.Button(self.left_col, text="3", font=("Arial", 12), bg="#d9d9d9", fg=self.light_fg, width=8, relief=tk.SUNKEN, bd=2, command=self.show_ports)
        self.port_btn.pack(anchor="w", pady=5)

        self.place_btn = tk.Button(self.left_col, text="Scan Artifact", font=("Arial", 12), bg=self.light_btn, fg=self.light_fg, width=18, height=2, relief=tk.RAISED, bd=2, command=self.capture_and_catalog)
        self.place_btn.pack(anchor="w", pady=10)

        self.scan_btn = tk.Button(self.left_col, text="Place Artifact", font=("Arial", 12), bg=self.light_btn, fg=self.light_fg, width=18, height=2, relief=tk.RAISED, bd=2, command=self.toggle_camera)
        self.scan_btn.pack(anchor="w", pady=5)

        self.calibrate_btn = tk.Button(self.left_col, text="Calibrate (C)", font=("Arial", 11, "bold"), bg=self.light_btn, fg=self.light_fg, width=18, relief=tk.RAISED, bd=2, command=self.calibrate_from_current)
        self.calibrate_btn.pack(anchor="w", pady=5)

        self.db_btn = tk.Button(self.left_col, text="Database", font=("Arial", 11, "bold"), bg=self.light_btn, fg=self.light_fg, width=18, relief=tk.RAISED, bd=2, command=self.open_database_window)
        self.db_btn.pack(anchor="w", pady=5)

        self.site_btn = tk.Button(self.left_col, text="Catalog site", font=("Arial", 11, "bold"), bg=self.light_btn, fg=self.light_fg, width=18, relief=tk.RAISED, bd=2, command=self.open_catalog_site)
        self.site_btn.pack(anchor="w", pady=5)

        self.location_label = tk.Label(self.left_col, text="Discovery location", font=("Arial", 11), bg=self.light_bg, fg=self.light_fg)
        self.location_label.pack(anchor="w", pady=(10, 4))
        self.location_var = tk.StringVar(value="")
        self.location_entry = tk.Entry(self.left_col, textvariable=self.location_var, font=("Arial", 11), width=30)
        self.location_entry.pack(anchor="w", pady=(0, 6))

        self.label_vol = tk.Label(self.left_col, text="Voice assistant volume", font=("Arial", 11), bg=self.light_bg, fg=self.light_fg)
        self.label_vol.pack(anchor="w", pady=(15, 5))

        self.vol_frame = tk.Frame(self.left_col, bg=self.light_bg)
        self.vol_frame.pack(anchor="w", fill=tk.X, pady=2)
        self.volume = tk.DoubleVar(value=50)
        self.vol_slider = tk.Scale(self.vol_frame, from_=0, to=100, orient=tk.HORIZONTAL, variable=self.volume, bg=self.light_bg, fg=self.light_fg, length=250, showvalue=False, troughcolor=self.light_slider_bg, highlightbackground=self.light_bg)
        self.vol_slider.pack(side=tk.LEFT)
        self.vol_value = tk.Label(self.vol_frame, textvariable=self.volume, width=3, bg=self.light_bg, fg=self.light_fg)
        self.vol_value.pack(side=tk.LEFT, padx=5)

        self.auto_btn = tk.Button(
            self.left_col,
            text="AUTO",
            font=("Arial", 11, "bold"),
            bg=self.light_btn,
            fg=self.light_fg,
            width=10,
            relief=tk.RAISED,
            bd=2,
            command=self.set_auto_mode,
        )
        self.auto_btn.pack(anchor="w", pady=5)
        self.manual_btn = tk.Button(
            self.left_col,
            text="MANUAL",
            font=("Arial", 11, "bold"),
            bg=self.light_btn,
            fg=self.light_fg,
            width=10,
            relief=tk.RAISED,
            bd=2,
            command=self.set_manual_mode,
        )
        self.manual_btn.pack(anchor="w", pady=2)
        self.led_btn = tk.Button(self.left_col, text="LED OFF", font=("Arial", 11, "bold"), bg="#808080", fg="white", width=10, relief=tk.RAISED, bd=2, command=self.toggle_led)
        self.led_btn.pack(anchor="w", pady=2)

        self.stop_btn = tk.Button(self.left_col, text="STOP", font=("Arial", 14, "bold"), bg="#ff4444", fg="white", width=12, height=1, relief=tk.RAISED, bd=3, command=self.emergency_stop)
        self.stop_btn.pack(anchor="w", pady=15)

        self.theme_frame = tk.Frame(self.left_col, bg=self.light_bg)
        self.theme_frame.pack(anchor="w", pady=5)
        self.light_btn_theme = tk.Button(self.theme_frame, text="Light mode", font=("Arial", 10), bg=self.light_btn, fg=self.light_fg, width=10, relief=tk.RAISED, bd=2, command=self.light_mode)
        self.light_btn_theme.pack(side=tk.LEFT, padx=2)
        self.dark_btn_theme = tk.Button(self.theme_frame, text="Dark mode", font=("Arial", 10), bg=self.light_btn, fg=self.light_fg, width=10, relief=tk.RAISED, bd=2, command=self.dark_mode)
        self.dark_btn_theme.pack(side=tk.LEFT, padx=2)

        self.right_col = tk.Frame(self.main, bg=self.light_bg, width=340)
        self.right_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.right_col.pack_propagate(False)

        self.cam_frame = tk.Frame(self.right_col, bg="black", relief=tk.RAISED, bd=3)
        self.cam_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 20))
        self.cam_label = tk.Label(self.cam_frame, text="CAM FEED", font=("Arial", 16, "bold"), fg="white", bg="black")
        self.cam_label.pack(expand=True, fill=tk.BOTH)

        self.date_frame = tk.Frame(self.right_col, bg=self.light_bg, height=80)
        self.date_frame.pack(fill=tk.X, pady=8)
        self.date_frame.pack_propagate(False)
        self.date_label = tk.Label(self.date_frame, text="DATE: -", font=("Arial", 12, "bold"), bg=self.light_bg, fg=self.light_fg, justify=tk.LEFT)
        self.date_label.pack(anchor="w", padx=6, pady=6)

        self.classify_frame = tk.Frame(self.right_col, bg=self.light_bg, height=120)
        self.classify_frame.pack(fill=tk.X, pady=8)
        self.classify_frame.pack_propagate(False)
        self.classify_label = tk.Label(self.classify_frame, text="CLASSIFICATION: -", font=("Arial", 12, "bold"), bg=self.light_bg, fg=self.light_fg, justify=tk.LEFT, wraplength=320)
        self.classify_label.pack(anchor="w", padx=6, pady=6)

        self.root.bind("<space>", lambda _e: self.capture_and_catalog())
        self.root.bind("<c>", lambda _e: self.calibrate_from_current())
        self.root.bind("<C>", lambda _e: self.calibrate_from_current())

    def start_catalog_server(self) -> str | None:
        if self.site_server is not None and self.site_url is not None:
            return self.site_url

        handler = partial(NoCacheRequestHandler, directory=str(Path(__file__).parent))
        for port in range(SITE_PORT_START, SITE_PORT_END + 1):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", port), handler)
            except OSError:
                continue

            self.site_server = server
            self.site_url = f"http://127.0.0.1:{port}/index.html"
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            return self.site_url

        return None

    def open_catalog_site(self) -> None:
        url = self.start_catalog_server()
        if url is None:
            messagebox.showerror("Catalog site", "No free port found between 8000 and 8010.")
            return

        webbrowser.open(url)
        self.classify_label.config(text=f"CLASSIFICATION: Catalog site opened: {url}")

    def show_ports(self) -> None:
        port_menu = tk.Menu(self.root, tearoff=0)

        ports = self.available_serial_ports()
        if not ports:
            ports = [f"COM{port}" for port in range(1, 9)]

        for port in ports:
            port_menu.add_command(label=port, command=lambda p=port: self.select_port(p))
        port_menu.post(self.port_btn.winfo_rootx(), self.port_btn.winfo_rooty() + self.port_btn.winfo_height())

    def available_serial_ports(self) -> list[str]:
        return self.arduino.available_ports()

    def connect_arduino(self) -> bool:
        ok, message = self.arduino.connect()
        if not ok:
            messagebox.showerror("Arduino", message)
            return False
        self.classify_label.config(text=f"CLASSIFICATION: {message}")
        return True

    def disconnect_arduino(self) -> None:
        self.arduino.disconnect()

    def start_serial_reader(self) -> None:
        self.arduino.start_reader()

    def serial_reader_loop(self) -> None:
        return

    def handle_arduino_line(self, line: str) -> None:
        if line == "<ALIVE>":
            return
        self.classify_label.config(text=f"CLASSIFICATION: Arduino -> {line}")

    def send_serial_command(self, command_name: str, show_error: bool = True) -> bool:
        ok, message = self.arduino.send(command_name)
        if not ok and show_error:
            messagebox.showerror("Arduino", message)
        return ok

    def open_manual_commands_window(self) -> None:
        if self.manual_window is not None and self.manual_window.winfo_exists():
            self.manual_window.deiconify()
            self.manual_window.lift()
            self.manual_window.focus_force()
            return

        win = tk.Toplevel(self.root)
        self.manual_window = win
        win.title("Robot Commands - Manual")
        win.geometry("340x420")
        win.resizable(False, False)

        def on_manual_close() -> None:
            if self.manual_window is not None and self.manual_window.winfo_exists():
                self.manual_window.destroy()
            self.manual_window = None

        win.protocol("WM_DELETE_WINDOW", on_manual_close)

        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Robot commands", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0, 10))

        status_var = tk.StringVar(value="Select a command.")
        commands = [
            ("Home", "home"),
            ("Camera up", "camera up"),
            ("Camera down", "camera down"),
            ("Scale front", "scale front"),
            ("Scale back", "scale back"),
            ("Electronic measurement", "measure"),
            ("LED ON", "led on"),
            ("LED OFF", "led off"),
        ]

        def run_command(command_name: str) -> None:
            if self.send_serial_command(command_name):
                status_var.set(f"Command sent: {command_name}")
                self.classify_label.config(text=f"CLASSIFICATION: Manual command -> {command_name}")

        for label, command_name in commands:
            ttk.Button(frame, text=label, command=lambda c=command_name: run_command(c)).pack(fill="x", pady=4)

        ttk.Label(frame, textvariable=status_var, wraplength=280).pack(anchor="w", pady=(10, 0))

    def set_manual_mode(self) -> None:
        self.mode = "MANUAL"
        self.open_manual_commands_window()
        self.classify_label.config(text="CLASSIFICATION: MANUAL mode active.")

    def set_auto_mode(self) -> None:
        self.mode = "AUTO"
        if self.manual_window is not None and self.manual_window.winfo_exists():
            self.manual_window.destroy()
        self.manual_window = None
        self.classify_label.config(text="CLASSIFICATION: -")

    def select_port(self, port: str) -> None:
        ok, message = self.arduino.select_port(port)
        self.port_btn.config(text=self.arduino.selected_port.replace("COM", ""))
        if ok:
            self.classify_label.config(text=f"CLASSIFICATION: {message}")
        else:
            messagebox.showerror("Arduino", message)

    def toggle_led(self) -> None:
        self.led_on = not self.led_on
        if self.led_on:
            self.led_btn.config(text="LED ON", bg="#ffff00", fg="black")
            self.send_serial_command("led on")
        else:
            self.led_btn.config(text="LED OFF", bg="#808080", fg="white")
            self.send_serial_command("led off")

    def emergency_stop(self) -> None:
        self.send_serial_command("stop", show_error=False)
        self.led_on = False
        self.led_btn.config(text="LED OFF", bg="#808080", fg="white")
        self.volume.set(0)
        self.classify_label.config(text="CLASSIFICATION: STOP sent to Arduino motors.")

    def light_mode(self) -> None:
        self.dark_mode_enabled = False
        self.apply_theme()

    def dark_mode(self) -> None:
        self.dark_mode_enabled = True
        self.apply_theme()

    def apply_theme(self) -> None:
        if self.dark_mode_enabled:
            bg, fg, btn, slider_bg = self.dark_bg, self.dark_fg, self.dark_btn, self.dark_slider_bg
        else:
            bg, fg, btn, slider_bg = self.light_bg, self.light_fg, self.light_btn, self.light_slider_bg

        self.root.configure(bg=bg)
        for frame in [self.main, self.left_col, self.right_col, self.vol_frame, self.theme_frame, self.date_frame, self.classify_frame]:
            frame.configure(bg=bg)
        for label in [self.label_init, self.label_vol, self.vol_value, self.date_label, self.classify_label, self.location_label]:
            label.configure(bg=bg, fg=fg)
        for button in [self.place_btn, self.scan_btn, self.calibrate_btn, self.db_btn, self.site_btn, self.auto_btn, self.manual_btn, self.light_btn_theme, self.dark_btn_theme]:
            button.configure(bg=btn, fg=fg)
        self.port_btn.configure(bg="#808080" if self.dark_mode_enabled else "#d9d9d9", fg=fg)
        self.location_entry.configure(bg=btn, fg=fg, insertbackground=fg)
        self.vol_slider.configure(bg=bg, fg=fg, troughcolor=slider_bg, highlightbackground=bg)
        self.stop_btn.configure(bg="#ff4444", fg="white")
        if self.led_on:
            self.led_btn.configure(bg="#ffff00", fg="black")
        else:
            self.led_btn.configure(bg="#808080", fg="white")

    def start_camera(self) -> None:
        if self.camera.is_open():
            return
        ok, message = self.camera.start()
        if not ok:
            messagebox.showerror("Camera", message)
            return
        self.scan_btn.config(text="Stop Place Artifact")
        self.update_camera_feed()

    def stop_camera(self) -> None:
        if self.camera_after_id is not None:
            try:
                self.root.after_cancel(self.camera_after_id)
            except Exception:
                pass
            self.camera_after_id = None
        self.camera.stop()
        self.cam_label.config(image="", text="CAM FEED")
        self.cam_label.image = None
        self.scan_btn.config(text="Place Artifact")

    def toggle_camera(self) -> None:
        if not self.camera.is_open():
            self.start_camera()
        else:
            self.stop_camera()

    def update_camera_feed(self) -> None:
        if not self.camera.is_open() or not self.root.winfo_exists():
            self.camera_after_id = None
            return
        if self.processing_capture:
            self.camera_after_id = self.root.after(CAMERA_REFRESH_MS, self.update_camera_feed)
            return
        try:
            frame = self.camera.read_frame()
            if frame is not None:
                display = frame.copy()
                if self.camera.mm_per_pixel is None:
                    status = "NECALIBRAT"
                    color = (0, 0, 255)
                else:
                    ref_px = self.camera.reference_diameter_px
                    if ref_px is None:
                        status = f"CALIBRAT ({self.camera.mm_per_pixel:.4f} mm/pixel)"
                    else:
                        status = f"CALIBRAT 23.75mm={ref_px:.1f}px"
                    color = (0, 255, 0)
                cv2.putText(display, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                cv2.putText(display, "SPACE=capture | C=calibrate", (10, display.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

                success, ppm = cv2.imencode(".ppm", display)
                if success:
                    tk_img = tk.PhotoImage(data=ppm.tobytes(), format="PPM")
                    self.cam_label.config(image=tk_img, text="")
                    self.cam_label.image = tk_img
        except Exception as exc:
            self.classify_label.config(text=f"CLASSIFICATION: Camera error: {exc}")
            self.stop_camera()
            return

        self.camera_after_id = self.root.after(CAMERA_REFRESH_MS, self.update_camera_feed)

    def detect_main_circle(self, frame: np.ndarray) -> tuple[int, int, int] | None:
        return self.camera.detect_main_circle(frame)

    def calibrate_from_current(self) -> None:
        try:
            ok, message = self.camera.calibrate_from_frame()
        except Exception as exc:
            ok = False
            message = f"Calibration error: {exc}"

        if not ok:
            messagebox.showwarning("Calibration", message)
            self.classify_label.config(text=f"CLASSIFICATION: {message}")
            return

        self.classify_label.config(text=f"CLASSIFICATION: {message}")

    def measure_diameter_from_live(self, sample_count: int = MEASUREMENT_FRAMES) -> tuple[float | None, np.ndarray | None]:
        return self.camera.measure_diameter(sample_count)

    def parse_float(self, value: str) -> float | None:
        return CoinDatabase.parse_float(value)

    def confidence_diameter(self, d1: float, d2: float | None, tolerance: float = 1.2) -> float:
        return CoinDatabase.confidence_diameter(d1, d2, tolerance)

    def best_matches(self, diameter_mm: float, limit: int = 3) -> list[tuple[dict[str, str], float]]:
        return self.database.best_matches(diameter_mm, limit)

    def save_capture(self, annotated: np.ndarray) -> Path:
        return CameraMeasurement.save_capture(IMAGE_FOLDER, annotated)

    def append_coin_row(self, image_path: Path, diameter_mm: float) -> None:
        location = self.location_var.get().strip()
        self.database.append_capture(image_path, diameter_mm, location)
        self.headers = self.database.headers
        self.rows = self.database.rows

    @staticmethod
    def format_match_list(matches: object) -> str:
        if not isinstance(matches, list):
            return ""

        lines: list[str] = []
        for match in matches:
            if not isinstance(match, tuple) or len(match) != 2:
                continue
            row, confidence = match
            if not isinstance(row, dict):
                continue
            denomination = row.get("denomination", "-")
            lines.append(f"- {denomination}: {float(confidence):.2f}%")

        return "\n".join(lines)

    def capture_and_catalog(self) -> None:
        if self.camera.current_frame is None:
            messagebox.showwarning("Capture", "Start the camera (Scan Artifact) before capturing.")
            return

        if self.processing_capture:
            return

        self.processing_capture = True
        self.place_btn.config(state=tk.DISABLED)
        self.classify_label.config(text="CLASSIFICATION: Processing capture...")
        worker = threading.Thread(target=self._capture_worker, daemon=True)
        worker.start()

    def _capture_worker(self) -> None:
        result: dict[str, object] = {"status": "error", "message": "Unknown error."}
        try:
            if self.camera.mm_per_pixel is None:
                result = {
                    "status": "warning",
                    "message": "Press Calibrate (C) first with the 23.75 mm coin in frame.",
                }
            else:
                diameter_mm, annotated = self.measure_diameter_from_live()
                if diameter_mm is None or annotated is None:
                    result = {
                        "status": "warning",
                        "message": "Could not measure the coin reliably. Try again.",
                    }
                else:
                    image_path = self.save_capture(annotated)
                    matches = self.best_matches(diameter_mm, limit=3)
                    best_row, best_conf = matches[0] if matches else ({}, 0.0)
                    result = {
                        "status": "ok",
                        "diameter_mm": float(diameter_mm),
                        "scan_diameter_px": self.camera.last_diameter_px,
                        "calibration_diameter_px": self.camera.reference_diameter_px,
                        "image_path": image_path,
                        "matches": matches,
                        "best_row": best_row,
                        "best_conf": float(best_conf),
                    }
        except Exception as exc:
            result = {"status": "error", "message": f"A measurement error occurred:\n{exc}"}

        self.root.after(0, lambda r=result: self._capture_worker_done(r))

    def _capture_worker_done(self, result: dict[str, object]) -> None:
        try:
            status = str(result.get("status", "error"))
            if status == "warning":
                messagebox.showwarning("Capture", str(result.get("message", "Operation cancelled.")))
                return
            if status == "error":
                messagebox.showerror("Measurement error", str(result.get("message", "Unknown error.")))
                return

            diameter_mm = float(result["diameter_mm"])
            scan_diameter_px = result.get("scan_diameter_px")
            calibration_diameter_px = result.get("calibration_diameter_px")
            image_path = Path(str(result["image_path"]))
            matches = result.get("matches", [])
            best_row = result.get("best_row", {})
            best_conf = float(result.get("best_conf", 0.0))
            match_text = self.format_match_list(matches)

            if best_conf > 80:
                if isinstance(best_row, dict):
                    denom = best_row.get("denomination", "-")
                else:
                    denom = "-"
                cls_text = f"Already exists ({best_conf:.2f}%) - {denom}"
            elif 35 <= best_conf <= 80:
                similar = match_text
                add_new = messagebox.askyesno("Confirmation", f"Similar matches:\n{similar}\n\nAdd this as a new coin?")
                if add_new:
                    self.append_coin_row(image_path, diameter_mm)
                    cls_text = f"New coin added after review ({best_conf:.2f}%)"
                else:
                    cls_text = f"Coin not added ({best_conf:.2f}%)"
            else:
                self.append_coin_row(image_path, diameter_mm)
                cls_text = f"New coin added automatically ({best_conf:.2f}%)"

            ts = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
            pixel_line = ""
            if isinstance(scan_diameter_px, (float, int)) and isinstance(calibration_diameter_px, (float, int)):
                pixel_line = f"\nCal px: {float(calibration_diameter_px):.1f} | Scan px: {float(scan_diameter_px):.1f}"
            self.date_label.config(text=f"DATE: {ts}\nDiameter: {diameter_mm:.2f} mm{pixel_line}\nImage: {image_path.name}")
            if match_text:
                self.classify_label.config(text=f"CLASSIFICATION: {cls_text}\nTop matches:\n{match_text}")
            else:
                self.classify_label.config(text=f"CLASSIFICATION: {cls_text}")
        finally:
            self.processing_capture = False
            self.place_btn.config(state=tk.NORMAL)

    def refresh_db_tree(self, tree: ttk.Treeview) -> None:
        tree.delete(*tree.get_children())
        for row in self.rows:
            tree.insert("", "end", values=[row.get(h, "") for h in self.headers])

    def open_db_selected_image(self, tree: ttk.Treeview) -> None:
        selection = tree.selection()
        if not selection:
            messagebox.showwarning("Database", "Select a row.")
            return
        index = tree.index(selection[0])
        image_ref = self.rows[index].get("link", "").strip()
        if not image_ref:
            messagebox.showwarning("Database", "The selected row has no image.")
            return
        webbrowser.open(image_ref)

    def open_db_edit_dialog(self, parent: tk.Toplevel, tree: ttk.Treeview, mode: str) -> None:
        selection = tree.selection() if mode == "edit" else ()
        selected_id = selection[0] if selection else None
        selected_index = tree.index(selected_id) if selected_id else None
        if mode == "edit" and selected_index is None:
            messagebox.showwarning("Database", "Select a row to update.")
            return

        selected_row = self.rows[selected_index] if selected_index is not None else {}

        dialog = tk.Toplevel(parent)
        dialog.title("Add row" if mode == "add" else "Update row")
        dialog.geometry("520x330")
        dialog.transient(parent)
        dialog.grab_set()

        form = ttk.Frame(dialog, padding=12)
        form.pack(fill="both", expand=True)

        entries: dict[str, ttk.Entry] = {}
        for idx, header in enumerate(self.headers):
            ttk.Label(form, text=header).grid(row=idx, column=0, sticky="w", padx=(0, 10), pady=5)
            entry = ttk.Entry(form, width=48)
            entry.insert(0, selected_row.get(header, ""))
            entry.grid(row=idx, column=1, sticky="ew", pady=5)
            entries[header] = entry
        form.columnconfigure(1, weight=1)

        def save_changes() -> None:
            new_row = {h: entries[h].get().strip() for h in self.headers}
            if not any(new_row.values()):
                messagebox.showwarning("Database", "Fill in at least one field.")
                return
            if mode == "add":
                self.rows.append(new_row)
            else:
                self.rows[selected_index] = new_row  # type: ignore[index]
            self.save_database()
            self.refresh_db_tree(tree)
            dialog.destroy()

        buttons = ttk.Frame(form)
        buttons.grid(row=len(self.headers), column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Button(buttons, text="Save", command=save_changes).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=(8, 0))

    def delete_db_selected(self, tree: ttk.Treeview) -> None:
        selection = tree.selection()
        if not selection:
            messagebox.showwarning("Database", "Select a row to delete.")
            return
        index = tree.index(selection[0])
        denomination = self.rows[index].get("denomination", "the selected row")

        confirm_1 = messagebox.askyesno("Delete row", f"Do you want to delete {denomination}?")
        if not confirm_1:
            return
        confirm_2 = messagebox.askyesno("Are you sure?", f"Are you sure you want to delete {denomination}?")
        if not confirm_2:
            return

        self.rows.pop(index)
        self.save_database()
        self.refresh_db_tree(tree)

    def open_database_window(self) -> None:
        if self.database_window is not None and self.database_window.winfo_exists():
            self.database_window.deiconify()
            self.database_window.lift()
            self.database_window.focus_force()
            return

        win = tk.Toplevel(self.root)
        self.database_window = win
        win.title("Coin database")
        win.geometry("980x520")

        def on_database_close() -> None:
            if self.database_window is not None and self.database_window.winfo_exists():
                self.database_window.destroy()
            self.database_window = None

        win.protocol("WM_DELETE_WINDOW", on_database_close)

        tree = ttk.Treeview(win, columns=self.headers, show="headings")
        ysb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        xsb = ttk.Scrollbar(win, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)

        for h in self.headers:
            tree.heading(h, text=h)
            tree.column(h, width=160 if h != "link" else 340, anchor="center")

        self.refresh_db_tree(tree)

        tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")

        actions = ttk.Frame(win)
        actions.grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=8)
        ttk.Button(actions, text="Open image", command=lambda: self.open_db_selected_image(tree)).pack(side="left")
        ttk.Button(actions, text="Add", command=lambda: self.open_db_edit_dialog(win, tree, "add")).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Update", command=lambda: self.open_db_edit_dialog(win, tree, "edit")).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Delete", command=lambda: self.delete_db_selected(tree)).pack(side="left", padx=(8, 0))

        win.rowconfigure(0, weight=1)
        win.columnconfigure(0, weight=1)

    def on_close(self) -> None:
        if self.manual_window is not None and self.manual_window.winfo_exists():
            self.manual_window.destroy()
        self.manual_window = None
        if self.database_window is not None and self.database_window.winfo_exists():
            self.database_window.destroy()
        self.database_window = None
        self.stop_camera()
        self.disconnect_arduino()
        if self.site_server is not None:
            self.site_server.shutdown()
            self.site_server.server_close()
            self.site_server = None
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    DeviceInterface(root)
    root.mainloop()


if __name__ == "__main__":
    main()
