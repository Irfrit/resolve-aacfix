#!/usr/bin/env bash
set -euo pipefail

ROOT=/
NO_AUTO=0
NO_SYSTEMD=0
AUTO_LOCK=/run/lock/omarchy-resolve.lock
INSTALL_LOCK_FD=""
OLD_LIB=""
TMP_LIB=""
CONFIG_TMP=""
PACKAGE_COMMITTED=0

usage() {
    printf '%s\n' "Usage: install.sh [--root DIR] [--no-auto-reapply] [--no-systemd]"
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --root) [ $# -ge 2 ] || usage 2; ROOT="$2"; shift 2 ;;
        --no-auto-reapply) NO_AUTO=1; shift ;;
        --no-systemd) NO_SYSTEMD=1; shift ;;
        -h|--help) usage 0 ;;
        *) usage 2 ;;
    esac
done

PKG="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PREFIX="${ROOT%/}"
LIBDIR="$PREFIX/usr/lib/resolve-aacfix"
BINDIR="$PREFIX/usr/bin"
SYSTEMDDIR="$PREFIX/usr/lib/systemd/system"
TMPFILESDIR="$PREFIX/usr/lib/tmpfiles.d"
POLKITDIR="$PREFIX/usr/share/polkit-1/actions"
TMP_LIB="$LIBDIR.tmp.$$"
OLD_LIB="$LIBDIR.previous.$$"

if [ "$ROOT" = / ] && [ "$(id -u)" -ne 0 ]; then
    printf '%s\n' "install.sh must run as root when installing /" >&2
    exit 1
fi
[ -f "$PKG/SHA256SUMS" ] || { printf '%s\n' "missing payload manifest" >&2; exit 1; }
( cd "$PKG" && sha256sum --quiet -c SHA256SUMS ) >/dev/null
for item in aac-fix aac-patch-tree aac-fix-reapply aacpatch vendor prebuilt systemd tmpfiles.d polkit; do
    [ -e "$PKG/$item" ] || { printf '%s\n' "missing package item: $item" >&2; exit 1; }
    [ ! -L "$PKG/$item" ] || { printf '%s\n' "package item is a symlink: $item" >&2; exit 1; }
    if [ -n "$(find "$PKG/$item" -type l -print -quit)" ]; then
        printf '%s\n' "package item contains a symlink: $item" >&2
        exit 1
    fi
done
if [ -L "$PREFIX/etc/resolve-aacfix" ] || { [ -e "$PREFIX/etc/resolve-aacfix" ] && [ ! -d "$PREFIX/etc/resolve-aacfix" ]; }; then
    printf '%s\n' "invalid configuration directory: $PREFIX/etc/resolve-aacfix" >&2
    exit 1
fi
if [ -L "$LIBDIR" ] || { [ -e "$LIBDIR" ] && [ ! -d "$LIBDIR" ]; }; then
    printf '%s\n' "invalid installation directory: $LIBDIR" >&2
    exit 1
fi
[ ! -e "$OLD_LIB" ] || { printf '%s\n' "temporary installation path exists: $OLD_LIB" >&2; exit 1; }

release_lock() {
    [ -n "$INSTALL_LOCK_FD" ] || return 0
    flock --unlock "$INSTALL_LOCK_FD"
    exec {INSTALL_LOCK_FD}>&-
    INSTALL_LOCK_FD=""
}

restore() {
    local code=$?
    trap - EXIT
    if [ "$PACKAGE_COMMITTED" -ne 1 ] && [ -n "$OLD_LIB" ] && [ -d "$OLD_LIB" ]; then
        rm -rf "$LIBDIR"
        mv "$OLD_LIB" "$LIBDIR"
        OLD_LIB=""
    fi
    rm -rf "$TMP_LIB"
    [ -z "$CONFIG_TMP" ] || rm -f "$CONFIG_TMP"
    release_lock
    exit "$code"
}
trap restore EXIT

if [ -n "$AUTO_LOCK" ] && { [ -e "$AUTO_LOCK" ] || [ "$ROOT" = / ]; }; then
    if [ "$ROOT" = / ] && [ ! -d "$(dirname "$AUTO_LOCK")" ]; then
        install -d -m 0755 "$(dirname "$AUTO_LOCK")"
    fi
    if [ -L "$AUTO_LOCK" ] || { [ -e "$AUTO_LOCK" ] && [ ! -f "$AUTO_LOCK" ]; }; then
        printf '%s\n' "invalid coordination lock: $AUTO_LOCK" >&2
        exit 1
    fi
    if [ "$ROOT" = / ]; then
        touch -- "$AUTO_LOCK"
        [ -O "$AUTO_LOCK" ] || { printf '%s\n' "coordination lock is not root-owned" >&2; exit 1; }
        chmod 0644 "$AUTO_LOCK"
    fi
    exec {INSTALL_LOCK_FD}<"$AUTO_LOCK"
    flock --exclusive "$INSTALL_LOCK_FD"
