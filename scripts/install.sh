#!/bin/sh
# Install or update Lyssna, the menu bar app that reads selected text aloud.
#   curl -fsSL https://raw.githubusercontent.com/andresvourakis/lyssna/main/scripts/install.sh | sh
# Run it again at any time to update. Lyssna is not notarized by Apple. A file that curl
# downloads carries no quarantine flag, so the app opens without the "cannot verify" detour
# that a browser download goes through.
# Overrides, mainly for testing:
#   LYSSNA_ZIP_URL      zip to install instead of the latest release (file:// works)
#   LYSSNA_SHA256       expected SHA-256 of that zip (default: fetched from <zip url>.sha256)
#   LYSSNA_INSTALL_DIR  folder to install into (default /Applications, else ~/Applications)
#   LYSSNA_NO_OPEN=1    do not open the app afterwards
# POSIX sh on purpose: it runs on the stock shell of every supported macOS.

REPO=andresvourakis/lyssna  # the public GitHub repo; scripts/release.sh has the same value

# Everything lives in main(), called on the last line, so a download cut short by the
# network runs nothing at all.
main() {
    set -eu
    APP_NAME=Lyssna.app

    [ "$(uname -s)" = Darwin ] || die "Lyssna runs on macOS only."

    # uname -m says x86_64 when this shell runs under Rosetta on Apple silicon; ask the
    # hardware instead.
    if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = 1 ]; then
        arch=arm64
    else
        arch=x86_64
    fi

    # The minimum matches LSMinimumSystemVersion in lyssna-mac.spec: macOS 14 on both chips
    # (the interface's Qt library needs it).
    if [ "$arch" = arm64 ]; then
        need=14; need_name="macOS 14 (Sonoma)"; chip="Apple silicon"
    else
        need=14; need_name="macOS 14 (Sonoma)"; chip="Intel"
    fi
    macos=$(sw_vers -productVersion)
    major=${macos%%.*}
    [ "$major" -ge "$need" ] 2>/dev/null \
        || die "Lyssna needs $need_name or later on an $chip Mac. This Mac has macOS $macos. Update macOS in System Settings > General > Software Update, then run this again."

    tmp=$(mktemp -d "${TMPDIR:-/tmp}/lyssna-install.XXXXXX")
    trap 'rm -rf "$tmp"' EXIT
    trap 'exit 1' HUP INT TERM

    zip_url=${LYSSNA_ZIP_URL:-}
    if [ -z "$zip_url" ]; then
        # The latest-release page redirects to .../releases/tag/v<version>. Reading the
        # redirect avoids the GitHub API and its rate limit.
        say "Looking up the latest Lyssna release..."
        latest=$(curl -fsSLI -o /dev/null -w '%{url_effective}' \
            "https://github.com/$REPO/releases/latest") \
            || die "Could not reach https://github.com/$REPO/releases. Check your internet connection and try again."
        tag=${latest##*/}
        case "$tag" in
            v[0-9]*) ;;
            *) die "No Lyssna release is published yet at https://github.com/$REPO/releases." ;;
        esac
        version=${tag#v}
        zip_url="https://github.com/$REPO/releases/download/$tag/Lyssna-$version-$arch.zip"
    fi
    zip_name=${zip_url##*/}

    say "Downloading $zip_name..."
    curl -fsSL -o "$tmp/$zip_name" "$zip_url" \
        || die "Could not download $zip_url. The release may not include a build for this Mac ($arch)."

    expected=${LYSSNA_SHA256:-}
    if [ -z "$expected" ]; then
        curl -fsSL -o "$tmp/sum" "$zip_url.sha256" \
            || die "Could not download the checksum file $zip_url.sha256."
        expected=$(awk '{print $1; exit}' "$tmp/sum")
    fi
    actual=$(shasum -a 256 "$tmp/$zip_name" | awk '{print $1}')
    expected=$(printf '%s' "$expected" | tr 'A-F' 'a-f')
    [ "$actual" = "$expected" ] \
        || die "The download is damaged or was changed (SHA-256 $actual, expected $expected). Nothing was installed."

    mkdir "$tmp/unzip"
    ditto -x -k "$tmp/$zip_name" "$tmp/unzip" || die "Could not unzip $zip_name."
    # The release zip holds a Lyssna folder with the app and a read-me; accept a bare app too.
    src=
    for candidate in "$tmp/unzip/$APP_NAME" "$tmp/unzip"/*/"$APP_NAME"; do
        if [ -d "$candidate" ]; then src=$candidate; break; fi
    done
    [ -n "$src" ] || die "$zip_name does not contain $APP_NAME."
    codesign --verify --deep --strict "$src" 2>/dev/null \
        || die "The app in $zip_name has a broken signature. Nothing was installed."

    # /Applications is writable for admin users. Everyone else gets ~/Applications, which
    # Finder and Spotlight treat the same way. Never sudo.
    dir=${LYSSNA_INSTALL_DIR:-}
    if [ -z "$dir" ]; then
        if [ -w /Applications ]; then dir=/Applications; else dir=$HOME/Applications; fi
    fi
    mkdir -p "$dir" || die "Could not create $dir."
    [ -w "$dir" ] || die "Cannot write to $dir."
    dest=$dir/$APP_NAME

    quit_running "$dest"

    # Copy next to the destination first, then swap, so a failed copy leaves the old app.
    staging=$dir/.Lyssna-installing.app
    rm -rf "$staging"
    ditto "$src" "$staging" || { rm -rf "$staging"; die "Could not copy Lyssna into $dir."; }
    rm -rf "$dest"
    mv "$staging" "$dest"
    # curl downloads are not quarantined, but a zip that came another way might be.
    xattr -dr com.apple.quarantine "$dest" 2>/dev/null || true

    say "Installed $dest"
    if [ "${LYSSNA_NO_OPEN:-}" != 1 ]; then
        open "$dest"
    fi
    say ""
    say "Next:"
    say "1. When Lyssna asks, turn on Accessibility for it in System Settings, then click Relaunch Lyssna."
    say "2. Click the speech bubble in the menu bar and pick a voice under Voice."
    say "3. Select text anywhere and press Control-Option-S to hear it."
}

# Quit any Lyssna running from the bundle about to be replaced, and only that one, so a copy
# installed elsewhere keeps running.
quit_running() {
    exe=$1/Contents/MacOS/Lyssna
    pids=$(ps -axo pid=,comm= | awk -v exe="$exe" '{ pid = $1; sub(/^ *[0-9]+ /, ""); if ($0 == exe) print pid }')
    [ -n "$pids" ] || return 0
    say "Quitting the running Lyssna..."
    # shellcheck disable=SC2086  # one pid per word
    kill $pids 2>/dev/null || true
    i=0
    while [ $i -lt 50 ]; do
        alive=
        for pid in $pids; do
            if kill -0 "$pid" 2>/dev/null; then alive=1; fi
        done
        [ -n "$alive" ] || return 0
        sleep 0.1
        i=$((i + 1))
    done
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
}

say() { printf '%s\n' "$1"; }
die() { printf 'Lyssna install: %s\n' "$1" >&2; exit 1; }

main "$@"
