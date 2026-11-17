#!/bin/sh
# Remove Lyssna.
#   curl -fsSL https://raw.githubusercontent.com/andresvourakis/lyssna/main/scripts/uninstall.sh | sh
#   ... | sh -s -- --purge     also delete settings, audio cache, logs, state
# LYSSNA_INSTALL_DIR limits removal to that one folder (for testing).

main() {
    set -eu
    purge=0
    for arg in "$@"; do
        case "$arg" in
            --purge) purge=1 ;;
            *) printf 'usage: uninstall.sh [--purge]\n' >&2; exit 2 ;;
        esac
    done

    # The same two places install.sh may have used.
    if [ -n "${LYSSNA_INSTALL_DIR:-}" ]; then
        set -- "$LYSSNA_INSTALL_DIR"
    else
        set -- /Applications "$HOME/Applications"
    fi
    removed=0
    for dir in "$@"; do
        app=$dir/Lyssna.app
        [ -d "$app" ] || continue
        quit_running "$app"
        rm -rf "$app" || die "Could not remove $app."
        say "Removed $app"
        removed=1
    done
    [ $removed = 1 ] || say "Lyssna.app was not found."

    if [ $purge = 1 ]; then
        # The same folders lyssna/config.py uses.
        for path in \
            "$HOME/.config/lyssna" \
            "$HOME/Library/Application Support/Lyssna" \
            "$HOME/Library/Caches/Lyssna" \
            "$HOME/Library/Logs/Lyssna"; do
            if [ -e "$path" ]; then
                rm -rf "$path"
                say "Removed $path"
            fi
        done
    else
        say "Your settings and audio cache were kept. Run with --purge to delete them too."
    fi
}

# Quit any Lyssna running from this bundle, so it is not deleted from under a live process.
quit_running() {
    exe=$1/Contents/MacOS/Lyssna
    pids=$(ps -axo pid=,comm= | awk -v exe="$exe" '{ pid = $1; sub(/^ *[0-9]+ /, ""); if ($0 == exe) print pid }')
    [ -n "$pids" ] || return 0
    # shellcheck disable=SC2086  # one pid per word
    kill $pids 2>/dev/null || true
    sleep 1
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
}

say() { printf '%s\n' "$1"; }
die() { printf 'Lyssna uninstall: %s\n' "$1" >&2; exit 1; }

main "$@"
