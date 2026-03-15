#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json
import shutil
import sys
from pathlib import Path
from urllib import error, parse, request

from PIL import Image

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app_paths import default_single_supabase_export_dir, single_save_supabase_config_file
from ps_single_renderer import DESIGN_NAME, sanitize_filename


MODEL_NAME = "ps-desktop-name-png-v1"
BLACK_THRESHOLD = 16
DEFAULT_STORAGE_BUCKET = "generated-maps"
DEFAULT_CACHE_TABLE = "ai_text_design_cache"
DEFAULT_STORAGE_FOLDER = "ai-text-design-cache"
STYLE_COLOR_KEYS = {
    "Black": "black_silver",
    "Gray": "silver_gray",
    "Blue": "cobalt_blue",
    "Blue Dark": "steel_blue",
    "Patina Blue": "patina_blue",
    "Turkuaz": "teal_cyan",
    "Green": "forest_green",
    "Green Dark": "dark_green",
    "Purple": "deep_purple",
    "Pink": "vivid_pink",
    "Red": "crimson_red",
    "Rose": "dusty_rose",
    "Gold": "gold_amber",
    "Yellow": "olive_yellow",
    "Brown": "warm_brown",
    "Brown Light": "light_brown",
}


@dataclass(frozen=True)
class SupabaseSingleSaveConfig:
    supabase_url: str
    service_role_key: str
    storage_bucket: str = DEFAULT_STORAGE_BUCKET
    cache_table: str = DEFAULT_CACHE_TABLE
    storage_folder: str = DEFAULT_STORAGE_FOLDER


@dataclass(frozen=True)
class ArchivedRender:
    style: str
    source_path: Path
    archived_path: Path


@dataclass(frozen=True)
class ArchivedRun:
    run_root: Path
    run_stamp: str
    target_text: str
    archived_renders: list[ArchivedRender]


@dataclass(frozen=True)
class UploadItemResult:
    style: str
    color_key: str
    status: str
    message: str


@dataclass(frozen=True)
class ImportResult:
    archived_run: ArchivedRun
    items: list[UploadItemResult]

    @property
    def ok(self) -> bool:
        return all(item.status != "failed" for item in self.items)

    @property
    def output(self) -> str:
        return "\n".join(
            f"{item.style} [{item.color_key}] {item.status}: {item.message}" for item in self.items
        )


def normalize_target_text(value: str) -> str:
    return " ".join(value.strip().split()).lower()


def normalize_color_key(value: str) -> str:
    return value.strip().lower()


def build_cache_key(model: str, target_text: str, color_key: str) -> str:
    return f"{model.strip()}::{normalize_target_text(target_text)}::{normalize_color_key(color_key)}"


def to_slug(value: str, fallback: str = "text") -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    trimmed = cleaned[:48].strip("-")
    return trimmed or fallback


def resolve_color_key(style: str) -> str:
    color_key = STYLE_COLOR_KEYS.get(style.strip())
    if not color_key:
        raise ValueError(f"Unsupported style for cache upload: {style}")
    return color_key


def load_single_save_config() -> SupabaseSingleSaveConfig:
    config_path = single_save_supabase_config_file()
    if not config_path.exists():
        raise FileNotFoundError(f"Missing Supabase single-save config: {config_path}")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    supabase_url = str(data.get("supabaseUrl", "")).strip().rstrip("/")
    service_role_key = str(data.get("serviceRoleKey", "")).strip()
    if not supabase_url:
        raise ValueError(f"supabaseUrl is missing in {config_path}")
    if not service_role_key:
        raise ValueError(f"serviceRoleKey is missing in {config_path}")
    return SupabaseSingleSaveConfig(
        supabase_url=supabase_url,
        service_role_key=service_role_key,
        storage_bucket=str(data.get("storageBucket", DEFAULT_STORAGE_BUCKET)).strip() or DEFAULT_STORAGE_BUCKET,
        cache_table=str(data.get("cacheTable", DEFAULT_CACHE_TABLE)).strip() or DEFAULT_CACHE_TABLE,
        storage_folder=str(data.get("storageFolder", DEFAULT_STORAGE_FOLDER)).strip() or DEFAULT_STORAGE_FOLDER,
    )


def archive_custom_render_outputs(
    render_name: str,
    rendered_items: list[tuple[str, Path]],
    export_root: Path | None = None,
) -> ArchivedRun:
    if not rendered_items:
        raise ValueError("No rendered items were provided for single export.")

    now = datetime.now()
    run_root = (export_root or default_single_supabase_export_dir()) / now.strftime("%Y-%m-%d") / now.strftime("%H%M%S_%f")
    archived_renders: list[ArchivedRender] = []
    safe_name = sanitize_filename(render_name)

    for style, source_path in rendered_items:
        source = source_path.expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Rendered PNG not found: {source}")
        style_dir = run_root / sanitize_filename(style)
        style_dir.mkdir(parents=True, exist_ok=True)
        archived_path = style_dir / f"{safe_name}.png"
        shutil.copy2(source, archived_path)
        archived_renders.append(ArchivedRender(style=style, source_path=source, archived_path=archived_path))

    return ArchivedRun(
        run_root=run_root,
        run_stamp=run_root.name,
        target_text=render_name,
        archived_renders=archived_renders,
    )


