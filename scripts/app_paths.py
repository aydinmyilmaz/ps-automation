#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


APP_STORAGE_NAME = "PS Automation"
DESKTOP_APP_NAME = "PSDBatchDesktop"
SCRIPTS_DIR = Path(__file__).resolve().parent
SOURCE_PROJECT_ROOT = SCRIPTS_DIR.parent


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    if is_frozen_app():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    return SOURCE_PROJECT_ROOT


def resource_path(*parts: str) -> Path:
    return bundle_root().joinpath(*parts)


def app_support_dir() -> Path:
    home = Path.home()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return base / APP_STORAGE_NAME


def documents_dir() -> Path:
    candidate = Path.home() / "Documents"
    return candidate if candidate.exists() else Path.home()


def desktop_dir() -> Path:
    candidate = Path.home() / "Desktop"
    return candidate if candidate.exists() else documents_dir()


def default_output_base() -> Path:
    if is_frozen_app():
        return documents_dir() / APP_STORAGE_NAME / "output"
    return SOURCE_PROJECT_ROOT / "output"


def desktop_settings_file() -> Path:
    if is_frozen_app():
        return app_support_dir() / "desktop_qt_last_config.json"
    return SOURCE_PROJECT_ROOT / "output" / "desktop_qt_last_config.json"


def default_batch_output_dir() -> Path:
    return default_output_base() / "batch_runs" / "desktop"


def default_web_output_dir() -> Path:
    return default_output_base() / "web_single"


def default_single_supabase_export_dir() -> Path:
    return desktop_dir() / "ps_single_supabase_exports"


def single_save_supabase_config_file() -> Path:
    if is_frozen_app():
        return resource_path("config", "supabase_single_save.json")
    return SOURCE_PROJECT_ROOT / "config" / "supabase_single_save.json"


def bundled_names_file() -> Path:
    return curated_names_dir() / "unified_popular_names_3000.txt"


def bundled_default_psd() -> Path:
    return resource_path("data", "selected-psd", "Bootleg STARTUP 2026 v4 all colors 3.psd")


def gmail_name_sync_root() -> Path:
    return SOURCE_PROJECT_ROOT / "output" / "gmail_name_sync"


def gmail_ranked_dir() -> Path:
    return gmail_name_sync_root() / "01_ranked"


def gmail_extracted_dir() -> Path:
    return gmail_name_sync_root() / "02_extracted"


def gmail_reports_dir() -> Path:
    return gmail_name_sync_root() / "03_reports"


def gmail_derived_dir() -> Path:
    return gmail_name_sync_root() / "04_derived"


def curated_names_dir() -> Path:
    if not is_frozen_app():
        migrated = gmail_name_sync_root() / "05_curated"
        if migrated.exists():
            return migrated
    return resource_path("data", "final_names")
