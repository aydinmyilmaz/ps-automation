# Desktop Qt App UI Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rewrite the UI layer of `scripts/desktop_qt_app.py` to be modern, professional, and functional without touching any logic methods.

**Architecture:** Single-file PySide6 Qt app. Only `_build_ui`, `_apply_theme`, `_render_styles`, and minor helper methods change. All logic methods (`build_cmd`, `start_run`, `stop_run`, `refresh_status`, `_save_settings`, `_load_settings`, etc.) stay untouched.

**Tech Stack:** Python 3, PySide6 6.x, QSS stylesheets

---

### Task 1: Update imports & constants

**Files:**
- Modify: `scripts/desktop_qt_app.py:1-48`

**Step 1: Add QProgressBar and QFrame to imports**

Replace the existing PySide6 import block with:

```python
from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtGui import QColor, QTextCursor
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
    QVBoxLayout,
    QWidget,
)
```

**Step 2: Add color map constant after DEFAULT_PSD line**

```python
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
```

**Step 3: Verify app still starts**
```bash
cd /Users/aydin/Desktop/apps/ps-automation
python3 scripts/desktop_qt_app.py &
sleep 3 && pkill -f desktop_qt_app.py
```
Expected: no import errors.

---

### Task 2: Rewrite `_apply_theme`

**Files:**
- Modify: `scripts/desktop_qt_app.py` — `_apply_theme` method

**Step 1: Replace the entire `_apply_theme` method with:**

```python
def _apply_theme(self) -> None:
    self.setStyleSheet("""
QWidget {
  font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
  font-size: 13px;
  color: #111827;
}
QWidget#root {
  background: #f1f5f9;
}
/* ── Header ── */
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
/* ── Cards ── */
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
/* ── Inputs ── */
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
/* ── Buttons ── */
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
/* ── Progress ── */
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
/* ── Status pills ── */
QLabel#statusPill {
  background: #eff6ff;
  color: #1d4ed8;
  border: 1.5px solid #bfdbfe;
  border-radius: 20px;
  padding: 4px 14px;
  font-weight: 600;
  font-size: 12px;
}
QLabel#statusPill[running="true"] {
  background: #f0fdf4;
  color: #15803d;
  border-color: #86efac;
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
/* ── Log ── */
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
```

---

### Task 3: Rewrite `_build_ui`

**Files:**
- Modify: `scripts/desktop_qt_app.py` — `_build_ui` method

**Step 1: Replace entire `_build_ui` method with:**

```python
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

    # content area with margins
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
    psd_btn.setFixedWidth(70)
    psd_btn.clicked.connect(self.pick_psd)
    scan_btn = QPushButton("Scan Colors")
    scan_btn.setObjectName("ghostBtn")
    scan_btn.clicked.connect(self.scan_styles)
    psd_row.addWidget(self.psd_edit, 1)
    psd_row.addWidget(psd_btn)
    psd_row.addWidget(scan_btn)
    files_form.addRow("PSD Template", self._wrap(psd_row))

    out_row = QHBoxLayout()
    self.out_edit = QLineEdit(str(DEFAULT_OUTPUT))
    out_btn = QPushButton("Browse")
    out_btn.setObjectName("ghostBtn")
    out_btn.setFixedWidth(70)
    out_btn.clicked.connect(self.pick_output)
    open_out_btn = QPushButton("Open ↗")
    open_out_btn.setObjectName("ghostBtn")
    open_out_btn.clicked.connect(self._open_output_folder)
    out_row.addWidget(self.out_edit, 1)
    out_row.addWidget(out_btn)
    out_row.addWidget(open_out_btn)
    files_form.addRow("Output Folder", self._wrap(out_row))

    # Photoshop Exec — only on Windows
    if sys.platform.startswith("win"):
        ps_row = QHBoxLayout()
        self.ps_exec_edit = QLineEdit()
        self.ps_exec_edit.setPlaceholderText("Photoshop.exe path")
        ps_btn = QPushButton("Browse")
        ps_btn.setObjectName("ghostBtn")
        ps_btn.setFixedWidth(70)
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
    mode_col.addWidget(QLabel("Mode"))
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
    letters_col.addWidget(QLabel("Letters  (ABC or A,B,C or all)"))
    self.letters_edit = QLineEdit("ABC")
    letters_col.addWidget(self.letters_edit)
    row1.addLayout(letters_col, 2)
    settings_layout.addLayout(row1)

    row2 = QHBoxLayout()
    row2.setSpacing(12)
    for label, attr, lo, hi, default in [
        ("Chunk",         "chunk_spin",   1, 200,  5),
        ("Retries",       "retry_spin",   1,  50,  5),
        ("Timeout (sec)", "timeout_spin", 30, 3600, 300),
        ("Restart/Chunks","restart_spin", 0, 200,  0),
    ]:
        col = QVBoxLayout()
        col.setSpacing(4)
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
    settings_layout.addWidget(self.custom_section)

    # ── Color Palette ────────────────────────────────────
    styles_box = QGroupBox("Color Palette")
    styles_outer = QVBoxLayout(styles_box)
    styles_outer.setSpacing(8)
    content_layout.addWidget(styles_box)

    ctl_row = QHBoxLayout()
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
    action_box.setStyleSheet("QGroupBox { border: none; margin-top: 0; padding: 0; }")
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
```

