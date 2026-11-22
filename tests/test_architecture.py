"""The shape of the code: the engine stays free of Qt, the interface's layout rules stay free of
Qt and of every platform framework, the legacy interface is reached only through host, and
the Mac and Windows twins under lyssna/mac/ and lyssna/win/ offer the same names wherever the
shared code uses them."""

import ast
import importlib.util
import pkgutil
import subprocess
import sys
import textwrap
from pathlib import Path

import lyssna.dictation
from lyssna import host

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "lyssna"
TESTS = REPO / "tests"

ENGINE = [
    "lyssna.controller",
    "lyssna.player",
    "lyssna.config",
    "lyssna.cli",
    "lyssna.keystore",
    "lyssna.policy",
    "lyssna.hotkeys",
    "lyssna.selection",
    "lyssna.gestures",
    "lyssna.providers",
    *sorted(m.name for m in pkgutil.walk_packages(lyssna.dictation.__path__, "lyssna.dictation.")),
]

NO_QT = textwrap.dedent(
    """
    import importlib, sys

    class NoQt:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in ("PySide6", "shiboken6"):
                raise ImportError(f"{name} is Qt, and the engine must not import it")
            return None

    sys.meta_path.insert(0, NoQt())
    for name in sys.argv[1:]:
        importlib.import_module(name)
    loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("PySide6", "shiboken6"))
    assert not loaded, loaded
    print("imported", len(sys.argv) - 1, "modules without Qt")
    """
)


