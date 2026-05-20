from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, Iterable


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
METADATA_EXTENSIONS = {".csv", ".json", ".jsonl"}

COIN_SPECS = {
    "1_cent": {
        "diametru": "16.25 mm",
        "greutate": "2.30 g",
        "compozitie": "Copper-covered steel",
    },
    "2_cent": {
        "diametru": "18.75 mm",
        "greutate": "3.06 g",
        "compozitie": "Copper-covered steel",
    },
    "5_cent": {
        "diametru": "21.25 mm",
        "greutate": "3.92 g",
        "compozitie": "Copper-covered steel",
    },
    "10_cent": {
        "diametru": "19.75 mm",
        "greutate": "4.10 g",
        "compozitie": "Nordic gold",
    },
    "20_cent": {
        "diametru": "22.25 mm",
        "greutate": "5.74 g",
        "compozitie": "Nordic gold",
    },
    "50_cent": {
        "diametru": "24.25 mm",
        "greutate": "7.80 g",
        "compozitie": "Nordic gold",
    },
    "1_euro": {
        "diametru": "23.25 mm",
        "greutate": "7.50 g",
        "compozitie": "Outer part: nickel-brass; Inner part: three layers: copper-nickel, nickel, copper-nickel",
    },
    "2_euro": {
        "diametru": "25.75 mm",
        "greutate": "8.50 g",
        "compozitie": "Outer part: copper-nickel; Inner part: three layers: nickel-brass, nickel, nickel-brass",
    },
}


def normalize_key(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def empty_record() -> Dict[str, str]:
    return {
        "diametru": "",
        "greutate": "",
        "compozitie": "",
    }


def find_candidate_value(row: Dict[str, object], aliases: Iterable[str]) -> str:
    normalized = {normalize_key(str(key)): value for key, value in row.items()}
    for alias in aliases:
        value = normalized.get(normalize_key(alias))
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def detect_coin_value(path_text: str) -> str | None:
    normalized = normalize_key(path_text)
    aliases = [
        ("1_cent", ["1cent", "cent1", "01cent"]),
        ("2_cent", ["2cent", "cent2", "02cent"]),
        ("5_cent", ["5cent", "cent5", "05cent"]),
        ("10_cent", ["10cent", "cent10"]),
        ("20_cent", ["20cent", "cent20"]),
        ("50_cent", ["50cent", "cent50"]),
        ("1_euro", ["1euro", "euro1"]),
        ("2_euro", ["2euro", "euro2"]),
    ]
    for coin_name, patterns in aliases:
        if any(pattern in normalized for pattern in patterns):
            return coin_name
    return None


def read_csv_metadata(file_path: Path) -> Dict[str, Dict[str, str]]:
    records: Dict[str, Dict[str, str]] = {}
    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return records
        for row in reader:
            image_ref = find_candidate_value(
                row,
                [
                    "poza",
                    "image",
                    "image_path",
                    "imagepath",
                    "image_name",
                    "imagename",
                    "filename",
                    "file_name",
                    "filepath",
                    "path",
                ],
            )
            if not image_ref:
                continue
            records[image_ref] = {
                "diametru": find_candidate_value(row, ["diametru", "diameter", "diameter_mm"]),
                "greutate": find_candidate_value(row, ["greutate", "weight", "weight_g"]),
                "compozitie": find_candidate_value(row, ["compozitie", "composition", "material"]),
            }
    return records


def read_json_rows(file_path: Path) -> Iterable[Dict[str, object]]:
    with file_path.open("r", encoding="utf-8-sig") as handle:
        if file_path.suffix.lower() == ".jsonl":
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)
            return
        data = json.load(handle)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield item
        elif isinstance(data, dict):
            for value in data.values():
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            yield item


def read_json_metadata(file_path: Path) -> Dict[str, Dict[str, str]]:
    records: Dict[str, Dict[str, str]] = {}
    for row in read_json_rows(file_path):
        image_ref = find_candidate_value(
            row,
            [
                "poza",
                "image",
                "image_path",
                "imagepath",
                "image_name",
                "imagename",
                "filename",
                "file_name",
                "filepath",
                "path",
            ],
        )
        if not image_ref:
            continue
        records[image_ref] = {
            "diametru": find_candidate_value(row, ["diametru", "diameter", "diameter_mm"]),
            "greutate": find_candidate_value(row, ["greutate", "weight", "weight_g"]),
            "compozitie": find_candidate_value(row, ["compozitie", "composition", "material"]),
        }
    return records


