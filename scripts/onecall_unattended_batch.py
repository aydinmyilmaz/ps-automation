#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from app_paths import bundled_names_file, default_batch_output_dir
from ps_single_renderer import (
    FONT_POSTSCRIPT_NAME,
    PSD_PATH,
    STYLE_CHOICES,
    crop_png_to_alpha_bounds,
    run_jsx,
    sanitize_filename,
)
PROJECT_ROOT = SCRIPTS_DIR.parent
DEFAULT_NAMES_FILE = bundled_names_file()
DEFAULT_OUTPUT_ROOT = default_batch_output_dir()
PHOTOSHOP_APP_CANDIDATES = (
    Path("/Applications/Adobe Photoshop 2026/Adobe Photoshop 2026.app"),
    Path("/Applications/Adobe Photoshop 2026.app"),
)
DEFAULT_TEST20 = [
    "Abby",
    "Batsheva",
    "Cade",
    "Damarion",
    "Easton",
    "Felicity",
    "Gael",
    "Hadassah",
    "Iker",
    "Jamarcus",
    "Kaeden",
    "Langston",
    "Maci",
    "Nathalia",
    "Paul",
    "Quentin",
    "Raul",
    "Salvador",
    "Tallulah",
    "Zachariah",
]

RETRYABLE_MARKERS = (
    "Connection is invalid",
    "scratch disks are full",
    "General Photoshop error",
    "Photoshop got an error",
    "timed out",
    "Application isn't running",
    "Application isn’t running",
    "(-600)",
)

MIN_FREE_DISK_GB_DEFAULT = 7.0
SCRATCH_RECOVERY_BUFFER_GB = 1.0
PERIODIC_RESTART_HEADROOM_BUFFER_GB = 3.0
MAX_PERIODIC_RESTART_DEFER_MULTIPLIER = 4
PHOTOSHOP_TEMP_PATTERNS = (
    "Photoshop Temp*",
    "PhotoshopScratch*",
)


@dataclass(frozen=True)
class Job:
    style: str
    text: str
    output_path: Path


@dataclass(frozen=True)
class ScratchRecoveryResult:
    path: Path | None
    free_gb: float | None
    photoshop_restarted: bool = False


class RecoverableScratchHeadroomError(RuntimeError):
    pass


def is_completed_output(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unattended one-call chunk batch renderer (Photoshop JSX per chunk)."
    )
    parser.add_argument(
        "--names-file",
        type=Path,
        default=DEFAULT_NAMES_FILE,
        help="Name list file (one per line). Defaults to bundled 3000-name list.",
    )
    parser.add_argument(
        "--name-source",
        choices=("full", "test20", "custom"),
        default="full",
        help='Name source: "full" uses names-file, "test20" uses built-in 20-name test set, "custom" uses --custom-names-json.',
    )
    parser.add_argument(
        "--test-count",
        type=int,
        default=20,
        help='When --name-source=test20, render first N names from test list (1..20).',
    )
    parser.add_argument(
        "--custom-names-json",
        default="",
        help='When --name-source=custom, JSON array of names. Example: ["KEREM","MERT"]',
    )
    parser.add_argument("--max-names", type=int, default=0, help="Limit number of names after loading.")
    parser.add_argument(
        "--styles",
        default="all",
        help='Comma-separated styles or "all".',
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=10,
        help="Jobs per single JSX call. Start small for stability, then increase.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Recoverable retries per chunk after the first failed attempt.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Output root folder.",
    )
    parser.add_argument("--psd", type=Path, default=PSD_PATH, help="PSD file path to render from.")
    parser.add_argument(
        "--letters",
        default="all",
        help='Filter names by first letter(s). Examples: "A,B,C" or "ABC" or "all".',
    )
    parser.add_argument(
        "--photoshop-exec",
        type=Path,
        default=None,
        help="Optional Photoshop executable/app path (useful on Windows).",
    )
    parser.add_argument("--uppercase", action="store_true", default=True, help="Uppercase names before render.")
    parser.add_argument("--no-uppercase", dest="uppercase", action="store_false")
    parser.add_argument("--kill-photoshop-first", action="store_true", help="Kill Photoshop once before starting.")
    parser.add_argument("--chunk-timeout", type=int, default=300, help="Timeout seconds for each chunk JSX call.")
    parser.add_argument(
        "--restart-every-chunks",
        type=int,
        default=0,
        help="Restart Photoshop after every N chunks (0 disables). PSD stays warm in memory when disabled.",
    )
    parser.add_argument(
        "--supervisor",
        action="store_true",
        default=True,
        help="Automatically relaunch the run after recoverable crashes until all jobs are completed.",
    )
    parser.add_argument("--no-supervisor", dest="supervisor", action="store_false")
    parser.add_argument("--max-supervisor-restarts", type=int, default=200, help="Max supervisor relaunch count.")
    parser.add_argument("--supervisor-sleep", type=int, default=8, help="Seconds to wait before relaunch.")
    parser.add_argument(
        "--min-free-disk-gb",
        type=float,
        default=MIN_FREE_DISK_GB_DEFAULT,
        help="Fail fast if the scratch/output disk has less free space than this threshold.",
    )
    parser.add_argument(
        "--prevent-idle-sleep",
        dest="prevent_idle_sleep",
        action="store_true",
        default=True,
        help="Prevent idle sleep/screensaver while the batch is running.",
    )
    parser.add_argument(
        "--allow-idle-sleep",
        dest="prevent_idle_sleep",
        action="store_false",
        help="Do not request OS sleep prevention while the batch is running.",
    )
    return parser.parse_args(argv)