---

### Task 4: Add helper methods

**Files:**
- Modify: `scripts/desktop_qt_app.py` — add new methods to `MainWindow`

**Step 1: Add `_toggle_log`, `_on_psd_changed`, `_update_style_badge`, `_open_output_folder` methods:**

```python
def _toggle_log(self) -> None:
    visible = not self._log_box.isVisible()
    self._log_box.setVisible(visible)
    self._log_toggle_btn.setText("Run Log  ▴" if visible else "Run Log  ▾")

def _on_psd_changed(self, text: str) -> None:
    name = Path(text).name if text.strip() else "no PSD selected"
    self.psd_badge.setText(name)
    self.psd_badge.setToolTip(text)

def _update_style_badge(self) -> None:
    selected = sum(1 for cb in self.style_checks.values() if cb.isChecked())
    total = len(self.style_checks)
    self.style_badge.setText(f"{selected} of {total} selected")

def _open_output_folder(self) -> None:
    path = Path(self.out_edit.text().strip() or str(DEFAULT_OUTPUT))
    path.mkdir(parents=True, exist_ok=True)
    self._open_path(path)
```

**Step 2: Update `select_all_styles` and `unselect_all_styles` to call badge:**

```python
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
```

---

### Task 5: Rewrite `_render_styles` with color dots

**Files:**
- Modify: `scripts/desktop_qt_app.py` — `_render_styles` method

**Step 1: Replace `_render_styles` with:**

```python
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

        # Color dot
        dot = QLabel()
        dot.setFixedSize(14, 14)
        color = STYLE_COLORS.get(s, "#94a3b8")
        dot.setStyleSheet(
            f"background:{color}; border-radius:7px; border:1px solid rgba(0,0,0,0.15);"
        )

        cb = QCheckBox(s)
        cb.setChecked(False)
        cb.stateChanged.connect(self._update_style_badge)
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
```

---

### Task 6: Update `refresh_status` to drive progress bar

**Files:**
- Modify: `scripts/desktop_qt_app.py` — `refresh_status` method

**Step 1: Replace `refresh_status` with:**

```python
def refresh_status(self) -> None:
    progress = self.current_output / "progress.json"
    if progress.exists():
        try:
            p = json.loads(progress.read_text(encoding="utf-8"))
            done = int(p.get("done", 0))
            total = int(p.get("total", 0))
            rate = p.get("last_chunk_sec_per_item", "?")
            if total > 0:
                self.progress_bar.setRange(0, total)
                self.progress_bar.setValue(done)
                self.progress_bar.setFormat(f"{done} / {total}  ({rate} s/item)")
                self.progress_bar.setVisible(True)
            self._set_status(f"done {done}/{total}  |  {rate} s/item")
        except Exception:
            pass
    if not self.proc or self.proc.state() == QProcess.NotRunning:
        log_path = self.current_output / "run.log"
        txt = tail_text(log_path)
        if txt and txt.strip() != self.log.toPlainText().strip():
            self.log.setPlainText(txt)
            self.log.moveCursor(QTextCursor.End)
```

**Step 2: Update `on_proc_finished` to hide progress bar on completion:**

```python
def on_proc_finished(self) -> None:
    exit_code = self.proc.exitCode() if self.proc else 0
    self._set_status("idle" if exit_code == 0 else f"idle (exit {exit_code})")
    self.progress_bar.setVisible(False)
    self._handle_custom_post_run(exit_code)
    self.proc = None
```

---

### Task 7: Update `_load_settings` to restore style selections

**Files:**
- Modify: `scripts/desktop_qt_app.py` — `_load_settings` method

**Step 1: Add style restoration at the end of `_load_settings`:**

After the existing `self.auto_open_custom_check.setChecked(...)` line, add:
```python
saved_styles = cfg.get("styles", [])
for name, cb in self.style_checks.items():
    cb.setChecked(name in saved_styles)
self._update_style_badge()
```

---

### Task 8: Final check & verification

**Step 1: Run the app**
```bash
cd /Users/aydin/Desktop/apps/ps-automation
python3 scripts/desktop_qt_app.py
```
Expected: App opens with:
- Dark header showing PSD filename badge
- Files section without Photoshop Exec row (macOS)
- Settings with 2-row layout
- Color palette with colored dots, "0 of 16 selected" badge
- Large Start/Stop buttons, hidden progress bar
- Collapsed log with "Run Log ▾" toggle

**Step 2: Test color selection**
- Click "Select All" → badge shows "16 of 16 selected"
- Click "Deselect All" → badge shows "0 of 16 selected"
- Click individual colors → badge updates

**Step 3: Test log toggle**
- Click "Run Log ▾" → log expands
- Click "Run Log ▴" → log collapses

**Step 4: Test PSD badge**
- Click Browse, select PSD → header badge shows filename