def load_metadata(dataset_dir: Path) -> Dict[str, Dict[str, str]]:
    records: Dict[str, Dict[str, str]] = {}
    for file_path in dataset_dir.rglob("*"):
        if not file_path.is_file() or file_path.suffix.lower() not in METADATA_EXTENSIONS:
            continue
        if file_path.suffix.lower() == ".csv":
            file_records = read_csv_metadata(file_path)
        else:
            file_records = read_json_metadata(file_path)
        for key, value in file_records.items():
            records[key.replace("\\", "/")] = value
            records[Path(key).name] = value
    return records


def load_class_map(class_map_arg: str | None) -> Dict[str, str]:
    if not class_map_arg:
        return {}
    path = Path(class_map_arg)
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        data = json.loads(class_map_arg)
    return {str(key): str(value) for key, value in data.items()}


def collect_image_rows(dataset_dir: Path, metadata: Dict[str, Dict[str, str]]) -> list[Dict[str, str]]:
    rows: list[Dict[str, str]] = []
    for image_path in sorted(dataset_dir.rglob("*")):
        if not image_path.is_file() or image_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        relative_path = image_path.relative_to(dataset_dir).as_posix()
        row = {"poza": relative_path, **empty_record()}

        metadata_match = metadata.get(relative_path) or metadata.get(image_path.name)
        if metadata_match:
            row.update(metadata_match)

        coin_value = detect_coin_value(relative_path)
        if coin_value:
            for key, value in COIN_SPECS[coin_value].items():
                if not row[key]:
                    row[key] = value

        rows.append(row)
    return rows


def collect_yolo_rows(
    dataset_dir: Path,
    metadata: Dict[str, Dict[str, str]],
    class_map: Dict[str, str],
) -> list[Dict[str, str]]:
    images_dir = dataset_dir / "images"
    labels_dir = dataset_dir / "labels"
    if not images_dir.exists() or not labels_dir.exists():
        return []

    rows: list[Dict[str, str]] = []
    for label_path in sorted(labels_dir.glob("*.txt")):
        image_candidates = [
            images_dir / f"{label_path.stem}{ext}"
            for ext in sorted(IMAGE_EXTENSIONS)
        ]
        image_path = next((candidate for candidate in image_candidates if candidate.exists()), None)
        if image_path is None:
            continue

        relative_path = image_path.relative_to(dataset_dir).as_posix()
        metadata_match = metadata.get(relative_path) or metadata.get(image_path.name) or {}

        with label_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.strip().split()
                if not parts:
                    continue

                row = {"poza": relative_path, **empty_record()}
                row.update(metadata_match)

                class_id = parts[0]
                coin_value = class_map.get(class_id) or detect_coin_value(relative_path)
                if coin_value in COIN_SPECS:
                    for key, value in COIN_SPECS[coin_value].items():
                        if not row[key]:
                            row[key] = value

                rows.append(row)

    return rows


def write_dataset(rows: list[Dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["poza", "diametru", "greutate", "compozitie"],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Construieste un CSV din datasetul Kaggle cu coloanele: "
            "poza, diametru, greutate, compozitie."
        )
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        help="Folderul in care ai extras datasetul Kaggle.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("euro_coins_4_properties.csv"),
        help="Fisierul CSV generat.",
    )
    parser.add_argument(
        "--class-map",
        type=str,
        default=None,
        help=(
            "Mapare JSON intre class_id si nominal, de ex. "
            '\'{"0":"1_cent","1":"2_cent","2":"5_cent"}\' '
            "sau calea catre un fisier JSON cu aceasta structura."
        ),
    )
    args = parser.parse_args()

    dataset_dir = args.input_dir.resolve()
    metadata = load_metadata(dataset_dir)
    class_map = load_class_map(args.class_map)
    rows = collect_yolo_rows(dataset_dir, metadata, class_map)
    if not rows:
        rows = collect_image_rows(dataset_dir, metadata)
    write_dataset(rows, args.output.resolve())

    print(f"Imagini gasite: {len(rows)}")
    print(f"CSV generat: {args.output.resolve()}")


if __name__ == "__main__":
    main()
