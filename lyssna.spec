# PyInstaller spec for the earlier Lyssna.app, which has only the legacy AppKit interface.
# scripts/build_app.sh builds lyssna-mac.spec now. This app has no Qt interface, which the Mac
# runs by default, so it starts only with interface = "legacy" under [ui] in the config file.
import os
import sys
import tomllib

import espeakng_loader
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

with open("pyproject.toml", "rb") as f:
    VERSION = tomllib.load(f)["project"]["version"]


def llama_libs():
    """llama.cpp for the cleanup model on this Mac (Apple silicon only; the Intel build has none).
    The wheel built on install ships each dylib three times (libX.dylib, libX.1.dylib,
    libX.1.0.0.dylib, all copies). llama_cpp opens libllama.dylib and libggml.dylib by name, and
    those link the others as @rpath/libX.1.dylib next to themselves (@loader_path), so only
    those two names and the .1 names are needed. The Metal shaders are compiled into
    libggml-metal; libmtmd (images, never used here) stays out."""
    try:
        import llama_cpp
    except ImportError:
        return []
    lib = os.path.join(os.path.dirname(llama_cpp.__file__), "lib")
    names = ["libllama.dylib", "libggml.dylib", "libllama.1.dylib", "libggml.1.dylib", "libggml-base.1.dylib",
             "libggml-cpu.1.dylib", "libggml-blas.1.dylib", "libggml-metal.1.dylib"]
    return [(os.path.join(lib, n), "llama_cpp/lib") for n in names if os.path.exists(os.path.join(lib, n))]


# The Qt interface's Mac twins. They load PySide6, which this build leaves out while it ships the
# legacy (AppKit) interface; they join when the Mac switches to the Qt interface.
QT_ONLY = {"lyssna.mac.window", "lyssna.mac.statusbar", "lyssna.mac.appearance"}


def host_modules() -> list[str]:
    """The Mac modules lyssna.host loads by name (lyssna/mac/ and, for the interface, the legacy
    app), read from host.MODULES itself so a new one is never left out. host imports nothing
    from the package when it loads, so this pulls in nothing else."""
    sys.path.insert(0, os.path.abspath("."))
    from lyssna import host

    names = sorted({mac for mac, _windows in host.MODULES.values() if mac} - QT_ONLY)
    print(f"lyssna.spec: modules host loads by name: {', '.join(names)}")
    return names


LYSSNA_MAC = host_modules()


a = Analysis(
    ["scripts/pyi_entry.py"],
    pathex=["."],
    # The local voice (kokoro-onnx) speaks through espeak-ng. Its dylib and data ship inside
    # the app; the data sits at a short path because espeak-ng exits the process when its
    # data path is longer than about 155 bytes.
    binaries=[*collect_dynamic_libs("espeakng_loader"), *llama_libs()],
    datas=[
        ("lyssna/ui/assets/menubar.png", "lyssna/ui/assets"),
        *collect_data_files("Quartz", subdir="ImageIO"),
        *collect_data_files("kokoro_onnx"),
        *copy_metadata("kokoro-onnx"),  # kokoro_onnx reads its own version at import
        (espeakng_loader.get_data_path(), "espeak-ng-data"),
        # Dictation: the common-words list the vocabulary weighs corrections against, and its
        # license; onnx-asr's audio preprocessors (small ONNX files) and its version metadata.
        ("lyssna/dictation/data/common_words.txt", "lyssna/dictation/data"),
        ("lyssna/dictation/data/common_words.LICENSE", "lyssna/dictation/data"),
        *collect_data_files("onnx_asr"),
        *copy_metadata("onnx-asr"),
        ("LICENSE", "."),
        ("THIRD_PARTY_NOTICES.md", "."),
    ],
    # The Keychain module imports Security lazily, and lyssna.host loads the app and the Mac
    # modules by name, so the analysis cannot see them by itself. The Mac build ships the legacy
    # (AppKit) interface until the Qt one in lyssna/app.py replaces it.
    hiddenimports=["Security", *LYSSNA_MAC],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lyssna",
    console=False,
    upx=False,
    target_arch=os.environ.get("LYSSNA_ARCH", "arm64"),
)

coll = COLLECT(exe, a.binaries, a.datas, name="Lyssna", upx=False)

app = BUNDLE(
    coll,
    name="Lyssna.app",
    icon="scripts/Lyssna.icns",
    bundle_identifier="io.github.andresvourakis.lyssna",
    version=VERSION,
    info_plist={
        "CFBundleName": "Lyssna",
        "CFBundleDisplayName": "Lyssna",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        # onnxruntime's Apple silicon wheel needs macOS 14; the Intel build pins an older one.
        "LSMinimumSystemVersion": "13.0" if os.environ.get("LYSSNA_ARCH") == "x86_64" else "14.0",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSMicrophoneUsageDescription":
            "Lyssna listens to the microphone only while you dictate, to turn your speech into text.",
    },
)
