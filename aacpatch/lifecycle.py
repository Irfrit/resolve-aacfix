"""Transactional lifecycle management for the AAC fix."""
import argparse
import hashlib
import json
import os
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass


BIN_REL = "bin/resolve"
LIB_NAMES = (
    "libavcodec.so.60.3.100",
    "libavformat.so.60.3.100",
    "libavutil.so.58.2.100",
    "libswscale.so.7.1.100",
)
LIB_RELS = tuple(f"libs/{name}" for name in LIB_NAMES)
TARGET_RELS = (BIN_REL,) + LIB_RELS
BACKUP_RELS = {
    BIN_REL: "bin/resolve.aac-orig",
    **{rel: f"libs/_bmd_orig/{os.path.basename(rel)}" for rel in LIB_RELS},
}
STATE_REL = "bin/.resolve.aac-state"
SHAFILE_REL = "bin/.resolve.aac-orig.sha256"
STATE_MAGIC = "aacfix-state-v1"
FAKE_MARKER = b"aacfix-test-patched\n"
CHUNK_SIZE = 1024 * 1024


class LifecycleError(Exception):
    pass


@dataclass(frozen=True)
class State:
    generation: str
    stock: dict
    active: dict
    scope: str = None


def info(message):
    print(f"\033[1m==>\033[0m {message}")


def message(value):
    print(f"  {value}")


def warn(message):
    print(f"\033[33m[warn]\033[0m {message}", file=sys.stderr)


def fail(message):
    raise LifecycleError(message)


def path_for(root, relative):
    return os.path.join(root, *relative.split("/"))


def require_regular(path, description):
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        fail(f"missing {description}: {path}")
    if not stat.S_ISREG(mode):
        fail(f"not a regular file: {path}")
    return path


def exists(path):
    return os.path.lexists(path)


def require_secure_path(path, description, directory=False):
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        fail(f"missing {description}: {path}")
    expected = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if not expected:
        fail(f"invalid {description}: {path}")
    if os.geteuid() == 0:
        if metadata.st_uid != 0:
            fail(f"unsafe ownership for {description}: {path}")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            fail(f"unsafe permissions for {description}: {path}")
    return path


