# PyInstaller spec for LyssnaProbe, the Windows probe (scripts/probe_win.py). Built on Windows
# by .github/workflows/windows-probe.yml with:  uv run pyinstaller lyssna-probe.spec --noconfirm
#
# One folder, two programs sharing it: LyssnaProbe.exe (windowed, the guided check the owner
# runs) and LyssnaProbeConsole.exe (a console, so --self-test prints into the workflow log).
import glob
import os
import sys

import espeakng_loader
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata


def pregenerate_comtypes():
    """comtypes writes Python wrappers for COM type libraries into comtypes.gen the first time
    they are used (UI Automation in lyssna/win/capture.py, SAPI in lyssna/providers/windows.py).
    A frozen app cannot write into its bundle, so comtypes would fall back to a cache folder
    under %TEMP% at run time. That works, but costs seconds on first use and depends on the
    temp folder, so the wrappers are generated here, on the build machine, and bundled. In a
    frozen app comtypes skips the type library date check, so wrappers made on another PC load.
    A failure here is printed and not fatal: the app then generates them at run time."""
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
            print(f"lyssna-probe.spec: comtypes wrapper for {label}: {module.__name__}")
        except Exception as e:
            print(f"lyssna-probe.spec: WARNING could not generate the comtypes wrapper for {label}: {type(e).__name__}: {e}")
    generated = collect_submodules("comtypes.gen")
    print(f"lyssna-probe.spec: bundling {len(generated)} comtypes.gen modules: {', '.join(generated)}")
    return generated


def ffmpeg_dlls():
    """Qt Multimedia's FFmpeg backend loads these DLLs from the PySide6 folder. PyInstaller's
    dependency scan usually finds them; listing them makes sure."""
    if sys.platform != "win32":
        return []
    import PySide6

    root = os.path.dirname(PySide6.__file__)
    names = ("avcodec-*.dll", "avformat-*.dll", "avutil-*.dll", "swresample-*.dll", "swscale-*.dll")
    return [(path, "PySide6") for pattern in names for path in glob.glob(os.path.join(root, pattern))]


LYSSNA_WIN = ["lyssna.win." + m for m in ("app", "audio", "autostart", "capture", "clipboard", "credentials",
                                           "events", "hotkeys", "ipc", "policy")]
PROBE = ["probe." + m for m in ("main", "selftest", "steps", "flow", "ui", "backend", "winapi", "analysis", "report")]

a = Analysis(
    ["scripts/probe_win.py"],
    pathex=[".", "scripts"],
    # Kokoro speaks through espeak-ng; its library and data ship inside, as in lyssna.spec.
    binaries=[*collect_dynamic_libs("espeakng_loader"), *ffmpeg_dlls()],
    datas=[
        *collect_data_files("kokoro_onnx"),
        *copy_metadata("kokoro-onnx"),  # kokoro_onnx reads its own version at import
        (espeakng_loader.get_data_path(), "espeak-ng-data"),
    ],
    # lyssna.host loads the Windows modules by name, and comtypes builds its wrappers at run
    # time, so the analysis cannot see these by itself.
    hiddenimports=[
        *LYSSNA_WIN,
        "lyssna.providers.windows",
        "lyssna.providers.kokoro",
        *PROBE,
        "PySide6.QtMultimedia",
        *collect_submodules("comtypes", filter=lambda name: ".test" not in name),
        *pregenerate_comtypes(),
    ],
    excludes=["llama_cpp", "onnx_asr", "rumps", "AppKit", "Quartz", "objc"],
    noarchive=False,
)
pyz = PYZ(a.pure)

windowed = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LyssnaProbe", console=False, upx=False)
console = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LyssnaProbeConsole", console=True, upx=False)

coll = COLLECT(windowed, console, a.binaries, a.datas, name="LyssnaProbe", upx=False)