@contextmanager
def prevent_idle_sleep(enabled: bool):
    if not enabled:
        yield
        return

    if sys.platform == "darwin":
        blocker = None
        try:
            blocker = subprocess.Popen(
                ["caffeinate", "-disu", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"[INFO] Preventing idle sleep via caffeinate (pid {blocker.pid}).")
        except FileNotFoundError:
            print("[WARN] caffeinate is unavailable; macOS may still enter screensaver/sleep.")
        try:
            yield
        finally:
            if blocker and blocker.poll() is None:
                blocker.terminate()
                try:
                    blocker.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    blocker.kill()
        return

    if sys.platform.startswith("win"):
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        try:
            import ctypes

            result = ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            )
            if not result:
                raise OSError("SetThreadExecutionState failed")
            print("[INFO] Preventing idle sleep via SetThreadExecutionState.")
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] Could not prevent idle sleep on Windows: {exc}")
            yield
            return
        try:
            yield
        finally:
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception:  # noqa: BLE001
                pass
        return

    yield


def parse_custom_names_json(value: str) -> list[str]:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("--custom-names-json is required when --name-source=custom")
    try:
        parsed = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("--custom-names-json must be valid JSON array.") from exc
    if not isinstance(parsed, list):
        raise ValueError("--custom-names-json must be a JSON array.")
    names = [str(x).strip() for x in parsed if str(x).strip()]
    if not names:
        raise ValueError("Custom names list is empty.")
    return names