def test_the_engine_imports_without_qt():
    result = subprocess.run([sys.executable, "-c", NO_QT, *ENGINE], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


# -- the layout rules are pure ----------------------------------------------------------------

LAYOUT = sorted(m.name for m in pkgutil.walk_packages([str(PACKAGE / "ui" / "layout")], "lyssna.ui.layout."))

NO_QT_NO_PLATFORM = textwrap.dedent(
    """
    import importlib, sys

    BLOCKED = {"PySide6", "shiboken6", "AppKit", "Foundation", "Quartz", "objc", "rumps", "AVFoundation",
               "ApplicationServices", "CoreFoundation", "Security", "ServiceManagement", "PyObjCTools",
               "comtypes", "winreg"}

    class Blocked:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ImportError(f"{name} is blocked: the layout rules must stay pure")
            return None

    # Python itself loads some of these before any code runs (winreg, for its own import
    # machinery on Windows), so only what the layout modules bring in counts.
    already = set(sys.modules)
    sys.meta_path.insert(0, Blocked())
    for name in sys.argv[1:]:
        importlib.import_module(name)
    ours = ("lyssna.mac", "lyssna.win", "lyssna.legacy")
    loaded = sorted(m for m in set(sys.modules) - already
                    if m.split(".")[0] in BLOCKED or any(m == p or m.startswith(p + ".") for p in ours))
    assert not loaded, loaded
    print("imported", len(sys.argv) - 1, "layout modules")
    """
)


def test_the_layout_rules_import_without_qt_or_a_platform():
    assert len(LAYOUT) >= 9, LAYOUT  # the scan itself is not silently empty
    result = subprocess.run([sys.executable, "-c", NO_QT_NO_PLATFORM, *LAYOUT], cwd=REPO, capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


# -- the legacy interface is reached only through host ----------------------------------------


def _resolved_imports(path: Path, package: str, *, names: bool = False) -> set[str]:
    """Every module `path` imports (relative imports resolved against `package`, the dotted
    package the file sits in). With `names`, also every string constant naming a lyssna module,
    which is how host names the modules it loads."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
        elif names and isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("lyssna."):
            found.add(node.value)
    return found


def _package_of(path: Path) -> str:
    return ".".join(path.relative_to(REPO).with_suffix("").parts[:-1])


def _reaches_legacy(path: Path, *, names: bool = False) -> bool:
    modules = _resolved_imports(path, _package_of(path), names=names)
    return any(m == "lyssna.legacy" or m.startswith("lyssna.legacy.") for m in modules)


def test_the_new_interface_never_imports_the_legacy_one():
    files = [*sorted((PACKAGE / "ui").rglob("*.py")), PACKAGE / "app.py"]
    assert not [str(p.relative_to(REPO)) for p in files if _reaches_legacy(p, names=True)]


def test_only_host_picks_the_legacy_interface():
    """In the package, host is the only file that even names lyssna.legacy; outside the legacy
    tests no test or script imports it."""
    outside = [p for p in PACKAGE.rglob("*.py") if not p.is_relative_to(PACKAGE / "legacy")]
    assert sorted(p.relative_to(REPO).as_posix() for p in outside if _reaches_legacy(p, names=True)) == ["lyssna/host.py"]
    assert host.MODULES["app"][0] == "lyssna.legacy.app"
    # scripts/compare draws the legacy interface next to the new one, so it must import it.
    allowed = (TESTS / "legacy", REPO / "scripts" / "compare")
    others = [p for p in [*TESTS.rglob("*.py"), *(REPO / "scripts").rglob("*.py")] if not any(p.is_relative_to(a) for a in allowed)]
    assert not [str(p.relative_to(REPO)) for p in others if _reaches_legacy(p)]


# -- the twins offer what the shared code uses ----------------------------------------------
#
# Approach: read the shared code with ast (nothing is imported, so this runs the same on any
# system) and collect every `<platform module>.<name>` it uses. A platform module is reached
# as `host.capture`, `host.require("mic")`, `from .host import capture`, or a name or
# attribute assigned from one of those (`_table = host.hotkey_table`, `self._events = events
# or host.events`). Then, for every neutral name that has both a Mac and a Windows module,
# each used name must be defined at the top level of both files.

SHARED_SKIP = ("mac", "win", "legacy")  # the platform code itself, and the Mac-only legacy interface


def _shared_files() -> list[Path]:
    files = [p for p in PACKAGE.rglob("*.py") if p.relative_to(PACKAGE).parts[0] not in SHARED_SKIP]
    return [p for p in files if p.name != "host.py"]


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _platform_module(node: ast.AST) -> str | None:
    """The neutral name when `node` is host.<name> or host.require("<name>")."""
    if isinstance(node, ast.Attribute) and _dotted(node.value) == "host" and node.attr in host.MODULES:
        return node.attr
    if (isinstance(node, ast.Call) and _dotted(node.func) == "host.require" and node.args
            and isinstance(node.args[0], ast.Constant) and node.args[0].value in host.MODULES):
        return node.args[0].value
    return None


def _uses(path: Path) -> set[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    aliases: dict[str, str] = {}
    assignments = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "host":
            for alias in node.names:
                if alias.name in host.MODULES:
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            options = node.value.values if isinstance(node.value, ast.BoolOp) else [node.value]  # `x or host.events`
            assignments.append((targets, options))
    changed = True
    while changed:  # until stable, so `cap = self._capture` after `self._capture = host.capture` counts too
        changed = False
        for targets, options in assignments:
            found = {name for part in options if (name := _platform_module(part) or aliases.get(_dotted(part) or ""))}
            if len(found) != 1:
                continue
            module = found.pop()
            for target in targets:
                if (dotted := _dotted(target)) is not None and aliases.get(dotted) != module:
                    aliases[dotted] = module
                    changed = True
    used = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        name = _platform_module(node.value) or aliases.get(_dotted(node.value) or "")
        if name is not None:
            used.add((name, node.attr))
    return used


def _defined(module: str) -> set[str]:
    origin = importlib.util.find_spec(module).origin
    names = set()
    for node in ast.parse(Path(origin).read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
    return names


def test_both_twins_define_what_the_shared_code_uses():
    used: dict[str, set[str]] = {}
    for path in _shared_files():
        for name, attr in _uses(path):
            used.setdefault(name, set()).add(attr)
    paired = {name: pair for name, pair in host.MODULES.items() if all(pair)}
    checked = {name: attrs for name, attrs in used.items() if name in paired}
    # Every pair the shared code reaches is seen here, so the scan itself is not silently empty.
    assert {"capture", "audio", "hotkey_table", "credentials", "policy", "autostart", "ipc", "events", "app", "instance",
            "window", "mic", "insert", "field_probe"} <= set(checked)
    missing = []
    for name, attrs in sorted(checked.items()):
        for module in paired[name]:
            defined = _defined(module)
            missing += [f"{module}.{attr}" for attr in sorted(attrs) if attr not in defined]
    assert not missing, missing


def _public(module: str) -> tuple[set[str], dict[str, set[str]]]:
    """The public top-level functions of a module, and the public methods of each public class."""
    tree = ast.parse(Path(importlib.util.find_spec(module).origin).read_text(encoding="utf-8"))
    functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}
    classes = {c.name: {f.name for f in c.body if isinstance(f, ast.FunctionDef) and not f.name.startswith("_")}
               for c in tree.body if isinstance(c, ast.ClassDef) and not c.name.startswith("_")}
    return functions, classes


def test_the_typing_and_field_probe_twins_match():
    """Dictation's typing and its correction watcher see the same operations on both systems."""
    assert _public("lyssna.mac.insert") == _public("lyssna.win.insert") == (
        {"text_target", "put_text", "snapshot", "restore_unless_changed", "paste"}, {})
    assert _public("lyssna.mac.fieldprobe") == _public("lyssna.win.fieldprobe") == (
        set(), {"FieldProbe": {"focused", "value", "same", "front_pid"}})
