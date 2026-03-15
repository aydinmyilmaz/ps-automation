#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from PIL import Image

from app_paths import SOURCE_PROJECT_ROOT, bundled_default_psd, default_web_output_dir

PROJECT_ROOT = SOURCE_PROJECT_ROOT
DESIGN_NAME = "bootleg_2026"
PSD_PATH = bundled_default_psd()
OUTPUT_DIR = default_web_output_dir()
PHOTOSHOP_APP = "Adobe Photoshop 2026"
STYLE_CHOICES = (
    "Yellow",
    "Turkuaz",
    "Rose",
    "Red",
    "Purple",
    "Pink",
    "Patina Blue",
    "Green",
    "Gray",
    "Gold",
    "Green Dark",
    "Brown Light",
    "Brown",
    "Blue Dark",
    "Blue",
    "Black",
)
FONT_POSTSCRIPT_NAME = "ClarendonBT-Black"
KEEP_PSD_OPEN = True
FAST_EXPORT_NO_TRIM = False
RESTORE_HISTORY_STATE = False


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "name"


def crop_png_to_alpha_bounds(path: Path) -> bool:
    with Image.open(path) as img:
        rgba = img.convert("RGBA")
        alpha_bbox = rgba.getchannel("A").getbbox()
        if not alpha_bbox:
            return False
        if alpha_bbox == (0, 0, rgba.width, rgba.height):
            return False
        cropped = rgba.crop(alpha_bbox)
        cropped.save(path)
    return True


def build_jsx(
    psd_path: Path,
    output_png: Path,
    text: str,
    style: str,
    keep_psd_open: bool = KEEP_PSD_OPEN,
    fast_export_no_trim: bool = FAST_EXPORT_NO_TRIM,
    restore_history_state: bool = RESTORE_HISTORY_STATE,
) -> str:
    style_groups_json = json.dumps(list(STYLE_CHOICES))
    return f"""#target photoshop
app.displayDialogs = DialogModes.NO;

var PSD_PATH = {json.dumps(psd_path.as_posix())};
var OUTPUT_PNG = {json.dumps(output_png.as_posix())};
var STYLE_NAME = {json.dumps(style)};
var RENDER_NAME = {json.dumps(text)};
var FONT_PS = {json.dumps(FONT_POSTSCRIPT_NAME)};
var STYLE_GROUPS = {style_groups_json};
var MIN_TRACKING = 0;
var KEEP_PSD_OPEN = {str(keep_psd_open).lower()};
var FAST_EXPORT_NO_TRIM = {str(fast_export_no_trim).lower()};
var RESTORE_HISTORY_STATE = {str(restore_history_state).lower()};

function normalizeName(name) {{
  if (!name) return "";
  return String(name).replace(/\\s+/g, " ").replace(/^\\s+|\\s+$/g, "").toLowerCase();
}}

function namesEqual(a, b) {{
  return normalizeName(a) === normalizeName(b);
}}

function findTopLevelGroup(doc, name) {{
  for (var i = 0; i < doc.layerSets.length; i++) {{
    if (namesEqual(doc.layerSets[i].name, name)) return doc.layerSets[i];
  }}
  return null;
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

function findGroupRecursive(container, name) {{
  for (var i = 0; i < container.layerSets.length; i++) {{
    var g = container.layerSets[i];
    if (namesEqual(g.name, name)) return g;
    var nested = findGroupRecursive(g, name);
    if (nested) return nested;
  }}
  return null;
}}

function collectGroupNames(container, out, depth) {{
  for (var i = 0; i < container.layerSets.length; i++) {{
    var g = container.layerSets[i];
    out.push((new Array(depth + 1).join("  ")) + g.name);
    collectGroupNames(g, out, depth + 1);
  }}
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
      var shouldShow = (STYLE_GROUPS[i] === styleName);
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

  var openedNow = false;
  var doc = findOpenDocumentByPath(PSD_PATH);
  if (!doc) {{
    doc = app.open(psdFile);
    openedNow = true;
  }}
  app.activeDocument = doc;
  var base = doc.activeHistoryState;

  setOnlyStyleVisible(doc, STYLE_NAME);
  var styleGroup = findGroupRecursive(doc, STYLE_NAME);
  if (!styleGroup) {{
    var available = [];
    collectGroupNames(doc, available, 0);
    throw new Error("Style group not found: " + STYLE_NAME + " | Available groups: " + available.join(" | "));
  }}

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

  if (FAST_EXPORT_NO_TRIM) {{
    exportPng24(doc, OUTPUT_PNG);
  }} else {{
    var dup = doc.duplicate();
    dup.trim(TrimType.TRANSPARENT, true, true, true, true);
    exportPng24(dup, OUTPUT_PNG);
    dup.close(SaveOptions.DONOTSAVECHANGES);
  }}

  if (RESTORE_HISTORY_STATE) {{
    doc.activeHistoryState = base;
  }}
  if (!KEEP_PSD_OPEN && openedNow) {{
    doc.close(SaveOptions.DONOTSAVECHANGES);
  }}
  "OK|" + OUTPUT_PNG;
}} catch (e) {{
  try {{
    // Keep documents open in warm-session mode for next runs.
    if (!KEEP_PSD_OPEN && app.documents.length > 0) app.activeDocument.close(SaveOptions.DONOTSAVECHANGES);
  }} catch (ignore) {{}}
  "ERR|" + e;
}}
"""


