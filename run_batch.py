#!/usr/bin/env python3
"""
Photoshop batch PNG exporter (macOS first).

Usage examples:
  python3 run_batch.py --layer "NAME_TEXT"
  python3 run_batch.py --layer "NAME_TEXT" --names "KEREM,MERT,ZEYNEP"
  python3 run_batch.py --layer "NAME_TEXT" --names-file names.txt
"""

from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_NAMES = [
    "KEREM",
    "MERT",
    "AYSE",
    "ZEYNEP",
    "ALI",
    "ELIF",
    "DENIZ",
    "ARDA",
    "SU",
    "MELIS",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render names from a PSD text layer and export PNG files via Photoshop."
    )
    parser.add_argument(
        "--psd",
        default="data/Bootleg_STARTUP_2026.psd",
        help="Path to PSD template.",
    )
    parser.add_argument(
        "--layer",
        required=True,
        help="Target text layer name (exact). Example: NAME_TEXT",
    )
    parser.add_argument(
        "--output",
        default="output",
        help="Output folder for PNG files and logs.",
    )
    parser.add_argument(
        "--names",
        help='Comma-separated list. Example: "KEREM,MERT,ZEYNEP"',
    )
    parser.add_argument(
        "--names-file",
        help="TXT file with one name per line.",
    )
    parser.add_argument(
        "--keep-jsx",
        action="store_true",
        help="Keep generated temp JSX script for debugging.",
    )
    return parser.parse_args()


def load_names(args: argparse.Namespace) -> list[str]:
    if args.names and args.names_file:
        raise ValueError("Use either --names or --names-file, not both.")
    if args.names:
        names = [n.strip() for n in args.names.split(",") if n.strip()]
        if not names:
            raise ValueError("--names is empty after parsing.")
        return names
    if args.names_file:
        path = Path(args.names_file).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Names file not found: {path}")
        names = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        names = [n for n in names if n]
        if not names:
            raise ValueError(f"Names file has no valid rows: {path}")
        return names
    return DEFAULT_NAMES


def discover_photoshop_app_name() -> str:
    apps = sorted(glob.glob("/Applications/Adobe Photoshop*.app"), reverse=True)
    if not apps:
        raise FileNotFoundError(
            "Photoshop app not found in /Applications. Install Photoshop first."
        )
    # Example path -> /Applications/Adobe Photoshop 2026.app
    return Path(apps[0]).stem


