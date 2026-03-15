# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['/Users/aydin/Desktop/apps/ps-automation/scripts/desktop_qt_app.py'],
    pathex=['/Users/aydin/Desktop/apps/ps-automation/scripts'],
    binaries=[],
    datas=[('/Users/aydin/Desktop/apps/ps-automation/output/gmail_name_sync/05_curated', 'data/final_names'), ('/Users/aydin/Desktop/apps/ps-automation/data/selected-psd', 'data/selected-psd')],
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