def sha256(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as stream:
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as error:
        fail(f"cannot read {path}: {error}")
    return digest.hexdigest()


def inventory(root, targets=TARGET_RELS, description="target"):
    result = {}
    for relative in targets:
        path = path_for(root, relative)
        require_secure_path(path, f"{description} {relative}")
        result[relative] = sha256(path)
    return result


def verify_inventory(root, expected, description):
    actual = inventory(root)
    if actual != expected:
        fail(f"{description} changed while committing")


def payload_inventory(libs_root, required=True):
    if not os.path.isdir(libs_root):
        if required:
            fail(f"FFmpeg payload directory not found: {libs_root}")
        return None
    result = {}
    for name in LIB_NAMES:
        path = os.path.join(libs_root, name)
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            metadata = None
        if metadata is None or not stat.S_ISREG(metadata.st_mode):
            if required:
                fail(f"FFmpeg payload missing: {path}")
            return None
        if os.geteuid() == 0 and (
            metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            if required:
                fail(f"unsafe FFmpeg payload permissions: {path}")
            return None
        result[f"libs/{name}"] = sha256(path)
    return result


def scope_name(mkv):
    return "mkv" if mkv else "quicktime"


def generation_id(stock):
    digest = hashlib.sha256()
    digest.update(STATE_MAGIC.encode("ascii"))
    digest.update(b"\0")
    for relative in TARGET_RELS:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(stock[relative].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def state_bytes(state):
    lines = [STATE_MAGIC, f"generation {state.generation}", f"scope {state.scope}"]
    for relative in TARGET_RELS:
        lines.append(f"stock {relative} {state.stock[relative]}")
    for relative in TARGET_RELS:
        lines.append(f"active {relative} {state.active[relative]}")
    return ("\n".join(lines) + "\n").encode("ascii")


def valid_digest(value):
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def parse_state(raw, path):
    if len(raw) > 16384:
        fail(f"state file is too large: {path}")
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError:
        fail(f"state file is not ASCII: {path}")
    if not lines or lines[0] != STATE_MAGIC:
        fail(f"invalid state file: {path}")
    generation = None
    scope = None
    stock = {}
    active = {}
    for line in lines[1:]:
        fields = line.split()
        if len(fields) == 2 and fields[0] == "scope":
            if scope is not None or fields[1] not in ("mkv", "quicktime"):
                fail(f"invalid state scope: {path}")
            scope = fields[1]
            continue
        if len(fields) == 2 and fields[0] == "generation":
            if generation is not None or not valid_digest(fields[1]):
                fail(f"invalid state generation: {path}")
            generation = fields[1]
            continue
        if len(fields) == 3 and fields[0] in ("stock", "active"):
            relative, digest = fields[1], fields[2]
            target = stock if fields[0] == "stock" else active
            if relative not in TARGET_RELS or relative in target or not valid_digest(digest):
                fail(f"invalid state entry: {path}")
            target[relative] = digest
            continue
        fail(f"invalid state entry: {path}")
    if generation is None or set(stock) != set(TARGET_RELS) or set(active) != set(TARGET_RELS):
        fail(f"incomplete state file: {path}")
    if generation_id(stock) != generation:
        fail(f"state generation checksum mismatch: {path}")
    return State(generation, stock, active, scope)


def load_state(root):
    path = path_for(root, STATE_REL)
    if not exists(path):
        return None
    require_secure_path(path, "state file")
    try:
        with open(path, "rb") as stream:
            raw = stream.read(16385)
    except OSError as error:
        fail(f"cannot read state file: {error}")
    return parse_state(raw, path)


def elf_load_data(path):
    try:
        with open(path, "rb") as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:4] != b"\x7fELF" or header[4] != 2 or header[5] != 1:
                return None
            fields = struct.unpack_from("<16sHHIQQQIHHHHHH", header, 0)
            phoff, phentsize, phnum = fields[5], fields[9], fields[10]
            if phentsize < 56:
                return None
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            if phoff + phentsize * phnum > size:
                return None
            stream.seek(phoff)
            program_headers = stream.read(phentsize * phnum)
            if len(program_headers) != phentsize * phnum:
                return None
            loads = 0
            maximum = 0
            for index in range(phnum):
                program = struct.unpack_from("<IIQQQQQQ", program_headers, index * phentsize)
                if program[0] == 1:
                    loads += 1
                    maximum = max(maximum, program[2] + program[5])
            if maximum > size:
                return None
            return loads, maximum, size
    except (OSError, struct.error, IndexError):
        return None


def binary_kind(path, fake=False):
    if fake:
        try:
            with open(path, "rb") as stream:
                prefix = stream.read(len(FAKE_MARKER))
        except OSError:
            return "unknown"
        return "patched" if prefix == FAKE_MARKER else "stock"
    data = elf_load_data(path)
    if data is None:
        return "unknown"
    loads, maximum, size = data
    if maximum > size:
        return "unknown"
    if loads == 4:
        return "stock"
    if loads > 4:
        return "patched"
    return "unknown"


def fsync_file(path):
    with open(path, "rb") as stream:
        os.fsync(stream.fileno())


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def copy_bytes(source, destination, mode=None):
    with open(source, "rb") as source_stream, open(destination, "wb") as destination_stream:
        shutil.copyfileobj(source_stream, destination_stream, CHUNK_SIZE)
    if mode is None:
        shutil.copystat(source, destination)
        if os.geteuid() == 0:
            source_stat = os.stat(source)
            os.chown(destination, source_stat.st_uid, source_stat.st_gid)
    else:
        os.chmod(destination, mode)
    fsync_file(destination)


class Transaction:
    def __init__(self, root, fail_after=None):
        self.root = root
        self.control = tempfile.mkdtemp(prefix=".aacfix-txn-", dir=root)
        self.snapshots = {}
        self.changed = []
        self.temporary_paths = []
        self.temporary_dirs = []
        self.created_dirs = []
        self.commit_count = 0
        self.fail_after = fail_after
        self.preserve = False
        self.journal_path = os.path.join(self.control, "journal.json")
        self.previous_signal_handlers = {}
        self._write_journal()
        fsync_directory(self.control)
        fsync_directory(root)

    def _write_journal(self):
        records = []
        for _key, record in self.snapshots.items():
            records.append({
                "kind": "file",
                "path": record["path"],
                "exists": record["exists"],
                "snapshot": record["snapshot"],
            })
        for path in self.temporary_paths:
            records.append({"kind": "temp-file", "path": path})
        for path in self.temporary_dirs:
            records.append({"kind": "temp-dir", "path": path})
        for path in self.created_dirs:
            records.append({"kind": "dir", "path": path})
        descriptor, temporary = tempfile.mkstemp(prefix=".journal.", dir=self.control)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(records, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.journal_path)
            fsync_directory(self.control)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)

    def temp_file(self, parent, label):
        descriptor, path = tempfile.mkstemp(prefix=f".{label}.aac-", dir=parent)
        os.close(descriptor)
        self.temporary_paths.append(path)
        self._write_journal()
        return path

    def temp_dir(self, parent, label):
        path = tempfile.mkdtemp(prefix=f".{label}.aac-", dir=parent)
        self.temporary_dirs.append(path)
        self._write_journal()
        return path

    def stage_copy(self, source, parent, label, mode=None):
        destination = self.temp_file(parent, label)
        copy_bytes(source, destination, mode)
        return destination

    def stage_link(self, source, parent, label):
        destination = self.temp_file(parent, label)
        os.unlink(destination)
        try:
            os.link(source, destination)
        except OSError:
            copy_bytes(source, destination)
        return destination

    def stage_text(self, text, parent, label, mode=0o644):
        destination = self.temp_file(parent, label)
        if isinstance(text, str):
            text = text.encode("utf-8")
        with open(destination, "wb") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(destination, mode)
        return destination

    def stage_backup(self, source, destination):
        parent = os.path.dirname(destination)
        if os.path.isdir(parent):
            return self.stage_link(source, parent, "backup")
        staging_parent = os.path.dirname(parent)
        staging_dir = self.temp_dir(staging_parent, "backup")
        staged = os.path.join(staging_dir, os.path.basename(destination))
        try:
            os.link(source, staged)
        except OSError:
            copy_bytes(source, staged)
        self.temporary_paths.append(staged)
        self._write_journal()
        return staged

    def prepare(self, path, expected=None):
        key = os.path.abspath(path)
        if key in self.snapshots:
            return
        record = {"path": path, "exists": exists(path), "snapshot": None}
        if record["exists"]:
            require_regular(path, "lifecycle file")
            snapshot = os.path.join(self.control, f"snapshot-{len(self.snapshots)}")
            try:
                os.link(path, snapshot)
            except OSError:
                copy_bytes(path, snapshot)
            if expected is not None and sha256(snapshot) != expected:
                fail(f"file changed while preparing transaction: {path}")
            record["snapshot"] = snapshot
        self.snapshots[key] = record
        self._write_journal()

    def ensure_dir(self, path):
        if os.path.isdir(path):
            return
        if exists(path):
            fail(f"backup path is not a directory: {path}")
        os.makedirs(path, 0o755)
        self.created_dirs.append(path)
        self._write_journal()

    def _record(self, path):
        key = os.path.abspath(path)
        if key not in self.snapshots:
            fail(f"transaction path was not prepared: {path}")
        if key not in {os.path.abspath(item) for item in self.changed}:
            self.changed.append(path)

    def _maybe_fail(self):
        self.commit_count += 1
        kill_after = os.environ.get("AAC_TEST_KILL_AFTER")
        if kill_after and self.commit_count >= int(kill_after):
            os.kill(os.getpid(), signal.SIGKILL)
        if self.fail_after is not None and self.commit_count >= self.fail_after:
            fail("injected lifecycle failure")

    def replace(self, source, destination):
        if not exists(source):
            fail(f"staged replacement is missing: {source}")
        os.replace(source, destination)
        self._record(destination)
        try:
            fsync_directory(os.path.dirname(destination))
        except OSError:
            raise
        self._maybe_fail()

    def remove(self, path):
        if not exists(path):
            return
        if os.path.isdir(path) and not os.path.islink(path):
            fail(f"cannot remove directory as lifecycle file: {path}")
        os.unlink(path)
        self._record(path)
        try:
            fsync_directory(os.path.dirname(path))
        except OSError:
            raise
        self._maybe_fail()

    def rollback(self):
        errors = []
        for path in reversed(self.changed):
            record = self.snapshots[os.path.abspath(path)]
            try:
                if record["exists"]:
                    restore = self.temp_file(os.path.dirname(path), "restore")
                    os.unlink(restore)
                    try:
                        os.link(record["snapshot"], restore)
                    except OSError:
                        copy_bytes(record["snapshot"], restore)
                    os.replace(restore, path)
                    fsync_directory(os.path.dirname(path))
                elif exists(path):
                    os.unlink(path)
                    fsync_directory(os.path.dirname(path))
            except OSError as error:
                errors.append(f"{path}: {error}")
        for path in reversed(self.created_dirs):
            try:
                if os.path.isdir(path):
                    os.rmdir(path)
            except OSError as error:
                errors.append(f"{path}: {error}")
        if errors:
            self.preserve = True
            fail("rollback incomplete: " + "; ".join(errors))
        self.changed.clear()

    def cleanup(self):
        if self.preserve:
            return
        for path in self.temporary_paths:
            try:
                if exists(path):
                    os.unlink(path)
            except OSError:
                pass
        for path in self.temporary_dirs:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
            except OSError:
                pass
        try:
            shutil.rmtree(self.control)
        except OSError:
            pass

    @staticmethod
    def _interrupt(_signum, _frame):
        raise KeyboardInterrupt

    def __enter__(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            self.previous_signal_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self._interrupt)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is not None and not self.preserve:
                try:
                    self.rollback()
                except LifecycleError as rollback_error:
                    if exc_value is None:
                        raise
                    exc_value.args = (f"{exc_value}; {rollback_error}",)
        finally:
            for signum, handler in self.previous_signal_handlers.items():
                signal.signal(signum, handler)
        self.cleanup()
        return False


def recover_transactions(root):
    if not os.path.isdir(root):
        return
    for name in sorted(os.listdir(root)):
        if not name.startswith(".aacfix-txn-"):
            continue
        control = os.path.join(root, name)
        if not os.path.isdir(control) or os.path.islink(control):
            fail(f"invalid lifecycle transaction directory: {control}")
        journal = os.path.join(control, "journal.json")
        try:
            with open(journal, "rb") as stream:
                records = json.loads(stream.read(1024 * 1024).decode("ascii"))
        except FileNotFoundError:
            if os.listdir(control):
                fail(f"cannot recover lifecycle transaction without journal: {control}")
            shutil.rmtree(control)
            fsync_directory(root)
            continue
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            fail(f"cannot recover lifecycle transaction {control}: {error}")
        if not isinstance(records, list):
            fail(f"invalid lifecycle transaction journal: {journal}")
        files = []
        temporary_files = []
        temporary_dirs = []
        directories = []
        for record in records:
            if not isinstance(record, dict):
                fail(f"invalid lifecycle transaction record: {journal}")
            path = record.get("path")
            if not isinstance(path, str) or os.path.commonpath((root, path)) != root:
                fail(f"invalid lifecycle transaction path: {journal}")
            if record.get("kind") == "file":
                files.append(record)
            elif record.get("kind") == "temp-file":
                temporary_files.append(path)
            elif record.get("kind") == "temp-dir":
                temporary_dirs.append(path)
            elif record.get("kind") == "dir":
                directories.append(path)
            else:
                fail(f"invalid lifecycle transaction record: {journal}")
        for record in reversed(files):
            path = record["path"]
            if record.get("exists"):
                snapshot = record.get("snapshot")
                if not isinstance(snapshot, str) or os.path.commonpath(
                    (control, snapshot)
                ) != control:
                    fail(f"invalid lifecycle transaction snapshot: {journal}")
                if not stat.S_ISREG(os.lstat(snapshot).st_mode):
                    fail(f"missing lifecycle transaction snapshot: {snapshot}")
                parent = os.path.dirname(path)
                if not os.path.isdir(parent):
                    fail(f"missing lifecycle transaction parent: {parent}")
                descriptor, restore = tempfile.mkstemp(prefix=".recover.", dir=parent)
                os.close(descriptor)
                os.unlink(restore)
                try:
                    os.link(snapshot, restore)
                except OSError:
                    copy_bytes(snapshot, restore)
                os.replace(restore, path)
                fsync_directory(parent)
            elif os.path.lexists(path):
                if not os.path.isdir(path) or os.path.islink(path):
                    os.unlink(path)
                else:
                    fail(f"lifecycle transaction expected a file: {path}")
        for path in reversed(temporary_files):
            if os.path.lexists(path):
                os.unlink(path)
        for path in reversed(temporary_dirs):
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
        for path in reversed(directories):
            if os.path.isdir(path) and not os.path.islink(path):
                os.rmdir(path)
        shutil.rmtree(control)
        fsync_directory(root)


def target_backup(root, relative):
    return path_for(root, BACKUP_RELS[relative])


def verify_existing_backup_artifacts(root):
    paths = [target_backup(root, relative) for relative in TARGET_RELS]
    paths.append(path_for(root, SHAFILE_REL))
    for path in paths:
        if exists(path):
            require_secure_path(path, "kept original")
    backup_directory = os.path.dirname(target_backup(root, LIB_RELS[0]))
    if exists(backup_directory):
        require_secure_path(backup_directory, "library backup directory", directory=True)


def backup_inventory(root):
    result = {}
    for relative in TARGET_RELS:
        path = target_backup(root, relative)
        if not exists(path):
            return None
        require_secure_path(path, f"kept original {BACKUP_RELS[relative]}")
        result[relative] = sha256(path)
    return result


def sidecar_valid(root, state):
    path = path_for(root, SHAFILE_REL)
    if not exists(path):
        return True
    require_secure_path(path, "binary checksum sidecar")
    try:
        with open(path, "r", encoding="ascii") as stream:
            value = stream.read(128)
    except (OSError, UnicodeError):
        return False
    return value.strip() == state.stock[BIN_REL]


def state_backups_valid(root, state):
    actual = backup_inventory(root)
    return actual is not None and actual == state.stock and sidecar_valid(root, state)


def verify_stock_source(path, fake):
    require_regular(path, "stock Resolve binary")
    if binary_kind(path, fake=fake) != "stock":
        fail(f"source is not a complete stock Resolve binary: {path}")


def patch_binary(transaction, root, source, repo, pylibs, patch_python, e9tool, trampoline,
                 mkv, verbose, fake):
    output = transaction.temp_file(path_for(root, BIN_REL).rsplit(os.sep, 1)[0], "resolve")
    os.unlink(output)
    if fake:
        with open(source, "rb") as source_stream, open(output, "wb") as output_stream:
            output_stream.write(FAKE_MARKER)
            shutil.copyfileobj(source_stream, output_stream, CHUNK_SIZE)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.chmod(output, 0o755)
        if os.geteuid() == 0:
            os.chown(output, 0, 0)
        if binary_kind(output, fake=True) != "patched":
            fail("fake patch did not produce a patched binary")
        return output
    command = [patch_python, "-m", "aacpatch.additive", source, "-o", output]
    if mkv:
        command.append("--mkv")
    environment = os.environ.copy()
    environment["AAC_E9TOOL"] = e9tool
    environment["AAC_TRAMPOLINE"] = trampoline
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (repo, pylibs) if value
    )
    if verbose:
        result = subprocess.run(command, cwd=repo, env=environment, check=False)
    else:
        descriptor, log_path = tempfile.mkstemp(prefix="aac-patch-", suffix=".log")
        os.close(descriptor)
        with open(log_path, "wb") as log:
            result = subprocess.run(command, cwd=repo, env=environment,
                                    stdout=log, stderr=subprocess.STDOUT, check=False)
        if result.returncode != 0:
            warn(f"patcher failed; full output: {log_path}")
            try:
                with open(log_path, "rb") as log:
                    detail = log.read()[-4000:].decode("utf-8", "replace")
                if detail:
                    warn(detail.rstrip())
            except OSError:
                pass
            fail("patching failed")
        os.unlink(log_path)
    if result.returncode != 0:
        fail("patching failed")
    if not exists(output):
        fail("patcher produced no binary")
    if binary_kind(output) != "patched":
        fail("patcher output is not a complete patched Resolve binary")
    os.chmod(output, 0o755)
    if os.geteuid() == 0:
        os.chown(output, 0, 0)
    fsync_file(output)
    return output