def build_jsx_script(
    psd_path: Path,
    output_dir: Path,
    layer_name: str,
    names: list[str],
    run_log: Path,
) -> str:
    psd = psd_path.as_posix()
    out = output_dir.as_posix()
    log = run_log.as_posix()
    names_js = json.dumps(names, ensure_ascii=False)
    layer_js = json.dumps(layer_name, ensure_ascii=False)

    return f"""#target photoshop
app.displayDialogs = DialogModes.NO;

var PSD_PATH = {json.dumps(psd)};
var OUTPUT_DIR = {json.dumps(out)};
var TARGET_LAYER_NAME = {layer_js};
var NAMES = {names_js};
var LOG_PATH = {json.dumps(log)};

function logLine(msg) {{
  var f = new File(LOG_PATH);
  if (f.open("a")) {{
    f.writeln(msg);
    f.close();
  }}
}}

function sanitizeFilename(name) {{
  var s = name.replace(/[^a-zA-Z0-9_-]/g, "_");
  s = s.replace(/_+/g, "_");
  s = s.replace(/^_+|_+$/g, "");
  if (s.length === 0) s = "untitled";
  return s;
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

function ensureUniqueFile(folder, baseName) {{
  var fileRef = new File(folder.fsName + "/" + baseName + ".png");
  if (!fileRef.exists) return fileRef;
  var idx = 2;
  while (true) {{
    fileRef = new File(folder.fsName + "/" + baseName + "_" + idx + ".png");
    if (!fileRef.exists) return fileRef;
    idx++;
  }}
}}

function exportPng24(documentRef, fileRef) {{
  var opts = new ExportOptionsSaveForWeb();
  opts.format = SaveDocumentType.PNG;
  opts.PNG8 = false;
  opts.transparency = true;
  opts.interlaced = false;
  documentRef.exportDocument(fileRef, ExportType.SAVEFORWEB, opts);
}}

var summary = {{
  total: NAMES.length,
  success: 0,
  fail: 0
}};

try {{
  logLine("START");
  var psdFile = new File(PSD_PATH);
  if (!psdFile.exists) throw new Error("PSD not found: " + PSD_PATH);
  var outFolder = new Folder(OUTPUT_DIR);
  if (!outFolder.exists) outFolder.create();

  var doc = app.open(psdFile);
  var targetLayer = findLayerRecursive(doc, TARGET_LAYER_NAME);
  if (!targetLayer) throw new Error("Layer not found: " + TARGET_LAYER_NAME);
  if (targetLayer.kind !== LayerKind.TEXT) throw new Error("Layer is not text: " + TARGET_LAYER_NAME);

  var baseState = doc.activeHistoryState;
  for (var n = 0; n < NAMES.length; n++) {{
    var currentName = NAMES[n];
    try {{
      doc.activeHistoryState = baseState;
      targetLayer.textItem.contents = currentName;
      var safe = sanitizeFilename(currentName);
      var outFile = ensureUniqueFile(outFolder, safe);
      exportPng24(doc, outFile);
      summary.success++;
      logLine("OK  : " + currentName + " -> " + outFile.fsName);
    }} catch (innerErr) {{
      summary.fail++;
      logLine("FAIL: " + currentName + " -> " + innerErr);
    }}
  }}

  doc.activeHistoryState = baseState;
  doc.close(SaveOptions.DONOTSAVECHANGES);
  logLine("END success=" + summary.success + " fail=" + summary.fail);
  "SUCCESS|" + summary.success + "|" + summary.fail;
}} catch (err) {{
  logLine("FATAL: " + err);
  "FATAL|" + err;
}}
"""


def run_jsx_with_photoshop(app_name: str, jsx_path: Path) -> subprocess.CompletedProcess[str]:
    applescript = f'''
set jsxFile to POSIX file "{jsx_path.as_posix()}"
tell application "{app_name}"
  activate
  do javascript file jsxFile
end tell
'''
    return subprocess.run(
        ["osascript", "-"],
        input=applescript,
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> int:
    try:
        args = parse_args()
        names = load_names(args)

        psd_path = Path(args.psd).expanduser().resolve()
        if not psd_path.exists():
            print(f"[ERROR] PSD not found: {psd_path}", file=sys.stderr)
            return 2

        output_dir = Path(args.output).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        run_log = output_dir / "run.log"
        run_log.write_text("", encoding="utf-8")

        app_name = discover_photoshop_app_name()
        jsx_content = build_jsx_script(
            psd_path=psd_path,
            output_dir=output_dir,
            layer_name=args.layer,
            names=names,
            run_log=run_log,
        )

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".jsx",
            prefix="ps_batch_",
            delete=False,
            encoding="utf-8",
        ) as tf:
            tf.write(jsx_content)
            jsx_path = Path(tf.name)

        print(f"[INFO] Photoshop app: {app_name}")
        print(f"[INFO] PSD: {psd_path}")
        print(f"[INFO] Layer: {args.layer}")
        print(f"[INFO] Names: {len(names)}")
        print(f"[INFO] Output: {output_dir}")
        print(f"[INFO] Log: {run_log}")

        proc = run_jsx_with_photoshop(app_name=app_name, jsx_path=jsx_path)

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if proc.returncode != 0:
            print("[ERROR] Photoshop script execution failed.", file=sys.stderr)
            if stderr:
                print(stderr, file=sys.stderr)
            else:
                print(stdout, file=sys.stderr)
            return 3

        print("[INFO] Photoshop response:")
        print(stdout if stdout else "(empty)")

        if not args.keep_jsx:
            try:
                jsx_path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            print(f"[INFO] Kept JSX: {jsx_path}")

        if stdout.startswith("FATAL|"):
            return 4

        print("[DONE] Batch finished. Check output folder and run.log.")
        return 0
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
