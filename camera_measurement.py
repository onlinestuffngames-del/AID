from __future__ import annotations

from pathlib import Path

import cv2  # type: ignore
import numpy as np  # type: ignore


MEASUREMENT_FRAMES = 5
CALIBRATION_FRAMES = 7
MIN_VALID_MM_PER_PIXEL = 0.01
MAX_VALID_MM_PER_PIXEL = 0.50
MIN_COIN_AREA_RATIO = 0.005
MAX_COIN_AREA_RATIO = 0.70


class CameraMeasurement:
    def __init__(self, reference_diameter_mm: float = 23.75):
        self.cap: cv2.VideoCapture | None = None
        self.current_frame: np.ndarray | None = None
        self.mm_per_pixel: float | None = None
        self.reference_diameter_px: float | None = None
        self.last_diameter_px: float | None = None
        self.reference_diameter_mm = reference_diameter_mm

    def start(self) -> tuple[bool, str]:
        if self.cap is not None:
            return True, ""

        self.cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            self.cap = None
            return False, "Cannot open the camera."

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return True, ""

    def stop(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.current_frame = None

    def is_open(self) -> bool:
        return self.cap is not None and self.cap.isOpened()

    def read_frame(self) -> np.ndarray | None:
        if not self.is_open():
            return None
        ok, frame = self.cap.read()  # type: ignore[union-attr]
        if not ok:
            return None
        self.current_frame = frame
        return frame

    def detect_main_circle(self, frame: np.ndarray) -> tuple[int, int, int] | None:
        if frame is None or frame.size == 0:
            return None
        try:
            contour = self.detect_main_coin_contour(frame)
            if contour is not None:
                (x, y), radius = cv2.minEnclosingCircle(contour)
                if radius > 0:
                    return int(round(x)), int(round(y)), int(round(radius))

            return self.detect_main_circle_hough(frame)
        except cv2.error:
            return None

    def detect_main_circle_hough(self, frame: np.ndarray) -> tuple[int, int, int] | None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        gray = cv2.equalizeHist(gray)
        height, width = gray.shape
        min_radius = max(12, int(min(height, width) * 0.03))
        max_radius = max(min_radius + 5, int(min(height, width) * 0.45))
        circles = cv2.HoughCircles(
            gray,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=40,
            param1=120,
            param2=28,
            minRadius=min_radius,
            maxRadius=max_radius,
        )
        if circles is None:
            return None
        circles = np.round(circles[0, :]).astype(int)
        center_x, center_y = width // 2, height // 2
        circles = sorted(circles, key=lambda c: (abs(c[0] - center_x) + abs(c[1] - center_y), -c[2]))
        x, y, radius = circles[0]
        return int(x), int(y), int(radius)

    def detect_diameter_px(self, frame: np.ndarray | None = None) -> tuple[float, int, int, int] | None:
        frame = frame if frame is not None else self.current_frame
        if frame is None:
            return None

        circle = self.detect_main_circle(frame)
        if circle is None:
            return None

        x, y, radius = circle
        diameter_px = float(2 * radius)
        if diameter_px <= 0:
            return None

        return diameter_px, x, y, radius

    def detect_main_coin_contour(self, frame: np.ndarray) -> np.ndarray | None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (7, 7), 0)
        gray = cv2.equalizeHist(gray)

        height, width = gray.shape
        image_area = float(height * width)
        min_area = image_area * MIN_COIN_AREA_RATIO
        max_area = image_area * MAX_COIN_AREA_RATIO
        center_x, center_y = width / 2.0, height / 2.0

        candidates: list[tuple[float, np.ndarray]] = []
        masks = []
        for mode in (cv2.THRESH_BINARY, cv2.THRESH_BINARY_INV):
            _, mask = cv2.threshold(gray, 0, 255, mode | cv2.THRESH_OTSU)
            kernel = np.ones((5, 5), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            masks.append(mask)

        for mask in masks:
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < min_area or area > max_area:
                    continue

                perimeter = cv2.arcLength(contour, True)
                if perimeter <= 0:
                    continue

                circularity = 4.0 * np.pi * area / (perimeter * perimeter)
                if circularity < 0.30:
                    continue

                (x, y), radius = cv2.minEnclosingCircle(contour)
                if radius <= 0:
                    continue

                center_distance = abs(x - center_x) + abs(y - center_y)
                score = center_distance - radius * 0.35 - circularity * 80.0
                candidates.append((score, contour))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def calibrate_from_frame(self, frame: np.ndarray | None = None) -> tuple[bool, str]:
        frame = frame if frame is not None else self.current_frame
        if frame is None:
            return False, "Start the camera first."

        detected = self.detect_diameter_px(frame)
        if detected is None:
            return False, "Reference coin was not detected."

        diameter_px, _, _, _ = detected
        if diameter_px <= 0:
            return False, "Invalid diameter."

        ratio = self.reference_diameter_mm / diameter_px
        if ratio < MIN_VALID_MM_PER_PIXEL or ratio > MAX_VALID_MM_PER_PIXEL:
            return False, "Calibration seems invalid."

        self.mm_per_pixel = ratio
        self.reference_diameter_px = diameter_px
        self.last_diameter_px = diameter_px
        return True, f"Calibration successful: 23.75 mm = {diameter_px:.1f} px ({self.mm_per_pixel:.4f} mm/pixel)"

    def calibrate_from_live(self, sample_count: int = CALIBRATION_FRAMES) -> tuple[bool, str]:
        if not self.is_open():
            return False, "Start the camera first."

        diameters_px: list[float] = []
        try:
            for _ in range(sample_count):
                frame = self.read_frame()
                if frame is None:
                    continue
                circle = self.detect_main_circle(frame)
                if circle is not None:
                    _, _, radius = circle
                    diameters_px.append(float(2 * radius))
        except cv2.error as exc:
            return False, f"OpenCV calibration error: {exc}"

        if not diameters_px:
            return False, "Reference coin was not detected."

        diameter_px = float(np.median(np.array(diameters_px, dtype=np.float32)))
        if diameter_px <= 0:
            return False, "Invalid diameter."

        ratio = self.reference_diameter_mm / diameter_px
        if ratio < MIN_VALID_MM_PER_PIXEL or ratio > MAX_VALID_MM_PER_PIXEL:
            return False, "Calibration seems invalid."

        self.mm_per_pixel = ratio
        self.reference_diameter_px = diameter_px
        return True, f"Calibration successful: 23.75 mm = {diameter_px:.1f} px ({self.mm_per_pixel:.4f} mm/pixel)"

    def measure_diameter(self, sample_count: int = MEASUREMENT_FRAMES) -> tuple[float | None, np.ndarray | None]:
        if self.mm_per_pixel is None:
            return None, None

        frame = self.current_frame
        if frame is None:
            return None, None

        detected = self.detect_diameter_px(frame)
        if detected is None:
            return None, None

        diameter_px, x, y, radius = detected
        self.last_diameter_px = diameter_px
        diameter_mm = diameter_px * self.mm_per_pixel

        annotated = frame.copy()
        cv2.circle(annotated, (x, y), radius, (0, 255, 0), 2)
        cv2.circle(annotated, (x, y), 2, (0, 0, 255), 3)
        cv2.putText(
            annotated,
            f"Diameter: {diameter_mm:.2f} mm",
            (10, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2,
        )
        cv2.putText(
            annotated,
            f"Pixels: {diameter_px:.1f} px",
            (10, 68),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
        )
        return diameter_mm, annotated

    @staticmethod
    def save_capture(image_folder: Path, annotated: np.ndarray) -> Path:
        from datetime import datetime

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = image_folder / f"moneda_{stamp}.jpg"
        cv2.imwrite(str(path), annotated)
        return path
