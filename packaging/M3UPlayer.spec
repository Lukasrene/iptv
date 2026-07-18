# PyInstaller spec for the standalone macOS app.
#
# Build with ./build_app.sh — it uses the same out-of-repo virtualenv as run.sh.
#
# The bundle carries everything the app needs: the Python runtime, Qt (including
# the FFmpeg media backend), and an ffmpeg binary for the DVR recorder. There is
# no VLC dependency and nothing for the user to install.
import os

from PyInstaller.utils.hooks import collect_dynamic_libs

import imageio_ffmpeg

block_cipher = None

# The DVR recorder and the thumbnailer shell out to this binary. imageio_ffmpeg
# ships it inside the wheel; carry it into the bundle and resolve it at runtime
# through _bundled_ffmpeg() in recorder.py.
_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

a = Analysis(
    ["../m3u_player/main.py"],
    pathex=[".."],
    binaries=[(_ffmpeg, "imageio_ffmpeg/binaries")],
    datas=[],
    # PySide6's hooks do not reliably collect the multimedia plugins, and the
    # FFmpeg one is exactly what replaced VLC — without it the bundle launches
    # and then cannot play anything.
    hiddenimports=[
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtNetwork",
        "PySide6.QtSvg",
    ],
    hookspath=[],
    runtime_hooks=[],
    # Trim the parts of Qt this app never touches; they add hundreds of MB.
    excludes=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore",
        "PySide6.QtQuick3D",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "tkinter",
        "matplotlib",
        "numpy",
        "pytest",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="M3U Player",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="M3U Player",
)

app = BUNDLE(
    coll,
    name="M3U Player.app",
    icon=None,
    bundle_identifier="com.lukaspapenfuss.m3uplayer",
    info_plist={
        "CFBundleName": "M3U Player",
        "CFBundleDisplayName": "M3U Player",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "NSHighResolutionCapable": True,
        # Playback and the DVR relay must keep running with the window in the
        # background, e.g. while casting to a TV.
        "LSBackgroundOnly": False,
        "LSMinimumSystemVersion": "11.0",
        # The relay listens on loopback and the LAN, and Chromecast discovery is
        # mDNS on the local network. macOS prompts for this on first use.
        "NSLocalNetworkUsageDescription":
            "M3U Player streams recorded video to Chromecast devices on your "
            "local network.",
        "NSBonjourServices": ["_googlecast._tcp"],
    },
)