def config_headers(config: SupabaseSingleSaveConfig, content_type: str | None = None, prefer: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": config.service_role_key,
        "Authorization": f"Bearer {config.service_role_key}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    if prefer:
        headers["Prefer"] = prefer
    return headers


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str],
    payload: bytes | None = None,
    timeout_seconds: int = 60,
) -> object:
    req = request.Request(url, data=payload, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def upload_storage_object(
    config: SupabaseSingleSaveConfig,
    storage_path: str,
    png_bytes: bytes,
    *,
    timeout_seconds: int = 120,
) -> None:
    encoded_path = parse.quote(storage_path, safe="/-_.")
    url = f"{config.supabase_url}/storage/v1/object/{config.storage_bucket}/{encoded_path}"
    headers = config_headers(config, content_type="image/png")
    headers["x-upsert"] = "true"
    req = request.Request(url, data=png_bytes, method="POST", headers=headers)
    try:
        with request.urlopen(req, timeout=timeout_seconds):
            return
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        if exc.code == 409:
            return
        raise RuntimeError(detail or f"Storage HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def postgrest_select(
    config: SupabaseSingleSaveConfig,
    *,
    filters: dict[str, str],
    select: str,
    timeout_seconds: int = 30,
) -> list[dict[str, object]]:
    query = {"select": select}
    query.update(filters)
    url = f"{config.supabase_url}/rest/v1/{config.cache_table}?{parse.urlencode(query)}"
    data = http_json(url, headers=config_headers(config), timeout_seconds=timeout_seconds)
    if isinstance(data, list):
        return data
    raise RuntimeError("Unexpected Supabase response shape.")


def postgrest_insert(
    config: SupabaseSingleSaveConfig,
    row: dict[str, object],
    *,
    timeout_seconds: int = 60,
) -> list[dict[str, object]]:
    url = f"{config.supabase_url}/rest/v1/{config.cache_table}"
    payload = json.dumps(row).encode("utf-8")
    data = http_json(
        url,
        method="POST",
        headers=config_headers(config, content_type="application/json", prefer="return=representation"),
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    if isinstance(data, list):
        return data
    raise RuntimeError("Unexpected Supabase insert response shape.")


def convert_black_to_alpha_png(image_path: Path, threshold: int = BLACK_THRESHOLD) -> bytes:
    with Image.open(image_path) as img:
        rgba = img.convert("RGBA")
        pixels = rgba.load()
        for y in range(rgba.height):
            for x in range(rgba.width):
                r, g, b, a = pixels[x, y]
                if r <= threshold and g <= threshold and b <= threshold:
                    pixels[x, y] = (r, g, b, 0)
        buf = BytesIO()
        rgba.save(buf, format="PNG")
        return buf.getvalue()


def storage_path_for(config: SupabaseSingleSaveConfig, target_text: str, color_key: str, image_hash: str) -> str:
    cache_key = build_cache_key(MODEL_NAME, target_text, color_key)
    key_hash = sha256(cache_key.encode("utf-8")).hexdigest()[:16]
    color_segment = normalize_color_key(color_key).replace("/", "-")[:32] or "color"
    text_segment = to_slug(target_text, "text")
    design_segment = to_slug(DESIGN_NAME, "design")
    return f"{config.storage_folder}/{design_segment}/{color_segment}/{text_segment}-{key_hash}/{image_hash}.png"


def public_url_for(config: SupabaseSingleSaveConfig, storage_path: str) -> str:
    encoded_path = parse.quote(storage_path, safe="/-_.")
    return f"{config.supabase_url}/storage/v1/object/public/{config.storage_bucket}/{encoded_path}"


def existing_cache_key_rows(
    config: SupabaseSingleSaveConfig,
    cache_key: str,
    *,
    timeout_seconds: int = 30,
) -> list[dict[str, object]]:
    return postgrest_select(
        config,
        filters={"cache_key": f"eq.{cache_key}", "limit": "1"},
        select="id,image_hash",
        timeout_seconds=timeout_seconds,
    )


def import_archived_run(archived_run: ArchivedRun, timeout_seconds: int = 120) -> ImportResult:
    config = load_single_save_config()
    items: list[UploadItemResult] = []

    for render in archived_run.archived_renders:
        try:
            color_key = resolve_color_key(render.style)
            cache_key = build_cache_key(MODEL_NAME, archived_run.target_text, color_key)
            if existing_cache_key_rows(config, cache_key, timeout_seconds=timeout_seconds):
                items.append(
                    UploadItemResult(
                        style=render.style,
                        color_key=color_key,
                        status="skipped",
                        message="existing cache_key found",
                    )
                )
                continue
            png_bytes = convert_black_to_alpha_png(render.archived_path)
            image_hash = sha256(png_bytes).hexdigest()
            storage_path = storage_path_for(config, archived_run.target_text, color_key, image_hash)
            upload_storage_object(config, storage_path, png_bytes, timeout_seconds=timeout_seconds)
            row = {
                "model": MODEL_NAME,
                "target_text": archived_run.target_text,
                "color_key": color_key,
                "design_name": DESIGN_NAME,
                "normalized_text": normalize_target_text(archived_run.target_text),
                "cache_key": cache_key,
                "image_url": public_url_for(config, storage_path),
                "image_hash": image_hash,
                "storage_path": storage_path,
                "use_count": 1,
                "last_used_at": datetime.utcnow().isoformat() + "Z",
            }
            postgrest_insert(config, row, timeout_seconds=timeout_seconds)
            items.append(
                UploadItemResult(
                    style=render.style,
                    color_key=color_key,
                    status="uploaded",
                    message=f"hash={image_hash[:12]}",
                )
            )
        except Exception as exc:  # noqa: BLE001
            fallback_color = STYLE_COLOR_KEYS.get(render.style.strip(), "unknown")
            items.append(
                UploadItemResult(
                    style=render.style,
                    color_key=fallback_color,
                    status="failed",
                    message=str(exc),
                )
            )

    return ImportResult(archived_run=archived_run, items=items)
