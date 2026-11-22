# PyInstaller spec for Lyssna.app with the Qt interface (lyssna/app.py, lyssna/ui/) as its only
# interface. It replaces lyssna.spec when the Mac switches over; until then scripts/build_app.sh
# still builds lyssna.spec. Build from the repository root:
#   uv run python scripts/make_icon.py scripts/Lyssna.icns
#   uv run --group build pyinstaller lyssna-mac.spec --noconfirm --clean
# then sign and zip as scripts/build_app.sh does (codesign --force --deep, ditto -c -k --keepParent).
#
# What differs from lyssna.spec:
#   * The legacy AppKit interface (lyssna/legacy/) and rumps are left out, and a run-time hook sets
#     LYSSNA_INTERFACE=new before the app starts, so lyssna.host always picks lyssna/app.py
#     whatever the config file says.
#   * PySide6 is in, trimmed to what the interface uses (see TRIM below). QtQuick, QtQml and the
#     other modules PyInstaller's Qt hooks drag in are dropped from the bundle, because nothing
#     in Lyssna links or imports them.
#   * The bundled fonts in lyssna/ui/assets/fonts/ stay out: they are for Windows only. On the Mac
#     lyssna/mac/appearance.py uses Apple's system fonts.
#
# Build check (not for a release): LYSSNA_CHECK_ENTRY=<path to a script> adds a second program,
# Contents/MacOS/lyssna-check, built from that script and sharing the bundle, so a script can run
# lyssna.app's demo or a self-check inside the frozen app. The release build never sets it.
import ast
import os
import subprocess
import sys
import tomllib

import espeakng_loader
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

ROOT = SPECPATH  # noqa: F821  (PyInstaller defines SPECPATH while it runs a spec)
ARCH = os.environ.get("LYSSNA_ARCH", "arm64")

with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
    VERSION = tomllib.load(f)["project"]["version"]


def llama_libs():
    """llama.cpp for the cleanup model, the same handpicked dylibs as lyssna.spec (see llama_libs()
    there): Apple silicon only, the Intel build has none."""
    try:
        import llama_cpp
    except ImportError:
        print("lyssna-mac.spec: llama_cpp is not installed; the cleanup model is NOT bundled")
        return []
    lib = os.path.join(os.path.dirname(llama_cpp.__file__), "lib")
    names = ["libllama.dylib", "libggml.dylib", "libllama.1.dylib", "libggml.1.dylib", "libggml-base.1.dylib",
             "libggml-cpu.1.dylib", "libggml-blas.1.dylib", "libggml-metal.1.dylib"]
    return [(os.path.join(lib, n), "llama_cpp/lib") for n in names if os.path.exists(os.path.join(lib, n))]


def host_modules() -> list[str]:
    """Every module lyssna.host loads by name on a Mac, read from host.MODULES itself so a new one
    is never left out. The interface is lyssna.app, not the legacy one in the Mac column. host
    imports nothing from the package when it loads, so this pulls in nothing else."""
    sys.path.insert(0, ROOT)
    from lyssna import host

    names = {mac for name, (mac, _windows) in host.MODULES.items() if mac and name != "app"}
    names.add(host.MODULES["app"][1])  # lyssna.app, the Qt interface
    print(f"lyssna-mac.spec: modules host loads by name: {', '.join(sorted(names))}")
    return sorted(names)


