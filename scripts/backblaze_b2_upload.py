#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.client import HTTPSConnection, HTTPResponse
from pathlib import Path
from typing import BinaryIO
from urllib import error, parse, request
from concurrent.futures import ThreadPoolExecutor, as_completed


ENV_FILES = (".env.local", ".env")
SINGLE_UPLOAD_LIMIT = 5 * 1024**3
DEFAULT_PART_SIZE = 100_000_000
RETRYABLE_ERROR_MARKERS = (
    "timed out",
    "timeout",
    "temporarily unavailable",
    "connection reset",
    "broken pipe",
    "remote end closed connection",
    "connection aborted",
    "connection refused",
    "service unavailable",
    "too many requests",
    "bad gateway",
    "gateway timeout",
)


@dataclass(frozen=True)
class StorageApiAuth:
    account_id: str
    api_url: str
    authorization_token: str
    recommended_part_size: int
    absolute_minimum_part_size: int
    allowed_buckets: tuple[dict[str, object], ...]


def load_env_files(root: Path) -> None:
    for name in ENV_FILES:
        env_path = root / name
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value


def env_first(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return ""


def json_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, object] | None = None,
    timeout_seconds: int = 120,
) -> dict[str, object]:
    body = None
    req_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = request.Request(url, data=body, method=method, headers=req_headers)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                raise RuntimeError(f"Unexpected response shape from {url}")
            return data
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"HTTP {exc.code} from {url}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"{url}: {exc.reason}") from exc


def is_retryable_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in RETRYABLE_ERROR_MARKERS)


def retry_call(func, *args, attempts: int = 5, delay_seconds: float = 2.0, **kwargs):
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= attempts or not is_retryable_error(exc):
                raise
            wait = delay_seconds * attempt
            print(
                f"Retrying after transient Backblaze error ({attempt}/{attempts - 1} retries used): {exc}",
                flush=True,
            )
            time.sleep(wait)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("retry_call reached an unexpected state.")


def authorize_account(application_key_id: str, application_key: str) -> StorageApiAuth:
    basic = base64.b64encode(f"{application_key_id}:{application_key}".encode("utf-8")).decode("ascii")
    payload = json_request(
        "https://api.backblazeb2.com/b2api/v4/b2_authorize_account",
        headers={"Authorization": f"Basic {basic}"},
    )
    api_info = payload.get("apiInfo") or {}
    storage_api = api_info.get("storageApi") or {}
    allowed = storage_api.get("allowed") or {}
    return StorageApiAuth(
        account_id=str(payload.get("accountId") or ""),
        api_url=str(storage_api.get("apiUrl") or ""),
        authorization_token=str(payload.get("authorizationToken") or ""),
        recommended_part_size=int(storage_api.get("recommendedPartSize") or DEFAULT_PART_SIZE),
        absolute_minimum_part_size=int(storage_api.get("absoluteMinimumPartSize") or 5_000_000),
        allowed_buckets=tuple(allowed.get("buckets") or ()),
    )


def resolve_bucket_id(auth: StorageApiAuth, bucket_id: str, bucket_name: str) -> str:
    if bucket_id:
        return bucket_id

    normalized_name = bucket_name.strip()
    if not normalized_name:
        raise ValueError("Bucket ID or bucket name is required.")

    for bucket in auth.allowed_buckets:
        if str(bucket.get("name") or "") == normalized_name:
            resolved = str(bucket.get("id") or "")
            if resolved:
                return resolved

    payload = json_request(
        f"{auth.api_url}/b2api/v4/b2_list_buckets",
        method="POST",
        headers={"Authorization": auth.authorization_token},
        payload={"accountId": auth.account_id, "bucketName": normalized_name},
    )
    buckets = payload.get("buckets") or []
    if not buckets:
        raise RuntimeError(f"Bucket not found: {normalized_name}")
    resolved = str(buckets[0].get("bucketId") or buckets[0].get("bucket_id") or buckets[0].get("id") or "")
    if not resolved:
        raise RuntimeError(f"Bucket ID missing from Backblaze response for {normalized_name}")
    return resolved


