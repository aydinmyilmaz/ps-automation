#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app_paths import bundled_default_psd, default_request_worker_dir
import onecall_unattended_batch as batch_runner
from ps_single_renderer import render_name
from single_supabase_export import (
    archive_custom_render_outputs,
    import_archived_run,
    load_single_save_config,
    postgrest_select,
    postgrest_update,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def as_bool(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def is_scratch_error(exc: Exception) -> bool:
    return "scratch disks are full" in str(exc).lower()


class RequestWorker:
    def __init__(self, poll_interval_seconds: int, status_path: Path, *, psd_path: Path, render_output_dir: Path) -> None:
        self.poll_interval_seconds = max(2, int(poll_interval_seconds))
        self.status_path = status_path.expanduser().resolve()
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.config = load_single_save_config()
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.psd_path = psd_path.expanduser().resolve()
        self.render_output_dir = render_output_dir.expanduser().resolve()
        self.render_output_dir.mkdir(parents=True, exist_ok=True)
        self.processed_count = 0
        self.failed_count = 0
        self.current_request_id = ""
        self.current_request_text = ""
        self.last_message = "starting"
        self.last_poll_at = ""
        self.last_claimed_at = ""
        self.should_stop = False

    def ensure_scratch_headroom(self, reason: str) -> None:
        try:
            batch_runner.assert_scratch_headroom(
                self.render_output_dir,
                self.psd_path,
                batch_runner.MIN_FREE_DISK_GB_DEFAULT,
            )
        except ValueError:
            log(f"scratch headroom low before {reason}; attempting automatic recovery")
            recovery = batch_runner.recover_scratch_headroom(
                output_root=self.render_output_dir,
                psd_path=self.psd_path,
                photoshop_exec=None,
                min_free_disk_gb=batch_runner.MIN_FREE_DISK_GB_DEFAULT,
                reason=reason,
            )
            if recovery.free_gb is not None:
                log(
                    f"scratch recovery result | free={recovery.free_gb:.2f} GiB "
                    f"| path={recovery.path} | restarted={recovery.photoshop_restarted}"
                )

    def run(self) -> int:
        self.write_status(state="idle", last_message="worker started")
        log(
            f"request worker started | worker_id={self.worker_id} "
            f"| poll={self.poll_interval_seconds}s | table={self.config.request_table}"
        )
        while not self.should_stop:
            self.last_poll_at = utc_now_iso()
            self.write_status(state="polling", last_message="checking for pending requests")
            try:
                claimed = self.claim_next_request()
            except Exception as exc:  # noqa: BLE001
                self.failed_count += 1
                self.last_message = f"poll failed: {exc}"
                self.write_status(state="error", last_message=self.last_message)
                log(self.last_message)
                self.sleep_with_heartbeat()
                continue

            if not claimed:
                self.last_message = "idle - no pending requests"
                self.write_status(state="idle", last_message=self.last_message)
                self.sleep_with_heartbeat()
                continue

            self.current_request_id = str(claimed.get("id") or "")
            self.current_request_text = str(claimed.get("request_text") or "").strip()
            self.last_claimed_at = utc_now_iso()
            self.write_status(state="processing", last_message="request claimed")
            try:
                self.process_request(claimed)
            except Exception as exc:  # noqa: BLE001
                self.failed_count += 1
                message = f"request {self.current_request_id} failed: {exc}"
                log(message)
                self.fail_request(self.current_request_id, str(exc))
                self.last_message = message
                self.write_status(state="error", last_message=message)
            finally:
                self.current_request_id = ""
                self.current_request_text = ""

        self.last_message = "worker stopped"
        self.write_status(state="stopped", last_message=self.last_message)
        log(self.last_message)
        return 0

    def sleep_with_heartbeat(self) -> None:
        deadline = time.time() + self.poll_interval_seconds
        while not self.should_stop and time.time() < deadline:
            time.sleep(min(1.0, max(0.1, deadline - time.time())))

    def write_status(self, *, state: str, last_message: str) -> None:
        self.last_message = last_message.strip()
        payload = {
            "ok": True,
            "pid": os.getpid(),
            "worker_id": self.worker_id,
            "state": state,
            "current_request_id": self.current_request_id,
            "current_request_text": self.current_request_text,
            "last_message": self.last_message,
            "last_poll_at": self.last_poll_at,
            "last_claimed_at": self.last_claimed_at,
            "processed_count": self.processed_count,
            "failed_count": self.failed_count,
            "poll_interval_seconds": self.poll_interval_seconds,
            "request_table": self.config.request_table,
            "psd_path": str(self.psd_path),
            "render_output_dir": str(self.render_output_dir),
            "updated_at": utc_now_iso(),
        }
        self.status_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def claim_next_request(self) -> dict[str, object] | None:
        rows = postgrest_select(
            self.config,
            table=self.config.request_table,
            filters={
                "status": "eq.pending",
                "order": "requested_at.asc,id.asc",
                "limit": "1",
            },
            select="id,request_text,style,uppercase,attempt_count,requested_at",
            timeout_seconds=30,
        )
        if not rows:
            return None

        row = rows[0]
        request_id = str(row.get("id") or "").strip()
        if not request_id:
            return None

        attempts = int(row.get("attempt_count") or 0)
        claimed_rows = postgrest_update(
            self.config,
            table=self.config.request_table,
            filters={"id": f"eq.{request_id}", "status": "eq.pending"},
            row={
                "status": "processing",
                "claimed_at": utc_now_iso(),
                "worker_id": self.worker_id,
                "attempt_count": attempts + 1,
                "error_message": None,
            },
            timeout_seconds=30,
        )
        return claimed_rows[0] if claimed_rows else None

    def process_request(self, request_row: dict[str, object]) -> None:
        request_id = str(request_row.get("id") or "").strip()
        raw_text = str(request_row.get("request_text") or "").strip()
        style = str(request_row.get("style") or "").strip()
        uppercase = as_bool(request_row.get("uppercase"), default=True)
        if not request_id:
            raise ValueError("Request row is missing id")
        if not raw_text:
            raise ValueError("Request text is empty")
        if not style:
            raise ValueError("Request style is empty")

        render_text = raw_text.upper() if uppercase else raw_text
        log(f"processing request {request_id} | text={render_text} | style={style}")
        self.ensure_scratch_headroom(f"request {request_id}")
        try:
            output_path = render_name(
                text=render_text,
                style=style,
                psd_path=self.psd_path,
                output_dir=self.render_output_dir,
            )
        except Exception as exc:  # noqa: BLE001
            if not is_scratch_error(exc):
                raise
            log(f"scratch error during request {request_id}; retrying after automatic recovery")
            recovery = batch_runner.recover_scratch_headroom(
                output_root=self.render_output_dir,
                psd_path=self.psd_path,
                photoshop_exec=None,
                min_free_disk_gb=batch_runner.MIN_FREE_DISK_GB_DEFAULT,
                reason=f"request {request_id} render failure",
            )
            if recovery.free_gb is not None:
                log(
                    f"post-failure scratch recovery | free={recovery.free_gb:.2f} GiB "
                    f"| path={recovery.path} | restarted={recovery.photoshop_restarted}"
                )
            output_path = render_name(
                text=render_text,
                style=style,
                psd_path=self.psd_path,
                output_dir=self.render_output_dir,
            )
        archived_run = archive_custom_render_outputs(render_text, [(style, output_path)])
        import_result = import_archived_run(archived_run)
        item = next((entry for entry in import_result.items if entry.style == style), None)
        if not item:
            raise RuntimeError(f"Supabase import result missing style: {style}")
        if item.status not in {"uploaded", "skipped"}:
            raise RuntimeError(item.message or "Supabase import failed")

        update_rows = postgrest_update(
            self.config,
            table=self.config.request_table,
            filters={"id": f"eq.{request_id}"},
            row={
                "status": "done",
                "completed_at": utc_now_iso(),
                "result_image_url": item.image_url or None,
                "result_storage_path": item.storage_path or None,
                "result_cache_key": item.cache_key or None,
                "error_message": None,
            },
            timeout_seconds=30,
        )
        if not update_rows:
            raise RuntimeError(f"Request update returned no rows: {request_id}")

        self.processed_count += 1
        self.last_message = f"request {request_id} done ({item.status})"
        self.write_status(state="idle", last_message=self.last_message)
        log(
            f"completed request {request_id} | import={item.status} "
            f"| image_url={item.image_url or '-'}"
        )

    def fail_request(self, request_id: str, error_message: str) -> None:
        if not request_id:
            return
        try:
            postgrest_update(
                self.config,
                table=self.config.request_table,
                filters={"id": f"eq.{request_id}"},
                row={
                    "status": "failed",
                    "completed_at": utc_now_iso(),
                    "error_message": error_message[:2000],
                },
                timeout_seconds=30,
            )
        except Exception as exc:  # noqa: BLE001
            log(f"failed to update request {request_id} -> failed: {exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Poll Supabase render requests and process them with local Photoshop.")
    parser.add_argument("--poll-interval", type=int, default=10, help="Poll interval in seconds. Default: 10")
    parser.add_argument(
        "--status-file",
        type=Path,
        default=default_request_worker_dir() / "worker_status.json",
        help="Where the worker heartbeat JSON should be written.",
    )
    parser.add_argument(
        "--psd",
        type=Path,
        default=bundled_default_psd(),
        help="PSD path used for single-name Photoshop renders.",
    )
    parser.add_argument(
        "--render-output-dir",
        type=Path,
        default=default_request_worker_dir() / "renders",
        help="Local folder where temporary worker render PNGs are written.",
    )
    return parser


def install_signal_handlers(worker: RequestWorker) -> None:
    def handle_signal(signum: int, _frame: object) -> None:
        worker.should_stop = True
        worker.last_message = f"stop requested via signal {signum}"

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    worker = RequestWorker(
        args.poll_interval,
        args.status_file,
        psd_path=args.psd,
        render_output_dir=args.render_output_dir,
    )
    install_signal_handlers(worker)
    return worker.run()


if __name__ == "__main__":
    raise SystemExit(main())
