#!/usr/bin/env bash
set -euo pipefail

ROOT=/
NO_REVERT=0

usage() {
    printf '%s\n' "Usage: uninstall.sh [--root DIR] [--no-revert]"
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --root) [ $# -ge 2 ] || usage 2; ROOT="$2"; shift 2 ;;
        --no-revert) NO_REVERT=1; shift ;;
        -h|--help) usage 0 ;;
        *) usage 2 ;;
    esac
done

PREFIX="${ROOT%/}"
BINDIR="$PREFIX/usr/bin"
LIBDIR="$PREFIX/usr/lib/resolve-aacfix"
SYSTEMDDIR="$PREFIX/usr/lib/systemd/system"
TMPFILESDIR="$PREFIX/usr/lib/tmpfiles.d"
POLKITDIR="$PREFIX/usr/share/polkit-1/actions"

if [ "$ROOT" = / ]; then
    if command -v systemctl >/dev/null 2>&1; then
        systemctl disable --now resolve-aacfix-reapply.service resolve-aacfix-reapply.path || true
    fi
    if [ -x "$BINDIR/aac-fix" ] && [ "$NO_REVERT" -eq 0 ] && [ -d "$PREFIX/opt/resolve" ]; then
        "$BINDIR/aac-fix" uninstall
    fi
fi

if [ -L "$BINDIR/aac-fix" ] && [ "$(readlink "$BINDIR/aac-fix")" = /usr/lib/resolve-aacfix/aac-fix ]; then
    rm -f "$BINDIR/aac-fix"
fi
rm -f "$SYSTEMDDIR/resolve-aacfix-reapply.service" \
      "$SYSTEMDDIR/resolve-aacfix-reapply.path" \
      "$TMPFILESDIR/resolve-aacfix.conf" \
      "$POLKITDIR/50-resolve-aacfix.policy"
rm -rf "$LIBDIR"
rm -f "$PREFIX/etc/resolve-aacfix/auto.conf" \
      "$PREFIX/var/lib/resolve-aacfix/last-result.json"
rmdir "$PREFIX/etc/resolve-aacfix" 2>/dev/null || true
rmdir "$PREFIX/var/lib/resolve-aacfix" 2>/dev/null || true

if [ "$ROOT" = / ] && command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
fi
printf '%s\n' "Removed resolve-aacfix"
