#!/usr/bin/env python3
"""
Simple Photoshop renderer UI.

Usage:
  python3 simple_render_app.py
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


BASE_DIR = Path(__file__).resolve().parent
PSD_PATH = BASE_DIR / "data" / "text_only_2.psd"
OUTPUT_DIR = BASE_DIR / "output" / "app_renders"
PHOTOSHOP_APP_NAME = "Adobe Photoshop 2026"
STYLES = ("metal", "ALTIN", "MAVI", "PEMBE")
FONT_POSTSCRIPT_NAME = "ClarendonBT-Black"


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "name"


def build_jsx(name: str, style: str, out_transparent: Path, out_black: Path) -> str:
    return f"""#target photoshop
app.displayDialogs = DialogModes.NO;

var PSD_PATH = {json.dumps(str(PSD_PATH.as_posix()))};
var STYLE_NAME = {json.dumps(style)};
var RENDER_NAME = {json.dumps(name)};
var OUT_TRANSPARENT = {json.dumps(str(out_transparent.as_posix()))};
var OUT_BLACK = {json.dumps(str(out_black.as_posix()))};
var FONT_PS = {json.dumps(FONT_POSTSCRIPT_NAME)};
var STYLE_GROUPS = ["metal","ALTIN","MAVI","PEMBE"];

