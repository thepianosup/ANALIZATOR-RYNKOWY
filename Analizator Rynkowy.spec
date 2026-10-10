# -*- mode: python ; coding: utf-8 -*-

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__)) if '__file__' in globals() else os.getcwd()
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config
from PyInstaller.utils.hooks import collect_system_data_files, collect_submodules
from PyInstaller.utils.hooks.qt import pyqt6_library_info


def collect_macos_qtwebengine_files():
    framework_path = os.path.join(
        pyqt6_library_info.location['LibrariesPath'],
        'QtWebEngineCore.framework',
    )
    versions_path = os.path.join(framework_path, 'Versions')
    versions = sorted(
        name
        for name in os.listdir(versions_path)
        if name != 'Current'
        and os.path.isfile(os.path.join(versions_path, name, 'QtWebEngineCore'))
    )
    if not versions:
        raise RuntimeError('Could not determine QtWebEngineCore.framework version!')

    version = versions[-1]
    helpers_src = os.path.join(framework_path, 'Helpers')
    helpers_dst = os.path.join(
        'PyQt6', 'Qt6', 'lib', 'QtWebEngineCore.framework', 'Versions', version, 'Helpers'
    )
    resources_src = os.path.join(framework_path, 'Resources')
    resources_dst = os.path.join(
        'PyQt6', 'Qt6', 'lib', 'QtWebEngineCore.framework', 'Versions', version, 'Resources'
    )

    binaries = []
    datas = []
    helper_executable = 'QtWebEngineProcess.app/Contents/MacOS/QtWebEngineProcess'

    for src_name, dest_name in collect_system_data_files(helpers_src, helpers_dst):
        if src_name.endswith(helper_executable):
            binaries.append((src_name, dest_name))
        else:
            datas.append((src_name, dest_name))

    datas.extend(collect_system_data_files(resources_src, resources_dst))
    return binaries, datas


qtwebengine_binaries, qtwebengine_datas = collect_macos_qtwebengine_files()
local_hiddenimports = collect_submodules('ui')


a = Analysis(
    ['main.py'],
    pathex=[PROJECT_ROOT],
    binaries=qtwebengine_binaries,
    datas=[('assets', 'assets'), ('icon.png', '.'), *qtwebengine_datas],
    hiddenimports=local_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=True,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Analizator Rynkowy',
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
    icon=['icon.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Analizator Rynkowy',
)
app = BUNDLE(
    coll,
    name='Analizator Rynkowy.app',
    icon='icon.icns',
    bundle_identifier='pl.bartosz.analizatorrynkowy',
    info_plist={
        'CFBundleDisplayName': config.APP_NAME,
        'CFBundleIdentifier': 'pl.bartosz.analizatorrynkowy',
        'CFBundleName': config.APP_NAME,
        'CFBundleShortVersionString': config.APP_VERSION,
        'CFBundleVersion': config.APP_VERSION,
    },
)
