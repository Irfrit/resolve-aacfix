"""Shared plumbing for the .text scanners.

These were written because Ghidra could not usefully chew on a 653 MB stripped
binary (the headless import was tried and abandoned).  Scanning the executable
segments directly with numpy turned out to be both faster and easier to reason
about, and it is how essentially every address in docs/reverse-engineering.md
was originally found.

Everything here derives its geometry from the ELF program headers via
aacpatch.elfmap, so nothing is pinned to one Resolve build.  That matters more
than it sounds: Resolve's last LOAD segment uses delta 0x401000, not 0x400000,
and an earlier generation of these scripts hardcoded a single base -- which
silently produced wrong vaddrs for every vtable and typeinfo it reported.
"""
import os
import sys

# Run from a git checkout without installing anything.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_ROOT, os.path.join(_ROOT, "vendor", "pylibs")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from aacpatch.elfmap import ElfMap          # noqa: E402

DEFAULT_BIN = os.environ.get("RESOLVE_BIN", "/opt/resolve/bin/resolve")


def add_binary_arg(ap):
    ap.add_argument("-b", "--binary", default=DEFAULT_BIN,
                    help=f"Resolve binary to scan (default: {DEFAULT_BIN}, "
                         "or $RESOLVE_BIN).  Prefer an UNPATCHED binary: "
                         "e9patch output has an extra LOAD segment whose "
                         "trampolines will show up in the results.")
    return ap


def open_target(path):
    """Return (bytes, ElfMap).  Reads the whole file: at 653 MB that is a few
    seconds and a lot simpler than windowed reads, and every scanner here wants
    random access anyway."""
    with open(path, "rb") as f:
        buf = f.read()
    return buf, ElfMap(path)


def exec_spans(em):
    """[(file_off, size, vaddr)] for the executable LOAD segments."""
    return em.exec_ranges()


def require_numpy():
    try:
        import numpy
    except ImportError:
        sys.exit("this scanner needs numpy (pip install numpy)")
    return numpy
