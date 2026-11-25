#!/bin/zsh
# Build a self-contained Lyssna.app with PyInstaller, sign it, and write a zip (plus its
# SHA-256) to share. The app is built from lyssna-mac.spec: the Qt interface, with the original
# AppKit one inside as the fallback (interface = "legacy" under [ui] in the config file).
#   ./scripts/build_app.sh [--release] [--no-install] [arm64|x86_64]
#   arm64 (default)  dist/Lyssna-<version>-arm64.zip; in developer mode the app is also
#                    installed at /Applications/Lyssna.app
#   x86_64           Intel: dist/Lyssna-<version>-x86_64.zip (never installed)
#   --no-install     only write the zip (also LYSSNA_NO_INSTALL=1)
#   --release        public release build: implies --no-install, and never signs with an
#                    Apple-issued certificate (see Signing below)
# Signing:
#   developer mode   LYSSNA_SIGN_IDENTITY=<name or SHA-1>, else the first valid "Apple
#                    Development" or "Developer ID Application" identity, else ad hoc
#   release mode     LYSSNA_SIGN_IDENTITY (a self-signed identity such as "Lyssna Release"),
#                    else ad hoc. The build fails if the identity turns out to be Apple-issued:
#                    those certificates carry the owner's name and email.
# The intermediate .app copies are deleted afterwards so Spotlight finds only the installed one.
set -e
cd "$(dirname "$0")/.."

ARCH=arm64
RELEASE=0
INSTALL=1
[[ "${LYSSNA_NO_INSTALL:-}" == 1 ]] && INSTALL=0
for arg in "$@"; do
    case "$arg" in
        --release) RELEASE=1; INSTALL=0 ;;
        --no-install) INSTALL=0 ;;
        arm64|x86_64) ARCH=$arg ;;
        *) echo "usage: $0 [--release] [--no-install] [arm64|x86_64]" >&2; exit 2 ;;
    esac
done

VERSION=$(uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
MODE=developer
(( RELEASE )) && MODE=release
echo "Building Lyssna $VERSION ($ARCH, $MODE mode)…"
uv run python scripts/make_icon.py scripts/Lyssna.icns

if [[ "$ARCH" == arm64 ]]; then
    uv run --group build pyinstaller lyssna-mac.spec --noconfirm --clean
    APP=dist/Lyssna.app
    STAGE=build/share/Lyssna  # not dist/Lyssna: PyInstaller COLLECT owns that folder
else
    # PyInstaller can't cross-compile, so build with an x86_64 Python (run by Rosetta) in its
    # own venv. uv itself is arm64; only the Python it installs is x86_64.
    X86_PYTHON=cpython-3.11-macos-x86_64-none
    export UV_PROJECT_ENVIRONMENT=.venv-x86_64
    uv python install "$X86_PYTHON"
    uv sync --python "$X86_PYTHON" --group build
    LYSSNA_ARCH=x86_64 arch -x86_64 .venv-x86_64/bin/python -m PyInstaller lyssna-mac.spec \
        --noconfirm --clean --distpath dist/x86_64 --workpath build/x86_64
    APP=dist/x86_64/Lyssna.app
    STAGE=build/x86_64/share/Lyssna
fi

# A certificate-based signature keeps the same designated requirement across rebuilds, so
# macOS keeps the Accessibility and Input Monitoring grants. An ad hoc one changes with every
# build, so each update asks for them again.
SIGN_ID=${LYSSNA_SIGN_IDENTITY:-}
if [[ -z "$SIGN_ID" ]] && (( ! RELEASE )); then
    # Use the SHA-1 hash so duplicate names cannot be ambiguous.
    SIGN_ID=$(security find-identity -v -p codesigning \
        | grep -E 'Apple Development|Developer ID Application' | head -1 \
        | sed -E 's/^ *[0-9]+\) ([0-9A-F]+) .*/\1/' || true)
fi
if [[ -z "$SIGN_ID" ]]; then
    SIGN_ID=-
    echo "warning: no stable signing identity; signing ad hoc. macOS will ask for Accessibility and Input Monitoring again after every update." >&2
fi
codesign --force --deep --sign "$SIGN_ID" "$APP"
codesign --verify --deep --strict "$APP"
SIG_INFO=$(codesign -dvv "$APP" 2>&1)
print -r -- "$SIG_INFO" | grep -E 'Authority=|Signature=' || true
if (( RELEASE )) && print -r -- "$SIG_INFO" | grep -qE '^Authority=.*Apple|^TeamIdentifier=[^n]'; then
    # Apple-issued certificates name their owner (and, for Apple Development, his email);
    # that must never ship in a public build.
    rm -rf "$APP"
    echo "error: release builds must not be signed with an Apple-issued certificate ($SIGN_ID). Set LYSSNA_SIGN_IDENTITY to a self-signed identity, or unset it to sign ad hoc." >&2
    exit 1
fi

ZIP="dist/Lyssna-$VERSION-$ARCH.zip"
rm -rf "$STAGE" "$ZIP" "$ZIP.sha256"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/Lyssna.app"
if [[ -f docs/INSTALL.md ]]; then
    cp docs/INSTALL.md "$STAGE/Read me first.txt"
else
    echo "warning: docs/INSTALL.md is missing; the zip has no Read me first.txt" >&2
fi
ditto -c -k --keepParent "$STAGE" "$ZIP"
# The installer checks the download against this file. Bare file name, as shasum -c expects.
(cd dist && shasum -a 256 "${ZIP:t}" > "${ZIP:t}.sha256")

if [[ "$ARCH" == arm64 ]] && (( INSTALL )); then
    INSTALLED=/Applications/Lyssna.app
    rm -rf "$INSTALLED"
    ditto "$APP" "$INSTALLED"
    echo "Installed $INSTALLED"
fi
rm -rf "$APP" "$STAGE"
echo "Wrote $PWD/$ZIP"
echo "Wrote $PWD/$ZIP.sha256"