def check_root(root):
    if not root or not os.path.isdir(root):
        fail(f"not a directory: {root}")
    if not os.path.isfile(path_for(root, BIN_REL)):
        fail(f"no Resolve binary at {path_for(root, BIN_REL)} -- is this a Resolve root?")
    if not os.path.isdir(path_for(root, "libs")):
        fail(f"no libs/ directory in {root} -- is this a Resolve root?")


def run_apply(root, keep, mkv, repo, pylibs, patch_python, e9tool, trampoline,
              libs_root, verbose, fake, fail_after):
    check_root(root)
    recover_transactions(root)
    current = inventory(root)
    payload = payload_inventory(libs_root, required=True)
    if payload is None:
        fail("AAC FFmpeg payload is unavailable")
    state = load_state(root)
    backup_paths = {relative: target_backup(root, relative) for relative in TARGET_RELS}
    verify_existing_backup_artifacts(root)
    state_path = path_for(root, STATE_REL)
    shafile_path = path_for(root, SHAFILE_REL)
    source_path = None
    stock = None
    replace_backups = True
    requested_scope = scope_name(mkv)

    if state is not None:
        if current == state.active:
            if binary_kind(path_for(root, BIN_REL), fake=fake) != "patched":
                fail("recorded active generation does not contain a patched Resolve binary")
            stock = dict(state.stock)
            payload_satisfied = all(
                payload[relative] == current[relative] for relative in LIB_RELS
            )
            artifacts_absent = not any(exists(path) for path in backup_paths.values())
            artifacts_absent = artifacts_absent and not exists(shafile_path)
            backups_valid = None
            if state.scope == requested_scope and payload_satisfied:
                if keep:
                    backups_valid = state_backups_valid(root, state)
                    if backups_valid:
                        message("AAC fix already active; nothing to do")
                        return 0
                elif artifacts_absent:
                    message("AAC fix already active; nothing to do")
                    return 0
            if backups_valid is None:
                backups_valid = state_backups_valid(root, state)
            if not backups_valid:
                fail("kept originals do not match the recorded Resolve generation")
            source_path = backup_paths[BIN_REL]
            replace_backups = False
            verify_stock_source(source_path, fake)
        elif binary_kind(path_for(root, BIN_REL), fake=fake) == "stock":
            stock = dict(current) if current != state.stock else dict(state.stock)
            source_path = path_for(root, BIN_REL)
            if current == state.stock and state_backups_valid(root, state):
                source_path = backup_paths[BIN_REL]
                replace_backups = False
        else:
            fail("current Resolve files are a mixed or unrecognized generation")
    else:
        kind = binary_kind(path_for(root, BIN_REL), fake=fake)
        if kind == "stock":
            stock = dict(current)
            source_path = path_for(root, BIN_REL)
        elif kind == "patched":
            backups = backup_inventory(root)
            if backups is None:
                fail("patched Resolve binary has no complete, verified original set")
            stock = backups
            source_path = backup_paths[BIN_REL]
            replace_backups = False
        else:
            fail("Resolve binary is neither stock nor a safely backed-up patched build")

    verify_stock_source(source_path, fake)
    with Transaction(root, fail_after=fail_after) as transaction:
        for relative, digest in current.items():
            transaction.prepare(path_for(root, relative), digest)
        for relative, path in backup_paths.items():
            expected = stock[relative] if not replace_backups else None
            transaction.prepare(path, expected)
        transaction.prepare(state_path)
        transaction.prepare(shafile_path)

        staged = {}
        staged[BIN_REL] = patch_binary(
            transaction, root, source_path, repo, pylibs, patch_python,
            e9tool, trampoline, mkv, verbose, fake,
        )
        for relative in LIB_RELS:
            source = os.path.join(libs_root, os.path.basename(relative))
            require_regular(source, "FFmpeg payload")
            staged[relative] = transaction.stage_copy(
                source, os.path.dirname(path_for(root, relative)), "lib", mode=0o755,
            )
            if os.geteuid() == 0:
                os.chown(staged[relative], 0, 0)
            if sha256(staged[relative]) != payload[relative]:
                fail(f"staged payload changed while copying: {source}")

        active = {relative: sha256(path) for relative, path in staged.items()}
        new_state = State(generation_id(stock), stock, active, scope_name(mkv))
        state_stage = transaction.stage_text(
            state_bytes(new_state), os.path.dirname(state_path), "state"
        )
        require_secure_path(state_stage, "staged state")
        if keep:
            staged_backups = []
            if replace_backups:
                for relative in TARGET_RELS:
                    backup_source = path_for(root, relative)
                    if sha256(backup_source) != stock[relative]:
                        fail(
                            "stock source does not match the recorded generation: "
                            f"{backup_source}"
                        )
                    transaction.ensure_dir(os.path.dirname(backup_paths[relative]))
                    staged_backup = transaction.stage_backup(
                        backup_source, backup_paths[relative]
                    )
                    require_secure_path(staged_backup, "staged original")
                    staged_backups.append((staged_backup, backup_paths[relative]))
            stock_binary_hash = stock[BIN_REL]
            shafile = transaction.stage_text(
                stock_binary_hash + "\n", os.path.dirname(shafile_path), "sha"
            )
            require_secure_path(shafile, "staged binary checksum")
            for staged_backup, destination in staged_backups:
                transaction.replace(staged_backup, destination)
            transaction.replace(shafile, shafile_path)
            for relative in (*LIB_RELS, BIN_REL):
                transaction.replace(staged[relative], path_for(root, relative))
            verify_inventory(root, active, "replacement inventory")
            transaction.replace(state_stage, state_path)
        else:
            for relative in (*LIB_RELS, BIN_REL):
                transaction.replace(staged[relative], path_for(root, relative))
            verify_inventory(root, active, "replacement inventory")
            for path in backup_paths.values():
                transaction.remove(path)
            transaction.remove(shafile_path)
            transaction.replace(state_stage, state_path)
        info("AAC fix is ACTIVE; restart Resolve")
    try:
        os.rmdir(os.path.dirname(backup_paths[LIB_RELS[0]]))
    except OSError:
        pass
    return 0


