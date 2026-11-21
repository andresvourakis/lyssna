# PyInstaller spec for the Windows app. Built on Windows by .github/workflows/windows-build.yml:
#   uv run python scripts/make_icon_win.py
#   uv run pyinstaller lyssna-win.spec --noconfirm
#
# One folder, dist/Lyssna/, with two programs sharing it:
#   Lyssna.exe      the app (windowed). The Start menu shortcut and start at login run this.
#   lyssna-cli.exe  the terminal command (a console program). With no arguments it starts
#                   Lyssna.exe and returns; otherwise it talks to the running app over the pipe.
# The approach (espeak data, comtypes wrappers made at build time, Qt Multimedia's FFmpeg DLLs,
# hidden imports for the modules lyssna/host.py loads by name) is the one lyssna-probe.spec
# proved on GitHub's Windows machine.
import ast
import glob
import os
import re
import sys
import tomllib

import espeakng_loader
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

ROOT = SPECPATH  # noqa: F821  (PyInstaller defines SPECPATH while it runs a spec)
ICON = os.path.join(ROOT, "scripts", "Lyssna.ico")

with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
    PROJECT = tomllib.load(f)["project"]
VERSION = PROJECT["version"]
AUTHOR = PROJECT["authors"][0]["name"]
LICENSE = PROJECT["license"]

if not os.path.exists(ICON):
    raise SystemExit(f"lyssna-win.spec: {ICON} is missing. Run: uv run python scripts/make_icon_win.py")


def pregenerate_comtypes():
    """comtypes writes Python wrappers for COM type libraries into comtypes.gen the first time
    they are used (UI Automation in lyssna/win/capture.py, SAPI in lyssna/providers/windows.py).
    A frozen app cannot write into its folder, so the wrappers are generated here, on the build
    machine, and bundled (the same as lyssna-probe.spec). A failure is printed and not fatal:
    the app then generates them at run time in a cache folder."""
    if sys.platform != "win32":
        return []
    import comtypes.client

    system = os.environ.get("SystemRoot", r"C:\Windows")
    targets = [
        ("UI Automation", "UIAutomationCore.dll"),
        ("SAPI", os.path.join(system, "System32", "Speech", "Common", "sapi.dll")),
        ("SAPI (by id)", ("{C866CA3A-32F7-11D2-9602-00C04F8EE628}", 5, 4)),
    ]
    for label, tlib in targets:
        try:
            module = comtypes.client.GetModule(tlib)
            print(f"lyssna-win.spec: comtypes wrapper for {label}: {module.__name__}")
        except Exception as e:
            print(f"lyssna-win.spec: WARNING could not generate the comtypes wrapper for {label}: {type(e).__name__}: {e}")
    generated = collect_submodules("comtypes.gen")
    print(f"lyssna-win.spec: bundling {len(generated)} comtypes.gen modules: {', '.join(generated)}")
    return generated


def ffmpeg_dlls():
    """Qt Multimedia's FFmpeg backend loads these DLLs from the PySide6 folder. PyInstaller's
    dependency scan usually finds them; listing them makes sure."""
    if sys.platform != "win32":
        return []
    import PySide6

    root = os.path.dirname(PySide6.__file__)
    names = ("avcodec-*.dll", "avformat-*.dll", "avutil-*.dll", "swresample-*.dll", "swscale-*.dll")
    found = [(path, "PySide6") for pattern in names for path in glob.glob(os.path.join(root, pattern))]
    print(f"lyssna-win.spec: bundling {len(found)} FFmpeg DLLs: {', '.join(os.path.basename(p) for p, _ in found)}")
    return found


def version_file(name: str, original: str) -> str:
    """Write the Windows version resource for one program (file and product version, product
    name, description, author and licence) and return its path. PyInstaller reads this text
    form; the numeric version takes up to four numbers from pyproject.toml's version."""
    numbers = [int(n) for n in re.findall(r"\d+", VERSION)[:4]]
    numbers += [0] * (4 - len(numbers))
    nums = tuple(numbers)
    strings = [
        ("CompanyName", AUTHOR),
        ("FileDescription", "Lyssna"),
        ("FileVersion", VERSION),
        ("InternalName", name),
        ("LegalCopyright", f"Copyright (C) {AUTHOR}. Licensed under {LICENSE}."),
        ("OriginalFilename", original),
        ("ProductName", "Lyssna"),
        ("ProductVersion", VERSION),
        ("Comments", f"Licence: {LICENSE}"),
    ]
    table = ",\n          ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings)
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
          {table}
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    folder = os.path.join(ROOT, "build")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"version-{name}.txt")
    with open(path, "w", encoding="utf-8") as out:
        out.write(text)
    print(f"lyssna-win.spec: version resource for {original}: {VERSION} {nums}")
    return path