def sha1_for_file(path: Path, buffer_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(buffer_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def upload_stream(url: str, headers: dict[str, str], body: BinaryIO, content_length: int) -> dict[str, object]:
    parsed = parse.urlsplit(url)
    path = parsed.path
    if parsed.query:
        path = f"{path}?{parsed.query}"
    connection = HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=300)
    connection.putrequest("POST", path)
    for key, value in headers.items():
        connection.putheader(key, value)
    connection.putheader("Content-Length", str(content_length))
    connection.endheaders()

    remaining = content_length
    while remaining > 0:
        chunk = body.read(min(8 * 1024 * 1024, remaining))
        if not chunk:
            raise RuntimeError("Unexpected EOF while streaming upload body.")
        connection.send(chunk)
        remaining -= len(chunk)

    response = connection.getresponse()
    result = decode_json_response(response)
    connection.close()
    return result


def upload_bytes(url: str, headers: dict[str, str], body: bytes) -> dict[str, object]:
    parsed = parse.urlsplit(url)
    path = parsed.path
    if parsed.query:
        path = f"{path}?{parsed.query}"
    connection = HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=300)
    connection.request("POST", path, body=body, headers=headers)
    response = connection.getresponse()
    result = decode_json_response(response)
    connection.close()
    return result


def decode_json_response(response: HTTPResponse) -> dict[str, object]:
    raw = response.read().decode("utf-8", errors="replace")
    if response.status >= 400:
        raise RuntimeError(raw.strip() or f"HTTP {response.status}")
    data = json.loads(raw) if raw else {}
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected upload response shape.")
    return data


def get_upload_target(auth: StorageApiAuth, bucket_id: str) -> tuple[str, str]:
    payload = json_request(
        f"{auth.api_url}/b2api/v4/b2_get_upload_url",
        method="POST",
        headers={"Authorization": auth.authorization_token},
        payload={"bucketId": bucket_id},
    )
    return str(payload.get("uploadUrl") or ""), str(payload.get("authorizationToken") or "")


def single_file_upload(
    auth: StorageApiAuth,
    bucket_id: str,
    local_path: Path,
    remote_name: str,
    content_type: str,
) -> dict[str, object]:
    upload_url, upload_token = get_upload_target(auth, bucket_id)
    if not upload_url or not upload_token:
        raise RuntimeError("Backblaze did not return a usable upload URL.")

    file_sha1 = sha1_for_file(local_path)
    headers = {
        "Authorization": upload_token,
        "X-Bz-File-Name": parse.quote(remote_name, safe="/!$'()*;=@:+,?-_~"),
        "X-Bz-Content-Sha1": file_sha1,
        "Content-Type": content_type,
    }
    with local_path.open("rb") as handle:
        def upload_once() -> dict[str, object]:
            handle.seek(0)
            return upload_stream(upload_url, headers, handle, local_path.stat().st_size)

        return retry_call(upload_once)


def start_large_file(
    auth: StorageApiAuth,
    bucket_id: str,
    remote_name: str,
    content_type: str,
    file_sha1: str | None = None,
) -> str:
    file_info: dict[str, str] = {}
    if file_sha1:
        file_info["large_file_sha1"] = file_sha1
    payload = json_request(
        f"{auth.api_url}/b2api/v4/b2_start_large_file",
        method="POST",
        headers={"Authorization": auth.authorization_token},
        payload={
            "bucketId": bucket_id,
            "fileName": remote_name,
            "contentType": content_type,
            "fileInfo": file_info,
        },
    )
    file_id = str(payload.get("fileId") or "")
    if not file_id:
        raise RuntimeError("Backblaze did not return a large file ID.")
    return file_id


def get_upload_part_target(auth: StorageApiAuth, file_id: str) -> tuple[str, str]:
    payload = retry_call(
        json_request,
        f"{auth.api_url}/b2api/v4/b2_get_upload_part_url",
        method="POST",
        headers={"Authorization": auth.authorization_token},
        payload={"fileId": file_id},
    )
    return str(payload.get("uploadUrl") or ""), str(payload.get("authorizationToken") or "")


