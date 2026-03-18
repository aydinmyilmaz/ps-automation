#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app_paths import SOURCE_PROJECT_ROOT


DEFAULT_SCORE_CSV = SOURCE_PROJECT_ROOT / "output" / "archive" / "names_data" / "unified_popular_names_3000_scored.csv"
DEFAULT_REPORT_JSON = SOURCE_PROJECT_ROOT / "output" / "archive" / "names_data" / "names_data_report.json"
DEFAULT_OUTPUT_DIR = SOURCE_PROJECT_ROOT / "output" / "gmail_name_sync" / "08_popular_name_lists"
DEFAULT_BATCH_SIZE = 500
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


@dataclass(frozen=True)
class PlannedBatch:
    batch_index: int
    names: list[str]
    file_name: str
    source_type: str
    source_label: str
    popularity_start_rank: int | None = None
    popularity_end_rank: int | None = None
    scored_overlap_count: int = 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a folder of processed and remaining name batches for desktop rendering.",
    )
    parser.add_argument("--score-csv", type=Path, default=DEFAULT_SCORE_CSV)
    parser.add_argument("--names-data-report", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--processed-dir", type=Path, action="append", default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    return parser.parse_args(argv)


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.replace("-", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip().casefold()
    return normalized


def load_processed_names(directories: list[Path]) -> tuple[set[str], set[str]]:
    processed_keys: set[str] = set()
    processed_raw: set[str] = set()
    for directory in directories:
        resolved = directory.expanduser().resolve()
        if not resolved.exists():
            continue
        for path in resolved.rglob("*.png"):
            processed_raw.add(path.stem)
            processed_keys.add(normalize_name(path.stem))
    return processed_keys, processed_raw


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


def load_text_names(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def unique_names(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = normalize_name(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def sort_names_alpha(items: list[str]) -> list[str]:
    return sorted(items, key=lambda value: (normalize_name(value), value.casefold()))


def write_lines(path: Path, items: list[str]) -> None:
    path.write_text("\n".join(items) + "\n", encoding="utf-8")


def cleanup_previous_outputs(output_dir: Path) -> None:
    patterns = [
        "popular_names_*.txt",
        "processed_actual_unique_names*.txt",
        "popular_name_list_folder_report.json",
    ]
    for pattern in patterns:
        for path in output_dir.glob(pattern):
            if path.is_file():
                path.unlink()


def load_processed_batches(
    directories: list[Path],
    score_rows: list[ScoreRow],
) -> tuple[list[PlannedBatch], set[str], list[dict[str, object]]]:
    scored_rank_by_key = {
        normalize_name(row.name): index
        for index, row in enumerate(score_rows, start=1)
    }
    batches: list[PlannedBatch] = []
    used_scored_keys: set[str] = set()
    source_reports: list[dict[str, object]] = []

    for batch_index, directory in enumerate(directories, start=1):
        selected_names_path = directory / "selected_names.txt"
        if not selected_names_path.exists():
            continue
        raw_names = unique_names(load_text_names(selected_names_path))
        if not raw_names:
            continue
        names = sort_names_alpha(raw_names)
        keys = {normalize_name(name) for name in raw_names}
        scored_ranks = sorted(scored_rank_by_key[key] for key in keys if key in scored_rank_by_key)
        used_scored_keys.update(key for key in keys if key in scored_rank_by_key)
        file_name = f"popular_names_batch_{batch_index}.txt"
        batches.append(
            PlannedBatch(
                batch_index=batch_index,
                names=names,
                file_name=file_name,
                source_type="processed_existing_batch",
                source_label=directory.name,
                popularity_start_rank=scored_ranks[0] if scored_ranks else None,
                popularity_end_rank=scored_ranks[-1] if scored_ranks else None,
                scored_overlap_count=len(scored_ranks),
            )
        )
        source_reports.append(
            {
                "batchIndex": batch_index,
                "sourceDir": str(directory),
                "selectedNamesPath": str(selected_names_path),
                "count": len(names),
                "scoredOverlapCount": len(scored_ranks),
                "popularityStartRank": scored_ranks[0] if scored_ranks else None,
                "popularityEndRank": scored_ranks[-1] if scored_ranks else None,
                "first10": names[:10],
            }
        )

    return batches, used_scored_keys, source_reports


def build_remaining_batches(
    score_rows: list[ScoreRow],
    excluded_scored_keys: set[str],
    *,
    batch_size: int,
    starting_batch_index: int,
) -> tuple[list[PlannedBatch], list[ScoreRow]]:
    remaining_rows = [
        row
        for row in score_rows
        if normalize_name(row.name) not in excluded_scored_keys
    ]
    batches: list[PlannedBatch] = []
    next_batch_index = starting_batch_index
    for start_index in range(0, len(remaining_rows), batch_size):
        chunk = remaining_rows[start_index : start_index + batch_size]
        chunk_names = [row.name for row in chunk]
        alpha_names = sort_names_alpha(chunk_names)
        batches.append(
            PlannedBatch(
                batch_index=next_batch_index,
                names=alpha_names,
                file_name=f"popular_names_batch_{next_batch_index}.txt",
                source_type="remaining_popularity_chunk",
                source_label=f"remaining ranks {start_index + 1}-{start_index + len(chunk)}",
                popularity_start_rank=start_index + 1,
                popularity_end_rank=start_index + len(chunk),
                scored_overlap_count=0,
            )
        )
        next_batch_index += 1
    return batches, remaining_rows


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.batch_size < 1:
        raise ValueError("--batch-size must be >= 1")

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cleanup_previous_outputs(output_dir)

    processed_dirs = args.processed_dir or DEFAULT_PROCESSED_DIRS
    processed_dirs = [path.expanduser().resolve() for path in processed_dirs]

    score_rows = load_score_rows(args.score_csv)
    processed_keys, processed_raw = load_processed_names(processed_dirs)
    processed_batches, used_scored_keys, processed_source_reports = load_processed_batches(processed_dirs, score_rows)
    remaining_batches, remaining_rows = build_remaining_batches(
        score_rows,
        used_scored_keys,
        batch_size=args.batch_size,
        starting_batch_index=len(processed_batches) + 1,
    )

    all_batches = processed_batches + remaining_batches
    combined_all_names: list[str] = []
    batch_entries: list[dict[str, object]] = []
    for batch in all_batches:
        batch_path = output_dir / batch.file_name
        write_lines(batch_path, batch.names)
        combined_all_names.extend(batch.names)
        batch_entries.append(
            {
                "batchIndex": batch.batch_index,
                "count": len(batch.names),
                "file": str(batch_path),
                "sourceType": batch.source_type,
                "sourceLabel": batch.source_label,
                "popularityStartRank": batch.popularity_start_rank,
                "popularityEndRank": batch.popularity_end_rank,
                "scoredOverlapCount": batch.scored_overlap_count,
                "first10": batch.names[:10],
            }
        )

    all_path = output_dir / "popular_names_all_batches.txt"
    write_lines(all_path, combined_all_names)

    report = {
        "scoreCsv": str(args.score_csv.expanduser().resolve()),
        "namesDataReport": str(args.names_data_report.expanduser().resolve()),
        "processedDirs": [str(path) for path in processed_dirs],
        "processedActualUniqueCount": len(processed_raw),
        "processedInScoredCount": sum(1 for row in score_rows if normalize_name(row.name) in processed_keys),
        "processedOutsideScoredCount": len(processed_raw) - sum(1 for row in score_rows if normalize_name(row.name) in processed_keys),
        "scoreRowCount": len(score_rows),
        "remainingScoredCountAfterBatch1And2": len(remaining_rows),
        "batchSize": args.batch_size,
        "allBatchesFile": str(all_path),
        "totalBatchCount": len(batch_entries),
        "processedBatchCount": len(processed_batches),
        "remainingBatchCount": len(remaining_batches),
        "allFile": str(all_path),
        "batches": batch_entries,
        "processedBatchSources": processed_source_reports,
        "remainingFirst20ByPopularity": [row.name for row in remaining_rows[:20]],
        "remainingLast20ByPopularity": [row.name for row in remaining_rows[-20:]],
    }
    report_path = output_dir / "popular_name_list_folder_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"[DONE] Output dir: {output_dir}")
    print(f"[DONE] Processed actual unique names: {len(processed_raw)}")
    print(f"[DONE] Processed batches preserved: {len(processed_batches)}")
    print(f"[DONE] Remaining scored names after processed batches: {len(remaining_rows)}")
    print(f"[DONE] All batches list: {all_path}")
    print(f"[DONE] Batch count: {len(batch_entries)}")
    print(f"[DONE] Report: {report_path}")


if __name__ == "__main__":
    main()