def host_modules() -> list[str]:
    """Every module lyssna/host.py can load by name on Windows (the app and the Windows
    plumbing), read from host.MODULES itself so a new one is never left out. host imports
    nothing from the package when it loads, so this pulls in nothing else."""
    sys.path.insert(0, ROOT)
    from lyssna import host

    names = sorted({windows for _mac, windows in host.MODULES.values() if windows})
    print(f"lyssna-win.spec: modules host loads by name: {', '.join(names)}")
    return names


def interface_modules() -> list[str]:
    """The interface modules lyssna/app.py imports inside functions (`from .ui.x import ...` and
    `from .ui import x`), read from its source so the list cannot go stale."""
    with open(os.path.join(ROOT, "lyssna", "app.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            if node.module == "ui":
                names.update(f"lyssna.ui.{alias.name}" for alias in node.names)
            elif node.module.startswith("ui."):
                names.add(f"lyssna.{node.module}")
    print(f"lyssna-win.spec: interface modules lyssna/app.py imports: {', '.join(sorted(names))}")
    return sorted(names)


LYSSNA_WIN = host_modules()
LYSSNA_QT = interface_modules()

# Qt modules the app never imports. PyInstaller only bundles what is imported, so this list
# guards against one of them being pulled in by a dependency. QtNetwork, QtGui and QtCore stay:
# Qt Multimedia needs them.
QT_UNUSED = ["PySide6." + m for m in (
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtWebView",
    "QtQuick", "QtQuickWidgets", "QtQuickControls2", "QtQuick3D", "QtQml", "QtMultimediaQuick",
    "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation", "Qt3DExtras",
    "QtCharts", "QtDataVisualization", "QtGraphs", "QtPdf", "QtPdfWidgets", "QtTest", "QtDesigner", "QtHelp",
)]

a = Analysis(
    ["scripts/pyi_entry.py", "scripts/pyi_entry_cli.py"],
    pathex=[".", "scripts"],
    # Kokoro speaks through espeak-ng; its library and data ship inside, at a short path
    # (espeak-ng exits the process when its data path is too long).
    binaries=[*collect_dynamic_libs("espeakng_loader"), *ffmpeg_dlls()],
    datas=[
        ("lyssna/ui/assets/menubar.png", "lyssna/ui/assets"),
        ("lyssna/ui/assets/fonts", "lyssna/ui/assets/fonts"),  # Inter and Literata, with their licences
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
    # lyssna.host loads the Windows modules by name, the interface is imported inside functions,
    # and comtypes builds its wrappers at run time, so the analysis cannot see these by itself.
    hiddenimports=[
        *LYSSNA_WIN,
        *LYSSNA_QT,
        "lyssna.providers.windows",
        "lyssna.providers.kokoro",
        "PySide6.QtMultimedia",
        *collect_submodules("comtypes", filter=lambda name: ".test" not in name),
        *pregenerate_comtypes(),
    ],
    excludes=["llama_cpp", "rumps", "AppKit", "Quartz", "objc", "Foundation", "tkinter", *QT_UNUSED],
    noarchive=False,
)
pyz = PYZ(a.pure)

# a.scripts holds both entry points; each program gets only its own.
app_script = [s for s in a.scripts if s[0] == "pyi_entry"]
cli_script = [s for s in a.scripts if s[0] == "pyi_entry_cli"]
runtime = [s for s in a.scripts if s[0] not in ("pyi_entry", "pyi_entry_cli")]  # PyInstaller's own run-time hooks
if len(app_script) != 1 or len(cli_script) != 1:
    raise SystemExit(f"lyssna-win.spec: could not find both entry points in a.scripts: {[s[0] for s in a.scripts]}")
print(f"lyssna-win.spec: run-time scripts shared by both programs: {[s[0] for s in runtime]}")

app = EXE(pyz, runtime + app_script, [], exclude_binaries=True, name="Lyssna", console=False, upx=False,
          icon=ICON, version=version_file("Lyssna", "Lyssna.exe"))
cli = EXE(pyz, runtime + cli_script, [], exclude_binaries=True, name="lyssna-cli", console=True, upx=False,
          icon=ICON, version=version_file("lyssna-cli", "lyssna-cli.exe"))

coll = COLLECT(app, cli, a.binaries, a.datas, name="Lyssna", upx=False)
