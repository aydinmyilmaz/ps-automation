# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path('/Users/aydin/Desktop/apps/ps-automation')
single_save_config = project_root / 'config' / 'supabase_single_save.json'
datas = [
    (str(project_root / 'output' / 'gmail_name_sync' / '05_curated'), 'data/final_names'),
    (str(project_root / 'data' / 'selected-psd'), 'data/selected-psd'),
]
if single_save_config.exists():
    datas.append((str(single_save_config), 'config'))

a = Analysis(
    ['/Users/aydin/Desktop/apps/ps-automation/scripts/desktop_qt_app.py'],
    pathex=['/Users/aydin/Desktop/apps/ps-automation/scripts'],
    binaries=[],
    datas=datas,
    hiddenimports=['onecall_unattended_batch', 'app_paths'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PSDBatchDesktop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PSDBatchDesktop',
)
app = BUNDLE(
    coll,
    name='PSDBatchDesktop.app',
    icon=None,
    bundle_identifier=None,
)
