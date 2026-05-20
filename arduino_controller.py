from __future__ import annotations

import threading
import time
from typing import Callable

try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except ImportError:
    serial = None  # type: ignore[assignment]
    list_ports = None  # type: ignore[assignment]


ARDUINO_BAUDRATE = 115200
ARDUINO_COMMANDS = {
    "home": "H",
    "camera up": "S",
    "camera down": "J",
    "scale front": "F",
    "scale back": "X",
    "led on": "Z",
    "led off": "Y",
    "measure": "M",
    "ping": "Q",
    "prepare capture": "P",
    "capture off": "O",
    "stop": "O",
}


class ArduinoController:
    def __init__(self, on_line: Callable[[str], None]):
        self.on_line = on_line
        self.selected_port = "COM3"
        self.conn = None
        self.lock = threading.Lock()
        self.reader_running = False

    @property
    def pyserial_available(self) -> bool:
        return serial is not None

    def available_ports(self) -> list[str]:
        if list_ports is None:
            return []
        return [port.device for port in list_ports.comports()]

    def connect(self) -> tuple[bool, str]:
        if serial is None:
            return False, "The pyserial library is missing. Install it with: python -m pip install pyserial"

        if self.conn is not None and getattr(self.conn, "is_open", False):
            return True, f"Arduino connected on {self.selected_port}."

        try:
            self.conn = serial.Serial(
                self.selected_port,
                ARDUINO_BAUDRATE,
                timeout=0.05,
                write_timeout=1,
            )
            time.sleep(2)
        except Exception as exc:
            self.conn = None
            return False, f"Cannot connect to {self.selected_port}: {exc}"

        self.start_reader()
        self.send("ping", connect_first=False)
        return True, f"Arduino connected on {self.selected_port}."

    def disconnect(self) -> None:
        self.reader_running = False
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None

    def select_port(self, port: str) -> tuple[bool, str]:
        self.disconnect()
        self.selected_port = port if port.upper().startswith("COM") else f"COM{port}"
        return self.connect()

    def start_reader(self) -> None:
        if self.reader_running:
            return
        self.reader_running = True
        worker = threading.Thread(target=self._reader_loop, daemon=True)
        worker.start()

    def _reader_loop(self) -> None:
        while self.reader_running:
            conn = self.conn
            if conn is None or not getattr(conn, "is_open", False):
                time.sleep(0.1)
                continue

            try:
                line = conn.readline().decode("utf-8", errors="replace").strip()
            except Exception:
                time.sleep(0.1)
                continue

            if line:
                self.on_line(line)

    def send(self, command_name: str, connect_first: bool = True) -> tuple[bool, str]:
        cmd = ARDUINO_COMMANDS.get(command_name)
        if cmd is None:
            return False, f"Unknown command: {command_name}"

        if connect_first:
            ok, message = self.connect()
            if not ok:
                return False, message

        try:
            with self.lock:
                self.conn.write(cmd.encode("ascii"))  # type: ignore[union-attr]
                self.conn.flush()  # type: ignore[union-attr]
        except Exception as exc:
            self.disconnect()
            return False, f"Cannot send command {cmd}: {exc}"

        return True, f"Command sent: {command_name}"
