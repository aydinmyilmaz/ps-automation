#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from app_paths import DESKTOP_APP_NAME


SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
ENTRYPOINT = SCRIPTS_DIR / "desktop_qt_app.py"
DIST_DIR = PROJECT_ROOT / "dist_desktop"
WORK_DIR = PROJECT_ROOT / "build" / "pyinstaller"
SPEC_DIR = PROJECT_ROOT / "build" / "spec"
RELEASE_DIR = PROJECT_ROOT / "release"
SINGLE_SAVE_CONFIG = PROJECT_ROOT / "config" / "supabase_single_save.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a bundled desktop app release for the current platform."
    )
    parser.add_argument("--name", default=DESKTOP_APP_NAME, help="Application name.")
    parser.add_argument(
        "--skip-archive",
        action="store_true",
        help="Skip creating the final zip archive.",
    )
    parser.add_argument(
        "--mac-sign-identity",
        default="",
        help="Optional macOS codesign identity. Example: 'Developer ID Application: ...'",
    )
    return parser.parse_args()


def platform_tag() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform


def pyinstaller_data_arg(source: Path, target: str) -> str:
    sep = ";" if sys.platform.startswith("win") else ":"
    return f"{source}{sep}{target}"


def artifact_path(app_name: str) -> Path:
    if sys.platform == "darwin":
        return DIST_DIR / f"{app_name}.app"
    return DIST_DIR / app_name


def build_pyinstaller(app_name: str) -> Path:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        app_name,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(WORK_DIR),
        "--specpath",
        str(SPEC_DIR),
        "--paths",
        str(SCRIPTS_DIR),
        "--hidden-import",
        "onecall_unattended_batch",
        "--hidden-import",
        "app_paths",
        "--add-data",
        pyinstaller_data_arg(PROJECT_ROOT / "output" / "gmail_name_sync" / "05_curated", "data/final_names"),
        "--add-data",
        pyinstaller_data_arg(PROJECT_ROOT / "data" / "selected-psd", "data/selected-psd"),
    ]
    if SINGLE_SAVE_CONFIG.exists():
        cmd.extend(
            [
                "--add-data",
                pyinstaller_data_arg(SINGLE_SAVE_CONFIG, "config"),
            ]
        )
    cmd.append(str(ENTRYPOINT))
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True)
    return artifact_path(app_name)


def maybe_codesign_macos(app_path: Path, identity: str) -> None:
    if sys.platform != "darwin" or not identity.strip():
        return
    cmd = [
        "codesign",
        "--force",
        "--deep",
        "--sign",
        identity.strip(),
        "--options",
        "runtime",
        str(app_path),
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def archive_release(app_name: str, artifact: Path) -> Path:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    release_base = RELEASE_DIR / f"{app_name}-{platform_tag()}"
    zip_path = release_base.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()

    if sys.platform == "darwin":
        cmd = [
            "ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(artifact),
            str(zip_path),
        ]
        print("Running:", " ".join(cmd))
        subprocess.run(cmd, check=True)
        return zip_path

    folder = artifact if artifact.is_dir() else artifact.parent
    archive_base = str(release_base)
    shutil.make_archive(archive_base, "zip", root_dir=str(folder.parent), base_dir=folder.name)
    return zip_path


def main() -> int:
    args = parse_args()
    artifact = build_pyinstaller(args.name)
    maybe_codesign_macos(artifact, args.mac_sign_identity)

    print(f"Build complete: {artifact}")
    if args.skip_archive:
        return 0

    archive = archive_release(args.name, artifact)
    print(f"Archive complete: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