def finish_large_file(auth: StorageApiAuth, file_id: str, part_sha1_array: list[str]) -> dict[str, object]:
    return json_request(
        f"{auth.api_url}/b2api/v4/b2_finish_large_file",
        method="POST",
        headers={"Authorization": auth.authorization_token},
        payload={"fileId": file_id, "partSha1Array": part_sha1_array},
    )


def cancel_large_file(auth: StorageApiAuth, file_id: str) -> None:
    try:
        json_request(
            f"{auth.api_url}/b2api/v4/b2_cancel_large_file",
            method="POST",
            headers={"Authorization": auth.authorization_token},
            payload={"fileId": file_id},
        )
    except Exception:
        return


def delete_file_version(auth: StorageApiAuth, file_name: str, file_id: str) -> None:
    json_request(
        f"{auth.api_url}/b2api/v4/b2_delete_file_version",
        method="POST",
        headers={"Authorization": auth.authorization_token},
        payload={"fileName": file_name, "fileId": file_id},
    )


def large_file_upload(
    auth: StorageApiAuth,
    bucket_id: str,
    local_path: Path,
    remote_name: str,
    content_type: str,
    part_size: int,
) -> dict[str, object]:
    file_sha1 = sha1_for_file(local_path)
    file_id = start_large_file(auth, bucket_id, remote_name, content_type, file_sha1)
    part_sha1s: list[str] = []
    upload_url = ""
    upload_token = ""
    try:
        with local_path.open("rb") as handle:
            part_number = 1
            while True:
                chunk = handle.read(part_size)
                if not chunk:
                    break
                part_sha1 = hashlib.sha1(chunk).hexdigest()
                if not upload_url or not upload_token:
                    upload_url, upload_token = get_upload_part_target(auth, file_id)
                    if not upload_url or not upload_token:
                        raise RuntimeError("Backblaze did not return a usable part upload URL.")
                try:
                    response = retry_call(
                        upload_bytes,
                        upload_url,
                        {
                            "Authorization": upload_token,
                            "X-Bz-Part-Number": str(part_number),
                            "X-Bz-Content-Sha1": part_sha1,
                            "Content-Type": "application/octet-stream",
                        },
                        chunk,
                    )
                except Exception:
                    upload_url = ""
                    upload_token = ""
                    raise
                returned_sha1 = str(response.get("contentSha1") or "")
                if returned_sha1 and returned_sha1 != part_sha1:
                    raise RuntimeError(f"Part {part_number} SHA1 mismatch after upload.")
                part_sha1s.append(part_sha1)
                print(
                    f"[part {part_number}] {handle.tell() / (1024**3):.2f}/{local_path.stat().st_size / (1024**3):.2f} GiB "
                    f"uploaded | remote={remote_name}",
                    flush=True,
                )
                part_number += 1
    except Exception:
        cancel_large_file(auth, file_id)
        raise
    return finish_large_file(auth, file_id, part_sha1s)


