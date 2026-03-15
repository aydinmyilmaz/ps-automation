#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import re
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app_paths import bundled_names_file, gmail_extracted_dir, gmail_reports_dir


DEFAULT_INPUT_GLOB = "gmail_extracted_names_20[0-9][0-9].txt"
DEFAULT_OUTPUT_JSON = gmail_reports_dir() / "gmail_first_name_summary.json"
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge Gmail extracted name files and summarize first-name frequencies versus the curated 3000-name list.",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=gmail_extracted_dir(),
        help="Directory containing year-based gmail_extracted_names_<year>.txt files.",
    )
    parser.add_argument(
        "--input-glob",
        default=DEFAULT_INPUT_GLOB,
        help="Glob for input files inside --input-dir.",
    )
    parser.add_argument(
        "--base-names-file",
        type=Path,
        default=bundled_names_file(),
        help="Curated 3000-name txt file for comparison.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUTPUT_JSON,
        help="Output JSON path.",
    )
    return parser.parse_args(argv)


def smart_title(word: str) -> str:
    separators = {"-", "'", "’"}
    parts: list[str] = []
    token = ""
    for char in word:
        if char in separators:
            if token:
                parts.append(token[:1].upper() + token[1:].lower())
                token = ""
            parts.append(char)
        else:
            token += char
    if token:
        parts.append(token[:1].upper() + token[1:].lower())
    return "".join(parts)


def normalize_token(token: str) -> str:
    value = token.strip()
    value = re.sub(r"^[^A-Za-zÀ-ÖØ-öø-ÿ]+|[^A-Za-zÀ-ÖØ-öø-ÿ'’\-]+$", "", value)
    return value


def extract_first_name(full_name: str) -> str | None:
    cleaned = " ".join(full_name.strip().split())
    if not cleaned:
        return None
    tokens = [normalize_token(token) for token in cleaned.split(" ")]
    tokens = [token for token in tokens if token]
    if not tokens:
        return None
    first = tokens[0]
    lowered = first.casefold()
    if lowered in SUFFIXES:
        return None
    if not re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]*", first):
        return None
    return smart_title(first)


def load_lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_base_names(path: Path) -> set[str]:
    names = load_lines(path.expanduser().resolve())
    return {smart_title(name).casefold() for name in names}


def year_from_filename(path: Path) -> str:
    match = re.search(r"_(20\d{2})\.txt$", path.name)
    return match.group(1) if match else path.stem


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    input_dir = args.input_dir.expanduser().resolve()
    files = sorted(
        path for path in input_dir.glob(args.input_glob)
        if path.is_file() and not path.name.endswith("_unique.txt")
    )
    if not files:
        raise FileNotFoundError(f"No input files found in {input_dir} with glob {args.input_glob}")

    curated_names = load_base_names(args.base_names_file)
    first_name_counter: Counter[str] = Counter()
    yearly_counter: dict[str, Counter[str]] = defaultdict(Counter)
    source_files: list[str] = []
    total_raw_rows = 0
    skipped_rows = 0

    for path in files:
        year = year_from_filename(path)
        source_files.append(str(path))
        for row in load_lines(path):
            total_raw_rows += 1
            first_name = extract_first_name(row)
            if not first_name:
                skipped_rows += 1
                continue
            first_name_counter[first_name] += 1
            yearly_counter[year][first_name] += 1

    sorted_names = sorted(first_name_counter.items(), key=lambda item: (-item[1], item[0].casefold()))
    names_in_curated = []
    names_not_in_curated = []
    for name, count in sorted_names:
        row = {
            "name": name,
            "count": count,
            "in_curated_3000": name.casefold() in curated_names,
            "year_counts": {year: yearly_counter[year][name] for year in sorted(yearly_counter) if yearly_counter[year][name]},
        }
        if row["in_curated_3000"]:
            names_in_curated.append(row)
        else:
            names_not_in_curated.append(row)

    payload = {
        "source_files": source_files,
        "base_names_file": str(args.base_names_file.expanduser().resolve()),
        "total_raw_rows": total_raw_rows,
        "skipped_rows": skipped_rows,
        "total_first_name_hits": sum(first_name_counter.values()),
        "unique_first_names": len(first_name_counter),
        "curated_3000_match_count": len(names_in_curated),
        "curated_3000_missing_count": len(names_not_in_curated),
        "names_in_curated_3000": names_in_curated,
        "names_not_in_curated_3000": names_not_in_curated,
        "all_first_names": [
            {
                "name": name,
                "count": count,
                "in_curated_3000": name.casefold() in curated_names,
                "year_counts": {year: yearly_counter[year][name] for year in sorted(yearly_counter) if yearly_counter[year][name]},
            }
            for name, count in sorted_names
        ],
    }

    output_json = args.output_json.expanduser().resolve()
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"[DONE] Input files: {len(files)}")
    print(f"[DONE] Total raw rows: {total_raw_rows}")
    print(f"[DONE] First-name hits: {payload['total_first_name_hits']}")
    print(f"[DONE] Unique first names: {payload['unique_first_names']}")
    print(f"[DONE] Present in curated 3000: {payload['curated_3000_match_count']}")
    print(f"[DONE] Missing from curated 3000: {payload['curated_3000_missing_count']}")
    print(f"[DONE] JSON summary: {output_json}")


if __name__ == "__main__":
    main()
