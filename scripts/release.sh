#!/bin/zsh
# Build the public release zips (arm64 and x86_64) in release mode, without installing, and
# print the gh command that publishes them. It never tags, pushes or publishes by itself:
# signing happens on this Mac, and publishing stays a deliberate step.
#   LYSSNA_SIGN_IDENTITY=<name or SHA-1>  self-signed release identity (ad hoc if unset)
set -e
cd "$(dirname "$0")/.."
REPO=andresvourakis/lyssna  # the public GitHub repo; install.sh has the same value

VERSION=$(uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
TAG="v$VERSION"  # install.sh derives the version from this tag, so keep the v prefix

if [[ -z "${LYSSNA_SIGN_IDENTITY:-}" ]]; then
    echo "warning: LYSSNA_SIGN_IDENTITY is not set, so this release is signed ad hoc. Everyone who updates to it will have to grant Accessibility again." >&2
fi
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "warning: the working tree has uncommitted changes; the release is built from them." >&2
fi

./scripts/build_app.sh --release arm64
./scripts/build_app.sh --release x86_64

ASSETS=()
for arch in arm64 x86_64; do
    ASSETS+=("dist/Lyssna-$VERSION-$arch.zip" "dist/Lyssna-$VERSION-$arch.zip.sha256")
done
for f in $ASSETS; do
    [[ -f "$f" ]] || { echo "error: $f is missing" >&2; exit 1; }
done

echo
echo "Built Lyssna $VERSION. To publish, push main, then run:"
echo
echo "  gh release create $TAG --repo $REPO --target main --title \"Lyssna $VERSION\" --generate-notes \\"
echo "    ${ASSETS[*]}"
echo
echo "This creates the $TAG tag on main if it does not exist yet."
