#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app_paths import SOURCE_PROJECT_ROOT


DEFAULT_SCORE_CSV = SOURCE_PROJECT_ROOT / "output" / "archive" / "names_data" / "unified_popular_names_3000_scored.csv"
DEFAULT_REPORT_JSON = SOURCE_PROJECT_ROOT / "output" / "archive" / "names_data" / "names_data_report.json"
DEFAULT_OUTPUT_DIR = SOURCE_PROJECT_ROOT / "output" / "gmail_name_sync" / "04_derived"
DEFAULT_TOTAL_COUNT = 1000
DEFAULT_BATCH_SIZE = 500
DEFAULT_OUTPUT_PREFIX = "popular_names_unprocessed_next_1000"
DEFAULT_PROCESSED_DIRS = [
    SOURCE_PROJECT_ROOT / "output" / "desktop_batch_runs_0_500",
    SOURCE_PROJECT_ROOT / "desktop_batch_runs_5000_1000",
]


@dataclass(frozen=True)
class ScoreRow:
    name: str
    score: int
    us_modern_2010_plus: int
    nyc_recent_2018_plus: int


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the next unprocessed popular-name batches from the scored 3000-name list.",
    )
    parser.add_argument(
        "--score-csv",
        type=Path,
        default=DEFAULT_SCORE_CSV,
        help="Scored popularity CSV ordered by descending popularity.",
    )
    parser.add_argument(
        "--names-data-report",
        type=Path,
        default=DEFAULT_REPORT_JSON,
        help="Optional names_data report JSON for metadata only.",
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        action="append",
        default=None,
        help="Directory of already rendered PNG outputs. Repeatable.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for generated batch files.",
    )
    parser.add_argument(
        "--total-count",
        type=int,
        default=DEFAULT_TOTAL_COUNT,
        help="How many unprocessed names to select.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Names per batch file.",
    )
    parser.add_argument(
        "--output-prefix",
        default=DEFAULT_OUTPUT_PREFIX,
        help="Filename prefix for generated outputs.",
    )
    return parser.parse_args(argv)


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.replace("-", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip().casefold()
    return normalized


def load_processed_names(directories: list[Path]) -> set[str]:
    processed: set[str] = set()
    for directory in directories:
        resolved = directory.expanduser().resolve()
        if not resolved.exists():
            continue
        for path in resolved.rglob("*.png"):
            processed.add(normalize_name(path.stem))
    return processed


def load_score_rows(path: Path) -> list[ScoreRow]:
    rows: list[ScoreRow] = []
    with path.expanduser().resolve().open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(
                ScoreRow(
                    name=str(row["name"]).strip(),
                    score=int(row["score"]),
                    us_modern_2010_plus=int(row["us_modern_2010_plus"]),
                    nyc_recent_2018_plus=int(row["nyc_recent_2018_plus"]),
                )
            )
    return rows


def write_lines(path: Path, rows: list[str]) -> None:
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    score_csv = args.score_csv.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    processed_dirs = args.processed_dir or DEFAULT_PROCESSED_DIRS
    processed_dirs = [path.expanduser().resolve() for path in processed_dirs]

    if args.total_count < 1:
        raise ValueError("--total-count must be >= 1")
    if args.batch_size < 1:
        raise ValueError("--batch-size must be >= 1")
    if not score_csv.exists():
        raise FileNotFoundError(f"Missing score CSV: {score_csv}")

    score_rows = load_score_rows(score_csv)
    processed = load_processed_names(processed_dirs)

    remaining = [row for row in score_rows if normalize_name(row.name) not in processed]
    selected = remaining[: args.total_count]
    if len(selected) < args.total_count:
        raise ValueError(f"Only {len(selected)} unprocessed names are available; need {args.total_count}.")

    selected_names = [row.name for row in selected]
    batches = [
        selected_names[index : index + args.batch_size]
        for index in range(0, len(selected_names), args.batch_size)
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    all_path = output_dir / f"{args.output_prefix}_all.txt"
    batch_paths: list[Path] = []
    write_lines(all_path, selected_names)

    for batch_index, names in enumerate(batches, start=1):
        start = (batch_index - 1) * args.batch_size + 1
        end = start + len(names) - 1
        batch_path = output_dir / f"{args.output_prefix}_{start}_{end}.txt"
        write_lines(batch_path, names)
        batch_paths.append(batch_path)

    report_path = output_dir / f"{args.output_prefix}_report.json"
    payload = {
        "scoreCsv": str(score_csv),
        "namesDataReport": str(args.names_data_report.expanduser().resolve()),
        "processedDirs": [str(path) for path in processed_dirs],
        "processedUniqueNameCount": len(processed),
        "scoreRowCount": len(score_rows),
        "remainingCount": len(remaining),
        "selectedCount": len(selected),
        "batchSize": args.batch_size,
        "batchCount": len(batches),
        "allOut": str(all_path),
        "batchOut": [str(path) for path in batch_paths],
        "first20": selected_names[:20],
        "last20": selected_names[-20:],
        "selectedRows": [asdict(row) for row in selected],
    }
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"[DONE] Processed unique names: {len(processed)}")
    print(f"[DONE] Remaining scored names: {len(remaining)}")
    print(f"[DONE] Selected names: {len(selected)}")
    print(f"[DONE] All names file: {all_path}")
    for path in batch_paths:
        print(f"[DONE] Batch file: {path}")
    print(f"[DONE] Report: {report_path}")


if __name__ == "__main__":
    main()
