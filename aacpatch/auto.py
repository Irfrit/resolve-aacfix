import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone

from aacpatch import lifecycle
from aacpatch.proc import find_processes


DEFAULT_ROOT = "/opt/resolve"
DEFAULT_CONFIG = "/etc/resolve-aacfix/auto.conf"
DEFAULT_RESULT = "/var/lib/resolve-aacfix/last-result.json"
DEFAULT_MARKER = "/opt/resolve/.omarchy-resolve.json"
DEFAULT_LOCK = "/run/lock/omarchy-resolve.lock"
SERVICE = "resolve-aacfix-reapply.service"
PATH_UNIT = "resolve-aacfix-reapply.path"


class AutoError(Exception):
    pass


@dataclass(frozen=True)
class AutoContext:
    root: str = DEFAULT_ROOT
    config: str = DEFAULT_CONFIG
    result: str = DEFAULT_RESULT
    marker: str = DEFAULT_MARKER
    lock: str = DEFAULT_LOCK
    repo: str = ""
    libs: str = ""
    pylibs: str = ""
    patch_python: str = ""
    e9tool: str = ""
    trampoline: str = ""
    proc_root: str = "/proc"
    verify_payload_manifest: bool = True

    def resolved(self):
        package_root = self.repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return AutoContext(
            root=self.root,
            config=self.config,
            result=self.result,
            marker=self.marker,
            lock=self.lock,
            repo=package_root,
            libs=self.libs or os.path.join(package_root, "prebuilt", "ffmpeg-aac"),
            pylibs=self.pylibs or os.path.join(package_root, "vendor", "pylibs"),
            patch_python=self.patch_python or sys.executable,
            e9tool=self.e9tool or os.path.join(package_root, "vendor", "e9patch", "e9tool"),
            trampoline=self.trampoline or os.path.join(package_root, "vendor", "aacadd"),
            proc_root=self.proc_root,
            verify_payload_manifest=self.verify_payload_manifest,
        )


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _secure_file(path, description):
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode):
        raise AutoError(f"invalid {description}: {path}")
    if os.geteuid() == 0:
        if metadata.st_uid != 0:
            raise AutoError(f"unsafe ownership for {description}: {path}")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise AutoError(f"unsafe permissions for {description}: {path}")
    return True