def large_stream_upload(
    auth: StorageApiAuth,
    bucket_id: str,
    source: BinaryIO,
    remote_name: str,
    content_type: str,
    part_size: int,
    *,
    approx_total_bytes: int | None = None,
) -> dict[str, object]:
    file_id = start_large_file(auth, bucket_id, remote_name, content_type, file_sha1=None)
    part_sha1s: list[str] = []
    uploaded_bytes = 0
    upload_url = ""
    upload_token = ""
    try:
        part_number = 1
        while True:
            chunk = source.read(part_size)
            if not chunk:
                break
            if not upload_url or not upload_token:
                upload_url, upload_token = get_upload_part_target(auth, file_id)
                if not upload_url or not upload_token:
                    raise RuntimeError("Backblaze did not return a usable part upload URL.")
            part_sha1 = hashlib.sha1(chunk).hexdigest()
            response = retry_call(
                upload_bytes,
                upload_url,
                {
                    "Authorization": upload_token,
                    "X-Bz-Part-Number": str(part_number),
                    "X-Bz-Content-Sha1": part_sha1,
                    "Content-Type": "application/octet-stream",
                },
                chunk,
            )
            returned_sha1 = str(response.get("contentSha1") or "")
            if returned_sha1 and returned_sha1 != part_sha1:
                raise RuntimeError(f"Part {part_number} SHA1 mismatch after upload.")
            part_sha1s.append(part_sha1)
            uploaded_bytes += len(chunk)
            if approx_total_bytes:
                print(
                    f"[part {part_number}] {uploaded_bytes / (1024**3):.2f}/{approx_total_bytes / (1024**3):.2f} GiB "
                    f"uploaded | remote={remote_name}",
                    flush=True,
                )
            else:
                print(
                    f"[part {part_number}] {uploaded_bytes / (1024**3):.2f} GiB uploaded | remote={remote_name}",
                    flush=True,
                )
            part_number += 1
    except Exception:
        cancel_large_file(auth, file_id)
        raise
    return finish_large_file(auth, file_id, part_sha1s)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload a file to Backblaze B2 using the Native API.")
    parser.add_argument("path", type=Path, help="Local file or directory to upload.")
    parser.add_argument("--bucket-id", default="", help="Backblaze bucket ID. Overrides env.")
    parser.add_argument("--bucket-name", default="", help="Backblaze bucket name. Used if bucket ID is not provided.")
    parser.add_argument("--remote-name", default="", help="Remote object name inside the bucket.")
    parser.add_argument("--prefix", default="", help="Remote prefix for directory uploads.")
    parser.add_argument(
        "--stream-tar",
        action="store_true",
        help="For directory input, stream a single .tar archive upload instead of uploading files individually.",
    )
    parser.add_argument(
        "--content-type",
        default="",
        help="Optional content type. Defaults to guessed MIME type or b2/x-auto.",
    )
    parser.add_argument(
        "--part-size-mb",
        type=int,
        default=0,
        help="Large-file part size in MB. Defaults to Backblaze recommendedPartSize.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrent uploads for directory mode.",
    )
    parser.add_argument(
        "--env-file-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project root used to load .env.local/.env.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_files(args.env_file_root.resolve())

    local_path = args.path.expanduser().resolve()
    if not local_path.exists():
        raise FileNotFoundError(f"Path not found: {local_path}")

    application_key = env_first(
        "BACKBLAZE_APPLICATION_KEY",
        "backblaze_applicationKey",
        "BACKBLAZE_KEY",
        "backblaze_key",
    )
    application_key_id = env_first(
        "BACKBLAZE_APPLICATION_KEY_ID",
        "backblaze_applicationKeyId",
        "BACKBLAZE_KEY_ID",
        "backblaze_keyID",
        "backbalze_keyID",
    )
    bucket_id = args.bucket_id.strip() or env_first(
        "BACKBLAZE_BUCKET_ID",
        "backblaze_bucketId",
        "BACKBLAZE_B2_BUCKET_ID",
    )
    bucket_name = args.bucket_name.strip() or env_first(
        "BACKBLAZE_BUCKET_NAME",
        "backblaze_bucketName",
        "BACKBLAZE_B2_BUCKET_NAME",
    )

    if not application_key:
        raise ValueError("Missing BACKBLAZE_APPLICATION_KEY / backblaze_applicationKey.")
    if not application_key_id:
        raise ValueError(
            "Missing BACKBLAZE_APPLICATION_KEY_ID / backblaze_applicationKeyId. "
            "Backblaze Native API authorization requires both key ID and application key."
        )

    auth = authorize_account(application_key_id, application_key)
    resolved_bucket_id = resolve_bucket_id(auth, bucket_id, bucket_name)
    remote_name = args.remote_name.strip()
    prefix = args.prefix.strip().strip("/")

    if local_path.is_file():
        if prefix and remote_name:
            raise ValueError("Use either --remote-name or --prefix for single-file uploads, not both.")
        if prefix:
            remote_name = f"{prefix}/{local_path.name}"
        remote_name = remote_name or local_path.name
        content_type = args.content_type.strip() or mimetypes.guess_type(local_path.name)[0] or "b2/x-auto"

        if local_path.stat().st_size <= SINGLE_UPLOAD_LIMIT:
            result = single_file_upload(auth, resolved_bucket_id, local_path, remote_name, content_type)
        else:
            part_size = auth.recommended_part_size
            if args.part_size_mb > 0:
                part_size = args.part_size_mb * 1024 * 1024
            part_size = max(part_size, auth.absolute_minimum_part_size)
            result = large_file_upload(auth, resolved_bucket_id, local_path, remote_name, content_type, part_size)

        print(
            json.dumps(
                {
                    "ok": True,
                    "fileName": result.get("fileName"),
                    "fileId": result.get("fileId"),
                    "bucketId": result.get("bucketId") or resolved_bucket_id,
                    "contentLength": result.get("contentLength"),
                    "action": result.get("action"),
                },
                indent=2,
            )
        )
        return 0

    if not local_path.is_dir():
        raise ValueError(f"Unsupported path type: {local_path}")
    if remote_name and not args.stream_tar:
        raise ValueError("--remote-name is only valid for single-file uploads.")

    if args.stream_tar:
        approx_total_bytes = sum(path.stat().st_size for path in local_path.rglob("*") if path.is_file())
        remote_tar_name = remote_name or f"{(prefix or local_path.name).strip('/').rstrip('/')}.tar"
        tar_cmd = ["tar", "-cf", "-", "-C", str(local_path.parent), local_path.name]
        proc = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.stdout is None:
            raise RuntimeError("Failed to open tar stdout stream.")
        try:
            result = large_stream_upload(
                auth,
                resolved_bucket_id,
                proc.stdout,
                remote_tar_name,
                "application/x-tar",
                part_size=max(auth.recommended_part_size, auth.absolute_minimum_part_size),
                approx_total_bytes=approx_total_bytes,
            )
        finally:
            if proc.stdout:
                proc.stdout.close()
            stderr = (proc.stderr.read().decode("utf-8", errors="replace").strip() if proc.stderr else "")
            returncode = proc.wait()
            if proc.stderr:
                proc.stderr.close()
            if returncode != 0:
                raise RuntimeError(stderr or f"tar failed with exit code {returncode}")
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "stream-tar",
                    "fileName": result.get("fileName"),
                    "fileId": result.get("fileId"),
                    "bucketId": result.get("bucketId") or resolved_bucket_id,
                    "action": result.get("action"),
                    "remoteName": remote_tar_name,
                },
                indent=2,
            )
        )
        return 0

    base_prefix = prefix or local_path.name
    files = sorted(path for path in local_path.rglob("*") if path.is_file())
    if not files:
        raise ValueError(f"No files found under directory: {local_path}")

    total_bytes = sum(path.stat().st_size for path in files)
    completed = 0
    uploaded_bytes = 0
    lock = threading.Lock()
    part_size = auth.recommended_part_size
    if args.part_size_mb > 0:
        part_size = args.part_size_mb * 1024 * 1024
    part_size = max(part_size, auth.absolute_minimum_part_size)

    def upload_one(path: Path) -> tuple[str, int]:
        rel = path.relative_to(local_path).as_posix()
        remote_path = f"{base_prefix}/{rel}"
        content_type = mimetypes.guess_type(path.name)[0] or "b2/x-auto"
        if path.stat().st_size <= SINGLE_UPLOAD_LIMIT:
            single_file_upload(auth, resolved_bucket_id, path, remote_path, content_type)
        else:
            large_file_upload(auth, resolved_bucket_id, path, remote_path, content_type, part_size)
        return remote_path, path.stat().st_size

    max_workers = max(1, args.workers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(upload_one, path): path for path in files}
        for future in as_completed(future_map):
            remote_path, size_bytes = future.result()
            with lock:
                completed += 1
                uploaded_bytes += size_bytes
                if completed == 1 or completed % 25 == 0 or completed == len(files):
                    print(
                        f"[{completed}/{len(files)}] "
                        f"{uploaded_bytes / (1024**3):.2f}/{total_bytes / (1024**3):.2f} GiB "
                        f"uploaded | last={remote_path}",
                        flush=True,
                    )

    print(
        json.dumps(
            {
                "ok": True,
                "mode": "directory",
                "bucketId": resolved_bucket_id,
                "prefix": base_prefix,
                "fileCount": len(files),
                "totalBytes": total_bytes,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
