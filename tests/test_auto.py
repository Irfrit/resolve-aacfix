import json
import os
from pathlib import Path

import pytest

from aacpatch.auto import (
    AutoContext,
    AutoError,
    marker_info,
    read_mode,
    reapply,
    status,
    write_mode,
)
from aacpatch.lifecycle import LifecycleError
from aacpatch.proc import find_processes


ROOT = Path(__file__).resolve().parents[1]


def make_context(tmp_path, edition="Studio", verify_payload_manifest=False):
    marker = tmp_path / "resolve" / ".omarchy-resolve.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps({
        "version": "21.1",
        "edition": edition,
        "engineVersion": "0.3.0",
    }))
    return AutoContext(
        root=str(marker.parent),
        config=str(tmp_path / "etc" / "auto.conf"),
        result=str(tmp_path / "var" / "last-result.json"),
        marker=str(marker),
        proc_root=str(tmp_path / "proc"),
        verify_payload_manifest=verify_payload_manifest,
    )


def patch_view(state="stock", generation="generation"):
    return {
        "state": state,
        "active": state == "active",
        "scope": "mkv",
        "files": {},
        "generation_id": generation,
        "errors": [],
    }


def no_processes(_proc_root, _resolve_root):
    return []


def test_default_auto_mode_requires_supported_marker(tmp_path):
    context = make_context(tmp_path)
    value = status(context, no_processes, lambda _context: patch_view())
    assert value["mode"] == "auto"
    assert value["enabled"] is True
    assert value["marker"]["supported"] is True

    marker = context.marker
    Path(marker).unlink()
    value = status(context, no_processes, lambda _context: patch_view())
    assert value["enabled"] is False
    assert value["marker"]["present"] is False


def test_auto_status_reports_service_process_and_patch(tmp_path):
    context = make_context(tmp_path)
    write_mode(context.config, "disabled")
    write_result = {
        "schema": 1,
        "status": "deferred_resolve_running",
    }
    Path(context.result).parent.mkdir(parents=True)
    Path(context.result).write_text(json.dumps(write_result))
    processes = [{"pid": 42, "comm": "resolve", "executable": None}]
    value = status(context, lambda *_: processes, lambda _context: patch_view("active"))
    assert value["enabled"] is False
    assert value["resolve"]["running"] is True
    assert value["patch"]["state"] == "active"
    assert value["last_result"]["status"] == "deferred_resolve_running"


def test_reapply_missing_marker_is_safe_noop(tmp_path):
    context = make_context(tmp_path)
    Path(context.marker).unlink()
    called = False

    def runner():
        nonlocal called
        called = True
        return 0

    assert reapply(context, runner, no_processes, lambda _context: patch_view()) == 0
    assert called is False
    result = json.loads(Path(context.result).read_text())
    assert result["status"] == "not_omarchy"


def test_reapply_disabled_is_persistent_noop(tmp_path):
    context = make_context(tmp_path)
    write_mode(context.config, "disabled")
    assert reapply(context, lambda: 0, no_processes, lambda _context: patch_view()) == 0
    assert read_mode(context.config) == "disabled"
    assert json.loads(Path(context.result).read_text())["status"] == "disabled"


def test_reapply_defers_when_resolve_is_running(tmp_path):
    context = make_context(tmp_path)
    processes = [{"pid": 42, "comm": "resolve", "executable": None}]
    assert reapply(context, lambda: 0, lambda *_: processes, lambda _context: patch_view()) == 0
    result = json.loads(Path(context.result).read_text())
    assert result["status"] == "deferred_resolve_running"
    assert result["resolve"]["pids"] == [42]


def test_reapply_rejects_non_studio_marker(tmp_path):
    context = make_context(tmp_path, edition="Free")
    assert reapply(context, lambda: 0, no_processes, lambda _context: patch_view()) == 1
    assert json.loads(Path(context.result).read_text())["status"] == "unsupported"


def test_reapply_requires_payload_manifest(tmp_path):
    context = make_context(tmp_path, verify_payload_manifest=True)
    assert reapply(context, lambda: 0, no_processes, lambda _context: patch_view()) == 1
    assert json.loads(Path(context.result).read_text())["status"] == "unsupported"


def test_reapply_records_already_active_and_applied(tmp_path):
    context = make_context(tmp_path)
    values = iter((patch_view("active"), patch_view("active")))
    assert reapply(context, lambda: 0, no_processes, lambda _context: next(values)) == 0
    assert json.loads(Path(context.result).read_text())["status"] == "already_active"

    values = iter((patch_view("stock"), patch_view("active")))
    assert reapply(context, lambda: 0, no_processes, lambda _context: next(values)) == 0
    assert json.loads(Path(context.result).read_text())["status"] == "applied"


def test_reapply_records_failures_without_retry(tmp_path):
    context = make_context(tmp_path)

    def runner():
        raise LifecycleError("unsupported Resolve signature")

    assert reapply(context, runner, no_processes, lambda _context: patch_view("stock")) == 1
    result = json.loads(Path(context.result).read_text())
    assert result["status"] == "unsupported"
    assert result["exit_code"] == 1
    assert "signature" in result["error"]


def test_malformed_configuration_fails_closed(tmp_path):
    context = make_context(tmp_path)
    Path(context.config).parent.mkdir()
    Path(context.config).write_text("mode=unknown\n")
    with pytest.raises(AutoError):
        read_mode(context.config)


def test_marker_info_rejects_symlink(tmp_path):
    context = make_context(tmp_path)
    real = Path(context.marker)
    link = tmp_path / "marker-link"
    link.symlink_to(real)
    with pytest.raises(AutoError):
        marker_info(str(link))


def test_proc_finds_comm_and_deleted_executable(tmp_path):
    proc_root = tmp_path / "proc"
    resolve_root = tmp_path / "resolve"
    target = resolve_root / "bin" / "resolve"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"resolve")
    for pid, comm, executable in (
        (10, "resolve", None),
        (11, "helper", f"{target} (deleted)"),
        (12, "helper", str(tmp_path / "other")),
    ):
        process = proc_root / str(pid)
        process.mkdir(parents=True)
        (process / "comm").write_text(comm + "\n")
        if executable is not None:
            os.symlink(executable, process / "exe")
    values = find_processes(proc_root, resolve_root)
    assert [item["pid"] for item in values] == [10, 11]


def test_systemd_units_are_fixed_and_hardened():
    service = (ROOT / "systemd/resolve-aacfix-reapply.service").read_text()
    path = (ROOT / "systemd/resolve-aacfix-reapply.path").read_text()
    policy = (ROOT / "polkit/50-resolve-aacfix.policy").read_text()
    assert "ExecStart=/usr/lib/resolve-aacfix/aac-fix auto reapply" in service
    assert "Restart=no" in service
    assert "ProtectSystem=strict" in service
    assert "PathChanged=/opt/resolve/.omarchy-resolve.json" in path
    assert "Unit=resolve-aacfix-reapply.service" in path
    assert "/usr/lib/resolve-aacfix/aac-fix-reapply" in policy
    assert "exec.argv" not in policy