def load_names(
    name_source: str,
    names_file: Path | None,
    max_names: int,
    test_count: int,
    custom_names_json: str,
) -> list[str]:
    if name_source == "test20":
        if test_count <= 0:
            raise ValueError("--test-count must be > 0")
        names = list(DEFAULT_TEST20[: min(test_count, len(DEFAULT_TEST20))])
    elif name_source == "custom":
        names = parse_custom_names_json(custom_names_json)
    else:
        if names_file is None:
            raise ValueError("--names-file is required.")
        p = names_file.expanduser().resolve()
        names = [x.strip() for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return names


def parse_styles(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return list(STYLE_CHOICES)
    requested = [x.strip() for x in value.split(",") if x.strip()]
    invalid = [s for s in requested if s not in STYLE_CHOICES]
    if invalid:
        raise ValueError(f"Invalid styles: {invalid}")
    return requested


def parse_letters(value: str) -> set[str]:
    raw = value.strip().upper()
    if raw in {"", "ALL", "*"}:
        return set()
    cleaned = raw.replace(",", "").replace(" ", "")
    letters = {ch for ch in cleaned if "A" <= ch <= "Z"}
    if not letters:
        raise ValueError("Invalid --letters value. Use e.g. A,B,C or ABC or all.")
    return letters


def filter_names_by_letters(names: list[str], letters: set[str]) -> list[str]:
    if not letters:
        return names
    return [n for n in names if n and n[0].upper() in letters]


def _run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def _gib(value: int) -> float:
    return value / (1024 ** 3)


def scratch_probe_paths(output_root: Path, psd_path: Path) -> list[Path]:
    candidates = [
        output_root,
        psd_path,
        Path.home(),
        Path(tempfile.gettempdir()),
        Path("/private/var/tmp"),
        Path("/tmp"),
    ]
    seen: set[Path] = set()
    resolved: list[Path] = []
    for candidate in candidates:
        try:
            target = candidate.expanduser().resolve()
        except OSError:
            continue
        if target in seen or not target.exists():
            continue
        seen.add(target)
        resolved.append(target)
    return resolved


def assert_scratch_headroom(output_root: Path, psd_path: Path, min_free_disk_gb: float) -> None:
    low_space: list[str] = []
    for path in scratch_probe_paths(output_root, psd_path):
        free_gb = _gib(shutil.disk_usage(path).free)
        if free_gb < min_free_disk_gb:
            low_space.append(f"{path} has {free_gb:.1f} GiB free")
    if low_space:
        joined = "; ".join(low_space)
        raise ValueError(
            "Low Photoshop scratch-disk headroom. "
            f"Need at least {min_free_disk_gb:.1f} GiB free before rendering; {joined}."
        )


def lowest_scratch_headroom(output_root: Path, psd_path: Path) -> tuple[Path | None, float | None]:
    lowest_path: Path | None = None
    lowest_free_gb: float | None = None
    for path in scratch_probe_paths(output_root, psd_path):
        free_gb = _gib(shutil.disk_usage(path).free)
        if lowest_free_gb is None or free_gb < lowest_free_gb:
            lowest_path = path
            lowest_free_gb = free_gb
    return lowest_path, lowest_free_gb


def recover_scratch_headroom(
    output_root: Path,
    psd_path: Path,
    photoshop_exec: Path | None,
    min_free_disk_gb: float,
    reason: str,
) -> ScratchRecoveryResult:
    lowest_path, lowest_free_gb = lowest_scratch_headroom(output_root, psd_path)
    if lowest_free_gb is None:
        return ScratchRecoveryResult(path=lowest_path, free_gb=lowest_free_gb)
    if lowest_free_gb >= min_free_disk_gb:
        return ScratchRecoveryResult(path=lowest_path, free_gb=lowest_free_gb)

    print(
        f"[WARN] Scratch headroom low after {reason}: "
        f"{lowest_free_gb:.2f} GiB free on {lowest_path}. Attempting recovery."
    )
    removed_count, removed_bytes = cleanup_photoshop_temp_files()
    if removed_count:
        print(
            f"[INFO] Removed {removed_count} Photoshop temp item(s), "
            f"freed {_gib(removed_bytes):.2f} GiB during recovery."
        )

    # Re-check after cleanup — only restart Photoshop if still below threshold.
    lowest_path, lowest_free_gb = lowest_scratch_headroom(output_root, psd_path)
    if lowest_free_gb is not None and lowest_free_gb >= min_free_disk_gb:
        print(
            f"[INFO] Cleanup alone recovered scratch to {lowest_free_gb:.2f} GiB "
            f"on {lowest_path} — Photoshop restart not needed."
        )
        return ScratchRecoveryResult(path=lowest_path, free_gb=lowest_free_gb)

    print(
        f"[INFO] Still only {lowest_free_gb:.2f} GiB free after cleanup — restarting Photoshop "
        "to release locked scratch files."
    )
    restart_photoshop(photoshop_exec)
    time.sleep(2)
    removed_count, removed_bytes = cleanup_photoshop_temp_files()
    if removed_count:
        print(
            f"[INFO] Removed {removed_count} more Photoshop temp item(s), "
            f"freed {_gib(removed_bytes):.2f} GiB after restart."
        )
    lowest_path, lowest_free_gb = lowest_scratch_headroom(output_root, psd_path)
    if lowest_free_gb is None:
        return ScratchRecoveryResult(path=lowest_path, free_gb=lowest_free_gb, photoshop_restarted=True)

    hard_stop_threshold = 2.0
    if lowest_free_gb < hard_stop_threshold:
        raise RecoverableScratchHeadroomError(
            "Critically low disk after recovery. "
            f"Only {lowest_free_gb:.1f} GiB free on {lowest_path}; "
            f"need at least {hard_stop_threshold:.1f} GiB to avoid data corruption."
        )
    if lowest_free_gb < min_free_disk_gb:
        print(
            f"[WARN] Scratch headroom recovered only partially to {lowest_free_gb:.2f} GiB on {lowest_path}. "
            "Continuing because it is above the hard-stop threshold."
        )
    else:
        print(f"[INFO] Scratch headroom recovered to {lowest_free_gb:.2f} GiB on {lowest_path}.")
    return ScratchRecoveryResult(path=lowest_path, free_gb=lowest_free_gb, photoshop_restarted=True)


def cleanup_photoshop_temp_files() -> tuple[int, int]:
    removed_count = 0
    removed_bytes = 0
    temp_roots = [Path(tempfile.gettempdir()), Path("/private/var/tmp"), Path("/tmp")]
    seen: set[Path] = set()
    for root in temp_roots:
        try:
            target_root = root.expanduser().resolve()
        except OSError:
            continue
        if target_root in seen or not target_root.exists():
            continue
        seen.add(target_root)
        for pattern in PHOTOSHOP_TEMP_PATTERNS:
            for path in target_root.glob(pattern):
                try:
                    if path.is_file():
                        removed_bytes += path.stat().st_size
                        path.unlink()
                        removed_count += 1
                    elif path.is_dir():
                        removed_bytes += sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
                        shutil.rmtree(path, ignore_errors=True)
                        removed_count += 1
                except OSError:
                    continue
    return removed_count, removed_bytes


def kill_photoshop(photoshop_exec: Path | None = None) -> None:
    if sys.platform == "darwin":
        _run_cmd(["pkill", "-f", "Adobe Photoshop 2026.app/Contents/MacOS/Adobe Photoshop 2026"])
        deadline = time.time() + 20.0
        while time.time() < deadline:
            if not is_photoshop_running(photoshop_exec):
                break
            time.sleep(0.5)
        return
    if sys.platform.startswith("win"):
        _run_cmd(["taskkill", "/IM", "Photoshop.exe", "/F"])
        _run_cmd(["taskkill", "/IM", "Adobe Photoshop 2026.exe", "/F"])
        time.sleep(1.0)
        return


def is_photoshop_running(photoshop_exec: Path | None = None) -> bool:
    if sys.platform == "darwin":
        probe = _run_cmd(["pgrep", "-f", "Adobe Photoshop 2026.app/Contents/MacOS/Adobe Photoshop 2026"])
        return probe.returncode == 0
    if sys.platform.startswith("win"):
        probe = _run_cmd(["tasklist", "/FI", "IMAGENAME eq Photoshop.exe"])
        if "Photoshop.exe" in (probe.stdout or ""):
            return True
        probe2 = _run_cmd(["tasklist", "/FI", "IMAGENAME eq Adobe Photoshop 2026.exe"])
        return "Adobe Photoshop 2026.exe" in (probe2.stdout or "")
    return False


def _photoshop_app_bundle(photoshop_exec: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if photoshop_exec:
        resolved = photoshop_exec.expanduser().resolve()
        candidates.append(resolved)
        candidates.extend(resolved.parents)
    candidates.extend(PHOTOSHOP_APP_CANDIDATES)
    for candidate in candidates:
        if candidate.suffix == ".app" and candidate.exists():
            return candidate
    return None


def start_photoshop(photoshop_exec: Path | None = None) -> None:
    if sys.platform == "darwin":
        launched = False
        app_bundle = _photoshop_app_bundle(photoshop_exec)
        if app_bundle:
            _run_cmd(["open", "-na", str(app_bundle)])
            launched = True
        if not launched:
            _run_cmd(["open", "-a", "Adobe Photoshop 2026"])
    elif sys.platform.startswith("win"):
        if photoshop_exec and photoshop_exec.exists():
            subprocess.Popen([str(photoshop_exec)])
            deadline = time.time() + 180.0
            while time.time() < deadline:
                if is_photoshop_running(photoshop_exec):
                    time.sleep(1.0)
                    return
                time.sleep(0.5)
            raise RuntimeError("Photoshop failed to start within timeout.")
        # If no explicit executable is provided on Windows, let COM auto-launch on first JSX call.
        return

    deadline = time.time() + 180.0
    while time.time() < deadline:
        if is_photoshop_running(photoshop_exec):
            time.sleep(1.0)
            return
        time.sleep(0.5)
    raise RuntimeError("Photoshop failed to start within timeout.")


def restart_photoshop(photoshop_exec: Path | None = None) -> None:
    kill_photoshop(photoshop_exec)
    if sys.platform.startswith("win") and not photoshop_exec:
        return
    start_photoshop(photoshop_exec)


def is_retryable_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(marker in msg for marker in RETRYABLE_MARKERS)


def build_jobs(names: list[str], styles: list[str], output_root: Path, uppercase: bool) -> list[Job]:
    jobs: list[Job] = []
    for style in styles:
        style_dir = output_root / sanitize_filename(style)
        style_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            render_text = name.upper() if uppercase else name
            out = style_dir / f"{sanitize_filename(render_text)}.png"
            jobs.append(Job(style=style, text=render_text, output_path=out))
    return jobs


def chunked(items: list[Job], size: int) -> list[list[Job]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def build_chunk_jsx(psd_path: Path, jobs: list[Job]) -> str:
    job_rows = [{"style": j.style, "text": j.text, "output": j.output_path.as_posix()} for j in jobs]
    return f"""#target photoshop
app.displayDialogs = DialogModes.NO;

var PSD_PATH = {json.dumps(psd_path.as_posix())};
var FONT_PS = {json.dumps(FONT_POSTSCRIPT_NAME)};
var STYLE_GROUPS = {json.dumps(list(STYLE_CHOICES))};
var JOBS = {json.dumps(job_rows)};
var MIN_TRACKING = 0;

function normalizeName(name) {{
  if (!name) return "";
  return String(name).replace(/\\s+/g, " ").replace(/^\\s+|\\s+$/g, "").toLowerCase();
}}

function namesEqual(a, b) {{
  return normalizeName(a) === normalizeName(b);
}}

function findGroupRecursive(container, name) {{
  for (var i = 0; i < container.layerSets.length; i++) {{
    var g = container.layerSets[i];
    if (namesEqual(g.name, name)) return g;
    var nested = findGroupRecursive(g, name);
    if (nested) return nested;
  }}
  return null;
}}

function findLayerRecursive(container, targetName) {{
  for (var i = 0; i < container.artLayers.length; i++) {{
    if (container.artLayers[i].name === targetName) return container.artLayers[i];
  }}
  for (var j = 0; j < container.layerSets.length; j++) {{
    var found = findLayerRecursive(container.layerSets[j], targetName);
    if (found) return found;
  }}
  return null;
}}

function findFirstSmartObject(container) {{
  for (var i = 0; i < container.artLayers.length; i++) {{
    if (container.artLayers[i].kind === LayerKind.SMARTOBJECT) return container.artLayers[i];
  }}
  for (var j = 0; j < container.layerSets.length; j++) {{
    var found = findFirstSmartObject(container.layerSets[j]);
    if (found) return found;
  }}
  return null;
}}

function findFirstTextLayer(container) {{
  for (var i = 0; i < container.artLayers.length; i++) {{
    if (container.artLayers[i].kind === LayerKind.TEXT) return container.artLayers[i];
  }}
  for (var j = 0; j < container.layerSets.length; j++) {{
    var found = findFirstTextLayer(container.layerSets[j]);
    if (found) return found;
  }}
  return null;
}}

function getBoundsPx(layer) {{
  var b = layer.bounds;
  return {{
    left: b[0].as("px"),
    top: b[1].as("px"),
    right: b[2].as("px"),
    bottom: b[3].as("px")
  }};
}}

function setOnlyStyleVisible(doc, styleName) {{
  for (var k = 0; k < doc.layers.length; k++) {{
    doc.layers[k].visible = false;
  }}
  var targetGroup = null;
  for (var i = 0; i < STYLE_GROUPS.length; i++) {{
    var g = findGroupRecursive(doc, STYLE_GROUPS[i]);
    if (g) {{
      var shouldShow = namesEqual(STYLE_GROUPS[i], styleName);
      g.visible = shouldShow;
      if (shouldShow) targetGroup = g;
    }}
  }}
  while (targetGroup && targetGroup.typename !== "Document") {{
    targetGroup.visible = true;
    targetGroup = targetGroup.parent;
  }}
}}

function exportPng24(documentRef, filePath) {{
  var outFile = new File(filePath);
  var parent = outFile.parent;
  if (parent && !parent.exists) parent.create();
  var opts = new ExportOptionsSaveForWeb();
  opts.format = SaveDocumentType.PNG;
  opts.PNG8 = false;
  opts.transparency = true;
  opts.interlaced = false;
  documentRef.exportDocument(outFile, ExportType.SAVEFORWEB, opts);
}}

function exportTrimmedPng24(documentRef, filePath) {{
  // Trimming is handled by Python PIL crop_png_to_alpha_bounds() after export.
  // This avoids the expensive doc.duplicate() + trim cycle on large PSD files.
  exportPng24(documentRef, filePath);
}}

function purgeHistoryAndCaches() {{
  try {{
    app.purge(PurgeTarget.HISTORYCACHES);
    return;
  }} catch (ignoreHistory) {{}}
  try {{
    app.purge(PurgeTarget.ALLCACHES);
    return;
  }} catch (ignoreAllCaches) {{}}
  try {{
    app.purge(PurgeTarget.HISTORIES);
  }} catch (ignoreHistories) {{}}
}}

function fontExists(postScriptName) {{
  for (var i = 0; i < app.fonts.length; i++) {{
    if (app.fonts[i].postScriptName === postScriptName) return true;
  }}
  return false;
}}

function normalizePath(path) {{
  if (!path) return "";
  return String(path).replace(/\\\\/g, "/").toLowerCase();
}}

function findOpenDocumentByPath(posixPath) {{
  var target = normalizePath(posixPath);
  for (var i = 0; i < app.documents.length; i++) {{
    var d = app.documents[i];
    try {{
      if (normalizePath(d.fullName.fsName) === target || normalizePath(d.fullName.fullName) === target) {{
        return d;
      }}
    }} catch (ignore) {{}}
  }}
  return null;
}}

function fitTextToTemplate(textLayer, templateWidth, templateCenterX) {{
  var nameLen = (textLayer.textItem.contents || "").length;
  var safety = 0.96;
  if (nameLen >= 10) safety = 0.94;
  if (nameLen >= 12) safety = 0.93;
  if (nameLen >= 14) safety = 0.92;
  if (nameLen >= 16) safety = 0.90;
  var targetWidth = templateWidth * safety;

  var currentBounds = getBoundsPx(textLayer);
  var currentWidth = currentBounds.right - currentBounds.left;
  var hScale = textLayer.textItem.horizontalScale;
  if (!hScale || hScale < 1) hScale = 100;
  var minScale = Math.max(30, hScale * 0.30);

  // Binary search for optimal hScale instead of decrementing by 1.
  if (currentWidth > targetWidth) {{
    var lo = Math.round(minScale);
    var hi = Math.round(hScale);
    while (lo < hi) {{
      var mid = Math.round((lo + hi + 1) / 2);
      textLayer.textItem.horizontalScale = mid;
      currentBounds = getBoundsPx(textLayer);
      currentWidth = currentBounds.right - currentBounds.left;
      if (currentWidth > targetWidth) {{
        hi = mid - 1;
      }} else {{
        lo = mid;
      }}
    }}
    hScale = lo;
    textLayer.textItem.horizontalScale = hScale;
    currentBounds = getBoundsPx(textLayer);
    currentWidth = currentBounds.right - currentBounds.left;
  }}

  // Last-resort: tighten tracking via binary search.
  if (currentWidth > targetWidth) {{
    var tracking = textLayer.textItem.tracking;
    if (typeof tracking !== "number") tracking = 0;
    var tLo = MIN_TRACKING;
    var tHi = tracking;
    while (tLo < tHi) {{
      var tMid = Math.round((tLo + tHi + 1) / 2);
      textLayer.textItem.tracking = tMid;
      currentBounds = getBoundsPx(textLayer);
      currentWidth = currentBounds.right - currentBounds.left;
      if (currentWidth > targetWidth) {{
        tHi = tMid - 1;
      }} else {{
        tLo = tMid;
      }}
    }}
    textLayer.textItem.tracking = tLo;
    currentBounds = getBoundsPx(textLayer);
    currentWidth = currentBounds.right - currentBounds.left;
  }}

  if (currentWidth > targetWidth) {{
    throw new Error("Text too long for template: " + textLayer.textItem.contents);
  }}

  var centerX = (currentBounds.left + currentBounds.right) / 2.0;
  textLayer.translate(templateCenterX - centerX, 0);
}}

try {{
  if (!fontExists(FONT_PS)) throw new Error("Font missing: " + FONT_PS);

  var psdFile = new File(PSD_PATH);
  if (!psdFile.exists) throw new Error("PSD not found: " + PSD_PATH);

  var doc = findOpenDocumentByPath(PSD_PATH);
  if (!doc) {{
    doc = app.open(psdFile);
  }}
  app.activeDocument = doc;
  var base = doc.activeHistoryState;
  var baselineByStyle = {{}};
  var processed = 0;
  var lastStyle = "";

  for (var i = 0; i < JOBS.length; i++) {{
    var job = JOBS[i];
    var styleGroup = findGroupRecursive(doc, job.style);
    if (!styleGroup) throw new Error("Style group not found: " + job.style);

    // Only toggle layer visibility when style actually changes.
    if (normalizeName(job.style) !== normalizeName(lastStyle)) {{
      setOnlyStyleVisible(doc, job.style);
      lastStyle = job.style;
    }}

    var textGroup = findLayerRecursive(styleGroup, "TEXT");
    var scope = textGroup ? textGroup : styleGroup;
    var smart = findLayerRecursive(scope, "CUSTOM copy 2");
    if (!smart) smart = findFirstSmartObject(scope);
    if (!smart) throw new Error("Smart object not found in style: " + job.style);

    doc.activeLayer = smart;
    executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);

    var sub = app.activeDocument;
    var textLayer = findLayerRecursive(sub, "Madafaka");
    if (!textLayer) textLayer = findFirstTextLayer(sub);
    if (!textLayer) throw new Error("Text layer not found in smart object.");

    var styleKey = normalizeName(job.style);
    if (!baselineByStyle[styleKey]) {{
      var bb = getBoundsPx(textLayer);
      var baseTemplateWidth = bb.right - bb.left;
      var canvasWidth = sub.width.as("px");
      var canvasPadding = 120;
      var canvasDrivenWidth = Math.max(0, canvasWidth - (canvasPadding * 2));
      baselineByStyle[styleKey] = {{
        hScale: (textLayer.textItem.horizontalScale && textLayer.textItem.horizontalScale >= 1) ? textLayer.textItem.horizontalScale : 100,
        tracking: (typeof textLayer.textItem.tracking === "number") ? textLayer.textItem.tracking : 0,
        centerX: (bb.left + bb.right) / 2.0,
        templateWidth: Math.max(baseTemplateWidth, canvasDrivenWidth),
        templateCenterX: canvasWidth / 2.0
      }};
    }}
    var baseline = baselineByStyle[styleKey];

    textLayer.textItem.horizontalScale = baseline.hScale;
    textLayer.textItem.tracking = baseline.tracking;
    var rb = getBoundsPx(textLayer);
    var rc = (rb.left + rb.right) / 2.0;
    textLayer.translate(baseline.centerX - rc, 0);

    textLayer.textItem.contents = job.text;
    if (textLayer.textItem.font !== FONT_PS) textLayer.textItem.font = FONT_PS;
    fitTextToTemplate(textLayer, baseline.templateWidth, baseline.templateCenterX);

    sub.save();
    sub.close(SaveOptions.SAVECHANGES);
    app.activeDocument = doc;

    exportTrimmedPng24(doc, job.output);
    try {{
      doc.activeHistoryState = base;
    }} catch (ignoreRestore) {{}}
    purgeHistoryAndCaches();
    processed++;
  }}

  // Keep PSD open for reuse by subsequent chunks (avoids expensive re-open).
  "OK|" + processed;
}} catch (e) {{
  // On error, do NOT close the PSD — let it stay open for the next retry/chunk.
  "ERR|" + e;
}}
"""


def render_chunk_with_retries(
    chunk: list[Job],
    max_retries: int,
    chunk_timeout: int,
    psd_path: Path,
    output_root: Path,
    min_free_disk_gb: float,
    photoshop_exec: Path | None,
) -> tuple[int, float, bool]:
    attempt = 0
    restarted_photoshop = False
    chunk_start = time.perf_counter()
    pending = [j for j in chunk if not j.output_path.exists()]
    while pending:
        attempt += 1
        try:
            if not is_photoshop_running(photoshop_exec):
                start_photoshop(photoshop_exec)
            result = run_jsx(build_chunk_jsx(psd_path, pending), timeout_seconds=chunk_timeout)
            if not result.startswith("OK|"):
                raise RuntimeError(result)
        except Exception as exc:  # noqa: BLE001
            retries_used = attempt - 1
            if retries_used >= max_retries or not is_retryable_error(exc):
                raise
            print(f"[WARN] Chunk retry {retries_used + 1}/{max_retries} after recoverable error: {exc}")
            if "scratch disks are full" in str(exc).lower():
                recovery = recover_scratch_headroom(
                    output_root=output_root,
                    psd_path=psd_path,
                    photoshop_exec=photoshop_exec,
                    min_free_disk_gb=min_free_disk_gb,
                    reason="scratch-full error",
                )
                restarted_photoshop = restarted_photoshop or recovery.photoshop_restarted
                if not recovery.photoshop_restarted:
                    restart_photoshop(photoshop_exec)
                    restarted_photoshop = True
            else:
                restart_photoshop(photoshop_exec)
                restarted_photoshop = True
            pending = [j for j in pending if not j.output_path.exists()]
            continue

        for produced_job in pending:
            if produced_job.output_path.exists():
                crop_png_to_alpha_bounds(produced_job.output_path)
        pending = [j for j in pending if not j.output_path.exists()]
        if pending:
            retries_used = attempt - 1
            if retries_used >= max_retries:
                raise RuntimeError(f"Chunk produced partial output; {len(pending)} job(s) still missing after retries.")
            print(
                f"[WARN] Chunk produced partial output; retrying remaining {len(pending)} job(s) "
                f"({retries_used + 1}/{max_retries})."
            )
            restart_photoshop(photoshop_exec)
            restarted_photoshop = True

    produced = sum(1 for j in chunk if j.output_path.exists())
    elapsed = time.perf_counter() - chunk_start
    return produced, elapsed, restarted_photoshop


def run_once(args: argparse.Namespace, kill_first: bool) -> tuple[int, int]:
    psd_path = args.psd.expanduser().resolve()
    if not psd_path.exists():
        raise FileNotFoundError(f"PSD not found: {psd_path}")
    if args.chunk_size <= 0:
        raise ValueError("--chunk-size must be > 0")
    if args.max_retries < 0:
        raise ValueError("--max-retries must be >= 0")
    if args.chunk_timeout <= 0:
        raise ValueError("--chunk-timeout must be > 0")
    if args.restart_every_chunks < 0:
        raise ValueError("--restart-every-chunks must be >= 0")

    styles = parse_styles(args.styles)
    names = load_names(
        args.name_source,
        args.names_file,
        args.max_names,
        args.test_count,
        args.custom_names_json,
    )
    names = filter_names_by_letters(names, parse_letters(args.letters))
    if args.max_names > 0:
        names = names[: args.max_names]
    if not names:
        raise ValueError("No names to render.")

    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    removed_count, removed_bytes = cleanup_photoshop_temp_files()
    if removed_count:
        print(
            f"[INFO] Removed {removed_count} stale Photoshop temp item(s) "
            f"before start, freed {_gib(removed_bytes):.2f} GiB."
        )
    # If disk is low, attempt automatic recovery (cleanup + PS restart) before starting.
    try:
        assert_scratch_headroom(output_root, psd_path, args.min_free_disk_gb)
    except ValueError:
        print("[INFO] Scratch disk below threshold — running automatic recovery before start.")
        photoshop_exec_early = args.photoshop_exec.expanduser().resolve() if args.photoshop_exec else None
        try:
            recover_scratch_headroom(
                output_root=output_root,
                psd_path=psd_path,
                photoshop_exec=photoshop_exec_early,
                min_free_disk_gb=args.min_free_disk_gb,
                reason="pre-start",
            )
        except RecoverableScratchHeadroomError as exc:
            print(f"[WARN] Recovery incomplete: {exc} — proceeding anyway, will retry during run.")
    (output_root / "selected_names.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
    (output_root / "run_config.json").write_text(
        json.dumps(
            {
                "psd_path": str(psd_path),
                "names_file": str(args.names_file.expanduser().resolve()) if args.names_file else None,
                "output_root": str(output_root),
                "styles": styles,
                "letters": args.letters,
                "name_source": args.name_source,
                "test_count": args.test_count,
                "chunk_size": args.chunk_size,
                "max_retries": args.max_retries,
                "chunk_timeout": args.chunk_timeout,
                "restart_every_chunks": args.restart_every_chunks,
                "prevent_idle_sleep": args.prevent_idle_sleep,
                "min_free_disk_gb": args.min_free_disk_gb,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    photoshop_exec = args.photoshop_exec.expanduser().resolve() if args.photoshop_exec else None
    if kill_first:
        restart_photoshop(photoshop_exec)

    all_jobs = build_jobs(names, styles, output_root, uppercase=args.uppercase)
    pending = [j for j in all_jobs if not is_completed_output(j.output_path)]
    total = len(all_jobs)
    already_done = total - len(pending)
    done = already_done
    chunks = chunked(pending, args.chunk_size)
    progress_path = output_root / "progress.json"

    print(f"[INFO] Names: {len(names)} | Styles: {len(styles)} | Total jobs: {total}")
    print(f"[INFO] Resume scan: {already_done} existing PNG(s) will be skipped.")
    print(f"[INFO] Pending jobs: {len(pending)} | Chunk size: {args.chunk_size} | Chunks: {len(chunks)}")
    print(f"[INFO] Output: {output_root}")

    start = time.perf_counter()
    deferred_periodic_restarts = 0
    for idx, chunk in enumerate(chunks, start=1):
        produced, elapsed, restarted_this_chunk = render_chunk_with_retries(
            chunk,
            args.max_retries,
            args.chunk_timeout,
            psd_path=psd_path,
            output_root=output_root,
            min_free_disk_gb=args.min_free_disk_gb,
            photoshop_exec=photoshop_exec,
        )
        done += produced
        sec_per_item = elapsed / produced if produced else 0.0
        total_elapsed = time.perf_counter() - start
        processed_this_run = max(done - already_done, 0)
        avg_per_item = total_elapsed / processed_this_run if processed_this_run else 0.0
        remaining = total - done
        eta_sec = int(avg_per_item * remaining) if processed_this_run else 0
        print(
            f"[CHUNK {idx}/{len(chunks)}] produced={produced}/{len(chunk)} "
            f"time={elapsed:.1f}s ({sec_per_item:.2f}s/item) "
            f"done={done}/{total} eta~{eta_sec//60}m"
        )
        progress_payload = {
            "done": done,
            "total": total,
            "already_done": already_done,
            "remaining": remaining,
            "chunk_index": idx,
            "chunk_count": len(chunks),
            "avg_sec_per_item": round(avg_per_item, 3),
            "last_chunk_sec_per_item": round(sec_per_item, 3),
            "eta_seconds": eta_sec,
            "updated_at_epoch": int(time.time()),
        }
        progress_path.write_text(json.dumps(progress_payload, indent=2) + "\n", encoding="utf-8")

        # Proactive temp cleanup after every chunk to prevent disk from filling up.
        proactive_removed, proactive_bytes = cleanup_photoshop_temp_files()
        if proactive_removed:
            print(
                f"[INFO] Proactive cleanup after chunk {idx}: removed {proactive_removed} temp item(s), "
                f"freed {_gib(proactive_bytes):.2f} GiB."
            )

        # Reactive recovery if disk is still low after cleanup.
        lowest_path, lowest_free_gb = lowest_scratch_headroom(output_root, psd_path)
        proactive_recovery_floor = args.min_free_disk_gb + SCRATCH_RECOVERY_BUFFER_GB
        if lowest_free_gb is not None and lowest_free_gb < proactive_recovery_floor:
            recovery = recover_scratch_headroom(
                output_root=output_root,
                psd_path=psd_path,
                photoshop_exec=photoshop_exec,
                min_free_disk_gb=args.min_free_disk_gb,
                reason=f"chunk {idx} (proactive floor {proactive_recovery_floor:.1f} GiB)",
            )
            restarted_this_chunk = restarted_this_chunk or recovery.photoshop_restarted
            lowest_path, lowest_free_gb = recovery.path, recovery.free_gb

        if args.restart_every_chunks > 0 and idx < len(chunks) and (idx % args.restart_every_chunks == 0):
            lowest_path, lowest_free_gb = lowest_scratch_headroom(output_root, psd_path)
            periodic_restart_floor = args.min_free_disk_gb + PERIODIC_RESTART_HEADROOM_BUFFER_GB
            periodic_restart_due = (
                lowest_free_gb is None
                or lowest_free_gb <= periodic_restart_floor
                or deferred_periodic_restarts >= (MAX_PERIODIC_RESTART_DEFER_MULTIPLIER - 1)
            )
            if restarted_this_chunk:
                deferred_periodic_restarts = 0
                print(
                    f"[INFO] Skipping periodic restart at chunk {idx}; Photoshop was already restarted during chunk recovery."
                )
            elif periodic_restart_due:
                print(
                    f"[INFO] Periodic Photoshop restart after chunk {idx} "
                    f"(free={lowest_free_gb:.2f} GiB on {lowest_path})."
                    if lowest_free_gb is not None
                    else f"[INFO] Periodic Photoshop restart after chunk {idx}."
                )
                deferred_periodic_restarts = 0
                try:
                    restart_photoshop(photoshop_exec)
                except Exception as restart_exc:  # noqa: BLE001
                    print(f"[WARN] Periodic restart failed, continuing without hard stop: {restart_exc}")
            else:
                deferred_periodic_restarts += 1
                print(
                    f"[INFO] Skipping periodic restart at chunk {idx}; scratch healthy "
                    f"({lowest_free_gb:.2f} GiB free on {lowest_path}). "
                    f"Deferred {deferred_periodic_restarts}/{MAX_PERIODIC_RESTART_DEFER_MULTIPLIER - 1}."
                )

    total_elapsed = time.perf_counter() - start
    print(f"[DONE] Completed {total} jobs in {total_elapsed/60:.1f}m ({total_elapsed/total:.2f}s/item)")
    return done, total


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.max_supervisor_restarts < 0:
        raise ValueError("--max-supervisor-restarts must be >= 0")
    if args.supervisor_sleep < 0:
        raise ValueError("--supervisor-sleep must be >= 0")

    with prevent_idle_sleep(args.prevent_idle_sleep):
        restarts = 0
        kill_first = bool(args.kill_photoshop_first)

        while True:
            try:
                done, total = run_once(args, kill_first=kill_first)
                if done >= total:
                    return
                # Defensive fallback: if run returned early, supervisor can relaunch.
                if not args.supervisor:
                    raise RuntimeError(f"Run ended early: done={done}, total={total}")
                restarts += 1
                if restarts > args.max_supervisor_restarts:
                    raise RuntimeError("Supervisor restart limit exceeded after partial completion.")
                print(f"[WARN] Run ended early ({done}/{total}). Relaunching in {args.supervisor_sleep}s...")
                time.sleep(args.supervisor_sleep)
                kill_first = False
                continue
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, (ValueError, FileNotFoundError)):
                    raise
                if not args.supervisor:
                    raise
                restarts += 1
                if restarts > args.max_supervisor_restarts:
                    raise RuntimeError("Supervisor restart limit exceeded.") from exc
                print(
                    f"[WARN] Run crashed ({type(exc).__name__}: {exc}). "
                    f"Relaunching in {args.supervisor_sleep}s... ({restarts}/{args.max_supervisor_restarts})"
                )
                time.sleep(args.supervisor_sleep)
                kill_first = False


if __name__ == "__main__":
    main()
