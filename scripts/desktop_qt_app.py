#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import onecall_unattended_batch as batch_runner
import render_request_worker as request_worker_runner
from app_paths import (
    SOURCE_PROJECT_ROOT,
    bundled_default_psd,
    bundled_names_file,
    default_batch_output_dir,
    default_request_worker_dir,
    default_single_supabase_export_dir,
    desktop_settings_file,
    is_frozen_app,
    popular_name_lists_dir,
    single_save_supabase_config_file,
)
from ps_single_renderer import STYLE_CHOICES, run_jsx, sanitize_filename
from single_supabase_export import archive_custom_render_outputs, import_archived_run, load_single_save_config

try:
    from PySide6.QtCore import QProcess, QTimer, Qt
    from PySide6.QtGui import QCloseEvent, QColor, QTextCursor
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpinBox,
        QTextEdit,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
except Exception as exc:  # noqa: BLE001
    print("PySide6 is required. Install with: pip install PySide6")
    raise SystemExit(1) from exc

PROJECT_ROOT = SOURCE_PROJECT_ROOT
WORKER_FLAG = "--run-batch-worker"
REQUEST_WORKER_FLAG = "--run-request-worker"
NAMES_FILE = bundled_names_file()
DEFAULT_OUTPUT = default_batch_output_dir()
REQUEST_WORKER_DIR = default_request_worker_dir()
SETTINGS_FILE = desktop_settings_file()
DEFAULT_PSD = bundled_default_psd()
AUTO_RESTART_EVERY_CHUNKS = 4
AUTO_FULL_CHUNK_SIZE = 3
LOW_SCRATCH_SAFE_MODE_GB = 10.0
LOW_SCRATCH_CHUNK_SIZE = 2
LOW_SCRATCH_RESTART_EVERY_CHUNKS = 2

STYLE_COLORS: dict[str, str] = {
    "Yellow":      "#FDE047",
    "Turkuaz":     "#2DD4BF",
    "Rose":        "#FB7185",
    "Red":         "#EF4444",
    "Purple":      "#A855F7",
    "Pink":        "#EC4899",
    "Patina Blue": "#60A5FA",
    "Green":       "#22C55E",
    "Gray":        "#9CA3AF",
    "Gold":        "#F59E0B",
    "Green Dark":  "#15803D",
    "Brown Light": "#D97706",
    "Brown":       "#92400E",
    "Blue Dark":   "#1E40AF",
    "Blue":        "#3B82F6",
    "Black":       "#374151",
}

ALPHABET = tuple(chr(code) for code in range(ord("A"), ord("Z") + 1))


def available_names_files() -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def add(label: str, path: Path) -> None:
        resolved = path.expanduser().resolve()
        if resolved in seen or not resolved.exists() or not resolved.is_file():
            return
        seen.add(resolved)
        items.append((label, resolved))

    add("3000 curated names", bundled_names_file())
    popular_dir = popular_name_lists_dir()
    report_path = popular_dir / "popular_name_list_folder_report.json"
    if report_path.exists():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            payload = {}
        all_file_raw = str(payload.get("allBatchesFile") or payload.get("allFile") or "").strip()
        if all_file_raw:
            add("Popular all batches", Path(all_file_raw))
        batch_entries: list[tuple[int, int, Path]] = []
        for row in payload.get("batches", []):
            try:
                batch_index = int(row.get("batchIndex"))
                count = int(row.get("count"))
                file_path = Path(str(row.get("file", "")).strip())
            except (TypeError, ValueError):
                continue
            batch_entries.append((batch_index, count, file_path))
        for batch_index, count, path in sorted(batch_entries, key=lambda item: item[0]):
            add(f"Popular batch {batch_index}  {count} names", path)
        return items

    batch_entries: list[tuple[int, Path]] = []
    for path in popular_dir.glob("popular_names_batch_*.txt"):
        match = re.fullmatch(r"popular_names_batch_(\d+)\.txt", path.name)
        if not match:
            continue
        batch_entries.append((int(match.group(1)), path))
    for batch_index, path in sorted(batch_entries, key=lambda item: item[0]):
        add(f"Popular batch {batch_index}", path)
    return items


def normalize_saved_mode(mode: str) -> str:
    value = mode.strip()
    if value.startswith("test20_"):
        return "test20_5"
    if value == "single":
        return "custom"
    if value in {"full", "custom"}:
        return value
    return "full"


@dataclass(frozen=True)
class LetterCoverage:
    letter: str
    total_names: int
    completed_names: int
    partial_names: int
    expected_files: int
    existing_files: int

    @property
    def is_complete(self) -> bool:
        return self.total_names > 0 and self.completed_names >= self.total_names and self.partial_names == 0

    @property
    def is_partial(self) -> bool:
        return self.partial_names > 0 or (0 < self.completed_names < self.total_names)

    def status_label(self) -> str:
        if self.is_complete:
            return "Done"
        if self.is_partial:
            return "Partial"
        return "New"

    def detail_label(self) -> str:
        if self.partial_names > 0:
            return f"{self.completed_names}/{self.total_names} names\n{self.partial_names} partial"
        return f"{self.completed_names}/{self.total_names} names\n{self.status_label()}"


@dataclass(frozen=True)
class ProcessedBatchReference:
    output_dir: Path
    psd_path: Path
    psd_signature: str
    names_file: Path | None
    selected_names_path: Path
    selected_keys: frozenset[str]


