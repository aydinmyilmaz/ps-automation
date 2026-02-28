#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PSD_PATH = BASE_DIR / "data" / "text_only_2.psd"
OUTPUT_DIR = BASE_DIR / "output" / "web_single"
PHOTOSHOP_APP = "Adobe Photoshop 2026"
STYLE_CHOICES = ("metal", "ALTIN", "MAVI", "PEMBE")
FONT_POSTSCRIPT_NAME = "ClarendonBT-Black"


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "name"


def build_jsx(psd_path: Path, output_png: Path, text: str, style: str) -> str:
    return f"""#target photoshop
app.displayDialogs = DialogModes.NO;

var PSD_PATH = {json.dumps(psd_path.as_posix())};
var OUTPUT_PNG = {json.dumps(output_png.as_posix())};
var STYLE_NAME = {json.dumps(style)};
var RENDER_NAME = {json.dumps(text)};
var FONT_PS = {json.dumps(FONT_POSTSCRIPT_NAME)};
var STYLE_GROUPS = ["metal","ALTIN","MAVI","PEMBE"];
var MIN_TRACKING = 0;

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

function fontExists(postScriptName) {{
  for (var i = 0; i < app.fonts.length; i++) {{
    if (app.fonts[i].postScriptName === postScriptName) return true;
  }}
  return false;
}}

function fitTextToTemplate(textLayer, templateWidth, templateCenterX) {{
  var nameLen = (textLayer.textItem.contents || "").length;
  var safety = 0.96;
  if (nameLen >= 10) safety = 0.94;
  if (nameLen >= 12) safety = 0.93;
  if (nameLen >= 14) safety = 0.92;
  if (nameLen >= 16) safety = 0.90;
  var targetWidth = templateWidth * safety;
  var baseScale = textLayer.textItem.horizontalScale;
  if (!baseScale || baseScale < 1) baseScale = 100;
  var minScale = Math.max(30, baseScale * 0.30);

  var currentBounds = getBoundsPx(textLayer);
  var currentWidth = currentBounds.right - currentBounds.left;
  var hScale = baseScale;
  var guard = 0;
  while (currentWidth > targetWidth && hScale > minScale && guard < 500) {{
    hScale -= 1;
    textLayer.textItem.horizontalScale = hScale;
    currentBounds = getBoundsPx(textLayer);
    currentWidth = currentBounds.right - currentBounds.left;
    guard++;
  }}

  // Last-resort squeeze for extremely long names without touching text size/transform.
  if (currentWidth > targetWidth) {{
    var tracking = textLayer.textItem.tracking;
    if (typeof tracking !== "number") tracking = 0;
    var minTracking = MIN_TRACKING;
    guard = 0;
    while (currentWidth > targetWidth && tracking > minTracking && guard < 200) {{
      tracking -= 5;
      textLayer.textItem.tracking = tracking;
      currentBounds = getBoundsPx(textLayer);
      currentWidth = currentBounds.right - currentBounds.left;
      guard++;
    }}
  }}

  if (currentWidth > targetWidth) {{
    throw new Error("Text too long for template. Please use a shorter name.");
  }}

  var newCenterX = (currentBounds.left + currentBounds.right) / 2.0;
  var deltaX = templateCenterX - newCenterX;
  textLayer.translate(deltaX, 0);
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
  if (!smart) throw new Error("Smart object not found in style group.");

  doc.activeLayer = smart;
  executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);

  var sub = app.activeDocument;
  var textLayer = findLayerRecursive(sub, "Madafaka");
  if (!textLayer) textLayer = findFirstTextLayer(sub);
  if (!textLayer) throw new Error("Text layer not found inside smart object.");

  var templateBounds = getBoundsPx(textLayer);
  var baseTemplateWidth = templateBounds.right - templateBounds.left;
  var canvasWidth = sub.width.as("px");
  var canvasPadding = 120;
  var canvasDrivenWidth = Math.max(0, canvasWidth - (canvasPadding * 2));
  var templateWidth = Math.max(baseTemplateWidth, canvasDrivenWidth);
  var templateCenterX = canvasWidth / 2.0;

  textLayer.textItem.contents = RENDER_NAME;
  if (textLayer.textItem.font !== FONT_PS) {{
    textLayer.textItem.font = FONT_PS;
  }}
  fitTextToTemplate(textLayer, templateWidth, templateCenterX);
  sub.save();
  sub.close(SaveOptions.SAVECHANGES);
  app.activeDocument = doc;

  var dup = doc.duplicate();
  dup.trim(TrimType.TRANSPARENT, true, true, true, true);
  exportPng24(dup, OUTPUT_PNG);
  dup.close(SaveOptions.DONOTSAVECHANGES);

  doc.activeHistoryState = base;
  doc.close(SaveOptions.DONOTSAVECHANGES);
  "OK|" + OUTPUT_PNG;
}} catch (e) {{
  try {{
    if (app.documents.length > 0) app.activeDocument.close(SaveOptions.DONOTSAVECHANGES);
  }} catch (ignore) {{}}
  "ERR|" + e;
}}
"""


def run_jsx(jsx: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".jsx",
        prefix="single_render_",
        delete=False,
        encoding="utf-8",
    ) as tf:
        tf.write(jsx)
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


def render_name(text: str, style: str) -> Path:
    if style not in STYLE_CHOICES:
        raise ValueError("Invalid style")
    if not text.strip():
        raise ValueError("Text cannot be empty")
    if not PSD_PATH.exists():
        raise FileNotFoundError(f"PSD not found: {PSD_PATH}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = sanitize_filename(text)
    out = OUTPUT_DIR / f"{safe}_{style}_{int(time.time())}.png"
    result = run_jsx(build_jsx(PSD_PATH, out, text.strip(), style))
    if not result.startswith("OK|"):
        raise RuntimeError(result)
    return out
