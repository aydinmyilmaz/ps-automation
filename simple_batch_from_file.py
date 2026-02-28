#!/usr/bin/env python3
"""
Simple Photoshop batch renderer that reads names from a text file.

Usage:
  python3 simple_batch_from_file.py
  python3 simple_batch_from_file.py --names-file names.txt --style PEMBE
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PSD_PATH = BASE_DIR / "data" / "text_only_2.psd"
DEFAULT_NAMES_FILE = BASE_DIR / "names.txt"
OUTPUT_DIR = BASE_DIR / "output" / "simple_batch"
PHOTOSHOP_APP = "Adobe Photoshop 2026"
STYLE_CHOICES = ("metal", "ALTIN", "MAVI", "PEMBE")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render names from names.txt via Photoshop.")
    parser.add_argument("--names-file", default=str(DEFAULT_NAMES_FILE), help="One name per line.")
    parser.add_argument("--style", default="PEMBE", choices=STYLE_CHOICES, help="Color style group.")
    return parser.parse_args()


def read_names(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Names file not found: {path}")
    names = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    names = [n for n in names if n]
    if not names:
        raise ValueError(f"Names file is empty: {path}")
    return names


def build_jsx(psd: Path, output_dir: Path, names: list[str], style: str) -> str:
    return f"""#target photoshop
app.displayDialogs = DialogModes.NO;

var PSD_PATH = {json.dumps(psd.as_posix())};
var OUTPUT_DIR = {json.dumps(output_dir.as_posix())};
var NAMES = {json.dumps(names, ensure_ascii=False)};
var STYLE_NAME = {json.dumps(style)};
var STYLE_GROUPS = ["metal","ALTIN","MAVI","PEMBE"];

function sanitizeFilename(name) {{
  var s = name.replace(/[^a-zA-Z0-9_-]/g, "_");
  s = s.replace(/_+/g, "_");
  s = s.replace(/^_+|_+$/g, "");
  return s.length ? s : "name";
}}

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

function getBoundsPx(layer) {{
  var b = layer.bounds;
  return {{
    left: b[0].as("px"),
    top: b[1].as("px"),
    right: b[2].as("px"),
    bottom: b[3].as("px")
  }};
}}

function fitTextToTemplate(textLayer, templateWidth, templateCenterX) {{
  var targetWidth = templateWidth * 0.98;
  var minScale = 65;
  var minSizePt = 6.0;

  var currentBounds = getBoundsPx(textLayer);
  var currentWidth = currentBounds.right - currentBounds.left;

  var hScale = textLayer.textItem.horizontalScale;
  var guard = 0;
  while (currentWidth > targetWidth && hScale > minScale && guard < 300) {{
    hScale -= 1;
    textLayer.textItem.horizontalScale = hScale;
    currentBounds = getBoundsPx(textLayer);
    currentWidth = currentBounds.right - currentBounds.left;
    guard++;
  }}

  var sizePt = textLayer.textItem.size.as("pt");
  guard = 0;
  while (currentWidth > targetWidth && sizePt > minSizePt && guard < 300) {{
    sizePt -= 0.25;
    textLayer.textItem.size = new UnitValue(sizePt, "pt");
    currentBounds = getBoundsPx(textLayer);
    currentWidth = currentBounds.right - currentBounds.left;
    guard++;
  }}

  var newCenterX = (currentBounds.left + currentBounds.right) / 2.0;
  var deltaX = templateCenterX - newCenterX;
  textLayer.translate(deltaX, 0);
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

try {{
  var psdFile = new File(PSD_PATH);
  if (!psdFile.exists) throw new Error("PSD not found: " + PSD_PATH);
  var outFolder = new Folder(OUTPUT_DIR);
  if (!outFolder.exists) outFolder.create();

  var doc = app.open(psdFile);
  var baseState = doc.activeHistoryState;

  var styleGroup = findTopLevelGroup(doc, STYLE_NAME);
  if (!styleGroup) throw new Error("Style group not found: " + STYLE_NAME);

  for (var n = 0; n < NAMES.length; n++) {{
    var currentName = NAMES[n];
    doc.activeHistoryState = baseState;
    setOnlyStyleVisible(doc, STYLE_NAME);

    var currentStyleGroup = findTopLevelGroup(doc, STYLE_NAME);
    var textGroup = findLayerRecursive(currentStyleGroup, "TEXT");
    var scope = textGroup ? textGroup : currentStyleGroup;
    var smart = findLayerRecursive(scope, "CUSTOM copy 2");
    if (!smart) smart = findFirstSmartObject(scope);
    if (!smart) throw new Error("Smart object not found for style: " + STYLE_NAME);

    doc.activeLayer = smart;
    executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);

    var sub = app.activeDocument;
    var textLayer = findLayerRecursive(sub, "Madafaka");
    if (!textLayer) textLayer = findFirstTextLayer(sub);
    if (!textLayer) throw new Error("Text layer not found inside smart object");

    var templateBounds = getBoundsPx(textLayer);
    var templateWidth = templateBounds.right - templateBounds.left;
    var templateCenterX = (templateBounds.left + templateBounds.right) / 2.0;

    textLayer.textItem.contents = currentName;
    fitTextToTemplate(textLayer, templateWidth, templateCenterX);
    sub.save();
    sub.close(SaveOptions.SAVECHANGES);
    app.activeDocument = doc;

    var safe = sanitizeFilename(currentName);
    var dup = doc.duplicate();
    dup.trim(TrimType.TRANSPARENT, true, true, true, true);
    exportPng24(dup, OUTPUT_DIR + "/" + safe + "_" + STYLE_NAME + "_transparent.png");
    dup.close(SaveOptions.DONOTSAVECHANGES);
  }}

  doc.activeHistoryState = baseState;
  doc.close(SaveOptions.DONOTSAVECHANGES);
  "OK|" + NAMES.length;
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
        prefix="simple_batch_",
        delete=False,
        encoding="utf-8",
    ) as tf:
        tf.write(jsx_content)
        jsx_path = Path(tf.name)

    applescript = f"""
with timeout of 3600 seconds
  tell application "{PHOTOSHOP_APP}"
    activate
    do javascript file (POSIX file "{jsx_path.as_posix()}")
  end tell
end timeout
"""
    proc = subprocess.run(
        ["osascript", "-"],
        input=applescript,
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        jsx_path.unlink(missing_ok=True)
    except OSError:
        pass

    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip())
    return (proc.stdout or "").strip()


def main() -> int:
    args = parse_args()
    names_file = Path(args.names_file).expanduser().resolve()
    names = read_names(names_file)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    jsx = build_jsx(PSD_PATH, OUTPUT_DIR, names, args.style)
    result = run_jsx(jsx)

    if result.startswith("ERR|"):
        raise RuntimeError(result)

    print(result)
    print(f"Done. Output folder: {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
