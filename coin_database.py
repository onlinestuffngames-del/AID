from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path


REQUIRED_HEADERS = ["denomination", "link", "diameter_mm", "weight_g", "material", "location"]


class CoinDatabase:
    def __init__(self, csv_path: Path):
        self.csv_path = csv_path
        self.headers, self.rows = self.load()
        self.save()

    def load(self) -> tuple[list[str], list[dict[str, str]]]:
        if not self.csv_path.exists():
            return list(REQUIRED_HEADERS), []

        with self.csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or []
            rows = [{key: (value or "").strip() for key, value in row.items()} for row in reader]

        merged_headers = [h for h in headers if h != "location_found"]
        for req in REQUIRED_HEADERS:
            if req not in merged_headers:
                merged_headers.append(req)

        for row in rows:
            for header in merged_headers:
                row.setdefault(header, "")

        return merged_headers, rows

    def save(self) -> None:
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.headers)
            writer.writeheader()
            for row in self.rows:
                writer.writerow({header: row.get(header, "") for header in self.headers})

    def append_capture(self, image_path: Path, diameter_mm: float, location: str) -> None:
        name = f"captura_{datetime.now().strftime('%H%M%S')}"
        self.rows.append(
            {
                "denomination": name,
                "link": str(image_path.resolve()),
                "diameter_mm": f"{diameter_mm:.2f}",
                "weight_g": "",
                "material": "",
                "location": location,
            }
        )
        self.save()

    @staticmethod
    def parse_float(value: str) -> float | None:
        text = value.strip().replace(",", ".")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def confidence_diameter(d1: float, d2: float | None, tolerance: float = 1.2) -> float:
        if d2 is None:
            return 0.0
        diff = abs(d1 - d2)
        return max(0.0, 1.0 - diff / tolerance) * 100

    def best_matches(self, diameter_mm: float, limit: int = 3) -> list[tuple[dict[str, str], float]]:
        scored: list[tuple[dict[str, str], float]] = []
        for row in self.rows:
            db_diameter = self.parse_float(row.get("diameter_mm", ""))
            confidence = round(self.confidence_diameter(diameter_mm, db_diameter), 2)
            scored.append((row, confidence))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]