def normalize_name_match_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.replace("-", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip().casefold()
    return normalized


def normalize_psd_signature(value: Path | str) -> str:
    path = Path(str(value).strip()) if str(value).strip() else Path("")
    stem = path.stem if path.stem else path.name
    return normalize_name_match_key(stem)


def load_text_names(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def processed_batch_references() -> list[ProcessedBatchReference]:
    refs: list[ProcessedBatchReference] = []
    seen_cfg: set[Path] = set()
    roots = [PROJECT_ROOT / "output", PROJECT_ROOT]
    for root in roots:
        if not root.exists():
            continue
        for cfg_path in root.rglob("run_config.json"):
            resolved_cfg = cfg_path.resolve()
            if resolved_cfg in seen_cfg:
                continue
            seen_cfg.add(resolved_cfg)
            selected_names_path = resolved_cfg.with_name("selected_names.txt")
            if not selected_names_path.exists():
                continue
            try:
                payload = json.loads(resolved_cfg.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if str(payload.get("name_source", "")).strip() != "full":
                continue
            psd_raw = str(payload.get("psd_path", "")).strip()
            if not psd_raw:
                continue
            try:
                names = load_text_names(selected_names_path)
            except OSError:
                continue
            selected_keys = frozenset(
                normalize_name_match_key(name)
                for name in names
                if normalize_name_match_key(name)
            )
            if not selected_keys:
                continue
            names_file_raw = str(payload.get("names_file", "")).strip()
            refs.append(
                ProcessedBatchReference(
                    output_dir=resolved_cfg.parent,
                    psd_path=Path(psd_raw).expanduser(),
                    psd_signature=normalize_psd_signature(psd_raw),
                    names_file=Path(names_file_raw).expanduser() if names_file_raw else None,
                    selected_names_path=selected_names_path,
                    selected_keys=selected_keys,
                )
            )
    refs.sort(key=lambda item: item.output_dir.name.casefold())
    return refs


@lru_cache(maxsize=4)
def load_names_by_letter(names_file: str) -> dict[str, tuple[str, ...]]:
    rows = [
        line.strip()
        for line in Path(names_file).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    buckets: dict[str, list[str]] = {letter: [] for letter in ALPHABET}
    for name in rows:
        letter = name[0].upper()
        if letter in buckets:
            buckets[letter].append(sanitize_filename(name.upper()))
    return {letter: tuple(values) for letter, values in buckets.items()}


def is_worker_mode(argv: list[str]) -> bool:
    return len(argv) > 1 and argv[1] == WORKER_FLAG


def is_request_worker_mode(argv: list[str]) -> bool:
    return len(argv) > 1 and argv[1] == REQUEST_WORKER_FLAG


def build_worker_command(args: list[str]) -> list[str]:
    if is_frozen_app():
        return [sys.executable, WORKER_FLAG, *args]
    return [sys.executable, "-u", str(Path(__file__).resolve()), WORKER_FLAG, *args]


def build_request_worker_command(args: list[str]) -> list[str]:
    if is_frozen_app():
        return [sys.executable, REQUEST_WORKER_FLAG, *args]
    return [sys.executable, "-u", str(Path(__file__).resolve()), REQUEST_WORKER_FLAG, *args]


def tail_text(path: Path, max_bytes: int = 30000) -> str:
    if not path.exists():
        return ""
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raw = raw[-max_bytes:]
    return raw.decode("utf-8", errors="replace")


def format_local_timestamp(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    return dt.astimezone().strftime("%d %b %Y %H:%M:%S")


def format_project_relative_path(value: Path | str) -> str:
    path_obj = Path(value).expanduser()
    try:
        resolved = path_obj.resolve()
    except OSError:
        resolved = path_obj
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def resolve_project_path(value: Path | str, *, default: Path | None = None) -> Path:
    raw = str(value).strip()
    if not raw:
        target = default if default is not None else PROJECT_ROOT
        return Path(target).expanduser().resolve()
    path_obj = Path(raw).expanduser()
    if not path_obj.is_absolute():
        path_obj = PROJECT_ROOT / path_obj
    return path_obj.resolve()


def format_preferred_path(value: Path | str) -> str:
    return format_project_relative_path(resolve_project_path(value))


def inspect_psd_styles(psd_path: Path, timeout_seconds: int = 240) -> list[str]:
    jsx = f"""#target photoshop
app.displayDialogs = DialogModes.NO;
var PSD_PATH = {json.dumps(psd_path.as_posix())};

function normalizeName(name) {{
  if (!name) return "";
  return String(name).replace(/\\s+/g, " ").replace(/^\\s+|\\s+$/g, "");
}}

function containsTextGroup(container) {{
  for (var i = 0; i < container.layerSets.length; i++) {{
    if (normalizeName(container.layerSets[i].name).toLowerCase() === "text") return true;
    if (containsTextGroup(container.layerSets[i])) return true;
  }}
  return false;
}}

function containsSmartObject(container) {{
  for (var i = 0; i < container.artLayers.length; i++) {{
    if (container.artLayers[i].kind === LayerKind.SMARTOBJECT) return true;
  }}
  for (var j = 0; j < container.layerSets.length; j++) {{
    if (containsSmartObject(container.layerSets[j])) return true;
  }}
  return false;
}}

function collectCandidates(container, out) {{
  for (var i = 0; i < container.layerSets.length; i++) {{
    var g = container.layerSets[i];
    if (containsTextGroup(g) && containsSmartObject(g)) out.push(normalizeName(g.name));
    collectCandidates(g, out);
  }}
}}

try {{
  var f = new File(PSD_PATH);
  if (!f.exists) throw new Error("PSD not found: " + PSD_PATH);
  var doc = app.open(f);
  var found = [];
  collectCandidates(doc, found);
  doc.close(SaveOptions.DONOTSAVECHANGES);
  var uniq = [];
  function hasItem(arr, val) {{
    for (var m = 0; m < arr.length; m++) {{
      if (arr[m] === val) return true;
    }}
    return false;
  }}
  for (var k = 0; k < found.length; k++) {{
    if (!hasItem(uniq, found[k])) uniq.push(found[k]);
  }}
  "OK|" + uniq.join("\\n");
}} catch (e) {{
  try {{ if (app.documents.length > 0) app.activeDocument.close(SaveOptions.DONOTSAVECHANGES); }} catch (ignore) {{}}
  "ERR|" + e;
}}
"""
    out = run_jsx(jsx, timeout_seconds=timeout_seconds)
    if not out.startswith("OK|"):
        raise RuntimeError(out)
    raw = out[3:].strip()
    parsed = raw.splitlines() if raw else []
    styles = [str(x).strip() for x in parsed if str(x).strip()]
    return styles or list(STYLE_CHOICES)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Photoshop Render Studio")
        self.resize(1220, 860)

        self.proc: QProcess | None = None
        self.worker_pid: int | None = None
        self.request_worker_pid: int | None = None
        self.current_output = DEFAULT_OUTPUT
        self.style_checks: dict[str, QCheckBox] = {}
        self.letter_checks: dict[str, QCheckBox] = {}
        self.letter_meta_labels: dict[str, QLabel] = {}
        self.letter_cells: dict[str, QWidget] = {}
        self.letter_coverage: dict[str, LetterCoverage] = {}
        self.last_run_meta: dict[str, object] = {}
        self.scratch_status_text = ""
        self._names_warning_confirm_required = False
        self._names_warning_dialog_text = ""
        self._syncing_letters = False
        self._stop_requested = False
        self._last_auto_resume_at = 0.0

        self._build_ui()
        self._apply_theme()
        self._render_letter_filters()
        self._render_styles(list(STYLE_CHOICES))
        self._load_settings()

        self.timer = QTimer(self)
        self.timer.setInterval(2500)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start()
        QTimer.singleShot(0, self._restore_request_worker_state)

    def _apply_theme(self) -> None:
        self.setStyleSheet("""
QWidget {
  font-family: "Helvetica Neue", "SF Pro Text", Arial, sans-serif;
  font-size: 13px;
  color: #111827;
}
QWidget#root {
  background: #f1f5f9;
}
QWidget#headerBar {
  background: #111827;
}
QLabel#appTitle {
  font-size: 20px;
  font-weight: 700;
  color: #f9fafb;
  letter-spacing: -0.3px;
}
QLabel#appLogo {
  min-width: 30px;
  max-width: 30px;
  min-height: 30px;
  max-height: 30px;
  border-radius: 9px;
  background: #a855f7;
  color: #ffffff;
  border: 1px solid #c084fc;
  font-size: 12px;
  font-weight: 800;
  qproperty-alignment: AlignCenter;
}
QLabel#psdBadge {
  background: #1f2937;
  color: #9ca3af;
  border: 1px solid #374151;
  border-radius: 6px;
  padding: 3px 10px;
  font-size: 11px;
}
QGroupBox {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  margin-top: 12px;
  padding: 10px 12px 12px 12px;
  font-weight: 600;
  font-size: 12px;
  color: #64748b;
}
QGroupBox::title {
  subcontrol-origin: margin;
  left: 12px;
  padding: 0 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
QGroupBox#setupCard {
  background: #f8fbff;
  border: 1px solid #dbeafe;
  border-radius: 14px;
  padding: 12px 14px 14px 14px;
}
QGroupBox#setupCard::title {
  color: #315a94;
  font-size: 15px;
  font-weight: 700;
  text-transform: none;
  letter-spacing: 0.1px;
}
QGroupBox#renderCard {
  background: #fcfbff;
  border: 1px solid #e9d5ff;
  border-radius: 14px;
  padding: 12px 14px 14px 14px;
}
QGroupBox#renderCard::title {
  color: #7c3aed;
  font-size: 15px;
  font-weight: 700;
  text-transform: none;
  letter-spacing: 0.1px;
}
QGroupBox#setupSubCard,
QGroupBox#renderSubCard {
  background: rgba(255, 255, 255, 0.75);
  border-radius: 12px;
  padding: 10px 12px 12px 12px;
}
QGroupBox#setupSubCard {
  border: 1px solid #dbeafe;
}
QGroupBox#renderSubCard {
  border: 1px solid #eadcff;
}
QGroupBox#setupSubCard::title,
QGroupBox#renderSubCard::title {
  text-transform: none;
  letter-spacing: 0.1px;
  font-size: 13px;
  font-weight: 700;
}
QGroupBox#setupSubCard::title {
  color: #5b7aa5;
}
QGroupBox#renderSubCard::title {
  color: #8b5cf6;
}
QLabel#fieldLabel {
  color: #1f2937;
  font-size: 13px;
  font-weight: 600;
}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {
  background: #ffffff;
  border: 1.5px solid #e2e8f0;
  border-radius: 7px;
  padding: 6px 10px;
  color: #111827;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {
  border-color: #3b82f6;
}
QLineEdit:disabled, QSpinBox:disabled {
  background: #f8fafc;
  color: #94a3b8;
}
QPushButton {
  background: #f1f5f9;
  border: 1.5px solid #e2e8f0;
  border-radius: 7px;
  padding: 7px 14px;
  font-weight: 600;
  color: #374151;
}
QPushButton:hover {
  background: #e2e8f0;
  border-color: #cbd5e1;
}
QPushButton:pressed {
  background: #cbd5e1;
}
QPushButton#primaryBtn {
  background: #2563eb;
  border-color: #1d4ed8;
  color: #ffffff;
  font-size: 14px;
  font-weight: 700;
  border-radius: 11px;
  padding: 11px 18px;
}
QPushButton#primaryBtn:hover  { background: #1d4ed8; }
QPushButton#primaryBtn:pressed { background: #1e40af; }
QPushButton#primaryBtn:disabled {
  background: #93c5fd;
  border-color: #93c5fd;
  color: #eff6ff;
}
QPushButton#dangerBtn {
  background: #ef4444;
  border-color: #dc2626;
  color: #ffffff;
  font-weight: 700;
  border-radius: 11px;
  padding: 11px 18px;
}
QPushButton#dangerBtn:hover  { background: #dc2626; }
QPushButton#dangerBtn:pressed { background: #b91c1c; }
QPushButton#dangerBtn:disabled {
  background: #fca5a5;
  border-color: #fca5a5;
  color: #fff1f2;
}
QPushButton#ghostBtn {
  background: transparent;
  border-color: #e2e8f0;
  color: #374151;
}
QPushButton#ghostBtn:hover { background: #f1f5f9; }
QPushButton#softBtn {
  background: #ffffff;
  border: 1.5px solid #dbeafe;
  border-radius: 10px;
  padding: 8px 14px;
  color: #1e3a8a;
  font-weight: 700;
}
QPushButton#softBtn:hover {
  background: #eff6ff;
  border-color: #93c5fd;
}
QPushButton#softBtn:pressed {
  background: #dbeafe;
  border-color: #60a5fa;
}
QPushButton#softBtn:disabled {
  background: #f8fafc;
  border-color: #e2e8f0;
  color: #94a3b8;
}
QProgressBar {
  border: 1.5px solid #e2e8f0;
  border-radius: 7px;
  background: #f1f5f9;
  height: 10px;
  text-align: center;
  font-size: 11px;
  color: #64748b;
}
QProgressBar::chunk {
  background: #2563eb;
  border-radius: 5px;
}
QLabel#statusPill {
  background: #eff6ff;
  color: #1d4ed8;
  border: 1.5px solid #bfdbfe;
  border-radius: 20px;
  padding: 4px 14px;
  font-weight: 600;
  font-size: 12px;
}
QLabel#resultPill {
  background: #f0fdf4;
  color: #15803d;
  border: 1.5px solid #86efac;
  border-radius: 20px;
  padding: 4px 14px;
  font-weight: 600;
  font-size: 12px;
}
QLabel#badgeLabel {
  background: #dbeafe;
  color: #1d4ed8;
  border-radius: 10px;
  padding: 2px 10px;
  font-size: 11px;
  font-weight: 700;
}
QLabel#subtleMeta {
  color: #64748b;
  font-size: 11px;
}
QLabel#warningBox {
  background: #fff7ed;
  color: #9a3412;
  border: 1.5px solid #fdba74;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  font-weight: 600;
}
QLabel#infoBox {
  background: #eff6ff;
  color: #1d4ed8;
  border: 1.5px solid #bfdbfe;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  font-weight: 600;
}
QLabel#errorBox {
  background: #fef2f2;
  color: #b91c1c;
  border: 1.5px solid #fca5a5;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  font-weight: 600;
}
QLabel#successBox {
  background: #f0fdf4;
  color: #15803d;
  border: 1.5px solid #86efac;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  font-weight: 600;
}
QFrame#singleSaveCard {
  background: #f8fafc;
  border: 1.5px solid #dbeafe;
  border-radius: 10px;
}
QToolButton#helpIconBtn {
  background: #eff6ff;
  color: #1d4ed8;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  min-width: 16px;
  max-width: 16px;
  min-height: 16px;
  max-height: 16px;
  padding: 0;
  font-size: 10px;
  font-weight: 700;
}
QToolButton#helpIconBtn:hover {
  background: #dbeafe;
  border-color: #93c5fd;
}
QToolButton#sectionToggleBtn {
  background: #ffffff;
  border: 1.5px solid #dbeafe;
  border-radius: 12px;
  padding: 10px 14px;
  color: #0f172a;
  font-size: 13px;
  font-weight: 700;
  text-align: left;
}
QToolButton#sectionToggleBtn:hover {
  background: #eff6ff;
  border-color: #93c5fd;
}
QToolButton#sectionToggleBtn:checked {
  background: #eff6ff;
  border-color: #60a5fa;
  color: #1d4ed8;
}
QToolButton#sectionToggleBtn[sectionTone="orders"] {
  background: #eff6ff;
  border-color: #93c5fd;
  color: #1d4ed8;
}
QToolButton#sectionToggleBtn[sectionTone="orders"]:hover {
  background: #dbeafe;
  border-color: #60a5fa;
}
QToolButton#sectionToggleBtn[sectionTone="orders"]:checked {
  background: #dbeafe;
  border-color: #2563eb;
  color: #1d4ed8;
}
QToolButton#sectionToggleBtn[sectionTone="local"] {
  background: #faf5ff;
  border-color: #d8b4fe;
  color: #7c3aed;
}
QToolButton#sectionToggleBtn[sectionTone="local"]:hover {
  background: #f3e8ff;
  border-color: #c084fc;
}
QToolButton#sectionToggleBtn[sectionTone="local"]:checked {
  background: #f3e8ff;
  border-color: #a855f7;
  color: #7c3aed;
}
QTextEdit#logView {
  background: #0f172a;
  color: #94a3b8;
  border: 1px solid #1e293b;
  border-radius: 8px;
  font-family: "SF Mono", "Fira Code", monospace;
  font-size: 11px;
  padding: 8px;
}
QScrollArea {
  border: none;
  background: transparent;
}
QScrollBar:vertical {
  background: #f1f5f9;
  width: 8px;
  border-radius: 4px;
}
QScrollBar::handle:vertical {
  background: #cbd5e1;
  border-radius: 4px;
  min-height: 20px;
}
        """)

    def _build_ui(self) -> None:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        self.setCentralWidget(scroll)

        root = QWidget()
        root.setObjectName("root")
        scroll.setWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 16)
        outer.setSpacing(0)

        # ── Header bar ──────────────────────────────────────
        header = QWidget()
        header.setObjectName("headerBar")
        header.setFixedHeight(58)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(20, 0, 20, 0)
        h_layout.setSpacing(10)
        logo_lbl = QLabel("PS")
        logo_lbl.setObjectName("appLogo")
        title_lbl = QLabel("Photoshop Render Studio")
        title_lbl.setObjectName("appTitle")
        self.psd_badge = QLabel("no PSD selected")
        self.psd_badge.setObjectName("psdBadge")
        self.psd_badge.setMaximumWidth(400)
        h_layout.addWidget(logo_lbl)
        h_layout.addWidget(title_lbl)
        h_layout.addWidget(self.psd_badge)
        h_layout.addStretch(1)
        outer.addWidget(header)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 14, 16, 0)
        content_layout.setSpacing(12)
        outer.addWidget(content)

        # ── Files ────────────────────────────────────────────
        files_box = QGroupBox("Setup")
        files_box.setObjectName("setupCard")
        files_layout = QVBoxLayout(files_box)
        files_layout.setSpacing(10)
        content_layout.addWidget(files_box)

        shared_setup_box = QGroupBox("Shared Setup")
        shared_setup_box.setObjectName("setupSubCard")
        shared_form = QFormLayout(shared_setup_box)
        shared_form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        shared_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        shared_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        shared_form.setHorizontalSpacing(12)
        shared_form.setVerticalSpacing(8)
        files_layout.addWidget(shared_setup_box)

        self.local_setup_box = QWidget()
        local_form = QFormLayout(self.local_setup_box)
        local_form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        local_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        local_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        local_form.setHorizontalSpacing(12)
        local_form.setVerticalSpacing(8)
        self.local_setup_expander, self.local_setup_toggle, self.local_setup_body = self._make_expander(
            "Local Generation Setup",
            expanded=False,
            tone="local",
        )
        local_setup_body_layout = QVBoxLayout(self.local_setup_body)
        local_setup_body_layout.setContentsMargins(0, 0, 0, 0)
        local_setup_body_layout.setSpacing(0)
        local_setup_body_layout.addWidget(self.local_setup_box)
        files_layout.addWidget(self.local_setup_expander)

        psd_row = QHBoxLayout()
        self.psd_edit = QLineEdit()
        self.psd_edit.setPlaceholderText("/path/to/template.psd")
        self.psd_edit.textChanged.connect(self._on_psd_changed)
        psd_btn = QPushButton("Browse")
        psd_btn.setObjectName("ghostBtn")
        psd_btn.setMinimumWidth(96)
        psd_btn.clicked.connect(self.pick_psd)
        scan_btn = QPushButton("Scan Colors")
        scan_btn.setObjectName("ghostBtn")
        scan_btn.clicked.connect(self.scan_styles)
        psd_row.addWidget(self.psd_edit, 1)
        psd_row.addWidget(psd_btn)
        psd_row.addWidget(scan_btn)
        shared_form.addRow(
            self._label_with_help(
                "PSD Template",
                "Main Photoshop source file. Local Generation and Website Orders both use this PSD unless the website-order worker is restarted with a different one.",
            ),
            self._wrap(psd_row),
        )

        out_row = QHBoxLayout()
        self.out_edit = QLineEdit(format_preferred_path(DEFAULT_OUTPUT))
        self.out_edit.textChanged.connect(self.refresh_letter_summary)
        self.out_edit.textChanged.connect(self.refresh_scratch_status)
        out_btn = QPushButton("Browse")
        out_btn.setObjectName("ghostBtn")
        out_btn.setMinimumWidth(96)
        out_btn.clicked.connect(self.pick_output)
        open_out_btn = QPushButton("Open ↗")
        open_out_btn.setObjectName("ghostBtn")
        open_out_btn.clicked.connect(self._open_output_folder)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(out_btn)
        out_row.addWidget(open_out_btn)
        local_form.addRow(
            self._label_with_help(
                "Output Folder",
                "Local Generation writes PNG files here. Resume logic also checks this folder and skips files that already exist.",
            ),
            self._wrap(out_row),
        )
        self.output_scope_label = QLabel(
            "Used by Local Generation only. Website Orders uses its own worker folder under output/request_worker. Repo folders are shown as relative paths when possible."
        )
        self.output_scope_label.setObjectName("subtleMeta")
        self.output_scope_label.setWordWrap(True)
        local_form.addRow("", self.output_scope_label)

        names_row = QHBoxLayout()
        self.names_file_combo = QComboBox()
        self._populate_names_file_combo()
        self.names_file_combo.currentIndexChanged.connect(self._on_names_file_changed)
        names_browse_btn = QPushButton("Browse...")
        names_browse_btn.setObjectName("ghostBtn")
        names_browse_btn.clicked.connect(self._browse_names_file)
        names_open_btn = QPushButton("Open ↗")
        names_open_btn.setObjectName("ghostBtn")
        names_open_btn.clicked.connect(self._open_selected_names_file)
        names_row.addWidget(self.names_file_combo, 1)
        names_row.addWidget(names_browse_btn)
        names_row.addWidget(names_open_btn)
        local_form.addRow(
            self._label_with_help(
                "Names TXT",
                "Choose which names list Local Generation uses in Full and Test modes. The built-in dropdown is intentionally short: curated names plus popular batch files. If you need a special TXT file, use Browse.",
            ),
            self._wrap(names_row),
        )
        self.names_scope_label = QLabel(
            "Used only in Local Generation Full and Test modes. Typed Name and Website Orders ignore this list."
        )
        self.names_scope_label.setObjectName("subtleMeta")
        self.names_scope_label.setWordWrap(True)
        local_form.addRow("", self.names_scope_label)
        self.names_warning_label = QLabel("")
        self.names_warning_label.setObjectName("warningBox")
        self.names_warning_label.setWordWrap(True)
        self.names_warning_label.setVisible(False)
        local_form.addRow("", self.names_warning_label)

        self.scratch_status_label = QLabel("Checking scratch disk...")
        self.scratch_status_label.setWordWrap(True)
        self.scratch_status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        scratch_row = QHBoxLayout()
        scratch_row.setSpacing(8)
        scratch_row.addWidget(self.scratch_status_label, 1)
        self.scratch_cleanup_btn = QPushButton("Free Temp Files")
        self.scratch_cleanup_btn.setObjectName("ghostBtn")
        self.scratch_cleanup_btn.setToolTip("Delete Photoshop temp/scratch cache files from system temp folders, then re-check free disk space.")
        self.scratch_cleanup_btn.clicked.connect(self.cleanup_scratch_temp_files)
        scratch_row.addWidget(self.scratch_cleanup_btn)
        shared_form.addRow(
            self._label_with_help(
                "Scratch Disk",
                "Live free-space check for the PSD, local output, home, and temp volumes Photoshop may use. Start already auto-cleans Photoshop temp files; Free Temp Files lets you run that cleanup manually first.",
            ),
            self._wrap(scratch_row),
        )

        # Photoshop Exec — only on Windows
        if sys.platform.startswith("win"):
            ps_row = QHBoxLayout()
            self.ps_exec_edit = QLineEdit()
            self.ps_exec_edit.setPlaceholderText("Photoshop.exe path")
            ps_btn = QPushButton("Browse")
            ps_btn.setObjectName("ghostBtn")
            ps_btn.setMinimumWidth(96)
            ps_btn.clicked.connect(self.pick_ps_exec)
            ps_row.addWidget(self.ps_exec_edit, 1)
            ps_row.addWidget(ps_btn)
            shared_form.addRow("Photoshop Exec", self._wrap(ps_row))
        else:
            self.ps_exec_edit = QLineEdit()  # hidden, kept for compat

        # ── Render Settings ──────────────────────────────────
        settings_box = QGroupBox("Render Settings")
        settings_box.setObjectName("renderCard")
        settings_layout = QVBoxLayout(settings_box)
        settings_layout.setSpacing(10)
        content_layout.addWidget(settings_box)

        self.single_name_expander, self.single_name_toggle, self.single_name_body = self._make_expander(
            "Local Generation",
            expanded=False,
            tone="local",
        )
        single_name_body_layout = QVBoxLayout(self.single_name_body)
        single_name_body_layout.setContentsMargins(0, 0, 0, 0)
        single_name_body_layout.setSpacing(10)

        row1 = QHBoxLayout()
        row1.setSpacing(12)
        mode_col = QVBoxLayout()
        mode_col.setSpacing(4)
        mode_col.addWidget(
            self._label_with_help(
                "Mode",
                "Full renders the selected names file. Test renders the first 5 names from the selected names file. Typed name renders one name on this computer.",
            )
        )
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Full  —  names file", "full")
        self.mode_combo.addItem("Test  —  first 5 names", "test20_5")
        self.mode_combo.addItem("Typed Name  —  local generation", "custom")
        self.mode_combo.currentIndexChanged.connect(self.on_mode_change)
        mode_col.addWidget(self.mode_combo)
        row1.addLayout(mode_col, 1)
        single_name_body_layout.addLayout(row1)

        self.workflow_pause_label = QLabel("")
        self.workflow_pause_label.setWordWrap(True)
        self.workflow_pause_label.setVisible(False)
        single_name_body_layout.addWidget(self.workflow_pause_label)
        self.batch_settings_panel = QWidget()
        batch_body_layout = QVBoxLayout(self.batch_settings_panel)
        batch_body_layout.setContentsMargins(0, 0, 0, 0)
        batch_body_layout.setSpacing(10)

        batch_note = QLabel(
            "Use this section for Full and Test modes. These settings control batch chunking, retry behavior, and letter filtering."
        )
        batch_note.setObjectName("subtleMeta")
        batch_note.setWordWrap(True)
        batch_body_layout.addWidget(batch_note)

        letters_row = QHBoxLayout()
        letters_row.setSpacing(12)
        letters_col = QVBoxLayout()
        letters_col.setSpacing(4)
        letters_col.addWidget(
            self._label_with_help(
                "Letters  (ABC or A,B,C or all)",
                "Filters names by first letter from the names txt file. Example: ABC, A,B,C, or all.",
            )
        )
        self.letters_edit = QLineEdit("ABC")
        self.letters_edit.textChanged.connect(self._on_letters_text_changed)
        letters_col.addWidget(self.letters_edit)
        letters_row.addLayout(letters_col, 1)
        batch_body_layout.addLayout(letters_row)

        row2 = QHBoxLayout()
        row2.setSpacing(12)
        for label, attr, lo, hi, default in [
            ("Chunk",          "chunk_spin",   1,   200,  10),
            ("Retries",        "retry_spin",   1,    50,  5),
            ("Timeout (sec)",  "timeout_spin", 30, 3600, 300),
            ("Restart/Chunks", "restart_spin", 0,   200,  0),
        ]:
            col = QVBoxLayout()
            col.setSpacing(4)
            help_text = ""
            if attr == "chunk_spin":
                help_text = "How many render jobs go into one Photoshop batch call. Smaller values are safer; larger values can be faster."
            elif attr == "retry_spin":
                help_text = "How many times a failed chunk can be retried after the first failure before the run stops."
            elif attr == "timeout_spin":
                help_text = "Maximum seconds to wait for one chunk before it is treated as failed."
            elif attr == "restart_spin":
                help_text = "Restart Photoshop after every N chunks to reduce memory and scratch-disk buildup. In the desktop app, 0 auto-picks a safe cadence for long runs."
            if help_text:
                col.addWidget(self._label_with_help(label, help_text))
            else:
                col.addWidget(QLabel(label))
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(default)
            setattr(self, attr, spin)
            col.addWidget(spin)
            row2.addLayout(col, 1)
        batch_body_layout.addLayout(row2)
        single_name_body_layout.addWidget(self.batch_settings_panel)

        self.custom_section = QGroupBox("Typed Name")
        self.custom_section.setObjectName("renderSubCard")
        custom_layout = QVBoxLayout(self.custom_section)
        custom_layout.setSpacing(6)
        custom_layout.addWidget(
            self._label_with_help(
                "Name",
                "Custom and Single modes render one typed name only. If comma-separated text is pasted, only the first parsed entry is used.",
            )
        )
        self.custom_names_edit = QLineEdit()
        self.custom_names_edit.setPlaceholderText("Enter one name  (example: Kerem)")
        custom_layout.addWidget(self.custom_names_edit)
        self.custom_name_hint_label = QLabel(
            "Generates one typed name on this computer and saves the PNG to the selected output folder."
        )
        self.custom_name_hint_label.setObjectName("subtleMeta")
        self.custom_name_hint_label.setWordWrap(True)
        custom_layout.addWidget(self.custom_name_hint_label)
        self.auto_open_custom_check = QCheckBox("Auto-open first PNG after render")
        self.auto_open_custom_check.setChecked(True)
        custom_layout.addWidget(self.auto_open_custom_check)

        self.single_save_card = QFrame()
        self.single_save_card.setObjectName("singleSaveCard")
        single_save_layout = QVBoxLayout(self.single_save_card)
        single_save_layout.setContentsMargins(12, 12, 12, 12)
        single_save_layout.setSpacing(8)

        single_save_title = QLabel("Save Single Name to Supabase")
        single_save_layout.addWidget(single_save_title)

        single_save_note = QLabel(
            "Single mode renders the typed name, archives the selected color PNGs locally, and imports them into the shared Supabase cache. Existing cache keys are skipped."
        )
        single_save_note.setObjectName("subtleMeta")
        single_save_note.setWordWrap(True)
        single_save_layout.addWidget(single_save_note)

        save_row = QHBoxLayout()
        save_row.setSpacing(6)
        self.save_single_supabase_check = QCheckBox("Supabase upload runs automatically in Single mode")
        self.save_single_supabase_check.setEnabled(False)
        save_row.addWidget(self.save_single_supabase_check)
        save_row.addWidget(
            self._help_icon(
                f"The exported PNGs are archived under {default_single_supabase_export_dir()} "
                f"and uploaded using config from {single_save_supabase_config_file()}."
            )
        )
        save_row.addStretch(1)
        single_save_layout.addWidget(self._wrap(save_row))

        self.single_save_target_label = QLabel("")
        self.single_save_target_label.setObjectName("subtleMeta")
        self.single_save_target_label.setWordWrap(True)
        single_save_layout.addWidget(self.single_save_target_label)

        self.single_save_styles_label = QLabel("")
        self.single_save_styles_label.setObjectName("subtleMeta")
        self.single_save_styles_label.setWordWrap(True)
        single_save_layout.addWidget(self.single_save_styles_label)

        self.single_save_config_label = QLabel("")
        self.single_save_config_label.setObjectName("subtleMeta")
        self.single_save_config_label.setWordWrap(True)
        self.single_save_config_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        single_save_layout.addWidget(self.single_save_config_label)

        self.single_save_archive_label = QLabel("")
        self.single_save_archive_label.setObjectName("subtleMeta")
        self.single_save_archive_label.setWordWrap(True)
        self.single_save_archive_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        single_save_layout.addWidget(self.single_save_archive_label)

        single_save_btn_row = QHBoxLayout()
        single_save_btn_row.setSpacing(8)
        self.single_save_open_config_btn = QPushButton("Open Config")
        self.single_save_open_config_btn.setObjectName("ghostBtn")
        self.single_save_open_config_btn.clicked.connect(self._open_single_save_config_location)
        self.single_save_open_archive_btn = QPushButton("Open Archive ↗")
        self.single_save_open_archive_btn.setObjectName("ghostBtn")
        self.single_save_open_archive_btn.clicked.connect(self._open_single_save_archive_dir)
        single_save_btn_row.addWidget(self.single_save_open_config_btn)
        single_save_btn_row.addWidget(self.single_save_open_archive_btn)
        single_save_btn_row.addStretch(1)
        single_save_layout.addWidget(self._wrap(single_save_btn_row))

        self.single_save_status_label = QLabel("")
        self.single_save_status_label.setWordWrap(True)
        self.single_save_status_label.setVisible(False)
        single_save_layout.addWidget(self.single_save_status_label)

        self.single_save_result_label = QLabel("")
        self.single_save_result_label.setWordWrap(True)
        self.single_save_result_label.setVisible(False)
        single_save_layout.addWidget(self.single_save_result_label)
        self.single_save_card.setVisible(False)

        custom_layout.addWidget(self.single_save_card)
        self.request_worker_section = QGroupBox("Automatic Website Orders")
        self.request_worker_section.setObjectName("renderSubCard")
        request_worker_layout = QVBoxLayout(self.request_worker_section)
        request_worker_layout.setSpacing(8)

        request_top_row = QHBoxLayout()
        request_top_row.setSpacing(12)
        self.request_worker_enabled_check = QCheckBox("Process website orders automatically")
        self.request_worker_enabled_check.stateChanged.connect(self._on_request_worker_enabled_changed)
        request_top_row.addWidget(self.request_worker_enabled_check, 1)

        poll_col = QVBoxLayout()
        poll_col.setSpacing(4)
        poll_col.addWidget(
            self._label_with_help(
                "Poll (sec)",
                "How often the desktop app checks Supabase for a new render request. "
                "Changing this while the worker is already running requires a restart.",
            )
        )
        self.request_worker_poll_spin = QSpinBox()
        self.request_worker_poll_spin.setRange(2, 3600)
        self.request_worker_poll_spin.setValue(10)
        self.request_worker_poll_spin.valueChanged.connect(self._refresh_request_worker_ui)
        poll_col.addWidget(self.request_worker_poll_spin)
        request_top_row.addLayout(poll_col)
        request_worker_layout.addLayout(request_top_row)

        request_note = QLabel(
            "This mode watches for new website orders, renders them automatically in Photoshop, and sends the result back online. While it is on, local render controls are paused."
        )
        request_note.setObjectName("subtleMeta")
        request_note.setWordWrap(True)
        request_worker_layout.addWidget(request_note)

        self.request_worker_psd_label = QLabel("")
        self.request_worker_psd_label.setObjectName("subtleMeta")
        self.request_worker_psd_label.setWordWrap(True)
        self.request_worker_psd_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        request_worker_layout.addWidget(self.request_worker_psd_label)

        self.request_worker_config_label = QLabel("")
        self.request_worker_config_label.setObjectName("subtleMeta")
        self.request_worker_config_label.setWordWrap(True)
        self.request_worker_config_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        request_worker_layout.addWidget(self.request_worker_config_label)

        self.request_worker_paths_label = QLabel("")
        self.request_worker_paths_label.setObjectName("subtleMeta")
        self.request_worker_paths_label.setWordWrap(True)
        self.request_worker_paths_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        request_worker_layout.addWidget(self.request_worker_paths_label)

        request_btn_row = QHBoxLayout()
        request_btn_row.setSpacing(8)
        self.request_worker_restart_btn = QPushButton("Restart Order Mode")
        self.request_worker_restart_btn.setObjectName("softBtn")
        self.request_worker_restart_btn.clicked.connect(self.restart_request_worker)
        self.request_worker_open_log_btn = QPushButton("Open Activity Log ↗")
        self.request_worker_open_log_btn.setObjectName("softBtn")
        self.request_worker_open_log_btn.clicked.connect(self._open_request_worker_log)
        request_btn_row.addWidget(self.request_worker_restart_btn)
        request_btn_row.addWidget(self.request_worker_open_log_btn)
        request_btn_row.addStretch(1)
        request_worker_layout.addWidget(self._wrap(request_btn_row))

        self.request_worker_status_label = QLabel("")
        self.request_worker_status_label.setWordWrap(True)
        self.request_worker_status_label.setVisible(False)
        request_worker_layout.addWidget(self.request_worker_status_label)

        self.request_worker_result_label = QLabel("")
        self.request_worker_result_label.setWordWrap(True)
        self.request_worker_result_label.setVisible(False)
        request_worker_layout.addWidget(self.request_worker_result_label)

        self.request_worker_expander, self.request_worker_toggle, self.request_worker_body = self._make_expander(
            "Website Orders",
            expanded=True,
            tone="orders",
        )
        request_worker_body_layout = QVBoxLayout(self.request_worker_body)
        request_worker_body_layout.setContentsMargins(0, 0, 0, 0)
        request_worker_body_layout.setSpacing(10)
        request_worker_body_layout.addWidget(self.request_worker_section)
        settings_layout.addWidget(self.request_worker_expander)
        single_name_body_layout.addWidget(self.custom_section)
        settings_layout.addWidget(self.single_name_expander)

        self.letter_section = QGroupBox("Letter Coverage")
        self.letter_section.setObjectName("renderSubCard")
        letter_outer = QVBoxLayout(self.letter_section)
        letter_outer.setSpacing(8)

        letter_ctl = QHBoxLayout()
        letter_ctl.addWidget(
            self._help_icon(
                "Coverage is matched against the names txt file and the currently selected colors. "
                "Done means every expected PNG exists, Partial means some exist, New means none exist."
            )
        )
        for text, slot in [
            ("Select All", self.select_all_letters),
            ("Deselect All", self.unselect_all_letters),
            ("Select Pending", self.select_pending_letters),
            ("Select Done", self.select_completed_letters),
            ("Refresh", self.refresh_letter_summary),
        ]:
            b = QPushButton(text)
            b.setObjectName("ghostBtn")
            b.clicked.connect(slot)
            letter_ctl.addWidget(b)
        skip_done_wrap = QHBoxLayout()
        skip_done_wrap.setSpacing(6)
        self.skip_done_letters_check = QCheckBox("Skip completed letters on start")
        self.skip_done_letters_check.setChecked(True)
        skip_done_wrap.addWidget(self.skip_done_letters_check)
        skip_done_wrap.addWidget(
            self._help_icon(
                "Fully completed letters are removed before the run starts. "
                "Partially completed letters still continue, but only their missing PNG files are rendered."
            )
        )
        letter_ctl.addWidget(self._wrap(skip_done_wrap))
        letter_ctl.addStretch(1)
        self.letter_badge = QLabel("0 of 26 selected")
        self.letter_badge.setObjectName("badgeLabel")
        letter_ctl.addWidget(self.letter_badge)
        letter_outer.addLayout(letter_ctl)

        self.letter_group = QWidget()
        self.letter_layout = QGridLayout(self.letter_group)
        self.letter_layout.setHorizontalSpacing(8)
        self.letter_layout.setVerticalSpacing(6)
        letter_outer.addWidget(self.letter_group)
        batch_body_layout.addWidget(self.letter_section)

        # ── Color Palette ────────────────────────────────────
        self.styles_box = QGroupBox("Color Palette")
        self.styles_box.setObjectName("renderSubCard")
        styles_outer = QVBoxLayout(self.styles_box)
        styles_outer.setSpacing(8)
        single_name_body_layout.addWidget(self.styles_box)

        ctl_row = QHBoxLayout()
        ctl_row.addWidget(
            self._help_icon(
                "Select the colors to render. Resume logic works per color folder, so existing PNG files in the selected colors are skipped."
            )
        )
        for text, slot in [("Select All", self.select_all_styles),
                           ("Deselect All", self.unselect_all_styles),
                           ("Rescan PSD", self.scan_styles)]:
            b = QPushButton(text)
            b.setObjectName("ghostBtn")
            b.clicked.connect(slot)
            ctl_row.addWidget(b)
        ctl_row.addStretch(1)
        self.style_badge = QLabel("0 of 0 selected")
        self.style_badge.setObjectName("badgeLabel")
        ctl_row.addWidget(self.style_badge)
        styles_outer.addLayout(ctl_row)

        self.styles_group = QWidget()
        self.styles_layout = QGridLayout(self.styles_group)
        self.styles_layout.setHorizontalSpacing(8)
        self.styles_layout.setVerticalSpacing(6)
        styles_outer.addWidget(self.styles_group)

        # ── Action bar ───────────────────────────────────────
        action_box = QGroupBox()
        action_box.setStyleSheet("QGroupBox { border: none; margin-top: 0; padding: 0; background: transparent; }")
        action_layout = QVBoxLayout(action_box)
        action_layout.setSpacing(8)
        content_layout.addWidget(action_box)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.start_btn = QPushButton("Start Local Generation")
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.setMinimumHeight(46)
        self.start_btn.clicked.connect(self.start_run)
        self.stop_btn = QPushButton("Stop Run")
        self.stop_btn.setObjectName("dangerBtn")
        self.stop_btn.setMinimumHeight(46)
        self.stop_btn.setMinimumWidth(120)
        self.stop_btn.clicked.connect(self.stop_run)
        self.refresh_btn = QPushButton("Refresh Status")
        self.refresh_btn.setObjectName("softBtn")
        self.refresh_btn.setMinimumHeight(46)
        self.refresh_btn.setMinimumWidth(140)
        self.refresh_btn.setToolTip("Refresh local run progress and status")
        self.refresh_btn.clicked.connect(self.refresh_status)
        btn_row.addWidget(self.start_btn, 1)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.refresh_btn)
        action_layout.addLayout(btn_row)

        self.action_hint_label = QLabel("")
        self.action_hint_label.setObjectName("subtleMeta")
        self.action_hint_label.setWordWrap(True)
        action_layout.addWidget(self.action_hint_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%v / %m  (%p%)")
        self.progress_bar.setFixedHeight(22)
        self.progress_bar.setVisible(False)
        action_layout.addWidget(self.progress_bar)

        pill_row = QHBoxLayout()
        pill_row.setSpacing(8)
        self.status_label = QLabel("idle")
        self.status_label.setObjectName("statusPill")
        self.result_label = QLabel("Last Result: —")
        self.result_label.setObjectName("resultPill")
        pill_row.addWidget(self.status_label, 1)
        pill_row.addWidget(self.result_label, 2)
        action_layout.addLayout(pill_row)

        # ── Collapsible Log ──────────────────────────────────
        log_header = QHBoxLayout()
        self._log_toggle_btn = QPushButton("Run Log  ▾")
        self._log_toggle_btn.setObjectName("ghostBtn")
        self._log_toggle_btn.clicked.connect(self._toggle_log)
        log_header.addWidget(self._log_toggle_btn)
        log_header.addStretch(1)
        content_layout.addLayout(log_header)

        self._log_box = QGroupBox()
        self._log_box.setStyleSheet(
            "QGroupBox { border: none; margin: 0; padding: 0; background: transparent; }"
        )
        log_inner = QVBoxLayout(self._log_box)
        log_inner.setContentsMargins(0, 0, 0, 0)
        self.log = QTextEdit()
        self.log.setObjectName("logView")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(200)
        self.log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        log_inner.addWidget(self.log)
        self._log_box.setVisible(False)
        content_layout.addWidget(self._log_box)

        self.on_mode_change()

    @staticmethod
    def _wrap(layout) -> QWidget:
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(layout)
        return w

    @staticmethod
    def _set_box_kind(widget: QLabel, kind: str) -> None:
        object_name = {
            "info": "infoBox",
            "success": "successBox",
            "warning": "warningBox",
            "error": "errorBox",
        }.get(kind, "infoBox")
        if widget.objectName() == object_name:
            return
        widget.setObjectName(object_name)
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _help_icon(self, text: str) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName("helpIconBtn")
        btn.setText("i")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(text)
        btn.setToolTipDuration(0)
        return btn

    def _label_with_help(self, text: str, help_text: str) -> QWidget:
        row = QHBoxLayout()
        row.setSpacing(6)
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        row.addWidget(label)
        row.addWidget(self._help_icon(help_text))
        row.addStretch(1)
        return self._wrap(row)

    def _make_expander(self, title: str, *, expanded: bool = False, tone: str = "local") -> tuple[QWidget, QToolButton, QWidget]:
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        toggle = QToolButton()
        toggle.setObjectName("sectionToggleBtn")
        toggle.setProperty("sectionTone", tone)
        toggle.setText(title)
        toggle.setCheckable(True)
        toggle.setChecked(expanded)
        toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        toggle.setCursor(Qt.PointingHandCursor)
        toggle.setMinimumHeight(42)
        toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        body = QWidget()
        body.setVisible(expanded)

        def on_toggled(checked: bool) -> None:
            body.setVisible(checked)
            toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

        toggle.toggled.connect(on_toggled)
        outer.addWidget(toggle)
        outer.addWidget(body)
        return container, toggle, body

    @staticmethod
    def _set_expander_state(toggle: QToolButton, body: QWidget, expanded: bool) -> None:
        if toggle.isChecked() == expanded and body.isVisible() == expanded:
            return
        toggle.blockSignals(True)
        toggle.setChecked(expanded)
        toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        toggle.blockSignals(False)
        body.setVisible(expanded)

    def on_mode_change(self) -> None:
        mode = str(self.mode_combo.currentData())
        is_batch_mode = mode != "custom"
        self.letters_edit.setEnabled(mode == "full")
        self.batch_settings_panel.setVisible(is_batch_mode)
        self.letter_section.setVisible(mode == "full")
        is_typed_name_mode = mode == "custom"
        self.custom_names_edit.setEnabled(is_typed_name_mode)
        self.custom_section.setVisible(is_typed_name_mode)
        self.single_save_card.setVisible(False)
        self.save_single_supabase_check.blockSignals(True)
        self.save_single_supabase_check.setChecked(False)
        self.save_single_supabase_check.blockSignals(False)
        if self.request_worker_enabled_check.isChecked():
            self._set_expander_state(self.request_worker_toggle, self.request_worker_body, True)
        self._update_names_file_warning()
        self._sync_workflow_state()

    def _render_letter_filters(self) -> None:
        while self.letter_layout.count():
            item = self.letter_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.letter_checks = {}
        self.letter_meta_labels = {}
        self.letter_cells = {}
        for idx, letter in enumerate(ALPHABET):
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(8, 6, 8, 6)
            cell_layout.setSpacing(3)

            cb = QCheckBox(letter)
            cb.stateChanged.connect(self._on_letter_checks_changed)
            self.letter_checks[letter] = cb

            meta = QLabel("0/0 names")
            meta.setObjectName("subtleMeta")
            meta.setWordWrap(True)
            self.letter_meta_labels[letter] = meta
            self.letter_cells[letter] = cell

            cell_layout.addWidget(cb)
            cell_layout.addWidget(meta)
            cell_layout.addStretch(1)
            self.letter_layout.addWidget(cell, idx // 6, idx % 6)

        self._apply_letters_to_checks(self.letters_edit.text().strip() or "ABC")
        self._update_letter_badge()
        self.refresh_letter_summary()

    def _active_letter_styles(self) -> list[str]:
        styles = self.selected_styles()
        if styles:
            return styles
        return list(self.style_checks) or list(STYLE_CHOICES)

    def _current_output_path(self) -> Path:
        return resolve_project_path(self.out_edit.text().strip(), default=DEFAULT_OUTPUT)

    @staticmethod
    def _free_gib(path: Path) -> float:
        return shutil.disk_usage(path).free / (1024 ** 3)

    def _run_log_path(self) -> Path:
        return self._current_output_path() / "run.log"

    def _worker_pid_path(self) -> Path:
        return self._current_output_path() / "worker.pid"

    def _manual_stop_flag_path(self) -> Path:
        return self._current_output_path() / "manual_stop.flag"

    def _request_worker_dir(self) -> Path:
        return REQUEST_WORKER_DIR

    def _request_worker_log_path(self) -> Path:
        return self._request_worker_dir() / "worker.log"

    def _request_worker_pid_path(self) -> Path:
        return self._request_worker_dir() / "worker.pid"

    def _request_worker_status_path(self) -> Path:
        return self._request_worker_dir() / "worker_status.json"

    @staticmethod
    def _is_pid_running(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _clear_worker_pid(self) -> None:
        self.worker_pid = None
        try:
            self._worker_pid_path().unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _write_worker_pid(self, pid: int) -> None:
        self.worker_pid = pid
        self._worker_pid_path().write_text(f"{pid}\n", encoding="utf-8")

    def _clear_request_worker_pid(self) -> None:
        self.request_worker_pid = None
        try:
            self._request_worker_pid_path().unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _write_request_worker_pid(self, pid: int) -> None:
        self.request_worker_pid = pid
        self._request_worker_pid_path().write_text(f"{pid}\n", encoding="utf-8")

    def _discover_worker_pid(self) -> int | None:
        try:
            result = subprocess.run(
                ["ps", "ax", "-o", "pid=", "-o", "command="],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:  # noqa: BLE001
            return None
        output_root = str(self._current_output_path())
        for line in result.stdout.splitlines():
            row = line.strip()
            if not row or WORKER_FLAG not in row or output_root not in row:
                continue
            parts = row.split(maxsplit=1)
            if not parts:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if self._is_pid_running(pid):
                return pid
        return None

    def _read_worker_pid(self) -> int | None:
        if self.worker_pid and self._is_pid_running(self.worker_pid):
            return self.worker_pid
        pid_path = self._worker_pid_path()
        if not pid_path.exists():
            discovered = self._discover_worker_pid()
            if discovered:
                self.worker_pid = discovered
                return discovered
            self.worker_pid = None
            return None
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            self._clear_worker_pid()
            return None
        if self._is_pid_running(pid):
            self.worker_pid = pid
            return pid
        discovered = self._discover_worker_pid()
        if discovered:
            self.worker_pid = discovered
            return discovered
        self._clear_worker_pid()
        return None

    def _discover_request_worker_pid(self) -> int | None:
        try:
            result = subprocess.run(
                ["ps", "ax", "-o", "pid=", "-o", "command="],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:  # noqa: BLE001
            return None
        worker_root = str(self._request_worker_dir())
        for line in result.stdout.splitlines():
            row = line.strip()
            if not row or REQUEST_WORKER_FLAG not in row or worker_root not in row:
                continue
            parts = row.split(maxsplit=1)
            if not parts:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if self._is_pid_running(pid):
                return pid
        return None

    def _read_request_worker_pid(self) -> int | None:
        if self.request_worker_pid and self._is_pid_running(self.request_worker_pid):
            return self.request_worker_pid
        pid_path = self._request_worker_pid_path()
        if not pid_path.exists():
            discovered = self._discover_request_worker_pid()
            if discovered:
                self.request_worker_pid = discovered
                return discovered
            self.request_worker_pid = None
            return None
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            self._clear_request_worker_pid()
            return None
        if self._is_pid_running(pid):
            self.request_worker_pid = pid
            return pid
        discovered = self._discover_request_worker_pid()
        if discovered:
            self.request_worker_pid = discovered
            return discovered
        self._clear_request_worker_pid()
        return None

    def _append_run_log(self, text: str) -> None:
        self._run_log_path().parent.mkdir(parents=True, exist_ok=True)
        with self._run_log_path().open("a", encoding="utf-8") as handle:
            handle.write(text)

    def _start_worker_detached(self, cmd: list[str], reset_log: bool) -> int:
        self.current_output.mkdir(parents=True, exist_ok=True)
        if reset_log:
            self._run_log_path().write_text("", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        log_handle = self._run_log_path().open("a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
        finally:
            log_handle.close()
        self._manual_stop_flag_path().unlink(missing_ok=True)
        self._write_worker_pid(proc.pid)
        return proc.pid

    def _start_request_worker_detached(self, cmd: list[str], reset_log: bool) -> int:
        worker_dir = self._request_worker_dir()
        worker_dir.mkdir(parents=True, exist_ok=True)
        if reset_log:
            self._request_worker_log_path().write_text("", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        log_handle = self._request_worker_log_path().open("a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
        finally:
            log_handle.close()
        self._write_request_worker_pid(proc.pid)
        return proc.pid

    def _terminate_detached_pid(self, pid: int) -> None:
        if pid <= 0:
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            return
        deadline = time.time() + 4.0
        while time.time() < deadline and self._is_pid_running(pid):
            time.sleep(0.2)
        if self._is_pid_running(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass

    def _set_scratch_status(self, text: str, tone: str, tooltip: str = "") -> None:
        self.scratch_status_text = text
        self.scratch_status_label.setText(text)
        self.scratch_status_label.setToolTip(tooltip)
        palette = {
            "ok": ("#ecfdf5", "#047857", "#a7f3d0"),
            "warn": ("#fef2f2", "#b91c1c", "#fecaca"),
            "info": ("#eff6ff", "#1d4ed8", "#bfdbfe"),
        }
        bg, fg, border = palette.get(tone, palette["info"])
        self.scratch_status_label.setStyleSheet(
            f"background:{bg}; color:{fg}; border:1.5px solid {border}; "
            "border-radius:8px; padding:8px 10px; font-weight:600;"
        )

    def refresh_scratch_status(self) -> None:
        threshold = batch_runner.MIN_FREE_DISK_GB_DEFAULT
        output_root = self._current_output_path()
        output_probe = output_root if output_root.exists() else output_root.parent
        psd_text = self.psd_edit.text().strip() or str(DEFAULT_PSD)
        psd_path = Path(psd_text).expanduser()
        psd_probe = psd_path if psd_path.exists() else psd_path.parent

        try:
            probe_paths = batch_runner.scratch_probe_paths(output_probe, psd_probe)
        except Exception as exc:  # noqa: BLE001
            self._set_scratch_status(f"Scratch disk check failed: {exc}", "warn")
            return

        if not probe_paths:
            self._set_scratch_status(
                f"Scratch disk check unavailable. Need at least {threshold:.1f} GiB free.",
                "warn",
            )
            return

        details: list[str] = []
        lowest_path: Path | None = None
        lowest_free_gb: float | None = None
        for probe in probe_paths:
            try:
                free_gb = self._free_gib(probe)
            except OSError as exc:
                details.append(f"{probe}: error reading disk usage ({exc})")
                continue
            details.append(f"{probe}: {free_gb:.1f} GiB free")
            if lowest_free_gb is None or free_gb < lowest_free_gb:
                lowest_free_gb = free_gb
                lowest_path = probe

        if lowest_free_gb is None or lowest_path is None:
            self._set_scratch_status(
                "Scratch disk check unavailable. Could not read any candidate volume.",
                "warn",
                "\n".join(details),
            )
            return

        tooltip = "\n".join(details)
        if lowest_free_gb < threshold:
            self._set_scratch_status(
                f"Scratch disk low: {lowest_free_gb:.1f} GiB free. Threshold {threshold:.1f} GiB. Rendering can continue — runtime recovery will manage.",
                "warn",
                tooltip,
            )
            if hasattr(self, "start_btn"):
                self.start_btn.setToolTip(
                    f"Low scratch disk on {lowest_path}. Runtime cleanup will handle during processing."
                )
            return

        self._set_scratch_status(
            f"Scratch disk OK: {lowest_free_gb:.1f} GiB free. Threshold {threshold:.1f} GiB.",
            "ok",
            tooltip,
        )
        if hasattr(self, "start_btn"):
            self.start_btn.setToolTip("")

    def _parse_letters_input(self, raw: str) -> list[str]:
        value = raw.strip().upper()
        if value in {"ALL", "*"}:
            return list(ALPHABET)
        cleaned = value.replace(",", "").replace(" ", "")
        return [letter for letter in ALPHABET if letter in cleaned]

    def _apply_letters_to_checks(self, raw: str) -> None:
        letters = self._parse_letters_input(raw)
        if not raw.strip():
            letters = []
        chosen = set(letters)
        self._syncing_letters = True
        try:
            for letter, cb in self.letter_checks.items():
                cb.setChecked(letter in chosen)
        finally:
            self._syncing_letters = False
        self._update_letter_badge()

    def _sync_letters_text_from_checks(self) -> None:
        selected = self.selected_letters()
        if len(selected) == len(ALPHABET):
            text = "all"
        else:
            text = "".join(selected)
        self._syncing_letters = True
        try:
            self.letters_edit.setText(text)
        finally:
            self._syncing_letters = False

    def _on_letters_text_changed(self, text: str) -> None:
        if self._syncing_letters:
            return
        self._apply_letters_to_checks(text)
        self.refresh_letter_summary()
        self._update_names_file_warning()

    def _on_letter_checks_changed(self) -> None:
        if self._syncing_letters:
            return
        self._sync_letters_text_from_checks()
        self._update_letter_badge()
        self._update_names_file_warning()

    def selected_letters(self) -> list[str]:
        return [letter for letter, cb in self.letter_checks.items() if cb.isChecked()]

    def _update_letter_badge(self) -> None:
        selected = len(self.selected_letters())
        total = len(self.letter_checks)
        self.letter_badge.setText(f"{selected} of {total} selected")

    def _compute_letter_coverage(self, output_root: Path, styles: list[str]) -> dict[str, LetterCoverage]:
        names_by_letter = load_names_by_letter(str(self.selected_names_file()))
        style_stems: dict[str, set[str]] = {}
        for style in styles:
            style_dir = output_root / sanitize_filename(style)
            if style_dir.exists():
                style_stems[style] = {png.stem for png in style_dir.glob("*.png")}
            else:
                style_stems[style] = set()

        coverage: dict[str, LetterCoverage] = {}
        for letter in ALPHABET:
            stems = names_by_letter.get(letter, ())
            completed = 0
            partial = 0
            existing_files = 0
            for stem in stems:
                present = sum(1 for stem_set in style_stems.values() if stem in stem_set)
                existing_files += present
                if present == len(styles):
                    completed += 1
                elif present > 0:
                    partial += 1
            coverage[letter] = LetterCoverage(
                letter=letter,
                total_names=len(stems),
                completed_names=completed,
                partial_names=partial,
                expected_files=len(stems) * len(styles),
                existing_files=existing_files,
            )
        return coverage

    def _apply_letter_coverage(self) -> None:
        for letter, meta in self.letter_meta_labels.items():
            info = self.letter_coverage.get(
                letter,
                LetterCoverage(letter=letter, total_names=0, completed_names=0, partial_names=0, expected_files=0, existing_files=0),
            )
            meta.setText(info.detail_label())
            meta.setToolTip(
                f"TXT names: {info.total_names}\n"
                f"Complete names: {info.completed_names}\n"
                f"Partial names: {info.partial_names}\n"
                f"Files: {info.existing_files}/{info.expected_files}"
            )

            cell = self.letter_cells[letter]
            if info.is_complete:
                bg = "#f0fdf4"
                border = "#86efac"
            elif info.is_partial:
                bg = "#fff7ed"
                border = "#fdba74"
            else:
                bg = "#f8fafc"
                border = "#e2e8f0"
            cell.setStyleSheet(f"background:{bg}; border:1px solid {border}; border-radius:8px;")

    def refresh_letter_summary(self) -> None:
        if not self.letter_checks:
            return
        output_root = self._current_output_path()
        styles = self._active_letter_styles()
        self.letter_coverage = self._compute_letter_coverage(output_root, styles)
        self._apply_letter_coverage()

    def select_all_letters(self) -> None:
        for cb in self.letter_checks.values():
            cb.setChecked(True)
        self._update_letter_badge()

    def unselect_all_letters(self) -> None:
        for cb in self.letter_checks.values():
            cb.setChecked(False)
        self._update_letter_badge()

    def select_pending_letters(self) -> None:
        for letter, cb in self.letter_checks.items():
            cb.setChecked(not self.letter_coverage.get(letter, LetterCoverage(letter, 0, 0, 0, 0, 0)).is_complete)
        self._update_letter_badge()

    def select_completed_letters(self) -> None:
        for letter, cb in self.letter_checks.items():
            cb.setChecked(self.letter_coverage.get(letter, LetterCoverage(letter, 0, 0, 0, 0, 0)).is_complete)
        self._update_letter_badge()

    def _render_styles(self, styles: list[str]) -> None:
        while self.styles_layout.count():
            item = self.styles_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.style_checks = {}
        for idx, s in enumerate(styles):
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(6, 3, 6, 3)
            cell_layout.setSpacing(8)

            dot = QLabel()
            dot.setFixedSize(14, 14)
            color = STYLE_COLORS.get(s, "#94a3b8")
            dot.setStyleSheet(
                f"background:{color}; border-radius:7px; border:1px solid rgba(0,0,0,0.15);"
            )

            cb = QCheckBox(s)
            cb.setChecked(False)
            cb.stateChanged.connect(self._update_style_badge)
            cb.stateChanged.connect(self.refresh_letter_summary)
            self.style_checks[s] = cb

            cell_layout.addWidget(dot)
            cell_layout.addWidget(cb)
            cell_layout.addStretch(1)

            cell.setStyleSheet(
                "QWidget { background:#f8fafc; border-radius:7px; }"
                "QWidget:hover { background:#f1f5f9; }"
            )
            self.styles_layout.addWidget(cell, idx // 4, idx % 4)
        self._update_style_badge()
        self.refresh_letter_summary()

    def selected_styles(self) -> list[str]:
        return [name for name, cb in self.style_checks.items() if cb.isChecked()]

    def select_all_styles(self) -> None:
        for cb in self.style_checks.values():
            cb.setChecked(True)
        self._update_style_badge()
        self._set_status("All styles selected.")

    def unselect_all_styles(self) -> None:
        for cb in self.style_checks.values():
            cb.setChecked(False)
        self._update_style_badge()
        self._set_status("All styles deselected.")

    def _toggle_log(self) -> None:
        visible = not self._log_box.isVisible()
        self._log_box.setVisible(visible)
        self._log_toggle_btn.setText("Run Log  ▴" if visible else "Run Log  ▾")

    def _populate_names_file_combo(self) -> None:
        current = self.selected_names_file() if hasattr(self, "names_file_combo") else None
        self.names_file_combo.blockSignals(True)
        self.names_file_combo.clear()
        for label, path in available_names_files():
            self.names_file_combo.addItem(label, str(path))
        if current is not None and current.exists() and self.names_file_combo.findData(str(current)) < 0:
            self.names_file_combo.addItem(f"Custom  {current.name}", str(current))
        self.names_file_combo.blockSignals(False)
        if current is not None:
            idx = self.names_file_combo.findData(str(current))
            if idx >= 0:
                self.names_file_combo.setCurrentIndex(idx)

    def selected_names_file(self) -> Path:
        raw = self.names_file_combo.currentData()
        if isinstance(raw, str) and raw.strip():
            return Path(raw).expanduser().resolve()
        return NAMES_FILE.resolve()

    def _open_selected_names_file(self) -> None:
        self._open_path(self.selected_names_file())

    def _browse_names_file(self) -> None:
        current = self.selected_names_file()
        start_dir = current.parent if current.exists() else NAMES_FILE.parent
        path, _ = QFileDialog.getOpenFileName(self, "Select Names TXT", str(start_dir), "Text Files (*.txt);;All Files (*)")
        if not path:
            return
        resolved = str(Path(path).expanduser().resolve())
        idx = self.names_file_combo.findData(resolved)
        if idx < 0:
            self.names_file_combo.addItem(f"Custom  {Path(resolved).name}", resolved)
            idx = self.names_file_combo.findData(resolved)
        if idx >= 0:
            self.names_file_combo.setCurrentIndex(idx)
            self._set_status(f"Names file selected: {Path(resolved).name}")

    def _on_names_file_changed(self) -> None:
        load_names_by_letter.cache_clear()
        self.refresh_letter_summary()
        self._update_names_file_warning()

    def _selected_full_mode_names(self) -> list[str]:
        names_file = self.selected_names_file()
        if not names_file.exists():
            return []
        try:
            rows = load_text_names(names_file)
        except OSError:
            return []
        letters = set(self.selected_letters())
        if not letters or len(letters) == len(ALPHABET):
            return rows
        return [name for name in rows if name and name[0].upper() in letters]

    def _names_batch_warning_info(self) -> dict[str, object] | None:
        if str(self.mode_combo.currentData()) != "full":
            return None
        psd_text = self.psd_edit.text().strip() or str(DEFAULT_PSD)
        psd_signature = normalize_psd_signature(psd_text)
        if not psd_signature:
            return None
        selected_names = self._selected_full_mode_names()
        selected_keys = {
            normalize_name_match_key(name)
            for name in selected_names
            if normalize_name_match_key(name)
        }
        if not selected_keys:
            return None

        matches: list[tuple[ProcessedBatchReference, int]] = []
        covered_keys: set[str] = set()
        for batch in processed_batch_references():
            if batch.psd_signature != psd_signature:
                continue
            overlap = selected_keys & batch.selected_keys
            if not overlap:
                continue
            covered_keys.update(overlap)
            matches.append((batch, len(overlap)))

        if not matches:
            return None

        total = len(selected_keys)
        covered = len(covered_keys)
        matches.sort(key=lambda item: (-item[1], item[0].output_dir.name.casefold()))
        full_cover = covered >= total
        lines = [
            f"{batch.output_dir.name}: {count}/{total} names"
            for batch, count in matches[:3]
        ]
        if len(matches) > 3:
            lines.append(f"+ {len(matches) - 3} more same-PSD batch reference(s)")

        if full_cover:
            title = f"Warning: this list is already fully covered by same-PSD processed batches ({covered}/{total})."
        else:
            title = f"Warning: this list overlaps same-PSD processed batches ({covered}/{total})."

        label_text = title + "\n" + "\n".join(f"- {line}" for line in lines)
        dialog_text = (
            title
            + "\n\n"
            + "\n".join(f"- {line}" for line in lines)
            + "\n\nContinue anyway?"
        )
        return {
            "label": label_text,
            "dialog": dialog_text,
            "confirm": True,
        }

    def _update_names_file_warning(self) -> None:
        info = self._names_batch_warning_info()
        if not info:
            self._names_warning_confirm_required = False
            self._names_warning_dialog_text = ""
            self.names_warning_label.clear()
            self.names_warning_label.setVisible(False)
            return
        self._names_warning_confirm_required = bool(info.get("confirm", False))
        self._names_warning_dialog_text = str(info.get("dialog", "")).strip()
        self.names_warning_label.setText(str(info.get("label", "")).strip())
        self.names_warning_label.setVisible(True)

    def cleanup_scratch_temp_files(self) -> None:
        self.scratch_cleanup_btn.setEnabled(False)
        self._set_status("Cleaning Photoshop temp files...")
        QApplication.processEvents()
        try:
            removed_count, removed_bytes = batch_runner.cleanup_photoshop_temp_files()
            freed_gib = removed_bytes / (1024 ** 3)
            if removed_count:
                message = (
                    f"Removed {removed_count} Photoshop temp item(s) and freed "
                    f"{freed_gib:.2f} GiB."
                )
                self.append_log(f"[APP] {message}\n")
                self._set_status(message)
                QMessageBox.information(self, "Scratch Cleanup", message)
            else:
                message = "No removable Photoshop temp files were found in the system temp folders."
                self.append_log(f"[APP] {message}\n")
                self._set_status("No Photoshop temp files found")
                QMessageBox.information(self, "Scratch Cleanup", message)
        finally:
            self.scratch_cleanup_btn.setEnabled(True)
            self.refresh_scratch_status()

    def _on_psd_changed(self, text: str) -> None:
        name = Path(text).name if text.strip() else "no PSD selected"
        self.psd_badge.setText(name)
        self.psd_badge.setToolTip(text)
        self.refresh_scratch_status()
        self._update_names_file_warning()
        self._refresh_request_worker_ui()

    def _update_style_badge(self) -> None:
        selected = sum(1 for cb in self.style_checks.values() if cb.isChecked())
        total = len(self.style_checks)
        self.style_badge.setText(f"{selected} of {total} selected")
        self._refresh_single_save_ui()

    def _open_output_folder(self) -> None:
        path = self._current_output_path()
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def pick_psd(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select PSD", str(DEFAULT_PSD.parent), "PSD Files (*.psd)")
        if path:
            self.psd_edit.setText(path)

    def pick_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Output Folder", str(self._current_output_path()))
        if path:
            self.out_edit.setText(format_preferred_path(path))

    def pick_ps_exec(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Photoshop Executable")
        if path:
            self.ps_exec_edit.setText(path)

    def scan_styles(self) -> None:
        psd = Path(self.psd_edit.text().strip())
        if not psd.exists():
            QMessageBox.critical(self, "Error", "Select a valid PSD path first.")
            return
        self._set_status("Scanning PSD styles...")
        QApplication.processEvents()
        try:
            styles = inspect_psd_styles(psd.resolve())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Scan Failed", str(exc))
            self._set_status("Scan failed.")
            return
        self._render_styles(styles)
        self._set_status(f"Loaded {len(styles)} styles from PSD.")

    def _set_status(self, msg: str) -> None:
        self.status_label.setText(msg)
        self._refresh_action_buttons()

    def _local_run_is_active(self) -> bool:
        live_pid = self._read_worker_pid()
        if live_pid:
            return True
        return bool(self.proc and self.proc.state() != QProcess.NotRunning)

    def _has_resume_candidate(self) -> bool:
        try:
            progress = self._current_output_path() / "progress.json"
        except Exception:  # noqa: BLE001
            return False
        if not progress.exists():
            return False
        try:
            payload = json.loads(progress.read_text(encoding="utf-8"))
            done = int(payload.get("done", 0))
            total = int(payload.get("total", 0))
            remaining = int(payload.get("remaining", max(total - done, 0)))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False
        return total > 0 and done > 0 and remaining > 0

    def _refresh_action_buttons(self) -> None:
        if not hasattr(self, "start_btn"):
            return
        website_orders_active = bool(
            hasattr(self, "request_worker_enabled_check") and self.request_worker_enabled_check.isChecked()
        )
        local_run_active = self._local_run_is_active()
        resume_candidate = (not website_orders_active) and (not local_run_active) and self._has_resume_candidate()

        if website_orders_active:
            self.start_btn.setText("Local Generation Paused")
            self.action_hint_label.setText("Website Orders is active. Turn it off to start a local render.")
        elif local_run_active:
            self.start_btn.setText("Local Generation Running")
            self.action_hint_label.setText("A local render is running now. Stop it only if you want to cancel the current run.")
        elif resume_candidate:
            self.start_btn.setText("Resume Local Generation")
            self.action_hint_label.setText("An unfinished local run was detected. You can resume from the current progress.")
        else:
            self.start_btn.setText("Start Local Generation")
            self.action_hint_label.setText("Ready to render on this computer.")

        self.start_btn.setEnabled((not website_orders_active) and (not local_run_active))
        self.stop_btn.setEnabled(local_run_active)
        self.refresh_btn.setEnabled(True)

    def append_log(self, text: str) -> None:
        self.log.moveCursor(QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.ensureCursorVisible()

    def _parsed_custom_names(self) -> list[str]:
        return self._parse_custom_names(self.custom_names_edit.text().strip())

    def _set_single_save_status(self, text: str, kind: str) -> None:
        self._set_box_kind(self.single_save_status_label, kind)
        self.single_save_status_label.setText(text.strip())
        self.single_save_status_label.setVisible(bool(text.strip()))

    def _set_single_save_result(self, text: str, kind: str, *, visible: bool = True) -> None:
        self._set_box_kind(self.single_save_result_label, kind)
        self.single_save_result_label.setText(text.strip())
        self.single_save_result_label.setVisible(visible and bool(text.strip()))

    def _set_request_worker_status(self, text: str, kind: str) -> None:
        self._set_box_kind(self.request_worker_status_label, kind)
        self.request_worker_status_label.setText(text.strip())
        self.request_worker_status_label.setVisible(bool(text.strip()))

    def _set_request_worker_result(self, text: str, kind: str, *, visible: bool = True) -> None:
        self._set_box_kind(self.request_worker_result_label, kind)
        self.request_worker_result_label.setText(text.strip())
        self.request_worker_result_label.setVisible(visible and bool(text.strip()))

    def _sync_workflow_state(self) -> None:
        if not hasattr(self, "request_worker_enabled_check"):
            return
        website_orders_active = self.request_worker_enabled_check.isChecked()
        local_render_enabled = not website_orders_active
        if hasattr(self, "workflow_pause_label"):
            self._set_box_kind(self.workflow_pause_label, "warning" if website_orders_active else "info")
            self.workflow_pause_label.setText(
                "Website Orders mode is on. You can still browse settings, but local render inputs and Start are paused."
                if website_orders_active
                else ""
            )
            self.workflow_pause_label.setVisible(website_orders_active)
        if hasattr(self, "batch_settings_panel"):
            self.batch_settings_panel.setEnabled(local_render_enabled)
        if hasattr(self, "single_name_body"):
            self.single_name_body.setEnabled(local_render_enabled)
        if hasattr(self, "styles_box"):
            self.styles_box.setEnabled(local_render_enabled)
        if hasattr(self, "start_btn"):
            self.start_btn.setEnabled(local_render_enabled)
            self.start_btn.setToolTip(
                "" if local_render_enabled else "Turn off Website Orders mode to use local render controls."
            )
        self._refresh_action_buttons()

    def _request_worker_cmd(self) -> list[str]:
        load_single_save_config()
        psd = Path(self.psd_edit.text().strip() or str(DEFAULT_PSD)).expanduser().resolve()
        if not psd.exists():
            raise ValueError(f"PSD not found for request worker: {psd}")
        worker_dir = self._request_worker_dir()
        worker_dir.mkdir(parents=True, exist_ok=True)
        return build_request_worker_command(
            [
                "--poll-interval",
                str(self.request_worker_poll_spin.value()),
                "--status-file",
                str(self._request_worker_status_path()),
                "--psd",
                str(psd),
                "--render-output-dir",
                str(worker_dir / "renders"),
            ]
        )

    def _load_request_worker_status_payload(self) -> dict[str, object]:
        path = self._request_worker_status_path()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _open_request_worker_log(self) -> None:
        path = self._request_worker_log_path()
        if path.exists():
            self._open_path(path)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._open_path(path.parent)

    def start_request_worker(self, *, quiet: bool = False) -> int | None:
        live_pid = self._read_request_worker_pid()
        if live_pid:
            self._refresh_request_worker_ui()
            return live_pid
        if self._read_worker_pid():
            raise RuntimeError("Stop the current local render first, then turn on Website Orders mode.")
        try:
            cmd = self._request_worker_cmd()
        except Exception as exc:  # noqa: BLE001
            self._set_request_worker_status(f"Website orders config error: {exc}", "error")
            self._set_request_worker_result("", "info", visible=False)
            if not quiet:
                QMessageBox.critical(self, "Website Orders", str(exc))
            raise

        self._request_worker_log_path().parent.mkdir(parents=True, exist_ok=True)
        self._request_worker_log_path().write_text("", encoding="utf-8")
        with self._request_worker_log_path().open("a", encoding="utf-8") as handle:
            handle.write("[APP] START " + " ".join(cmd) + "\n")
        pid = self._start_request_worker_detached(cmd, reset_log=False)
        self._set_request_worker_status(f"Website orders mode starting (pid {pid}).", "info")
        self._set_request_worker_result(
            "Website orders mode is active. Local render controls are paused. Use Restart Order Mode after changing PSD path or poll interval.",
            "info",
        )
        return pid

    def stop_request_worker(self) -> None:
        live_pid = self._read_request_worker_pid()
        if live_pid:
            self._terminate_detached_pid(live_pid)
        self._clear_request_worker_pid()
        self._set_request_worker_status("Website orders mode stopped. Local render controls are available again.", "info")
        self._set_request_worker_result("", "info", visible=False)

    def restart_request_worker(self) -> None:
        if not self.request_worker_enabled_check.isChecked():
            self.request_worker_enabled_check.setChecked(True)
            return
        live_pid = self._read_request_worker_pid()
        if live_pid:
            self._terminate_detached_pid(live_pid)
            self._clear_request_worker_pid()
        try:
            self.start_request_worker()
        except Exception:  # noqa: BLE001
            self.request_worker_enabled_check.blockSignals(True)
            self.request_worker_enabled_check.setChecked(False)
            self.request_worker_enabled_check.blockSignals(False)
            self._save_settings()
        self._refresh_request_worker_ui()

    def _restore_request_worker_state(self) -> None:
        if not self.request_worker_enabled_check.isChecked():
            self._refresh_request_worker_ui()
            return
        if self._read_worker_pid():
            self.request_worker_enabled_check.blockSignals(True)
            self.request_worker_enabled_check.setChecked(False)
            self.request_worker_enabled_check.blockSignals(False)
            self._set_request_worker_status(
                "Website orders mode stayed off because a local render is already running.",
                "warning",
            )
            self._sync_workflow_state()
            return
        try:
            self.start_request_worker(quiet=True)
        except Exception:  # noqa: BLE001
            self.request_worker_enabled_check.blockSignals(True)
            self.request_worker_enabled_check.setChecked(False)
            self.request_worker_enabled_check.blockSignals(False)
        self._save_settings()
        self._refresh_request_worker_ui()

    def _on_request_worker_enabled_changed(self) -> None:
        enabled = self.request_worker_enabled_check.isChecked()
        try:
            if enabled:
                self.start_request_worker()
            else:
                self.stop_request_worker()
        except Exception as exc:  # noqa: BLE001
            self.request_worker_enabled_check.blockSignals(True)
            self.request_worker_enabled_check.setChecked(False)
            self.request_worker_enabled_check.blockSignals(False)
            self._set_request_worker_status(
                str(exc) or "Website orders mode could not be turned on.",
                "warning",
            )
        self._save_settings()
        self._refresh_request_worker_ui()

    def _refresh_request_worker_ui(self) -> None:
        if not hasattr(self, "request_worker_enabled_check"):
            return
        config_path = single_save_supabase_config_file()
        psd_text = self.psd_edit.text().strip() or str(DEFAULT_PSD)
        self.request_worker_psd_label.setText(
            f"PSD used for website orders: {format_project_relative_path(psd_text)}"
        )
        self.request_worker_paths_label.setText(
            f"Orders folder: {format_project_relative_path(self._request_worker_dir())}\n"
            f"Activity log: {format_project_relative_path(self._request_worker_log_path())}\n"
            f"Status file: {format_project_relative_path(self._request_worker_status_path())}"
        )

        config_error = ""
        config_meta = ""
        try:
            config = load_single_save_config()
            config_meta = (
                f"Config file: {format_project_relative_path(config_path)}\n"
                f"Request table: {config.request_table}  |  Cache table: {config.cache_table}  |  Bucket: {config.storage_bucket}"
            )
        except Exception as exc:  # noqa: BLE001
            config_error = str(exc)
            config_meta = (
                f"Config file: {format_project_relative_path(config_path)}\n"
                f"Config issue: {config_error}"
            )
        self.request_worker_config_label.setText(config_meta)

        payload = self._load_request_worker_status_payload()
        live_pid = self._read_request_worker_pid()
        state = str(payload.get("state") or "").strip()
        last_message = str(payload.get("last_message") or "").strip()
        current_request_id = str(payload.get("current_request_id") or "").strip()
        current_request_text = str(payload.get("current_request_text") or "").strip()
        last_poll_at = str(payload.get("last_poll_at") or "").strip()
        last_claimed_at = str(payload.get("last_claimed_at") or "").strip()
        try:
            processed_count = int(payload.get("processed_count") or 0) if payload else 0
        except (TypeError, ValueError):
            processed_count = 0
        try:
            failed_count = int(payload.get("failed_count") or 0) if payload else 0
        except (TypeError, ValueError):
            failed_count = 0
        try:
            poll_interval = int(payload.get("poll_interval_seconds") or self.request_worker_poll_spin.value()) if payload else self.request_worker_poll_spin.value()
        except (TypeError, ValueError):
            poll_interval = self.request_worker_poll_spin.value()

        if not self.request_worker_enabled_check.isChecked():
            self._set_request_worker_status("Website orders mode is off. Local render controls are available.", "info")
            self._set_request_worker_result("", "info", visible=False)
            self._sync_workflow_state()
            return
        if config_error:
            self._set_request_worker_status(f"Website orders config error: {config_error}", "error")
            self._set_request_worker_result("", "info", visible=False)
            self._sync_workflow_state()
            return
        if live_pid:
            kind = "success" if state == "processing" else "info"
            if state == "error":
                kind = "warning"
            status_text = (
                f"Website orders mode is on (pid {live_pid}). Checking every {poll_interval}s. "
                "Local render controls are paused."
            )
            if current_request_text:
                status_text += f" Current name: {current_request_text}."
            elif current_request_id:
                status_text += f" Current order: {current_request_id}."
            self._set_request_worker_status(status_text, kind)
        else:
            self._set_request_worker_status(
                "Website orders mode is on but no background process was detected. Use Restart Order Mode if needed.",
                "warning",
            )

        detail_lines: list[str] = []
        if state:
            detail_lines.append(f"Mode state: {state}")
        if last_message:
            detail_lines.append(f"Last update: {last_message}")
        if last_poll_at:
            detail_lines.append(f"Last check: {format_local_timestamp(last_poll_at)}")
        if last_claimed_at:
            detail_lines.append(f"Last order picked up: {format_local_timestamp(last_claimed_at)}")
        detail_lines.append(f"Completed orders: {processed_count}  |  Failed checks: {failed_count}")
        self._set_request_worker_result("\n".join(detail_lines), "info")
        self._sync_workflow_state()

    def _open_single_save_config_location(self) -> None:
        path = single_save_supabase_config_file()
        if path.exists():
            self._open_path(path)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._open_path(path.parent)
        QMessageBox.information(
            self,
            "Single-Save Config",
            f"Config file not found yet.\n\nCreate or copy it here:\n{path}",
        )

    def _open_single_save_archive_dir(self) -> None:
        archive_dir = default_single_supabase_export_dir()
        archive_dir.mkdir(parents=True, exist_ok=True)
        self._open_path(archive_dir)

    def _refresh_single_save_ui(self) -> None:
        if not hasattr(self, "save_single_supabase_check"):
            return
        self.single_save_card.setVisible(False)
        self.save_single_supabase_check.blockSignals(True)
        self.save_single_supabase_check.setChecked(False)
        self.save_single_supabase_check.blockSignals(False)
        self._set_single_save_status("", "info")
        self._set_single_save_result("", "info", visible=False)

    @staticmethod
    def _parse_custom_names(raw: str) -> list[str]:
        parsed: list[str] = []
        for row in raw.splitlines():
            for part in row.split(","):
                name = part.strip()
                if name:
                    parsed.append(name)
        # Preserve order, remove duplicates.
        return list(dict.fromkeys(parsed))

    def build_cmd(self) -> list[str]:
        psd = Path(self.psd_edit.text().strip()).expanduser().resolve()
        output = self._current_output_path()
        names_file = self.selected_names_file()
        output.mkdir(parents=True, exist_ok=True)
        self.current_output = output
        removed_count, removed_bytes = batch_runner.cleanup_photoshop_temp_files()
        if removed_count:
            self.append_log(
                f"[APP] Removed {removed_count} Photoshop temp item(s) before start "
                f"({removed_bytes / (1024 ** 3):.2f} GiB).\n"
            )
        # If disk is low, auto-recover (cleanup + PS restart) before launching batch.
        try:
            batch_runner.assert_scratch_headroom(output, psd, batch_runner.MIN_FREE_DISK_GB_DEFAULT)
        except ValueError:
            self.append_log("[APP] Scratch disk below threshold — running automatic recovery...\n")
            try:
                batch_runner.recover_scratch_headroom(
                    output_root=output,
                    psd_path=psd,
                    photoshop_exec=None,
                    min_free_disk_gb=batch_runner.MIN_FREE_DISK_GB_DEFAULT,
                    reason="pre-start",
                )
                self.append_log("[APP] Scratch recovery succeeded.\n")
            except batch_runner.RecoverableScratchHeadroomError as exc:
                self.append_log(f"[APP] Recovery partial: {exc} — batch runner will continue managing.\n")

        mode = str(self.mode_combo.currentData())
        styles = self.selected_styles()
        if not styles:
            raise ValueError("Select at least one style.")
        chunk_size = self.chunk_spin.value()
        restart_every_chunks = self.restart_spin.value()

        runner_args = [
            "--psd",
            str(psd),
            "--names-file",
            str(names_file),
            "--styles",
            ",".join(styles),
            "--chunk-size",
            str(chunk_size),
            "--max-retries",
            str(self.retry_spin.value()),
            "--chunk-timeout",
            str(self.timeout_spin.value()),
            "--restart-every-chunks",
            str(restart_every_chunks),
            "--output-root",
            str(output),
            "--supervisor",
        ]

        if mode == "full":
            selected_letters = self.selected_letters()
            if not selected_letters:
                raise ValueError("Select at least one letter.")
            effective_letters = list(selected_letters)
            skipped_letters: list[str] = []
            if self.skip_done_letters_check.isChecked():
                skipped_letters = [
                    letter for letter in effective_letters if self.letter_coverage.get(letter, LetterCoverage(letter, 0, 0, 0, 0, 0)).is_complete
                ]
                effective_letters = [letter for letter in effective_letters if letter not in skipped_letters]
            if not effective_letters:
                raise ValueError("All selected letters are already fully processed for the selected colors.")
            if skipped_letters:
                self.append_log(f"[APP] Skipping completed letters: {''.join(skipped_letters)}\n")
            letters_arg = "all" if len(effective_letters) == len(ALPHABET) else "".join(effective_letters)
            lowest_path, lowest_free_gb = batch_runner.lowest_scratch_headroom(output, psd)
            if chunk_size > AUTO_FULL_CHUNK_SIZE and len(styles) >= 8:
                chunk_size = AUTO_FULL_CHUNK_SIZE
                self.append_log(
                    f"[APP] Large full batch detected; chunk size auto-reduced to {chunk_size} "
                    f"for {len(styles)} selected colors.\n"
                )
            if lowest_free_gb is not None and lowest_free_gb < LOW_SCRATCH_SAFE_MODE_GB:
                if chunk_size > LOW_SCRATCH_CHUNK_SIZE:
                    chunk_size = LOW_SCRATCH_CHUNK_SIZE
                    self.append_log(
                        f"[APP] Low scratch headroom ({lowest_free_gb:.1f} GiB on {lowest_path}); "
                        f"chunk size forced to {LOW_SCRATCH_CHUNK_SIZE}.\n"
                    )
                if restart_every_chunks <= 0 or restart_every_chunks > LOW_SCRATCH_RESTART_EVERY_CHUNKS:
                    restart_every_chunks = LOW_SCRATCH_RESTART_EVERY_CHUNKS
                    self.append_log(
                        f"[APP] Low scratch headroom ({lowest_free_gb:.1f} GiB on {lowest_path}); "
                        f"Photoshop restart cadence forced to every {LOW_SCRATCH_RESTART_EVERY_CHUNKS} chunk.\n"
                    )
            elif restart_every_chunks <= 0:
                restart_every_chunks = AUTO_RESTART_EVERY_CHUNKS
                self.append_log(
                    f"[APP] Restart/Chunks=0 auto-upgraded to {AUTO_RESTART_EVERY_CHUNKS} "
                    "for Full mode to reduce scratch buildup.\n"
                )
            runner_args[runner_args.index("--chunk-size") + 1] = str(chunk_size)
            runner_args[runner_args.index("--restart-every-chunks") + 1] = str(restart_every_chunks)
            runner_args.extend(["--name-source", "full", "--letters", letters_arg])
            self.last_run_meta = {
                "mode": mode,
                "styles": styles,
                "letters": effective_letters,
                "names_file": str(names_file),
                "chunk_size": chunk_size,
                "restart_every_chunks": restart_every_chunks,
            }
        elif mode == "custom":
            raw = self.custom_names_edit.text().strip()
            if not raw:
                raise ValueError("Typed-name mode selected but no single name was provided.")
            parsed = self._parse_custom_names(raw)
            if not parsed:
                raise ValueError("Single name is empty. Enter one name.")
            selected_name = parsed[0]
            if len(parsed) > 1:
                self.append_log(
                    f"[APP] Typed-name modes use only first name: {selected_name} "
                    f"(ignored {len(parsed)-1} extra name(s)).\n"
                )
            runner_args.extend(
                [
                    "--name-source",
                    "custom",
                    "--custom-names-json",
                    json.dumps([selected_name], ensure_ascii=False),
                    "--letters",
                    "all",
                ]
            )
            self.last_run_meta = {
                "mode": mode,
                "styles": styles,
                "custom_name": selected_name,
                "custom_name_input": raw,
                "save_single_supabase": False,
                "names_file": str(names_file),
                "chunk_size": chunk_size,
                "restart_every_chunks": restart_every_chunks,
            }
        else:
            count = int(mode.split("_")[1])
            runner_args.extend(["--name-source", "test20", "--test-count", str(count), "--letters", "all"])
            self.last_run_meta = {
                "mode": mode,
                "styles": styles,
                "names_file": str(names_file),
                "chunk_size": chunk_size,
                "restart_every_chunks": restart_every_chunks,
            }

        ps_exec = self.ps_exec_edit.text().strip()
        if ps_exec:
            runner_args.extend(["--photoshop-exec", ps_exec])
        return build_worker_command(runner_args)

    def start_run(self) -> None:
        if self.request_worker_enabled_check.isChecked():
            QMessageBox.information(
                self,
                "Website Orders Active",
                "Turn off Website Orders mode before starting a local render.",
            )
            return
        live_pid = self._read_worker_pid()
        if live_pid:
            QMessageBox.information(self, "Running", "A run is already in progress.")
            return
        psd = Path(self.psd_edit.text().strip())
        if not psd.exists():
            QMessageBox.critical(self, "Error", "Select a valid PSD path.")
            return
        self._update_names_file_warning()
        if str(self.mode_combo.currentData()) == "full" and self._names_warning_confirm_required:
            reply = QMessageBox.warning(
                self,
                "Same PSD Batch Warning",
                self._names_warning_dialog_text or "This list overlaps with same-PSD processed batches. Continue anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self._set_status("start cancelled")
                return
        if str(self.mode_combo.currentData()) == "custom":
            self._set_single_save_result("", "info", visible=False)
        try:
            cmd = self.build_cmd()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Error", str(exc))
            return

        self._save_settings()
        self._stop_requested = False
        self._run_log_path().write_text("", encoding="utf-8")
        self.append_log("\n[APP] START " + " ".join(cmd) + "\n")
        self._append_run_log("[APP] START " + " ".join(cmd) + "\n")
        started_at = time.time()

        try:
            pid = self._start_worker_detached(cmd, reset_log=False)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Error", "Failed to start process.")
            self._set_status("start failed")
            self.append_log(f"[APP] Failed to start detached worker: {exc}\n")
            return
        self.last_run_meta["worker_pid"] = pid
        self.last_run_meta["post_run_handled"] = False
        self.last_run_meta["started_at"] = started_at
        self._set_status(f"running (pid {pid})")

    def stop_run(self) -> None:
        self._stop_requested = True
        self.current_output.mkdir(parents=True, exist_ok=True)
        self._manual_stop_flag_path().write_text("1\n", encoding="utf-8")

        live_pid = self._read_worker_pid()
        if not live_pid and (not self.proc or self.proc.state() == QProcess.NotRunning):
            self._set_status("idle")
            return
        if live_pid:
            try:
                os.killpg(live_pid, signal.SIGTERM)
            except OSError:
                pass
            deadline = time.time() + 4.0
            while time.time() < deadline and self._is_pid_running(live_pid):
                time.sleep(0.2)
            if self._is_pid_running(live_pid):
                try:
                    os.killpg(live_pid, signal.SIGKILL)
                except OSError:
                    pass
            self._clear_worker_pid()
        if self.proc and self.proc.state() != QProcess.NotRunning:
            pid = int(self.proc.processId())
            self.proc.terminate()
            if not self.proc.waitForFinished(4000):
                self.proc.kill()
            if sys.platform == "darwin":
                if pid > 0:
                    subprocess.run(["pkill", "-P", str(pid)], check=False, capture_output=True, text=True)
                subprocess.run(["pkill", "-f", "osascript -"], check=False, capture_output=True, text=True)
        self.last_run_meta["post_run_handled"] = True
        self._set_status("stopped")

    def on_proc_output(self) -> None:
        proc = self.proc
        if not proc:
            return
        data = proc.readAllStandardOutput().data().decode("utf-8", errors="replace")
        if data:
            self.append_log(data)
            try:
                with (self.current_output / "run.log").open("a", encoding="utf-8") as handle:
                    handle.write(data)
            except OSError:
                pass

    def on_proc_finished(self) -> None:
        if not self.proc:
            return
        exit_code = self.proc.exitCode()
        self._set_status("idle" if exit_code == 0 else f"idle (exit {exit_code})")
        self.progress_bar.setVisible(False)
        self._finalize_last_run()
        self.proc = None

    def _auto_resume_incomplete_run(self) -> None:
        if self._stop_requested or self._manual_stop_flag_path().exists():
            return
        if self._read_worker_pid():
            return
        now = time.time()
        if now - self._last_auto_resume_at < 15:
            return
        self._last_auto_resume_at = now
        try:
            cmd = self.build_cmd()
            self.append_log("[APP] Worker stopped unexpectedly; auto-resuming batch.\n")
            self._append_run_log("[APP] Worker stopped unexpectedly; auto-resuming batch.\n")
            pid = self._start_worker_detached(cmd, reset_log=False)
            self._set_status(f"running (pid {pid})")
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"[APP] Auto-resume failed: {exc}\n")
            self._set_status("auto-resume failed")

    def refresh_status(self) -> None:
        self.current_output = self._current_output_path()
        progress_data: dict[str, object] | None = None
        live_pid = self._read_worker_pid()
        allow_post_run = True
        progress = self.current_output / "progress.json"
        if progress.exists():
            try:
                p = json.loads(progress.read_text(encoding="utf-8"))
                progress_data = p
                done = int(p.get("done", 0))
                total = int(p.get("total", 0))
                remaining = int(p.get("remaining", max(total - done, 0)))
                rate = p.get("last_chunk_sec_per_item", "?")
                if total > 0:
                    self.progress_bar.setRange(0, total)
                    self.progress_bar.setValue(done)
                    self.progress_bar.setFormat(f"{done} / {total}  ({rate} s/item)")
                    self.progress_bar.setVisible(True)
                if live_pid:
                    self._set_status(f"running (pid {live_pid})  |  {done}/{total}  |  {rate} s/item")
                else:
                    self._set_status(f"done {done}/{total}  |  {rate} s/item")
                if total > 0 and remaining > 0 and not live_pid:
                    allow_post_run = False
                    self._auto_resume_incomplete_run()
                    live_pid = self._read_worker_pid()
            except (json.JSONDecodeError, ValueError, KeyError):
                pass
        elif live_pid:
            self._set_status(f"running (pid {live_pid})")
        log_path = self._run_log_path()
        txt = tail_text(log_path)
        if txt and txt.strip() != self.log.toPlainText().strip():
            self.log.setPlainText(txt)
            self.log.moveCursor(QTextCursor.End)
        if str(self.mode_combo.currentData()) == "full":
            self.refresh_letter_summary()
        self.refresh_scratch_status()
        self._refresh_request_worker_ui()
        if not live_pid and allow_post_run:
            self._finalize_last_run()

    def _open_path(self, path: Path) -> None:
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
                return
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
                return
            subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"[APP] Failed to open path: {path} ({exc})\n")

    def _progress_is_complete(self) -> bool:
        progress = self.current_output / "progress.json"
        if not progress.exists():
            return False
        try:
            payload = json.loads(progress.read_text(encoding="utf-8"))
            done = int(payload.get("done", 0))
            total = int(payload.get("total", 0))
            remaining = int(payload.get("remaining", max(total - done, 0)))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False
        return total > 0 and remaining == 0 and done >= total

    def _custom_render_outputs(self) -> tuple[str, list[tuple[str, Path]]]:
        custom_name = str(self.last_run_meta.get("custom_name", "")).strip()
        styles = self.last_run_meta.get("styles")
        if not custom_name or not isinstance(styles, list):
            return "", []
        render_name = custom_name.upper()
        started_at = float(self.last_run_meta.get("started_at", 0.0))
        fresh_outputs: list[tuple[str, Path]] = []
        existing_outputs: list[tuple[str, Path]] = []
        missing_styles: list[str] = []
        stale_styles: list[str] = []
        for style in styles:
            out = self.current_output / sanitize_filename(str(style)) / f"{sanitize_filename(render_name)}.png"
            if not out.exists():
                missing_styles.append(str(style))
                continue
            existing_outputs.append((str(style), out))
            if out.stat().st_mtime >= max(0.0, started_at - 1.0):
                fresh_outputs.append((str(style), out))
            else:
                stale_styles.append(str(style))
        if fresh_outputs:
            if missing_styles:
                self.append_log(f"[APP] Missing custom PNG(s): {', '.join(missing_styles)}\n")
            return render_name, fresh_outputs
        if existing_outputs and self._progress_is_complete():
            if stale_styles:
                self.append_log("[APP] Reusing existing custom PNG(s) already present on disk.\n")
            return render_name, existing_outputs
        if missing_styles:
            self.append_log(f"[APP] Missing custom PNG(s): {', '.join(missing_styles)}\n")
        return render_name, []

    def _finalize_last_run(self) -> None:
        worker_pid = self.last_run_meta.get("worker_pid")
        if not isinstance(worker_pid, int):
            return
        if bool(self.last_run_meta.get("post_run_handled", False)):
            return
        self.last_run_meta["post_run_handled"] = True
        if str(self.last_run_meta.get("mode", "")) != "custom":
            return
        self._handle_custom_post_run()

    def _handle_custom_post_run(self) -> None:
        mode = str(self.last_run_meta.get("mode", ""))
        if mode != "custom":
            return
        self.current_output = self._current_output_path()
        render_name, outputs = self._custom_render_outputs()
        if not render_name or not outputs:
            self.append_log("[APP] Typed-name run finished but no PNGs were found.\n")
            if bool(self.last_run_meta.get("save_single_supabase", False)):
                self._set_single_save_result(
                    "Render finished but no PNGs were found for the single-save upload.",
                    "warning",
                )
            return
        first_style, first_output = outputs[0]
        self.result_label.setText(f"Last Result: {render_name} ({first_style})")
        for style, out in outputs:
            self.append_log(f"[APP] Typed-name result ready: {out} ({style})\n")
        if self.auto_open_custom_check.isChecked():
            self._open_path(first_output)
        if not bool(self.last_run_meta.get("save_single_supabase", False)):
            return
        try:
            archived_run = archive_custom_render_outputs(render_name, outputs)
            self.append_log(f"[APP] Archived single export: {archived_run.run_root}\n")
            import_result = import_archived_run(archived_run)
            if import_result.output:
                for line in import_result.output.splitlines():
                    self.append_log(f"[SUPABASE] {line}\n")
            uploaded_count = sum(1 for item in import_result.items if item.status == "uploaded")
            skipped_count = sum(1 for item in import_result.items if item.status == "skipped")
            failed_count = sum(1 for item in import_result.items if item.status == "failed")
            if import_result.ok:
                self.append_log(
                    f"[APP] Supabase single-save completed for {render_name} "
                    f"({len(archived_run.archived_renders)} PNGs).\n"
                )
                summary_kind = "success" if uploaded_count else "info"
                self._set_single_save_result(
                    f"Supabase import finished for {render_name}. Uploaded {uploaded_count}, skipped {skipped_count}, failed {failed_count}. Archive: {archived_run.run_root}",
                    summary_kind,
                )
            else:
                self.append_log(
                    f"[APP] Supabase single-save had failures for {render_name}. "
                    f"Uploaded {uploaded_count}, skipped {skipped_count}, failed {failed_count}.\n"
                )
                self._set_single_save_result(
                    f"Supabase import had failures for {render_name}. Uploaded {uploaded_count}, skipped {skipped_count}, failed {failed_count}. Check the run log for per-style errors.",
                    "error",
                )
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"[APP] Supabase single-save failed: {exc}\n")
            self._set_single_save_result(
                f"Supabase import failed: {exc}",
                "error",
            )

    def _save_settings(self) -> None:
        payload = {
            "psd": self.psd_edit.text().strip(),
            "output": format_preferred_path(self.out_edit.text().strip() or DEFAULT_OUTPUT),
            "names_file": str(self.selected_names_file()),
            "mode": self.mode_combo.currentData(),
            "letters": self.letters_edit.text().strip(),
            "chunk": self.chunk_spin.value(),
            "retries": self.retry_spin.value(),
            "timeout": self.timeout_spin.value(),
            "restart": self.restart_spin.value(),
            "ps_exec": self.ps_exec_edit.text().strip(),
            "styles": self.selected_styles(),
            "skip_done_letters": self.skip_done_letters_check.isChecked(),
            "custom_name": self.custom_names_edit.text().strip(),
            "custom_names": self.custom_names_edit.text().strip(),
            "auto_open_custom": self.auto_open_custom_check.isChecked(),
            "save_single_supabase": self.save_single_supabase_check.isChecked(),
            "request_worker_enabled": self.request_worker_enabled_check.isChecked(),
            "request_worker_poll_seconds": self.request_worker_poll_spin.value(),
        }
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _load_settings(self) -> None:
        if not SETTINGS_FILE.exists():
            self.psd_edit.setText(str(DEFAULT_PSD))
            self.out_edit.setText(format_preferred_path(DEFAULT_OUTPUT))
            self._refresh_single_save_ui()
            return
        try:
            cfg = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, TypeError):
            self.psd_edit.setText(str(DEFAULT_PSD))
            self.out_edit.setText(format_preferred_path(DEFAULT_OUTPUT))
            self._refresh_single_save_ui()
            return
        self.psd_edit.setText(str(cfg.get("psd", str(DEFAULT_PSD))))
        self.out_edit.setText(format_preferred_path(cfg.get("output", DEFAULT_OUTPUT)))
        self.current_output = self._current_output_path()
        self._populate_names_file_combo()
        names_file = str(cfg.get("names_file", str(NAMES_FILE.resolve())))
        idx = self.names_file_combo.findData(names_file)
        if idx >= 0:
            self.names_file_combo.setCurrentIndex(idx)
        raw_mode = str(cfg.get("mode", "full"))
        mode = normalize_saved_mode(raw_mode)
        idx = self.mode_combo.findData(mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self.letters_edit.setText(str(cfg.get("letters", "ABC")))
        self.chunk_spin.setValue(int(cfg.get("chunk", 10)))
        self.retry_spin.setValue(int(cfg.get("retries", 5)))
        self.timeout_spin.setValue(int(cfg.get("timeout", 300)))
        self.restart_spin.setValue(int(cfg.get("restart", 0)))
        self.ps_exec_edit.setText(str(cfg.get("ps_exec", "")))
        self.custom_names_edit.setText(str(cfg.get("custom_name", cfg.get("custom_names", ""))))
        self.auto_open_custom_check.setChecked(bool(cfg.get("auto_open_custom", True)))
        self.save_single_supabase_check.setChecked(False)
        self.skip_done_letters_check.setChecked(bool(cfg.get("skip_done_letters", True)))
        self.request_worker_poll_spin.setValue(int(cfg.get("request_worker_poll_seconds", 10)))
        self.request_worker_enabled_check.blockSignals(True)
        self.request_worker_enabled_check.setChecked(bool(cfg.get("request_worker_enabled", False)))
        self.request_worker_enabled_check.blockSignals(False)
        saved_styles = cfg.get("styles", [])
        for name, cb in self.style_checks.items():
            cb.setChecked(name in saved_styles)
        self._update_style_badge()
        self._set_single_save_result("", "info", visible=False)
        self._set_request_worker_result("", "info", visible=False)
        self._refresh_single_save_ui()
        self._refresh_request_worker_ui()
        self.refresh_letter_summary()
        self.refresh_scratch_status()
        self._update_names_file_warning()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._save_settings()
        live_pid = self._read_worker_pid()
        if live_pid:
            try:
                self._append_run_log(f"[APP] Window closed; detached batch continues in background (pid {live_pid}).\n")
            except OSError:
                pass
        super().closeEvent(event)


def main() -> None:
    if is_request_worker_mode(sys.argv):
        raise SystemExit(request_worker_runner.main(sys.argv[2:]))
    if is_worker_mode(sys.argv):
        batch_runner.main(sys.argv[2:])
        return
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
