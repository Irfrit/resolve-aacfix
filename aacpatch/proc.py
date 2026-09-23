import argparse
import os
from pathlib import Path


def _executable_path(proc_root, pid):
    path = proc_root / str(pid) / "exe"
    try:
        return os.readlink(path)
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _comm(proc_root, pid):
    try:
        return (proc_root / str(pid) / "comm").read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError, UnicodeError):
        return None


def find_processes(proc_root="/proc", resolve_root="/opt/resolve"):
    root = Path(proc_root)
    target = os.path.abspath(os.path.join(resolve_root, "bin", "resolve"))
    result = []
    try:
        entries = list(root.iterdir())
    except (FileNotFoundError, PermissionError, OSError):
        return result
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        comm = _comm(root, pid)
        executable = _executable_path(root, pid)
        executable_matches = executable == target or executable == f"{target} (deleted)"
        if comm == "resolve" or executable_matches:
            result.append({"pid": pid, "comm": comm, "executable": executable})
    return sorted(result, key=lambda item: item["pid"])


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aacpatch.proc")
    parser.add_argument("--proc-root", default="/proc")
    parser.add_argument("--resolve-root", default="/opt/resolve")
    args = parser.parse_args(argv)
    processes = find_processes(args.proc_root, args.resolve_root)
    for process in processes:
        print(f"{process['pid']} {process['executable'] or process['comm']}")
    return 0 if processes else 1


if __name__ == "__main__":
    raise SystemExit(main())
