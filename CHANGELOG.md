# Changelog

## 0.2.0

- Replaced the legacy in-place lifecycle with generation-bound, transactional apply, revert, and status handling.
- Added exact stock and active inventories, checksum-verified originals, stale-restore refusal, and strict mixed-state reporting.
- Added stable external locking, no-op mtime preservation, MKV scope binding, JSON status, and `--require-active`.
- Added shared `aac-fix auto status|enable|disable` control with persistent opt-out and atomic service results.
- Added systemd boot and Omarchy-marker triggers, tmpfiles lock setup, and a fixed polkit action.
- Added installed `/usr/lib/resolve-aacfix` layout with `install.sh` and `uninstall.sh`.
- Added unprivileged integration tests for auto-control, process detection, units, and installed layout.
- Preserved upstream MIT licensing and added explicit fork and AI provenance.

## 0.1.1

- Verified the original additive AAC patch and lifecycle behavior on supported Resolve 21.x builds.
