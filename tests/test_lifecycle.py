import fcntl
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from aacpatch.lifecycle import (
    BACKUP_RELS,
    BIN_REL,
    LIB_RELS,
    SHAFILE_REL,
    STATE_REL,
    TARGET_RELS,
    load_state,
)


ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "aac-patch-tree"
LIB_NAMES = tuple(Path(relative).name for relative in LIB_RELS)


def make_tree(tmp_path, generation="one"):
    root = tmp_path / "resolve"
    payload = tmp_path / "payload"
    (root / "bin").mkdir(parents=True)
    (root / "libs").mkdir()
    payload.mkdir()
    write_stock(root, generation)
    for name in LIB_NAMES:
        (payload / name).write_bytes(f"payload:{name}".encode())
    return root, payload


def write_stock(root, generation):
    (root / BIN_REL).write_bytes(f"stock:{generation}:resolve".encode())
    for relative in LIB_RELS:
        (root / relative).write_bytes(f"stock:{generation}:{relative}".encode())


def run(root, payload, *args, fail_after=None, kill_after=None, lock_file=None):
    environment = os.environ.copy()
    environment.pop("SUDO_USER", None)
    environment.pop("AAC_LOCK_FILE", None)
    environment.pop("AAC_TEST_FAIL_AFTER", None)
    environment.pop("AAC_TEST_KILL_AFTER", None)
    environment["AAC_LIBS"] = str(payload)
    environment["AAC_TEST_FAKE_PATCH"] = "1"
    if fail_after is not None:
        environment["AAC_TEST_FAIL_AFTER"] = str(fail_after)
    if kill_after is not None:
        environment["AAC_TEST_KILL_AFTER"] = str(kill_after)
    if lock_file is not None:
        environment["AAC_LOCK_FILE"] = str(lock_file)
    return subprocess.run(
        [str(PATCHER), *args, str(root)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )


def hashes(root, relatives=TARGET_RELS):
    return {relative: (root / relative).read_bytes() for relative in relatives}


def mtimes(root, relatives):
    return {relative: (root / relative).stat().st_mtime_ns for relative in relatives}


def ancillary(root):
    relatives = (STATE_REL, SHAFILE_REL, *BACKUP_RELS.values())
    return {
        relative: (root / relative).read_bytes()
        for relative in relatives
        if (root / relative).exists()
    }


def assert_failed(result, text):
    assert result.returncode != 0, result.stdout + result.stderr
    assert text in result.stdout + result.stderr


def test_apply_is_atomic_and_idempotent(tmp_path):
    root, payload = make_tree(tmp_path)
    first = run(root, payload, "apply", "--originals", "keep")
    assert first.returncode == 0, first.stdout + first.stderr
    state_before = (root / STATE_REL).read_bytes()
    files_before = hashes(root, (*TARGET_RELS, *BACKUP_RELS.values(), STATE_REL, SHAFILE_REL))

    second = run(root, payload, "apply", "--originals", "keep")
    assert second.returncode == 0, second.stdout + second.stderr
    assert (root / STATE_REL).read_bytes() == state_before
    tracked = (*TARGET_RELS, *BACKUP_RELS.values(), STATE_REL, SHAFILE_REL)
    assert hashes(root, tracked) == files_before
    status = run(root, payload, "status")
    assert status.returncode == 0
    assert "AAC support: ACTIVE" in status.stdout


def test_apply_noop_preserves_mtimes(tmp_path):
    root, payload = make_tree(tmp_path)
    assert run(root, payload, "apply").returncode == 0
    tracked = (*TARGET_RELS, *BACKUP_RELS.values(), STATE_REL, SHAFILE_REL)
    fixed = 1_700_000_000_123_456_789
    for relative in tracked:
        os.utime(root / relative, ns=(fixed, fixed))
    before = mtimes(root, tracked)
    result = run(root, payload, "apply")
    assert result.returncode == 0, result.stdout + result.stderr
    assert mtimes(root, tracked) == before


def test_scope_change_is_not_a_noop(tmp_path):
    root, payload = make_tree(tmp_path)
    assert run(root, payload, "apply").returncode == 0
    state_path = root / STATE_REL
    fixed = 1_700_000_000_123_456_789
    os.utime(state_path, ns=(fixed, fixed))
    result = run(root, payload, "apply", "--no-mkv")
    assert result.returncode == 0, result.stdout + result.stderr
    assert load_state(str(root)).scope == "quicktime"
    assert state_path.stat().st_mtime_ns != fixed
    assert run(root, payload, "apply").returncode == 0
    assert load_state(str(root)).scope == "mkv"


def test_upgrade_then_revert_refuses_stale_generation(tmp_path):
    root, payload = make_tree(tmp_path)
    assert run(root, payload, "apply").returncode == 0
    state_before = ancillary(root)
    write_stock(root, "two")
    stock_after_update = hashes(root)

    result = run(root, payload, "revert")
    assert_failed(result, "stale")
    assert hashes(root) == stock_after_update
    assert ancillary(root) == state_before


def test_upgrade_then_apply_adopts_new_generation(tmp_path):
    root, payload = make_tree(tmp_path)
    assert run(root, payload, "apply").returncode == 0
    write_stock(root, "two")
    stock = hashes(root)

    result = run(root, payload, "apply")
    assert result.returncode == 0, result.stdout + result.stderr
    state = load_state(str(root))
    assert state.stock == {
        relative: hashlib.sha256(value).hexdigest() for relative, value in stock.items()
    }
    assert {relative: (root / relative).read_bytes() for relative in LIB_RELS} == {
        relative: (payload / Path(relative).name).read_bytes() for relative in LIB_RELS
    }
    revert = run(root, payload, "revert")
    assert revert.returncode == 0, revert.stdout + revert.stderr
    assert hashes(root) == stock


def test_backup_tampering_blocks_revert(tmp_path):
    for relative in (BIN_REL, LIB_RELS[0], LIB_RELS[-1]):
        root, payload = make_tree(tmp_path / relative.replace("/", "_"))
        assert run(root, payload, "apply").returncode == 0
        before = hashes(root)
        backup = root / BACKUP_RELS[relative]
        backup.write_bytes(b"tampered")
        result = run(root, payload, "revert")
        assert_failed(result, "SHA-256")
        assert hashes(root) == before
        assert (root / STATE_REL).exists()


def test_mixed_libraries_are_never_reported_active(tmp_path):
    root, payload = make_tree(tmp_path)
    assert run(root, payload, "apply").returncode == 0
    (root / LIB_RELS[1]).write_bytes(b"one library from another generation")
    result = run(root, payload, "status")
    assert result.returncode == 0
    assert "AAC support: ACTIVE" not in result.stdout
    assert "MIXED/INCONSISTENT" in result.stdout


def test_status_json_and_require_active(tmp_path):
    root, payload = make_tree(tmp_path)
    assert run(root, payload, "apply").returncode == 0
    result = run(root, payload, "status", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["state"] == "active"
    assert data["active"] is True
    assert data["generation"]["verified"] is True
    assert data["backups"]["verified"] is True
    assert data["scope"] == "mkv"
    assert run(root, payload, "status", "--require-active").returncode == 0
    (root / LIB_RELS[0]).write_bytes(b"changed")
    result = run(root, payload, "status", "--json", "--require-active")
    assert result.returncode == 1
    assert json.loads(result.stdout)["state"] == "mixed"
    assert run(root, payload, "status").returncode == 0


def test_missing_library_is_refused_before_changes(tmp_path):
    root, payload = make_tree(tmp_path)
    (root / LIB_RELS[2]).unlink()
    before = hashes(root, (BIN_REL, *LIB_RELS[:2], *LIB_RELS[3:]))
    result = run(root, payload, "apply")
    assert_failed(result, "missing")
    assert hashes(root, (BIN_REL, *LIB_RELS[:2], *LIB_RELS[3:])) == before
    assert not (root / STATE_REL).exists()
    assert not (root / BACKUP_RELS[BIN_REL]).exists()


def test_apply_failure_rolls_back_exact_stock_inventory(tmp_path):
    root, payload = make_tree(tmp_path)
    before = hashes(root)
    result = run(root, payload, "apply", fail_after=7)
    assert_failed(result, "injected")
    assert hashes(root) == before
    assert not (root / STATE_REL).exists()
    assert not (root / BACKUP_RELS[BIN_REL]).exists()


def test_interrupted_apply_is_recovered_on_next_run(tmp_path):
    root, payload = make_tree(tmp_path)
    killed = run(root, payload, "apply", kill_after=2)
    assert killed.returncode != 0
    assert list(root.glob(".aacfix-txn-*"))
    recovered = run(root, payload, "apply")
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert not list(root.glob(".aacfix-txn-*"))
    assert load_state(str(root)) is not None


def test_revert_failure_rolls_back_exact_active_inventory(tmp_path):
    root, payload = make_tree(tmp_path)
    assert run(root, payload, "apply").returncode == 0
    before = hashes(root)
    state_before = ancillary(root)
    result = run(root, payload, "revert", fail_after=3)
    assert_failed(result, "injected")
    assert hashes(root) == before
    assert ancillary(root) == state_before


def test_originals_remove_keeps_cli_behavior(tmp_path):
    root, payload = make_tree(tmp_path)
    result = run(root, payload, "apply", "--originals", "remove")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / STATE_REL).exists()
    assert not (root / BACKUP_RELS[BIN_REL]).exists()
    status = run(root, payload, "status")
    assert "AAC support: ACTIVE" in status.stdout
    revert = run(root, payload, "revert")
    assert revert.returncode != 0


def test_status_requires_generation_state_for_active_files(tmp_path):
    root, payload = make_tree(tmp_path)
    assert run(root, payload, "apply", "--originals", "remove").returncode == 0
    (root / STATE_REL).unlink()
    result = run(root, payload, "status")
    assert result.returncode == 0
    assert "AAC support: ACTIVE" not in result.stdout
    assert "LEGACY/UNBOUND" in result.stdout


def test_external_lock_file_is_used(tmp_path):
    root, payload = make_tree(tmp_path)
    lock_file = tmp_path / "aacfix.lock"
    lock_file.touch()
    descriptor = os.open(lock_file, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    environment = os.environ.copy()
    environment.pop("SUDO_USER", None)
    environment["AAC_LIBS"] = str(payload)
    environment["AAC_TEST_FAKE_PATCH"] = "1"
    environment["AAC_LOCK_FILE"] = str(lock_file)
    process = subprocess.Popen(
        [str(PATCHER), "status", str(root)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.2)
        assert process.poll() is None
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stdout + stderr


def test_explicit_lock_file_is_used_without_environment_override(tmp_path):
    root, payload = make_tree(tmp_path)
    lock_file = tmp_path / "aacfix-explicit.lock"
    lock_file.touch()
    descriptor = os.open(lock_file, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    environment = os.environ.copy()
    environment.pop("SUDO_USER", None)
    environment.pop("AAC_LOCK_FILE", None)
    environment["AAC_LIBS"] = str(payload)
    environment["AAC_TEST_FAKE_PATCH"] = "1"
    process = subprocess.Popen(
        [str(PATCHER), "apply", "--lock-file", str(lock_file), str(root)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.2)
        assert process.poll() is None
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stdout + stderr


def test_status_uses_a_shared_lock(tmp_path):
    root, payload = make_tree(tmp_path)
    descriptor = os.open(root, os.O_RDONLY)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    environment = os.environ.copy()
    environment.pop("SUDO_USER", None)
    environment["AAC_LIBS"] = str(payload)
    environment["AAC_TEST_FAKE_PATCH"] = "1"
    process = subprocess.Popen(
        [str(PATCHER), "status", str(root)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.2)
        assert process.poll() is None
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stdout + stderr