fi
if [ "$ROOT" = / ] && [ "$NO_SYSTEMD" -eq 0 ] && command -v systemctl >/dev/null 2>&1; then
    systemctl stop resolve-aacfix-reapply.path 2>/dev/null || true
    systemctl stop resolve-aacfix-reapply.service 2>/dev/null || true
fi

rm -rf "$TMP_LIB"
mkdir -p "$TMP_LIB"
for item in aac-fix aac-patch-tree aac-fix-reapply aacpatch vendor prebuilt systemd tmpfiles.d polkit; do
    cp -a "$PKG/$item" "$TMP_LIB/"
done
cp -a "$PKG/SHA256SUMS" "$TMP_LIB/"
find "$TMP_LIB" -type d -exec chmod 0755 {} +
find "$TMP_LIB" -type f -perm /022 -exec chmod go-w {} +
chmod 0755 "$TMP_LIB/aac-fix" "$TMP_LIB/aac-patch-tree" "$TMP_LIB/aac-fix-reapply"
if [ "$(id -u)" -eq 0 ]; then
    chown -hR 0:0 "$TMP_LIB"
fi

mkdir -p "$BINDIR" "$SYSTEMDDIR" "$TMPFILESDIR" "$POLKITDIR" \
    "$PREFIX/etc/resolve-aacfix" "$PREFIX/var/lib/resolve-aacfix"
if [ -e "$LIBDIR" ]; then
    mv "$LIBDIR" "$OLD_LIB"
fi
mv "$TMP_LIB" "$LIBDIR"
TMP_LIB=""
ln -sfn /usr/lib/resolve-aacfix/aac-fix "$BINDIR/aac-fix"
install -m 0644 "$LIBDIR/systemd/resolve-aacfix-reapply.service" "$SYSTEMDDIR/resolve-aacfix-reapply.service"
install -m 0644 "$LIBDIR/systemd/resolve-aacfix-reapply.path" "$SYSTEMDDIR/resolve-aacfix-reapply.path"
install -m 0644 "$LIBDIR/tmpfiles.d/resolve-aacfix.conf" "$TMPFILESDIR/resolve-aacfix.conf"
install -m 0644 "$LIBDIR/polkit/50-resolve-aacfix.policy" "$POLKITDIR/50-resolve-aacfix.policy"
PACKAGE_COMMITTED=1
rm -rf "$OLD_LIB"
OLD_LIB=""
release_lock
if [ "$NO_AUTO" -eq 1 ]; then
    CONFIG_TMP="$PREFIX/etc/resolve-aacfix/.auto.conf.tmp.$$"
    printf 'mode=disabled\n' > "$CONFIG_TMP"
    chmod 0644 "$CONFIG_TMP"
    if [ "$(id -u)" -eq 0 ]; then
        chown 0:0 "$CONFIG_TMP"
    fi
    mv -f "$CONFIG_TMP" "$PREFIX/etc/resolve-aacfix/auto.conf"
    CONFIG_TMP=""
fi

if [ "$ROOT" = / ] && [ "$NO_SYSTEMD" -eq 0 ] && command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
    systemd-tmpfiles --create "$TMPFILESDIR/resolve-aacfix.conf"
fi

if [ "$ROOT" = / ] && [ "$NO_SYSTEMD" -eq 0 ] && [ -f "$PREFIX/opt/resolve/.omarchy-resolve.json" ]; then
    if [ "$NO_AUTO" -eq 1 ]; then
        "$BINDIR/aac-fix" auto disable
    elif [ ! -f "$PREFIX/etc/resolve-aacfix/auto.conf" ] || grep -qx 'mode=auto' "$PREFIX/etc/resolve-aacfix/auto.conf"; then
        "$BINDIR/aac-fix" auto enable
    fi
elif [ -x "$PREFIX/opt/resolve/bin/resolve" ]; then
    "$BINDIR/aac-fix" install "$PREFIX/opt/resolve"
fi

if [ "$ROOT" = / ] && [ "$NO_SYSTEMD" -eq 0 ] && [ "$NO_AUTO" -eq 1 ]; then
    systemctl disable --now resolve-aacfix-reapply.service resolve-aacfix-reapply.path 2>/dev/null || true
fi
printf '%s\n' "Installed resolve-aacfix under $LIBDIR"