def _file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(repo):
    manifest = os.path.join(repo, "SHA256SUMS")
    if not os.path.isfile(manifest) or not stat.S_ISREG(os.lstat(manifest).st_mode):
        raise AutoError(f"payload checksum manifest is missing: {manifest}")
    _secure_file(manifest, "payload checksum manifest")
    try:
        with open(manifest, encoding="ascii") as stream:
            lines = stream.read(1024 * 1024).splitlines()
    except (OSError, UnicodeError) as error:
        raise AutoError(f"cannot read payload checksum manifest: {error}") from error
    for line in lines:
        fields = line.split(None, 1)
        if len(fields) != 2 or len(fields[0]) != 64:
            raise AutoError(f"invalid payload checksum manifest: {manifest}")
        expected, relative = fields
        relative = relative.lstrip("*")
        relative_path = os.path.normpath(relative)
        if os.path.isabs(relative_path) or relative_path == ".." or relative_path.startswith("../"):
            raise AutoError(f"invalid payload path in manifest: {relative}")
        path = os.path.join(repo, relative_path)
        try:
            metadata = os.lstat(path)
        except FileNotFoundError as error:
            raise AutoError(f"payload file is missing: {path}") from error
        if not stat.S_ISREG(metadata.st_mode):
            raise AutoError(f"payload file is not regular: {path}")
        if os.geteuid() == 0 and (
            metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise AutoError(f"unsafe payload file permissions: {path}")
        if _file_digest(path) != expected:
            raise AutoError(f"payload checksum mismatch: {path}")


def _secure_directory(path):
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        os.makedirs(path, 0o755)
        metadata = os.lstat(path)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise AutoError(f"invalid directory: {path}")
    if os.geteuid() == 0:
        if metadata.st_uid != 0:
            raise AutoError(f"unsafe ownership for directory: {path}")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise AutoError(f"unsafe permissions for directory: {path}")
    return path


def read_mode(path):
    if not os.path.lexists(path):
        return "auto"
    _secure_file(path, "auto configuration")
    try:
        with open(path, encoding="ascii") as stream:
            lines = stream.read(256).splitlines()
    except (OSError, UnicodeError) as error:
        raise AutoError(f"cannot read auto configuration: {error}") from error
    if len(lines) != 1:
        raise AutoError(f"invalid auto configuration: {path}")
    value = lines[0].split("=", 1)
    if len(value) != 2 or value[0] != "mode" or value[1] not in ("auto", "enabled", "disabled"):
        raise AutoError(f"invalid auto configuration: {path}")
    return value[1]


def write_mode(path, mode):
    if mode not in ("auto", "enabled", "disabled"):
        raise AutoError(f"invalid auto mode: {mode}")
    parent = os.path.dirname(path) or "."
    _secure_directory(parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".auto.", dir=parent)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(f"mode={mode}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        if os.geteuid() == 0:
            os.chown(temporary, 0, 0)
        os.replace(temporary, path)
        fsync_directory(parent)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def marker_info(path):
    if not os.path.lexists(path):
        return {
            "present": False,
            "valid": False,
            "supported": False,
            "version": None,
            "edition": None,
            "engine_version": None,
        }
    _secure_file(path, "Omarchy marker")
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AutoError(f"invalid Omarchy marker: {error}") from error
    if not isinstance(value, dict):
        raise AutoError("invalid Omarchy marker: expected an object")
    version = value.get("version")
    edition = value.get("edition")
    engine_version = value.get("engineVersion")
    valid = all(isinstance(item, str) and item for item in (version, edition, engine_version))
    return {
        "present": True,
        "valid": valid,
        "supported": valid and edition == "Studio",
        "version": version if isinstance(version, str) else None,
        "edition": edition if isinstance(edition, str) else None,
        "engine_version": engine_version if isinstance(engine_version, str) else None,
    }


def _read_result(path):
    if not os.path.lexists(path):
        return None
    _secure_file(path, "last service result")
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AutoError(f"invalid last service result: {error}") from error
    if not isinstance(value, dict):
        raise AutoError("invalid last service result: expected an object")
    return value


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_result(path, value):
    parent = os.path.dirname(path) or "."
    _secure_directory(parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".result.", dir=parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        if os.geteuid() == 0:
            os.chown(temporary, 0, 0)
        os.replace(temporary, path)
        fsync_directory(parent)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _result_value(context, status, exit_code, started, marker, processes, patch, error=None):
    return {
        "schema": 1,
        "status": status,
        "exit_code": exit_code,
        "trigger": "service",
        "started_at": started,
        "finished_at": _now(),
        "resolve_root": context.root,
        "marker": marker,
        "resolve": {
            "running": bool(processes),
            "pids": [item["pid"] for item in processes],
        },
        "patch": {
            "state": patch.get("state") if patch else None,
            "generation": patch.get("generation_id") if patch else None,
        },
        "error": error,
    }


def _patch_view(context):
    return lifecycle.status_snapshot(context.root, context.libs, fake=False)


def status(context, process_finder=find_processes, patch_view=None):
    context = context.resolved()
    mode = read_mode(context.config)
    marker = marker_info(context.marker)
    processes = process_finder(context.proc_root, context.root)
    patch = (patch_view or _patch_view)(context)
    result = None
    result_error = None
    try:
        result = _read_result(context.result)
    except AutoError as error:
        result_error = str(error)
    enabled = mode == "enabled" or (mode == "auto" and marker["supported"])
    return {
        "schema": 1,
        "enabled": enabled,
        "mode": mode,
        "resolve_root": context.root,
        "resolve": {
            "running": bool(processes),
            "pids": [item["pid"] for item in processes],
        },
        "marker": marker,
        "patch": {
            "state": patch["state"],
            "active": patch["active"],
            "scope": patch["scope"],
            "generation": patch["generation_id"],
        },
        "last_result": result,
        "errors": [*patch["errors"], *([result_error] if result_error else [])],
    }


def reapply(context, runner=None, process_finder=find_processes, patch_view=None):
    context = context.resolved()
    started = _now()
    try:
        mode = read_mode(context.config)
    except AutoError as error:
        value = _result_value(context, "failed", 1, started, None, [], None, str(error))
        write_result(context.result, value)
        raise
    if mode == "disabled":
        value = _result_value(context, "disabled", 0, started, None, [], None)
        write_result(context.result, value)
        return 0
    try:
        marker = marker_info(context.marker)
    except AutoError as error:
        value = _result_value(context, "unsupported", 1, started, None, [], None, str(error))
        write_result(context.result, value)
        return 1
    if not marker["present"]:
        value = _result_value(context, "not_omarchy", 0, started, marker, [], None)
        write_result(context.result, value)
        return 0
    if not marker["valid"] or not marker["supported"]:
        value = _result_value(context, "unsupported", 1, started, marker, [], None)
        write_result(context.result, value)
        return 1
    processes = process_finder(context.proc_root, context.root)
    if processes:
        value = _result_value(
            context, "deferred_resolve_running", 0, started, marker, processes, None
        )
        write_result(context.result, value)
        return 0
    if context.verify_payload_manifest:
        try:
            verify_manifest(context.repo)
        except AutoError as error:
            value = _result_value(context, "unsupported", 1, started, marker, [], None, str(error))
            write_result(context.result, value)
            return 1
    patch_view = patch_view or _patch_view
    before = patch_view(context)
    if runner is None:
        def runner():
            return lifecycle.run_apply(
                context.root, True, True, context.repo, context.pylibs,
                context.patch_python, context.e9tool, context.trampoline,
                context.libs, False, False, None,
            )
    try:
        code = runner()
    except (lifecycle.LifecycleError, OSError) as error:
        after = patch_view(context)
        detail = str(error)
        status_value = "unsupported" if any(
            word in detail.lower() for word in ("signature", "source", "binary", "payload")
        ) else "failed"
        value = _result_value(context, status_value, 1, started, marker, [], after, detail)
        write_result(context.result, value)
        return 1
    after = patch_view(context)
    unchanged = (
        before["state"] == "active"
        and after["state"] == "active"
        and before["files"] == after["files"]
        and before["scope"] == after["scope"]
        and before["generation_id"] == after["generation_id"]
    )
    if code:
        status_value = "failed"
        error = f"lifecycle returned exit code {code}"
    elif unchanged:
        status_value = "already_active"
        error = None
    else:
        status_value = "applied"
        error = None
    value = _result_value(
        context, status_value, int(code or 0), started, marker, [], after, error
    )
    write_result(context.result, value)
    return int(code or 0)


def set_mode(context, mode):
    write_mode(context.resolved().config, mode)
    return 0


def render(value, json_output):
    if json_output:
        print(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return
    print(f"==> Automatic AAC reapply: {'enabled' if value['enabled'] else 'disabled'}")
    print(f"  mode: {value['mode']}")
    marker = value["marker"]
    if marker["present"]:
        support = "supported" if marker["supported"] else "unsupported"
        print(f"  Omarchy: {marker['version']} {marker['edition']} ({support})")
    else:
        print("  Omarchy: not detected")
    resolve = value["resolve"]
    if resolve["running"]:
        pids = " ".join(str(pid) for pid in resolve["pids"])
        print(f"  Resolve: running (pid: {pids})")
    else:
        print("  Resolve: not running")
    patch = value["patch"]
    print(f"  AAC support: {patch['state']}")
    if value["last_result"] is not None:
        result = value["last_result"]
        print(f"  last service result: {result.get('status', 'unknown')}")
    for error in value["errors"]:
        print(f"[warn] {error}", file=sys.stderr)


def parser():
    result = argparse.ArgumentParser(prog="aacpatch.auto")
    result.add_argument("command", choices=("status", "set-mode", "reapply", "check-marker"))
    result.add_argument("--root", default=DEFAULT_ROOT)
    result.add_argument("--config", default=DEFAULT_CONFIG)
    result.add_argument("--result", default=DEFAULT_RESULT)
    result.add_argument("--marker")
    result.add_argument("--lock", default=DEFAULT_LOCK)
    result.add_argument("--proc-root", default="/proc")
    result.add_argument("--repo", default="")
    result.add_argument("--libs", default="")
    result.add_argument("--pylibs", default="")
    result.add_argument("--patch-python", default="")
    result.add_argument("--e9tool", default="")
    result.add_argument("--trampoline", default="")
    result.add_argument("--mode", choices=("auto", "enabled", "disabled"), default="enabled")
    result.add_argument("--json", dest="json_output", action="store_true")
    result.add_argument("--require-active", action="store_true")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    marker = args.marker or os.path.join(args.root, ".omarchy-resolve.json")
    context = AutoContext(
        root=args.root, config=args.config, result=args.result, marker=marker,
        lock=args.lock, repo=args.repo, libs=args.libs, pylibs=args.pylibs,
        patch_python=args.patch_python, e9tool=args.e9tool, trampoline=args.trampoline,
        proc_root=args.proc_root,
    )
    try:
        if args.command == "set-mode":
            return set_mode(context, args.mode)
        if args.command == "check-marker":
            value = marker_info(context.marker)
            if value["present"] and not value["valid"]:
                raise AutoError("invalid Omarchy marker")
            if value["present"] and not value["supported"]:
                raise AutoError("unsupported Resolve edition")
            return 0
        if args.command == "reapply":
            return reapply(context)
        value = status(context)
        render(value, args.json_output)
        if args.require_active and not value["patch"]["active"]:
            return 1
        return 0
    except (AutoError, lifecycle.LifecycleError) as error:
        print(f"[error] {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"[error] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
