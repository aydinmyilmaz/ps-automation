#!/usr/bin/env python3
"""
Chunked pipeline for first N names using the existing single-render path.

Important: each name is rendered in a fresh run to avoid Photoshop state carryover.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from ps_single_renderer import STYLE_CHOICES, render_name, sanitize_filename


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_NAMES_FILE = BASE_DIR / "data" / "final_names" / "unified_popular_names_3000.txt"
DEFAULT_OUT_ROOT = BASE_DIR / "output" / "chunked_runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chunked first-N renderer.")
    parser.add_argument("--names-file", default=str(DEFAULT_NAMES_FILE), help="Source txt (1 name per line)")
    parser.add_argument("--limit", type=int, default=10, help="Take first N names")
    parser.add_argument("--chunk-size", type=int, default=5, help="Chunk size for progress checkpoints")
    parser.add_argument("--style", default="PEMBE", choices=STYLE_CHOICES, help="Style group")
    parser.add_argument("--uppercase", action="store_true", help="Render names as uppercase")
    parser.add_argument("--output-dir", default="", help="Run directory")
    parser.add_argument("--resume", action="store_true", help="Resume from progress.json")
    return parser.parse_args()


def read_names(path: Path, limit: int) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Names file not found: {path}")
    names = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not names:
        raise ValueError(f"Names file empty: {path}")
    if limit <= 0:
        raise ValueError("--limit must be > 0")
    return names[:limit]


def make_chunks(values: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        raise ValueError("--chunk-size must be > 0")
    return [values[i : i + size] for i in range(0, len(values), size)]


def unique_target(run_dir: Path, base_name: str, style: str) -> Path:
    first = run_dir / f"{base_name}_{style}.png"
    if not first.exists():
        return first
    i = 2
    while True:
        p = run_dir / f"{base_name}_{style}_{i}.png"
        if not p.exists():
            return p
        i += 1


def main() -> int:
    args = parse_args()
    names_file = Path(args.names_file).expanduser().resolve()
    selected = read_names(names_file, args.limit)
    chunked = make_chunks(selected, args.chunk_size)

    if args.output_dir:
        run_dir = Path(args.output_dir).expanduser().resolve()
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = (DEFAULT_OUT_ROOT / f"first{args.limit}_{args.style}_{ts}").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    progress_file = run_dir / "progress.json"

    state = {
        "names_file": str(names_file),
        "style": args.style,
        "limit": args.limit,
        "chunk_size": args.chunk_size,
        "uppercase": args.uppercase,
        "selected_names": selected,
        "completed_names": [],
        "results": [],
    }
    if args.resume and progress_file.exists():
        state = json.loads(progress_file.read_text(encoding="utf-8"))

    print(f"[INFO] Output dir: {run_dir}")
    print(f"[INFO] Names: {len(selected)} | Chunks: {len(chunked)} | Style: {args.style}")

    for idx, chunk in enumerate(chunked):
        print(f"[RUN ] Chunk {idx + 1}/{len(chunked)} -> {chunk}")
        for raw_name in chunk:
            if raw_name in state.get("completed_names", []):
                print(f"[SKIP] {raw_name} already done")
                continue

            render_text = raw_name.upper() if args.uppercase else raw_name
            try:
                generated = render_name(render_text, args.style)
                target = unique_target(run_dir, sanitize_filename(raw_name), args.style)
                shutil.move(str(generated), str(target))
                state["completed_names"].append(raw_name)
                state["results"].append(
                    {
                        "name": raw_name,
                        "render_text": render_text,
                        "ok": True,
                        "file": str(target),
                    }
                )
                print(f"[ OK ] {raw_name} -> {target.name}")
            except Exception as exc:  # noqa: BLE001
                state["results"].append(
                    {
                        "name": raw_name,
                        "render_text": render_text,
                        "ok": False,
                        "error": str(exc),
                    }
                )
                progress_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
                print(f"[FAIL] {raw_name}: {exc}")
                print(f"[INFO] Progress saved: {progress_file}")
                return 1

        progress_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(f"[SAVE] Progress checkpoint after chunk {idx + 1}")

    print("[DONE] Pipeline completed.")
    print(f"[INFO] Progress file: {progress_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
