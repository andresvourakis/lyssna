"""The shape of the code: the engine stays free of Qt, and the Mac and Windows twins under
lyssna/mac/ and lyssna/win/ offer the same names wherever the shared code uses them."""

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


# -- the twins offer what the shared code uses ----------------------------------------------
#
# Approach: read the shared code with ast (nothing is imported, so this runs the same on any
# system) and collect every `<platform module>.<name>` it uses. A platform module is reached
# as `host.capture`, `host.require("mic")`, `from .host import capture`, or a name or
# attribute assigned from one of those (`_table = host.hotkey_table`, `self._events = events
# or host.events`). Then, for every neutral name that has both a Mac and a Windows module,
# each used name must be defined at the top level of both files.

SHARED_SKIP = ("mac", "win", "ui")  # the platform code itself, and the interfaces (checked below)
SHARED_EXTRA = [PACKAGE / "win" / "app.py", *sorted((PACKAGE / "ui" / "qt").glob("*.py"))]  # the Qt app runs on both


def _shared_files() -> list[Path]:
    files = [p for p in PACKAGE.rglob("*.py") if p.relative_to(PACKAGE).parts[0] not in SHARED_SKIP]
    return [p for p in files if p.name not in ("app.py", "host.py")] + SHARED_EXTRA


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
    tree = ast.parse(path.read_text(), str(path))
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
    for node in ast.parse(Path(origin).read_text()).body:
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
    assert {"capture", "audio", "hotkey_table", "credentials", "policy", "autostart", "ipc", "events", "app"} <= set(checked)
    missing = []
    for name, attrs in sorted(checked.items()):
        for module in paired[name]:
            defined = _defined(module)
            missing += [f"{module}.{attr}" for attr in sorted(attrs) if attr not in defined]
    assert not missing, missing
