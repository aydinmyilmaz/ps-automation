#!/usr/bin/env python3
"""
Local web UI for Photoshop text rendering.

Run:
  python3 web_ui_app.py

Open:
  http://127.0.0.1:8000
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import tempfile
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
PSD_PATH = BASE_DIR / "data" / "text_only_2.psd"
OUTPUT_DIR = BASE_DIR / "output" / "web_ui"
PHOTOSHOP_APP = "Adobe Photoshop 2026"
STYLE_CHOICES = ("metal", "ALTIN", "MAVI", "PEMBE")
FONT_POSTSCRIPT_NAME = "ClarendonBT-Black"
HOST = "127.0.0.1"
PORT = 8000


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

try {{
  if (!fontExists(FONT_PS)) throw new Error("Font missing: " + FONT_PS);

  var psdFile = new File(PSD_PATH);
  if (!psdFile.exists) throw new Error("PSD not found: " + PSD_PATH);

  var doc = app.open(psdFile);
  var baseState = doc.activeHistoryState;

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
  var templateWidth = templateBounds.right - templateBounds.left;
  var templateCenterX = (templateBounds.left + templateBounds.right) / 2.0;

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

  doc.activeHistoryState = baseState;
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
        mode="w", suffix=".jsx", prefix="web_render_", delete=False, encoding="utf-8"
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
        raise ValueError("Invalid style selection")
    if not text.strip():
        raise ValueError("Text cannot be empty")
    if not PSD_PATH.exists():
        raise FileNotFoundError(f"PSD not found: {PSD_PATH}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = sanitize_filename(text)
    ts = int(time.time())
    out_png = OUTPUT_DIR / f"{safe}_{style}_{ts}.png"
    jsx = build_jsx(PSD_PATH, out_png, text.strip(), style)
    result = run_jsx(jsx)
    if not result.startswith("OK|"):
        raise RuntimeError(result)
    return out_png


def page_html(text: str = "", style: str = "PEMBE", image_url: str = "", error: str = "") -> str:
    options = "".join(
        f'<option value="{s}" {"selected" if s == style else ""}>{s}</option>' for s in STYLE_CHOICES
    )
    preview_block = ""
    if image_url:
        preview_block = f"""
        <div class="preview-wrap">
          <img class="preview" src="{html.escape(image_url)}" alt="render result" />
        </div>
        <p class="actions">
          <a href="{html.escape(image_url)}" download>Download PNG</a>
        </p>
        """
    err_block = f'<p class="error">{html.escape(error)}</p>' if error else ""

    return f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PS Text Renderer</title>
  <style>
    body {{
      margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: linear-gradient(135deg, #f4f6f8, #e9eef5);
      color: #111;
    }}
    .container {{
      max-width: 980px; margin: 30px auto; background: #fff; border-radius: 14px;
      box-shadow: 0 12px 30px rgba(0,0,0,.10); padding: 20px;
    }}
    h1 {{ margin: 0 0 16px; font-size: 24px; }}
    form {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: end; }}
    .field {{ display: flex; flex-direction: column; gap: 6px; }}
    input, select, button {{
      padding: 10px 12px; border: 1px solid #cfd7e3; border-radius: 10px; font-size: 15px;
    }}
    button {{
      background: #0b5fff; color: #fff; border: none; cursor: pointer; min-width: 120px;
    }}
    .hint {{ color: #5d6a7a; margin-top: 10px; font-size: 13px; }}
    .error {{ color: #b42318; font-weight: 600; }}
    .preview-wrap {{
      margin-top: 16px; background: #000; border-radius: 10px; padding: 12px;
      display: flex; justify-content: center; align-items: center; min-height: 280px;
    }}
    .preview {{ max-width: 100%; max-height: 560px; }}
    .actions {{ margin-top: 10px; }}
    .actions a {{ color: #0b5fff; font-weight: 600; text-decoration: none; }}
    @media (max-width: 680px) {{
      .container {{ margin: 10px; padding: 14px; }}
      form {{ flex-direction: column; align-items: stretch; }}
      button {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Photoshop Text Renderer</h1>
    <form method="post" action="/render">
      <div class="field">
        <label for="text">Text</label>
        <input id="text" name="text" type="text" value="{html.escape(text)}" placeholder="Ornek: CHARLIE" required />
      </div>
      <div class="field">
        <label for="style">Renk</label>
        <select id="style" name="style">{options}</select>
      </div>
      <button type="submit">Render</button>
    </form>
    <p class="hint">Sonuc backgroundsuz PNG olarak uretilir. Onizleme siyah arka planda gosterilir.</p>
    {err_block}
    {preview_block}
  </div>
</body>
</html>
"""


class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(page_html())
            return
        if parsed.path.startswith("/files/"):
            self._serve_file(parsed.path[len("/files/") :])
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/render":
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        form = parse_qs(body)
        text = (form.get("text", [""])[0] or "").strip()
        style = (form.get("style", ["PEMBE"])[0] or "PEMBE").strip()

        try:
            image_path = render_name(text=text, style=style)
            rel = image_path.relative_to(OUTPUT_DIR)
            img_url = f"/files/{rel.as_posix()}"
            self._send_html(page_html(text=text, style=style, image_url=img_url))
        except Exception as exc:  # noqa: BLE001
            self._send_html(page_html(text=text, style=style, error=str(exc)), status=500)

    def _serve_file(self, rel_path: str) -> None:
        safe_rel = rel_path.strip("/").replace("\\", "/")
        file_path = (OUTPUT_DIR / safe_rel).resolve()
        if OUTPUT_DIR.resolve() not in file_path.parents and file_path != OUTPUT_DIR.resolve():
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.end_headers()
        self.wfile.write(file_path.read_bytes())

    def _send_html(self, content: str, status: int = 200) -> None:
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), RenderHandler)
    print(f"Server running: http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