def interface_modules() -> list[str]:
    """The interface modules lyssna/app.py imports inside functions (`from .ui.x import ...` and
    `from .ui import x`), read from its source so the list cannot go stale (as in lyssna-win.spec)."""
    with open(os.path.join(ROOT, "lyssna", "app.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            if node.module == "ui":
                names.update(f"lyssna.ui.{alias.name}" for alias in node.names)
            elif node.module.startswith("ui."):
                names.add(f"lyssna.{node.module}")
    print(f"lyssna-mac.spec: interface modules lyssna/app.py imports: {', '.join(sorted(names))}")
    return sorted(names)


def interface_hook() -> str:
    """A run-time hook that makes the frozen app run the Qt interface. lyssna.host picks the legacy
    interface on a Mac unless LYSSNA_INTERFACE=new; this bundle has no legacy interface, so the
    variable is set, not defaulted. Written into PyInstaller's work folder."""
    path = os.path.join(workpath, "lyssna_interface_hook.py")  # noqa: F821  (PyInstaller defines workpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as out:
        out.write('import os\n\nos.environ["LYSSNA_INTERFACE"] = "new"  # this bundle has only the Qt interface\n')
    return path


LYSSNA_MAC = host_modules()
LYSSNA_QT = interface_modules()
CHECK_ENTRY = os.environ.get("LYSSNA_CHECK_ENTRY")

# Qt modules the app never imports. PyInstaller only bundles what is imported, so this list guards
# against one of them being pulled in by a dependency (the same list as lyssna-win.spec).
QT_UNUSED = ["PySide6." + m for m in (
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtWebView",
    "QtQuick", "QtQuickWidgets", "QtQuickControls2", "QtQuick3D", "QtQml", "QtMultimediaQuick",
    "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation", "Qt3DExtras",
    "QtCharts", "QtDataVisualization", "QtGraphs", "QtPdf", "QtPdfWidgets", "QtTest", "QtDesigner", "QtHelp",
)]

# Qt Multimedia (with its FFmpeg libraries, about 10 MB of the zip) is what the shared Qt player in
# lyssna/win/audio.py and the Qt microphone in lyssna/win/mic.py use. On the Mac today host.audio
# and host.mic are lyssna/mac/audio.py (AVAudioPlayer) and lyssna/mac/mic.py (AVAudioEngine), so
# nothing in this bundle imports it. It stays in until the plan decides (Stage 6, by measurement)
# whether the Mac moves to the shared player; set this to False to leave it out.
QT_MULTIMEDIA = False  # the Mac plays and records through Apple's frameworks (lyssna/mac/audio.py, mic.py); saves about 10 MB

# Files PyInstaller's Qt hooks bundle that Lyssna never uses, under Contents/Frameworks. Nothing
# kept links any of them (qt_trim checks that with otool), and each kind was removed from a built
# app that then still started, drew with Apple's fonts, loaded menubar.png and, with Qt Multimedia
# in, made a QMediaPlayer that loaded a WAV file and a QAudioSource.
QT_LIB = "PySide6/Qt/lib/"
QT_PLUGINS = "PySide6/Qt/plugins/"
QT_DROP = [
    # Qt Quick and QML, the virtual keyboard built on them, and QtOpenGL, which only those link.
    *(f"{QT_LIB}{m}.framework" for m in ("QtQuick", "QtQml", "QtQmlMeta", "QtQmlModels", "QtQmlWorkerScript",
                                          "QtVirtualKeyboard", "QtVirtualKeyboardQml", "QtOpenGL")),
    f"{QT_PLUGINS}platforminputcontexts",  # the virtual keyboard's plugin
    # Qt's own translations: Lyssna loads no QTranslator, its text is its own.
    "PySide6/Qt/translations",
    # Plugins for things Lyssna does not do: TLS and network status (it downloads through httpx),
    # touch input over the network, SVG icons, and the headless platforms (the app runs on cocoa).
    f"{QT_PLUGINS}tls", f"{QT_PLUGINS}networkinformation", f"{QT_PLUGINS}generic", f"{QT_PLUGINS}iconengines",
    f"{QT_PLUGINS}platforms/libqminimal.dylib", f"{QT_PLUGINS}platforms/libqoffscreen.dylib",
    # Image format plugins and QtSvg, which only the SVG plugins link. The interface draws its
    # icons in code and loads one PNG (menubar.png), and PNG is built into QtGui.
    f"{QT_PLUGINS}imageformats", f"{QT_LIB}QtSvg.framework",
    # Qt Multimedia's widgets, which PyInstaller's QtMultimedia hook adds and nothing uses.
    f"{QT_LIB}QtMultimediaWidgets.framework", "PySide6/QtMultimediaWidgets.abi3.so",
]


def qt_trim(toc, drop: list[str]):
    """The TOC without the files under `drop` and without the links that point at them. Fails the
    build when a Qt binary that stays links one of the dropped libraries (a newer PySide6 could
    start to), because that app would not start."""
    def dropped(path: str) -> bool:
        return any(path == d or path.startswith(d + "/") for d in drop)

    kept, gone = [], 0
    for dest, src, kind in toc:
        target = os.path.normpath(os.path.join(os.path.dirname(dest), src)) if kind == "SYMLINK" else None
        if dropped(dest) or (target and dropped(target)):
            gone += os.path.getsize(src) if kind != "SYMLINK" and os.path.isfile(src) else 0
            continue
        kept.append((dest, src, kind))
    gone_libs = {os.path.basename(d).removesuffix(".framework") for d in drop if d.startswith(QT_LIB)}
    for dest, src, kind in kept:
        if kind in ("BINARY", "EXTENSION") and dest.startswith(("PySide6/", "shiboken6/")):
            links = subprocess.run(["otool", "-L", src], capture_output=True, text=True, check=True).stdout
            needed = {line.split()[0].removeprefix("@rpath/").split("/")[0].removesuffix(".framework")
                      for line in links.splitlines()[1:]}
            if needed & gone_libs:
                raise SystemExit(f"lyssna-mac.spec: {dest} links {sorted(needed & gone_libs)}, which QT_DROP leaves out")
    print(f"lyssna-mac.spec: left out {len(toc) - len(kept)} unused Qt files and links ({gone / 1e6:.1f} MB)")
    return kept


a = Analysis(
    ["scripts/pyi_entry.py", *([CHECK_ENTRY] if CHECK_ENTRY else [])],
    pathex=[ROOT],
    # Kokoro speaks through espeak-ng; its dylib and data ship inside, the data at a short path
    # (espeak-ng exits the process when its data path is longer than about 155 bytes).
    binaries=[*collect_dynamic_libs("espeakng_loader"), *llama_libs()],
    datas=[
        ("lyssna/ui/assets/menubar.png", "lyssna/ui/assets"),
        *collect_data_files("Quartz", subdir="ImageIO"),
        *collect_data_files("kokoro_onnx"),
        *copy_metadata("kokoro-onnx"),  # kokoro_onnx reads its own version at import
        (espeakng_loader.get_data_path(), "espeak-ng-data"),
        # Dictation: the common-words list the vocabulary weighs corrections against, and its
        # licence; onnx-asr's audio preprocessors and its version metadata.
        ("lyssna/dictation/data/common_words.txt", "lyssna/dictation/data"),
        ("lyssna/dictation/data/common_words.LICENSE", "lyssna/dictation/data"),
        *collect_data_files("onnx_asr"),
        *copy_metadata("onnx-asr"),
        ("LICENSE", "."),
        ("THIRD_PARTY_NOTICES.md", "."),
    ],
    # The Keychain module imports Security lazily, lyssna.host loads the app and the Mac modules by
    # name, and lyssna/app.py imports the interface inside functions, so the analysis cannot see
    # these by itself.
    hiddenimports=["Security", *LYSSNA_MAC, *LYSSNA_QT, *(["PySide6.QtMultimedia"] if QT_MULTIMEDIA else [])],
    excludes=["lyssna.legacy", "rumps", "tkinter", "PySide6.QtMultimediaWidgets", *QT_UNUSED,
              *([] if QT_MULTIMEDIA else ["PySide6.QtMultimedia"])],
    runtime_hooks=[interface_hook()],
    noarchive=False,
)
a.binaries = qt_trim(a.binaries, QT_DROP)
a.datas = qt_trim(a.datas, QT_DROP)
pyz = PYZ(a.pure)

app_script = [s for s in a.scripts if s[0] == "pyi_entry"]
check_script = [s for s in a.scripts if CHECK_ENTRY and s[0] == os.path.splitext(os.path.basename(CHECK_ENTRY))[0]]
runtime = [s for s in a.scripts if s not in app_script and s not in check_script]  # the run-time hooks
programs = [EXE(pyz, runtime + app_script, [], exclude_binaries=True, name="Lyssna", console=False, upx=False,
                target_arch=ARCH)]
if CHECK_ENTRY:
    print(f"lyssna-mac.spec: build check: adding lyssna-check from {CHECK_ENTRY}")
    programs.append(EXE(pyz, runtime + check_script, [], exclude_binaries=True, name="lyssna-check", console=True,
                        upx=False, target_arch=ARCH))

coll = COLLECT(*programs, a.binaries, a.datas, name="Lyssna", upx=False)

app = BUNDLE(
    coll,
    name="Lyssna.app",
    icon=os.path.join(ROOT, "scripts", "Lyssna.icns"),
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
        "LSMinimumSystemVersion": "13.0" if ARCH == "x86_64" else "14.0",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSMicrophoneUsageDescription":
            "Lyssna listens to the microphone only while you dictate, to turn your speech into text.",
    },
)
