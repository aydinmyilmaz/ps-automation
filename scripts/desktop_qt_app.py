#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import onecall_unattended_batch as batch_runner
from app_paths import (
    SOURCE_PROJECT_ROOT,
    bundled_default_psd,
    bundled_names_file,
    default_batch_output_dir,
    default_single_supabase_export_dir,
    desktop_settings_file,
    gmail_derived_dir,
    gmail_ranked_dir,
    is_frozen_app,
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
        QPlainTextEdit,
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
NAMES_FILE = bundled_names_file()
DEFAULT_OUTPUT = default_batch_output_dir()
SETTINGS_FILE = desktop_settings_file()
DEFAULT_PSD = bundled_default_psd()
AUTO_RESTART_EVERY_CHUNKS = 4
AUTO_FULL_CHUNK_SIZE = 3
LOW_SCRATCH_SAFE_MODE_GB = 12.0
LOW_SCRATCH_CHUNK_SIZE = 1
LOW_SCRATCH_RESTART_EVERY_CHUNKS = 1

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
    gmail_dir = gmail_ranked_dir()
    derived_dir = gmail_derived_dir()
    add("Gmail first names  top 500", gmail_dir / "gmail_first_names_top_500.txt")
    add("Gmail first names  top 1000", gmail_dir / "gmail_first_names_top_1000.txt")
    add("Gmail first names  all 1624", gmail_dir / "gmail_first_names_top_1624.txt")
    add("Batch fill  top 500", derived_dir / "gmail_names_next_batch_fill_after_3000_top_500.txt")
    add("Gmail in 3000  500_1000", derived_dir / "gmail_names_in_3000_unprocessed_500_1000.txt")
    add("Gmail in 3000  all", derived_dir / "gmail_names_in_3000_unprocessed_all.txt")
    add("Gmail fallback  top 106", derived_dir / "gmail_names_not_in_3000_unprocessed_top_106.txt")
    add("Gmail fallback  all", derived_dir / "gmail_names_not_in_3000_unprocessed_all.txt")
    return items


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


def build_worker_command(args: list[str]) -> list[str]:
    if is_frozen_app():
        return [sys.executable, WORKER_FLAG, *args]
    return [sys.executable, "-u", str(Path(__file__).resolve()), WORKER_FLAG, *args]


def tail_text(path: Path, max_bytes: int = 30000) -> str:
    if not path.exists():
        return ""
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raw = raw[-max_bytes:]
    return raw.decode("utf-8", errors="replace")


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
        self.setWindowTitle("PSD Batch Desktop App")
        self.resize(1220, 860)

        self.proc: QProcess | None = None
        self.worker_pid: int | None = None
        self.current_output = DEFAULT_OUTPUT
        self.style_checks: dict[str, QCheckBox] = {}
        self.letter_checks: dict[str, QCheckBox] = {}
        self.letter_meta_labels: dict[str, QLabel] = {}
        self.letter_cells: dict[str, QWidget] = {}
        self.letter_coverage: dict[str, LetterCoverage] = {}
        self.last_run_meta: dict[str, object] = {}
        self.scratch_status_text = ""
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
}
QPushButton#primaryBtn:hover  { background: #1d4ed8; }
QPushButton#primaryBtn:pressed { background: #1e40af; }
QPushButton#primaryBtn:disabled {
  background: #93c5fd;
  border-color: #93c5fd;
}
QPushButton#dangerBtn {
  background: #ef4444;
  border-color: #dc2626;
  color: #ffffff;
  font-weight: 700;
}
QPushButton#dangerBtn:hover  { background: #dc2626; }
QPushButton#dangerBtn:pressed { background: #b91c1c; }
QPushButton#dangerBtn:disabled {
  background: #fca5a5;
  border-color: #fca5a5;
}
QPushButton#ghostBtn {
  background: transparent;
  border-color: #e2e8f0;
  color: #374151;
}
QPushButton#ghostBtn:hover { background: #f1f5f9; }
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
        h_layout.setSpacing(12)
        title_lbl = QLabel("PSD Batch Renderer")
        title_lbl.setObjectName("appTitle")
        self.psd_badge = QLabel("no PSD selected")
        self.psd_badge.setObjectName("psdBadge")
        self.psd_badge.setMaximumWidth(400)
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
        files_box = QGroupBox("Files")
        files_form = QFormLayout(files_box)
        files_form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        files_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        files_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        files_form.setHorizontalSpacing(12)
        files_form.setVerticalSpacing(8)
        content_layout.addWidget(files_box)

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
        files_form.addRow(
            self._label_with_help(
                "PSD Template",
                "Photoshop source file. Scan Colors reads the available style groups from this PSD.",
            ),
            self._wrap(psd_row),
        )

        out_row = QHBoxLayout()
        self.out_edit = QLineEdit(str(DEFAULT_OUTPUT))
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
        files_form.addRow(
            self._label_with_help(
                "Output Folder",
                "PNG files are written here. Resume logic also checks this folder and skips files that already exist.",
            ),
            self._wrap(out_row),
        )

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
        files_form.addRow(
            self._label_with_help(
                "Names TXT",
                "Choose which names list Full mode uses. Letter counts, coverage, and batch rendering all follow this selected txt file.",
            ),
            self._wrap(names_row),
        )

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
        files_form.addRow(
            self._label_with_help(
                "Scratch Disk",
                "Live free-space check for the PSD, output, home, and temp volumes Photoshop may use. Start already auto-cleans Photoshop temp files; Free Temp Files lets you run that cleanup manually first.",
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
            files_form.addRow("Photoshop Exec", self._wrap(ps_row))
        else:
            self.ps_exec_edit = QLineEdit()  # hidden, kept for compat

        # ── Render Settings ──────────────────────────────────
        settings_box = QGroupBox("Render Settings")
        settings_layout = QVBoxLayout(settings_box)
        settings_layout.setSpacing(10)
        content_layout.addWidget(settings_box)

        row1 = QHBoxLayout()
        row1.setSpacing(12)
        mode_col = QVBoxLayout()
        mode_col.setSpacing(4)
        mode_col.addWidget(
            self._label_with_help(
                "Mode",
                "Full uses the names txt file with letter filters. Test runs a small sample. Custom renders the first manual name only.",
            )
        )
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Full  —  3000 names (letters filter)", "full")
        self.mode_combo.addItem("Test  —  1 name", "test20_1")
        self.mode_combo.addItem("Test  —  first 5", "test20_5")
        self.mode_combo.addItem("Test  —  first 10", "test20_10")
        self.mode_combo.addItem("Test  —  all 20", "test20_20")
        self.mode_combo.addItem("Custom  —  manual list", "custom")
        self.mode_combo.currentIndexChanged.connect(self.on_mode_change)
        mode_col.addWidget(self.mode_combo)
        row1.addLayout(mode_col, 3)

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
        row1.addLayout(letters_col, 2)
        settings_layout.addLayout(row1)

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
                help_text = "How many times a failed chunk can be retried before the run stops."
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
        settings_layout.addLayout(row2)

        self.custom_section = QGroupBox("Custom Names")
        custom_layout = QVBoxLayout(self.custom_section)
        custom_layout.setSpacing(6)
        self.custom_names_edit = QPlainTextEdit()
        self.custom_names_edit.setPlaceholderText("One name per line or comma-separated  (e.g. KEREM, BATSHEVA)")
        self.custom_names_edit.setMaximumHeight(80)
        custom_layout.addWidget(self.custom_names_edit)
        self.auto_open_custom_check = QCheckBox("Auto-open PNG after render")
        self.auto_open_custom_check.setChecked(True)
        custom_layout.addWidget(self.auto_open_custom_check)
        save_row = QHBoxLayout()
        save_row.setSpacing(6)
        self.save_single_supabase_check = QCheckBox("Save single render to Supabase")
        save_row.addWidget(self.save_single_supabase_check)
        save_row.addWidget(
            self._help_icon(
                f"If enabled, custom-mode PNGs are archived under "
                f"{default_single_supabase_export_dir()} and then imported into Supabase "
                "using the shared PS-local cache schema."
            )
        )
        save_row.addStretch(1)
        custom_layout.addWidget(self._wrap(save_row))
        custom_layout.addWidget(
            QLabel(f"Uses bundled config: {single_save_supabase_config_file()}")
        )
        settings_layout.addWidget(self.custom_section)

        self.letter_section = QGroupBox("Letter Coverage")
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
        settings_layout.addWidget(self.letter_section)

        # ── Color Palette ────────────────────────────────────
        styles_box = QGroupBox("Color Palette")
        styles_outer = QVBoxLayout(styles_box)
        styles_outer.setSpacing(8)
        content_layout.addWidget(styles_box)

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
        self.start_btn = QPushButton("▶  Start / Resume")
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.setMinimumHeight(44)
        self.start_btn.clicked.connect(self.start_run)
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setObjectName("dangerBtn")
        self.stop_btn.setMinimumHeight(44)
        self.stop_btn.setFixedWidth(100)
        self.stop_btn.clicked.connect(self.stop_run)
        refresh_btn = QPushButton("↻")
        refresh_btn.setObjectName("ghostBtn")
        refresh_btn.setMinimumHeight(44)
        refresh_btn.setFixedWidth(44)
        refresh_btn.setToolTip("Refresh status")
        refresh_btn.clicked.connect(self.refresh_status)
        btn_row.addWidget(self.start_btn, 1)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(refresh_btn)
        action_layout.addLayout(btn_row)

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
        row.addWidget(QLabel(text))
        row.addWidget(self._help_icon(help_text))
        row.addStretch(1)
        return self._wrap(row)

    def on_mode_change(self) -> None:
        mode = str(self.mode_combo.currentData())
        self.letters_edit.setEnabled(mode == "full")
        self.letter_section.setVisible(mode == "full")
        self.custom_names_edit.setEnabled(mode == "custom")
        self.custom_section.setVisible(mode == "custom")

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
        return Path(self.out_edit.text().strip() or str(DEFAULT_OUTPUT)).expanduser().resolve()

    @staticmethod
    def _free_gib(path: Path) -> float:
        return shutil.disk_usage(path).free / (1024 ** 3)

    def _run_log_path(self) -> Path:
        return self._current_output_path() / "run.log"

    def _worker_pid_path(self) -> Path:
        return self._current_output_path() / "worker.pid"

    def _manual_stop_flag_path(self) -> Path:
        return self._current_output_path() / "manual_stop.flag"

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

    def _on_letter_checks_changed(self) -> None:
        if self._syncing_letters:
            return
        self._sync_letters_text_from_checks()
        self._update_letter_badge()

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

    def _update_style_badge(self) -> None:
        selected = sum(1 for cb in self.style_checks.values() if cb.isChecked())
        total = len(self.style_checks)
        self.style_badge.setText(f"{selected} of {total} selected")

    def _open_output_folder(self) -> None:
        path = Path(self.out_edit.text().strip() or str(DEFAULT_OUTPUT))
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def pick_psd(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select PSD", str(DEFAULT_PSD.parent), "PSD Files (*.psd)")
        if path:
            self.psd_edit.setText(path)

    def pick_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Output Folder", str(DEFAULT_OUTPUT))
        if path:
            self.out_edit.setText(path)

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

    def append_log(self, text: str) -> None:
        self.log.moveCursor(QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.ensureCursorVisible()

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
        output = Path(self.out_edit.text().strip() or str(DEFAULT_OUTPUT)).expanduser().resolve()
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
            raw = self.custom_names_edit.toPlainText().strip()
            if not raw:
                raise ValueError("Custom mode selected but no custom names were provided.")
            parsed = self._parse_custom_names(raw)
            if not parsed:
                raise ValueError("Custom names are empty. Enter at least one name.")
            selected_name = parsed[0]
            if self.save_single_supabase_check.isChecked():
                load_single_save_config()
            if len(parsed) > 1:
                self.append_log(
                    f"[APP] Custom mode uses only first name: {selected_name} "
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
                "save_single_supabase": self.save_single_supabase_check.isChecked(),
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
        live_pid = self._read_worker_pid()
        if live_pid:
            QMessageBox.information(self, "Running", "A run is already in progress.")
            return
        psd = Path(self.psd_edit.text().strip())
        if not psd.exists():
            QMessageBox.critical(self, "Error", "Select a valid PSD path.")
            return
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
        if str(self.last_run_meta.get("mode", "")) != "custom":
            return
        self.current_output = self._current_output_path()
        render_name, outputs = self._custom_render_outputs()
        if not render_name or not outputs:
            self.append_log("[APP] Custom run finished but no PNGs were found.\n")
            return
        first_style, first_output = outputs[0]
        self.result_label.setText(f"Last Result: {render_name} ({first_style})")
        for style, out in outputs:
            self.append_log(f"[APP] Custom result ready: {out} ({style})\n")
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
            if import_result.ok:
                self.append_log(
                    f"[APP] Supabase single-save completed for {render_name} "
                    f"({len(archived_run.archived_renders)} PNGs).\n"
                )
            else:
                self.append_log(
                    f"[APP] Supabase single-save failed with exit code {import_result.returncode}.\n"
                )
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"[APP] Supabase single-save failed: {exc}\n")

    def _save_settings(self) -> None:
        payload = {
            "psd": self.psd_edit.text().strip(),
            "output": self.out_edit.text().strip(),
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
            "custom_names": self.custom_names_edit.toPlainText(),
            "auto_open_custom": self.auto_open_custom_check.isChecked(),
            "save_single_supabase": self.save_single_supabase_check.isChecked(),
        }
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _load_settings(self) -> None:
        if not SETTINGS_FILE.exists():
            self.psd_edit.setText(str(DEFAULT_PSD))
            self.out_edit.setText(str(DEFAULT_OUTPUT))
            return
        try:
            cfg = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, TypeError):
            self.psd_edit.setText(str(DEFAULT_PSD))
            self.out_edit.setText(str(DEFAULT_OUTPUT))
            return
        self.psd_edit.setText(str(cfg.get("psd", str(DEFAULT_PSD))))
        self.out_edit.setText(str(cfg.get("output", str(DEFAULT_OUTPUT))))
        self.current_output = self._current_output_path()
        self._populate_names_file_combo()
        names_file = str(cfg.get("names_file", str(NAMES_FILE.resolve())))
        idx = self.names_file_combo.findData(names_file)
        if idx >= 0:
            self.names_file_combo.setCurrentIndex(idx)
        mode = str(cfg.get("mode", "full"))
        idx = self.mode_combo.findData(mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self.letters_edit.setText(str(cfg.get("letters", "ABC")))
        self.chunk_spin.setValue(int(cfg.get("chunk", 10)))
        self.retry_spin.setValue(int(cfg.get("retries", 5)))
        self.timeout_spin.setValue(int(cfg.get("timeout", 300)))
        self.restart_spin.setValue(int(cfg.get("restart", 0)))
        self.ps_exec_edit.setText(str(cfg.get("ps_exec", "")))
        self.custom_names_edit.setPlainText(str(cfg.get("custom_names", "")))
        self.auto_open_custom_check.setChecked(bool(cfg.get("auto_open_custom", True)))
        self.save_single_supabase_check.setChecked(bool(cfg.get("save_single_supabase", False)))
        self.skip_done_letters_check.setChecked(bool(cfg.get("skip_done_letters", True)))
        saved_styles = cfg.get("styles", [])
        for name, cb in self.style_checks.items():
            cb.setChecked(name in saved_styles)
        self._update_style_badge()
        self.refresh_letter_summary()
        self.refresh_scratch_status()

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
    if is_worker_mode(sys.argv):
        batch_runner.main(sys.argv[2:])
        return
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