def run_revert(root, libs_root, fake, fail_after=None):
    check_root(root)
    recover_transactions(root)
    current = inventory(root)
    state = load_state(root)
    backup_paths = {relative: target_backup(root, relative) for relative in TARGET_RELS}
    verify_existing_backup_artifacts(root)
    state_path = path_for(root, STATE_REL)
    shafile_path = path_for(root, SHAFILE_REL)
    if state is None:
        if backup_inventory(root) is not None:
            fail("kept originals have no generation state; refusing an unbound restore")
        if binary_kind(path_for(root, BIN_REL), fake=fake) == "stock":
            payload = payload_inventory(libs_root, required=False)
            if payload is not None and any(
                sha256(path_for(root, relative)) == payload[relative] for relative in LIB_RELS
            ):
                fail("library replacements are present without a generation state")
            message("Resolve binary is already original")
            return 0
        fail("patched Resolve has no verified generation state; reinstall Resolve to recover")

    actual = backup_inventory(root)
    if actual is None or actual != state.stock or not sidecar_valid(root, state):
        fail("kept originals failed SHA-256 verification; refusing to restore")
    verify_stock_source(backup_paths[BIN_REL], fake)
    if current not in (state.active, state.stock):
        fail(
            "current Resolve files are a mixed or stale generation; refusing to restore "
            "old originals"
        )
    if current == state.active and binary_kind(
        path_for(root, BIN_REL), fake=fake
    ) != "patched":
        fail("recorded active generation does not contain a patched Resolve binary")
    if current == state.stock and binary_kind(path_for(root, BIN_REL), fake=fake) != "stock":
        fail("recorded stock generation does not contain a stock Resolve binary")
    with Transaction(root, fail_after=fail_after) as transaction:
        for relative, digest in current.items():
            transaction.prepare(path_for(root, relative), digest)
        for path in backup_paths.values():
            transaction.prepare(path)
        transaction.prepare(state_path)
        transaction.prepare(shafile_path)
        staged_restores = []
        if current == state.active:
            for relative in TARGET_RELS:
                source = backup_paths[relative]
                if sha256(source) != state.stock[relative]:
                    fail(f"kept original changed during preflight: {source}")
                staged = transaction.stage_link(
                    source, os.path.dirname(path_for(root, relative)), "restore"
                )
                if sha256(staged) != state.stock[relative]:
                    fail(f"staged original changed during preflight: {source}")
                staged_restores.append((staged, path_for(root, relative)))
        for staged, destination in staged_restores:
            transaction.replace(staged, destination)
        verify_inventory(root, state.stock, "restored inventory")
        transaction.remove(state_path)
        transaction.remove(shafile_path)
        for relative in reversed(TARGET_RELS):
            transaction.remove(backup_paths[relative])
        info("AAC fix reverted; Resolve is original")
    try:
        os.rmdir(os.path.dirname(backup_paths[LIB_RELS[0]]))
    except OSError:
        pass
    return 0


