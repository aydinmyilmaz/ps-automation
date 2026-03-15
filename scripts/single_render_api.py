#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# Ensure sibling modules are importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ps_single_renderer import OUTPUT_DIR, STYLE_CHOICES, render_name


HOST = "127.0.0.1"
PORT = 8000


class ApiHandler(BaseHTTPRequestHandler):
    @staticmethod
    def _as_bool(value: object, default: bool = True) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json({"ok": True, "styles": list(STYLE_CHOICES)})
            return
        if parsed.path.startswith("/files/"):
            self._serve_file(parsed.path[len("/files/"):])
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/render":
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            data = json.loads(body or "{}")
            text = (data.get("text") or "").strip()
            style = (data.get("style") or "").strip()
            uppercase = self._as_bool(data.get("uppercase"), default=True)
            render_text = text.upper() if uppercase else text

            out = render_name(text=render_text, style=style)
            rel = out.relative_to(OUTPUT_DIR).as_posix()
            self._send_json(
                {
                    "ok": True,
                    "text": text,
                    "render_text": render_text,
                    "style": style,
                    "uppercase": uppercase,
                    "file": rel,
                    "image_url": f"/files/{rel}",
                }
            )
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _serve_file(self, rel_path: str) -> None:
        safe_rel = rel_path.strip("/").replace("\\", "/")
        file_path = (OUTPUT_DIR / safe_rel).resolve()
        output_root = OUTPUT_DIR.resolve()
        if output_root not in file_path.parents and file_path != output_root:
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        self.send_response(HTTPStatus.OK)
        self._cors()
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.end_headers()
        self.wfile.write(file_path.read_bytes())

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), ApiHandler)
    print(f"API running at http://{HOST}:{PORT}")
    print('POST /api/render  body: {"text":"KEREM","style":"Yellow"}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