def _run_jsx_macos(jsx_path: Path, timeout_seconds: int) -> str:
    applescript = f"""
with timeout of {int(timeout_seconds)} seconds
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
        timeout=timeout_seconds + 15,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip())
    return (proc.stdout or "").strip()


def _run_jsx_windows(jsx_path: Path, timeout_seconds: int) -> str:
    jsx_win = str(jsx_path)
    escaped = jsx_win.replace("'", "''")
    # Photoshop COM usually auto-launches Photoshop if not running.
    ps_script = (
        "$ErrorActionPreference='Stop';"
        f"$jsx='{escaped}';"
        "$app=New-Object -ComObject Photoshop.Application;"
        "$res=$app.DoJavaScriptFile($jsx);"
        "if($null -ne $res){Write-Output $res}"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds + 30,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip())
    return (proc.stdout or "").strip()


def run_jsx(jsx: str, timeout_seconds: int = 3600) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".jsx",
        prefix="single_render_",
        delete=False,
        encoding="utf-8",
    ) as tf:
        tf.write(jsx)
        jsx_path = Path(tf.name)

    try:
        if sys.platform == "darwin":
            result = _run_jsx_macos(jsx_path, timeout_seconds)
        elif sys.platform.startswith("win"):
            result = _run_jsx_windows(jsx_path, timeout_seconds)
        else:
            raise RuntimeError(f"Unsupported platform for Photoshop JSX automation: {sys.platform}")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Photoshop JSX timed out after {timeout_seconds}s") from exc
    try:
        jsx_path.unlink(missing_ok=True)
    except OSError:
        pass

    return result


def render_name(
    text: str,
    style: str,
    keep_psd_open: bool = KEEP_PSD_OPEN,
    fast_export_no_trim: bool = FAST_EXPORT_NO_TRIM,
    restore_history_state: bool = RESTORE_HISTORY_STATE,
) -> Path:
    if style not in STYLE_CHOICES:
        raise ValueError("Invalid style")
    if not text.strip():
        raise ValueError("Text cannot be empty")
    if not PSD_PATH.exists():
        raise FileNotFoundError(f"PSD not found: {PSD_PATH}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = sanitize_filename(text)
    safe_style = sanitize_filename(style)
    out = OUTPUT_DIR / f"{safe}_{safe_style}_{int(time.time())}.png"
    result = run_jsx(
        build_jsx(
            PSD_PATH,
            out,
            text.strip(),
            style,
            keep_psd_open=keep_psd_open,
            fast_export_no_trim=fast_export_no_trim,
            restore_history_state=restore_history_state,
        )
    )
    if not result.startswith("OK|"):
        raise RuntimeError(result)
    crop_png_to_alpha_bounds(out)
    return out