def safe_hash(path):
    try:
        metadata = os.lstat(path)
        if not stat.S_ISREG(metadata.st_mode):
            return None
        if os.geteuid() == 0 and (
            metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            return None
        return sha256(path)
    except (FileNotFoundError, NotADirectoryError):
        return None


def status_snapshot(root, libs_root, fake):
    recover_transactions(root)
    values = {
        relative: safe_hash(path_for(root, relative)) if os.path.isdir(root) else None
        for relative in TARGET_RELS
    }
    payload = None
    errors = []
    try:
        payload = payload_inventory(libs_root, required=False)
    except LifecycleError as error:
        errors.append(str(error))
    state = None
    state_error = None
    try:
        state = load_state(root)
    except LifecycleError as error:
        state_error = str(error)
        errors.append(state_error)
    binary_hash = values[BIN_REL]
    binary_state = binary_kind(path_for(root, BIN_REL), fake=fake) if binary_hash else "missing"
    state_view_error = None
    if state is not None and state_error is None and all(
        values[relative] is not None for relative in TARGET_RELS
    ):
        if values == state.active and binary_state != "patched":
            state_view_error = (
                "recorded active generation does not contain a patched Resolve binary"
            )
        elif values == state.stock and binary_state != "stock":
            state_view_error = "recorded stock generation does not contain a stock Resolve binary"
        if state_view_error:
            errors.append(state_view_error)

    backup_values = {
        relative: safe_hash(target_backup(root, relative)) for relative in TARGET_RELS
    }
    backups_present = any(value is not None for value in backup_values.values())
    backup_status = "missing"
    backups_verified = False
    if state is None:
        if backups_present:
            backup_status = "unbound"
    else:
        try:
            backups_verified = state_backups_valid(root, state)
        except LifecycleError as error:
            backup_status = "unsafe"
            errors.append(str(error))
        else:
            backup_status = "verified" if backups_verified else (
                "mismatch" if backups_present else "missing"
            )
    artifact_error = None
    try:
        verify_existing_backup_artifacts(root)
    except LifecycleError as error:
        artifact_error = str(error)
        if artifact_error not in errors:
            errors.append(artifact_error)
        backup_status = "unsafe"

    complete = all(values[relative] is not None for relative in TARGET_RELS)
    if not complete:
        top_state = "missing"
    elif binary_state == "unknown":
        top_state = "unsupported"
    elif state_error or state_view_error or artifact_error:
        top_state = "mixed"
    elif state is not None and values == state.active and binary_state == "patched":
        top_state = "active" if state.scope is not None else "legacy"
        if backups_present and not backups_verified:
            top_state = "mixed"
    elif state is not None and values == state.stock and binary_state == "stock":
        top_state = "stock" if state.scope is not None else "legacy"
    elif state is None and binary_state in ("stock", "patched"):
        top_state = "legacy"
    else:
        top_state = "mixed"

    generation = state.generation if state is not None else None
    generation_verified = bool(
        state is not None and state_error is None and state_view_error is None
        and generation_id(state.stock) == state.generation
    )
    return {
        "state": top_state,
        "active": top_state == "active",
        "root": root,
        "binary": {"state": binary_state, "sha256": binary_hash},
        "files": values,
        "recorded": {
            "stock": state.stock if state is not None else None,
            "active": state.active if state is not None else None,
        },
        "scope": state.scope if state is not None else None,
        "scope_known": state is not None and state.scope is not None,
        "generation": {"id": generation, "verified": generation_verified},
        "generation_id": generation,
        "generation_verified": generation_verified,
        "backups": {
            "present": backups_present,
            "verified": backups_verified,
            "state": backup_status,
            "files": {
                BACKUP_RELS[relative]: backup_values[relative]
                for relative in TARGET_RELS
            },
        },
        "backups_verified": backups_verified,
        "payload": {
            "available": payload is not None,
            "verified": payload is not None,
            "files": payload,
        },
        "errors": errors,
    }


def render_status(snapshot):
    info(f"AAC-fix status for {snapshot['root']}")
    binary_label = {
        "patched": "PATCHED",
        "stock": "original",
        "missing": "MISSING",
    }.get(snapshot["binary"]["state"], "unrecognized")
    message(f"binary:  {binary_label}")
    backups = snapshot["backups"]
    if backups["present"]:
        if backups["state"] == "verified":
            message(f"         original kept ({BACKUP_RELS[BIN_REL]}, verified)")
        elif backups["state"] == "unbound":
            message(f"         original kept ({BACKUP_RELS[BIN_REL]}, unbound)")
        else:
            message(f"         original kept ({BACKUP_RELS[BIN_REL]}, {backups['state'].upper()})")
    if any(value is not None for value in backups["files"].values()):
        message("         library originals kept (libs/_bmd_orig/)")
    payload = snapshot["payload"]["files"]
    recorded = snapshot["recorded"]
    for relative in LIB_RELS:
        current = snapshot["files"][relative]
        name = os.path.basename(relative)
        if current is None:
            message(f"lib:     {name}  MISSING")
        elif payload is not None and current == payload[relative]:
            suffix = "AAC-enabled" if name.startswith("libavcodec.") else "installed"
            message(f"lib:     {name}  {suffix} (this build)")
        elif recorded["stock"] is not None and current == recorded["stock"][relative]:
            if name.startswith("libavcodec."):
                message(f"lib:     {name}  no AAC (Blackmagic's build)")
            else:
                message(f"lib:     {name}  present (original)")
        elif recorded["active"] is not None and current == recorded["active"][relative]:
            if name.startswith("libavcodec."):
                message(f"lib:     {name}  AAC-enabled (a different build)")
            else:
                message(f"lib:     {name}  installed (a different build)")
        else:
            message(f"lib:     {name}  present")
    labels = {
        "active": "ACTIVE",
        "stock": "not installed",
        "mixed": "MIXED/INCONSISTENT",
        "legacy": "LEGACY/UNBOUND",
        "unsupported": "UNSUPPORTED",
        "missing": "MISSING",
    }
    info(f"AAC support: {labels[snapshot['state']]}")
    for error in snapshot["errors"]:
        warn(error)


def run_status(root, libs_root, fake, json_output=False, require_active=False):
    if not json_output:
        check_root(root)
    snapshot = status_snapshot(root, libs_root, fake)
    if json_output:
        print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
    else:
        render_status(snapshot)
    if require_active and snapshot["state"] != "active":
        return 1
    return 0


def parser():
    result = argparse.ArgumentParser(prog="aacpatch.lifecycle")
    result.add_argument("command", choices=("apply", "revert", "status"))
    result.add_argument("root")
    result.add_argument("--originals", choices=("keep", "remove"), default="keep")
    result.add_argument("--no-mkv", dest="mkv", action="store_false")
    result.add_argument("--verbose", action="store_true")
    result.add_argument("--json", dest="json_output", action="store_true")
    result.add_argument("--require-active", action="store_true")
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result.add_argument("--repo", default=package_root)
    result.add_argument("--pylibs", default=os.path.join(package_root, "vendor", "pylibs"))
    result.add_argument("--patch-python", default=sys.executable)
    result.add_argument(
        "--e9tool", default=os.path.join(package_root, "vendor", "e9patch", "e9tool")
    )
    result.add_argument("--trampoline", default=os.path.join(package_root, "vendor", "aacadd"))
    result.add_argument("--libs", default=os.environ.get("AAC_LIBS", ""))
    result.add_argument("--fake-patch", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--fail-after-commit", type=int, default=None, help=argparse.SUPPRESS)
    result.set_defaults(mkv=True)
    return result


def main(argv):
    args = parser().parse_args(argv[1:])
    try:
        if args.command == "apply":
            return run_apply(
                args.root, args.originals == "keep", args.mkv, args.repo,
                args.pylibs, args.patch_python, args.e9tool, args.trampoline,
                args.libs, args.verbose, args.fake_patch, args.fail_after_commit,
            )
        if args.command == "revert":
            return run_revert(args.root, args.libs, args.fake_patch, args.fail_after_commit)
        return run_status(
            args.root, args.libs, args.fake_patch, args.json_output, args.require_active
        )
    except LifecycleError as error:
        print(f"\033[31m[error]\033[0m {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"\033[31m[error]\033[0m {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"\033[31m[error]\033[0m {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\033[31m[error]\033[0m interrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