function findTopLevelGroup(doc, name) {{
  for (var i = 0; i < doc.layerSets.length; i++) {{
    if (doc.layerSets[i].name === name) return doc.layerSets[i];
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

function fontExists(postScriptName) {{
  for (var i = 0; i < app.fonts.length; i++) {{
    if (app.fonts[i].postScriptName === postScriptName) return true;
  }}
  return false;
}}

function setOnlyStyleVisible(doc, styleName) {{
  for (var i = 0; i < STYLE_GROUPS.length; i++) {{
    var g = findTopLevelGroup(doc, STYLE_GROUPS[i]);
    if (g) g.visible = (STYLE_GROUPS[i] === styleName);
  }}
}}

function exportPng24(documentRef, filePath) {{
  var outFile = new File(filePath);
  var opts = new ExportOptionsSaveForWeb();
  opts.format = SaveDocumentType.PNG;
  opts.PNG8 = false;
  opts.transparency = true;
  opts.interlaced = false;
  documentRef.exportDocument(outFile, ExportType.SAVEFORWEB, opts);
}}

function addBlackBackgroundAtBottom(documentRef) {{
  var bg = documentRef.artLayers.add();
  bg.name = "preview_black_bg";
  bg.move(documentRef.layers[documentRef.layers.length - 1], ElementPlacement.PLACEAFTER);
  documentRef.selection.selectAll();
  var black = new SolidColor();
  black.rgb.red = 0;
  black.rgb.green = 0;
  black.rgb.blue = 0;
  documentRef.selection.fill(black);
  documentRef.selection.deselect();
}}

try {{
  if (!fontExists(FONT_PS)) throw new Error("Font missing: " + FONT_PS);

  var psdFile = new File(PSD_PATH);
  if (!psdFile.exists) throw new Error("PSD not found: " + PSD_PATH);

  var doc = app.open(psdFile);
  var base = doc.activeHistoryState;

  setOnlyStyleVisible(doc, STYLE_NAME);

  var styleGroup = findTopLevelGroup(doc, STYLE_NAME);
  if (!styleGroup) throw new Error("Style group not found: " + STYLE_NAME);

  var textGroup = findLayerRecursive(styleGroup, "TEXT");
  var scope = textGroup ? textGroup : styleGroup;
  var smart = findLayerRecursive(scope, "CUSTOM copy 2");
  if (!smart) smart = findFirstSmartObject(scope);
  if (!smart) throw new Error("Smart object not found in style: " + STYLE_NAME);

  doc.activeLayer = smart;
  executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);

  var sub = app.activeDocument;
  var textLayer = findLayerRecursive(sub, "Madafaka");
  if (!textLayer) textLayer = findFirstTextLayer(sub);
  if (!textLayer) throw new Error("Text layer not found inside smart object");

  if (textLayer.textItem.font !== FONT_PS) {{
    textLayer.textItem.font = FONT_PS;
  }}
  textLayer.textItem.contents = RENDER_NAME;
  sub.save();
  sub.close(SaveOptions.SAVECHANGES);
  app.activeDocument = doc;

  var dup = doc.duplicate();
  dup.trim(TrimType.TRANSPARENT, true, true, true, true);
  exportPng24(dup, OUT_TRANSPARENT);
  addBlackBackgroundAtBottom(dup);
  exportPng24(dup, OUT_BLACK);
  dup.close(SaveOptions.DONOTSAVECHANGES);

  doc.activeHistoryState = base;
  doc.close(SaveOptions.DONOTSAVECHANGES);

  "OK|" + OUT_TRANSPARENT + "|" + OUT_BLACK;
}} catch (e) {{
  try {{
    if (app.documents.length > 0) app.activeDocument.close(SaveOptions.DONOTSAVECHANGES);
  }} catch (ignore) {{}}
  "ERR|" + e;
}}
"""


def run_jsx(jsx_content: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".jsx",
        prefix="ui_render_",
        delete=False,
        encoding="utf-8",
    ) as tf:
        tf.write(jsx_content)
        jsx_path = Path(tf.name)

    apple = f"""
with timeout of 3600 seconds
  tell application "{PHOTOSHOP_APP_NAME}"
    activate
    do javascript file (POSIX file "{jsx_path.as_posix()}")
  end tell
end timeout
"""
    proc = subprocess.run(
        ["osascript", "-"],
        input=apple,
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        jsx_path.unlink(missing_ok=True)
    except OSError:
        pass

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"AppleScript failed: {err}")
    return (proc.stdout or "").strip()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PS Text Renderer")
        self.geometry("980x760")

        self.name_var = tk.StringVar(value="KEREM")
        self.style_var = tk.StringVar(value="PEMBE")
        self.status_var = tk.StringVar(value="Hazir")
        self.path_var = tk.StringVar(value="")
        self.photo: tk.PhotoImage | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        controls = ttk.Frame(main)
        controls.pack(fill=tk.X)

        ttk.Label(controls, text="Text").grid(row=0, column=0, sticky="w")
        name_entry = ttk.Entry(controls, textvariable=self.name_var, width=28)
        name_entry.grid(row=0, column=1, padx=8, sticky="w")

        ttk.Label(controls, text="Renk").grid(row=0, column=2, sticky="w")
        style_box = ttk.Combobox(
            controls,
            textvariable=self.style_var,
            values=STYLES,
            width=12,
            state="readonly",
        )
        style_box.grid(row=0, column=3, padx=8, sticky="w")

        self.render_btn = ttk.Button(controls, text="Render", command=self.on_render)
        self.render_btn.grid(row=0, column=4, padx=8, sticky="w")

        ttk.Label(main, textvariable=self.status_var).pack(anchor="w", pady=(10, 4))
        ttk.Label(main, textvariable=self.path_var, foreground="#666").pack(anchor="w", pady=(0, 8))

        self.preview = ttk.Label(main, text="Onizleme burada gorunecek")
        self.preview.pack(fill=tk.BOTH, expand=True)

    def on_render(self) -> None:
        text = self.name_var.get().strip()
        style = self.style_var.get().strip()

        if not text:
            messagebox.showerror("Hata", "Text bos olamaz.")
            return
        if style not in STYLES:
            messagebox.showerror("Hata", "Gecersiz renk secimi.")
            return
        if not PSD_PATH.exists():
            messagebox.showerror("Hata", f"PSD bulunamadi:\n{PSD_PATH}")
            return

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        safe = sanitize_filename(text)
        out_trans = OUTPUT_DIR / f"{safe}_{style}_transparent.png"
        out_black = OUTPUT_DIR / f"{safe}_{style}_black_preview.png"

        self.render_btn.configure(state=tk.DISABLED)
        self.status_var.set("Render basladi...")
        self.path_var.set("")

        def worker() -> None:
            try:
                jsx = build_jsx(text, style, out_trans, out_black)
                result = run_jsx(jsx)
                if not result.startswith("OK|"):
                    raise RuntimeError(result)
                self.after(0, lambda: self._on_success(out_black))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: self._on_error(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_success(self, image_path: Path) -> None:
        self.status_var.set("Render tamamlandi.")
        self.path_var.set(str(image_path))
        self.render_btn.configure(state=tk.NORMAL)
        self._show_image(image_path)

    def _on_error(self, err: str) -> None:
        self.status_var.set("Hata")
        self.path_var.set(err)
        self.render_btn.configure(state=tk.NORMAL)
        messagebox.showerror("Render Hatasi", err)

    def _show_image(self, image_path: Path) -> None:
        img = tk.PhotoImage(file=str(image_path))
        max_w, max_h = 900, 560
        x_factor = max(1, int((img.width() + max_w - 1) / max_w))
        y_factor = max(1, int((img.height() + max_h - 1) / max_h))
        factor = max(x_factor, y_factor)
        if factor > 1:
            img = img.subsample(factor, factor)
        self.photo = img
        self.preview.configure(image=self.photo, text="")


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
