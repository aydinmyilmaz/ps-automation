#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from app_paths import bundled_names_file
from ps_single_renderer import STYLE_CHOICES, run_jsx


SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
RUNNER = SCRIPTS_DIR / "onecall_unattended_batch.py"
NAMES_FILE = bundled_names_file()
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "batch_runs" / "desktop"
HOST = "127.0.0.1"
PORT = 8787

STATE_LOCK = threading.Lock()
STATE: dict[str, object] = {
    "proc": None,
    "cmd": None,
    "output_root": str(DEFAULT_OUTPUT),
}

TEST_MODE_OPTIONS = [
    ("full", "Full (3000, letters filter)"),
    ("test20_1", "Test20 - single"),
    ("test20_5", "Test20 - first 5"),
    ("test20_10", "Test20 - first 10"),
    ("test20_20", "Test20 - all 20"),
]


HTML_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PSD Batch Desktop App</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 0; background:#f4f6f9; color:#111; }
    .wrap { max-width: 1080px; margin: 24px auto; background:#fff; border-radius:14px; box-shadow:0 8px 24px rgba(0,0,0,.08); padding:18px; }
    .row { display:flex; gap:10px; margin-bottom:10px; flex-wrap:wrap; }
    label { display:flex; flex-direction:column; gap:4px; font-size:13px; min-width:220px; flex:1; }
    input, select, button, textarea { font-size:14px; padding:8px 10px; border:1px solid #ccd3de; border-radius:8px; }
    .styles { display:grid; grid-template-columns:repeat(4,minmax(140px,1fr)); gap:6px; padding:8px; border:1px solid #e1e6ef; border-radius:8px; }
    .actions { display:flex; gap:10px; margin-top:10px; }
    button { background:#0b5fff; color:#fff; border:none; cursor:pointer; min-width:120px; }
    button.secondary { background:#6b7280; }
    .status { margin-top:10px; font-weight:600; }
    textarea { width:100%; height:330px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background:#0a0f18; color:#d7e1f7; }
    @media (max-width: 900px) { .styles { grid-template-columns:repeat(2,minmax(130px,1fr)); } }
  </style>
</head>
<body>
  <div class="wrap">
    <h2>PSD Batch Desktop App</h2>
    <p>Local render app. Starts/resumes from output folder automatically.</p>
    <div class="row">
      <label>PSD Path
        <input id="psd" placeholder="/path/to/template.psd" />
      </label>
      <label>Output Folder
        <input id="output" value="__DEFAULT_OUTPUT__" />
      </label>
    </div>
    <div class="row">
      <button id="btnScan" class="secondary">Scan PSD Colors</button>
      <button id="btnSelectAll" class="secondary">Select All</button>
      <button id="btnUnselectAll" class="secondary">Unselect All</button>
    </div>
    <div class="row">
      <label>Mode
        <select id="mode">
          <option value="full">Full (3000, letters filter)</option>
          <option value="test20_1">Test20 - single</option>
          <option value="test20_5">Test20 - first 5</option>
          <option value="test20_10">Test20 - first 10</option>
          <option value="test20_20">Test20 - all 20</option>
        </select>
      </label>
      <label>Letters (ABC or A,B,C or all)
        <input id="letters" value="ABC" />
      </label>
      <label>Chunk Size
        <input id="chunk" type="number" value="5" />
      </label>
      <label>Retries
        <input id="retries" type="number" value="5" />
      </label>
      <label>Timeout (sec)
        <input id="timeout" type="number" value="300" />
      </label>
      <label>Restart Every Chunks
        <input id="restart" type="number" value="0" />
      </label>
    </div>
    <div class="row">
      <label>Photoshop Exec Path (optional, Windows)
        <input id="ps_exec" placeholder="C:\\Program Files\\Adobe\\...\\Photoshop.exe" />
      </label>
    </div>
    <div class="row">
      <div style="width:100%">
        <div style="margin-bottom:6px;font-size:13px;">Styles</div>
        <div class="styles" id="styles"></div>
      </div>
    </div>
    <div class="actions">
      <button id="btnStart">Start / Resume</button>
      <button id="btnStop" class="secondary">Stop</button>
      <button id="btnRefresh" class="secondary">Refresh</button>
    </div>
    <div id="msg" style="margin-top:8px; color:#0f5132; font-weight:600;"></div>
    <div class="status" id="status">Status: idle</div>
    <textarea id="log" readonly></textarea>
  </div>
  <script>
    let STYLES = __STYLES_JSON__;
    const stylesRoot = document.getElementById("styles");
    const modeEl = document.getElementById("mode");
    const lettersEl = document.getElementById("letters");

    function renderStyles() {
      stylesRoot.innerHTML = "";
      STYLES.forEach(s => {
      const wrap = document.createElement("label");
      wrap.style.display = "flex";
      wrap.style.alignItems = "center";
      wrap.style.gap = "8px";
      wrap.style.flexDirection = "row";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = false;
      cb.value = s;
      cb.className = "style-cb";
      const sp = document.createElement("span");
      sp.textContent = s;
      wrap.appendChild(cb);
      wrap.appendChild(sp);
      stylesRoot.appendChild(wrap);
      });
    }

    function showMsg(msg, isError=false) {
      const el = document.getElementById("msg");
      el.textContent = msg || "";
      el.style.color = isError ? "#b42318" : "#0f5132";
    }

    function selectAllStyles() {
      document.querySelectorAll(".style-cb").forEach(cb => cb.checked = true);
    }
    function unselectAllStyles() {
      document.querySelectorAll(".style-cb").forEach(cb => cb.checked = false);
    }

    function selectedStyles() {
      return [...document.querySelectorAll(".style-cb:checked")].map(x => x.value);
    }

    async function scanPsdStyles() {
      const psd = document.getElementById("psd").value.trim();
      if (!psd) {
        alert("Enter PSD path first.");
        return;
      }
      const res = await fetch("/api/inspect-psd", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({ psd })
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        showMsg(data.error || "scan failed", true);
        return;
      }
      STYLES = data.styles || [];
      renderStyles();
      selectAllStyles();
      showMsg(`Loaded ${STYLES.length} styles from PSD.`);
    }

    async function startRun() {
      const mode = modeEl.value;
      const body = {
        psd: document.getElementById("psd").value.trim(),
        output: document.getElementById("output").value.trim(),
        mode: mode,
        letters: mode === "full" ? (lettersEl.value.trim() || "all") : "all",
        chunk: Number(document.getElementById("chunk").value || "5"),
        retries: Number(document.getElementById("retries").value || "5"),
        timeout: Number(document.getElementById("timeout").value || "300"),
        restart: Number(document.getElementById("restart").value || "0"),
        ps_exec: document.getElementById("ps_exec").value.trim(),
        styles: selectedStyles()
      };
      const res = await fetch("/api/start", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        showMsg(data.error || "start failed", true);
        return;
      }
      showMsg(`Started. PID ${data.pid}`);
      refreshStatus();
    }

    async function stopRun() {
      const res = await fetch("/api/stop", {method:"POST"});
      const data = await res.json();
      if (!res.ok || !data.ok) {
        showMsg(data.error || "stop failed", true);
        return;
      }
      showMsg("Stop signal sent.");
      refreshStatus();
    }

    async function refreshStatus() {
      const res = await fetch("/api/status");
      const data = await res.json();
      const s = data.running ? `running (pid ${data.pid})` : "idle";
      document.getElementById("status").textContent = "Status: " + s;
      document.getElementById("log").value = data.log || "";
      if (data.progress) {
        document.getElementById("status").textContent += ` | done ${data.progress.done}/${data.progress.total}`;
      }
    }

    modeEl.addEventListener("change", () => {
      lettersEl.disabled = modeEl.value !== "full";
    });
    document.getElementById("btnScan").addEventListener("click", scanPsdStyles);
    document.getElementById("btnSelectAll").addEventListener("click", selectAllStyles);
    document.getElementById("btnUnselectAll").addEventListener("click", unselectAllStyles);
    document.getElementById("btnStart").addEventListener("click", startRun);
    document.getElementById("btnStop").addEventListener("click", stopRun);
    document.getElementById("btnRefresh").addEventListener("click", refreshStatus);

    renderStyles();
    modeEl.dispatchEvent(new Event("change"));
    setInterval(refreshStatus, 3000);
    refreshStatus();
  </script>
</body>
</html>
"""


def _proc() -> subprocess.Popen[str] | None:
    p = STATE.get("proc")
    return p if isinstance(p, subprocess.Popen) else None


def _tail(path: Path, limit: int = 30000) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()
    if len(data) > limit:
        data = data[-limit:]
    return data.decode("utf-8", errors="replace")


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
    if not styles:
        styles = list(STYLE_CHOICES)
    return styles


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            html_str = HTML_PAGE.replace("__STYLES_JSON__", json.dumps(list(STYLE_CHOICES))).replace(
                "__DEFAULT_OUTPUT__", str(DEFAULT_OUTPUT)
            )
            html = html_str.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        if parsed.path == "/api/status":
            with STATE_LOCK:
                p = _proc()
                running = bool(p and p.poll() is None)
                output_root = Path(str(STATE.get("output_root", DEFAULT_OUTPUT)))
            run_log = output_root / "run.log"
            progress_file = output_root / "progress.json"
            progress = None
            if progress_file.exists():
                try:
                    progress = json.loads(progress_file.read_text(encoding="utf-8"))
                except Exception:
                    progress = None
            payload = {
                "ok": True,
                "running": running,
                "pid": p.pid if running and p else None,
                "output_root": str(output_root),
                "progress": progress,
                "log": _tail(run_log),
            }
            self._json(payload)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/start":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            data = json.loads(body or "{}")
            self._start(data)
            return
        if parsed.path == "/api/inspect-psd":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            data = json.loads(body or "{}")
            self._inspect_psd(data)
            return
        if parsed.path == "/api/stop":
            with STATE_LOCK:
                p = _proc()
                if p and p.poll() is None:
                    try:
                        p.terminate()
                        p.wait(timeout=4)
                    except Exception:
                        try:
                            p.kill()
                        except Exception:
                            pass
                    # Ensure runner helper processes are not left behind.
                    try:
                        if sys.platform == "darwin":
                            subprocess.run(
                                ["pkill", "-f", "onecall_unattended_batch.py"],
                                check=False,
                                capture_output=True,
                                text=True,
                            )
                            subprocess.run(
                                ["pkill", "-f", "osascript -"],
                                check=False,
                                capture_output=True,
                                text=True,
                            )
                    except Exception:
                        pass
                STATE["proc"] = None
            self._json({"ok": True, "stopped": True})
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def _inspect_psd(self, data: dict) -> None:
        psd = Path(str(data.get("psd", "")).strip()).expanduser()
        if not psd.exists():
            self._json({"ok": False, "error": "Invalid PSD path"}, status=400)
            return
        try:
            styles = inspect_psd_styles(psd.resolve())
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": str(exc)}, status=500)
            return
        self._json({"ok": True, "styles": styles})

    def _start(self, data: dict) -> None:
        psd = Path(str(data.get("psd", "")).strip()).expanduser()
        if not psd.exists():
            self._json({"ok": False, "error": "Invalid PSD path"}, status=400)
            return

        styles = data.get("styles") or []
        if not isinstance(styles, list) or not styles:
            self._json({"ok": False, "error": "Select at least one style"}, status=400)
            return
        invalid = [s for s in styles if s not in STYLE_CHOICES]
        if invalid:
            self._json({"ok": False, "error": f"Invalid styles: {invalid}"}, status=400)
            return

        output = Path(str(data.get("output", "")).strip() or str(DEFAULT_OUTPUT)).expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        run_log = output / "run.log"
        mode = str(data.get("mode", "full")).strip().lower()
        if mode not in {"full", "test20_1", "test20_5", "test20_10", "test20_20"}:
            self._json({"ok": False, "error": "Invalid mode"}, status=400)
            return

        cmd = [
            sys.executable,
            str(RUNNER),
            "--psd",
            str(psd.resolve()),
            "--names-file",
            str(NAMES_FILE.resolve()),
            "--styles",
            ",".join(styles),
            "--chunk-size",
            str(int(data.get("chunk", 5))),
            "--max-retries",
            str(int(data.get("retries", 5))),
            "--chunk-timeout",
            str(int(data.get("timeout", 300))),
            "--restart-every-chunks",
            str(int(data.get("restart", 0))),
            "--output-root",
            str(output),
            "--supervisor",
        ]
        if mode == "full":
            cmd.extend(
                [
                    "--name-source",
                    "full",
                    "--letters",
                    str(data.get("letters", "all")).strip() or "all",
                ]
            )
        else:
            count = int(mode.split("_")[1])
            cmd.extend(
                [
                    "--name-source",
                    "test20",
                    "--test-count",
                    str(count),
                    "--letters",
                    "all",
                ]
            )
        ps_exec = str(data.get("ps_exec", "")).strip()
        if ps_exec:
            cmd.extend(["--photoshop-exec", ps_exec])

        with STATE_LOCK:
            p = _proc()
            if p and p.poll() is None:
                self._json({"ok": False, "error": "A run is already in progress."}, status=400)
                return
            log_file = run_log.open("a", encoding="utf-8")
            log_file.write("[APP] START " + " ".join(cmd) + "\n")
            log_file.flush()
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
            STATE["proc"] = proc
            STATE["cmd"] = cmd
            STATE["output_root"] = str(output)
        self._json({"ok": True, "pid": proc.pid, "output_root": str(output)})

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def main() -> None:
    DEFAULT_OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"Desktop web app running: {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
