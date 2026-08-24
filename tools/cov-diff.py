#!/usr/bin/env python3
"""Diff two basic-block coverage bitmaps produced by tools/cov.c.

    cov-diff.py -b RESOLVE_BIN /tmp/cov_ac3.bin /tmp/cov_aac.bin

The method, and the reason this exists: instrument every branch/call in a region
with cov.c, then run Resolve twice -- once importing a file whose codec WORKS
(AC-3), once importing the AAC equivalent -- and diff the bitmaps.

  "AC-3-only" blocks = the good path AAC never reached, i.e. where AAC was
                       filtered out.  Read from the top; the first one is the
                       gate.
  "AAC-only"  blocks = where AAC went instead.

This is what localised the Matroska divergence to a single function
(0x5c6ad30, the container stream-setup) in one run, after static reading had
produced three wrong models in a row.  It is the right first tool for any
"format X doesn't work but format Y does" question -- including the standalone
.aac/ADTS work that is still open.

Its blind spot, learned the hard way: it only sees CONTROL FLOW.  The MKV
admission gate turned out to be a data-flow decision (a support-category value
set without any branch divergence upstream), and no amount of coverage diffing
could find it.  When the diff goes quiet but the behaviour still differs, switch
to a gdb watchpoint on the value.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor", "pylibs"))

from capstone import Cs, CS_ARCH_X86, CS_MODE_64      # noqa: E402
from aacpatch.elfmap import ElfMap                    # noqa: E402

# Must match BASE in tools/cov.c -- the bitmap is 1 bit per byte-address from
# there, so a mismatch silently reports addresses that are off by a constant.
DEFAULT_BASE = 0x5B00000


def load(path, base):
    bits = set()
    with open(path, "rb") as f:
        data = f.read()
    for i, byte in enumerate(data):
        if byte:
            for b in range(8):
                if byte & (1 << b):
                    bits.add(base + i * 8 + b)
    return bits


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("good", nargs="?", default="/tmp/cov_ac3.bin",
                    help="bitmap from the run that WORKS (default /tmp/cov_ac3.bin)")
    ap.add_argument("bad", nargs="?", default="/tmp/cov_aac.bin",
                    help="bitmap from the run that fails (default /tmp/cov_aac.bin)")
    ap.add_argument("-b", "--binary",
                    default=os.environ.get("RESOLVE_BIN", "/opt/resolve/bin/resolve"),
                    help="binary to disassemble against; use the UNPATCHED one "
                         "so vaddrs line up with the signature engine's output")
    ap.add_argument("--base", type=lambda s: int(s, 0), default=DEFAULT_BASE,
                    help=f"bitmap base address, matching cov.c BASE "
                         f"(default {DEFAULT_BASE:#x})")
    ap.add_argument("--limit", type=int, default=200,
                    help="max addresses to print per side (default 200)")
    args = ap.parse_args()

    good = load(args.good, args.base)
    bad = load(args.bad, args.base)
    good_only = sorted(good - bad)
    bad_only = sorted(bad - good)
    print(f"{os.path.basename(args.good)} blocks={len(good)}  "
          f"{os.path.basename(args.bad)} blocks={len(bad)}  "
          f"good-only={len(good_only)}  bad-only={len(bad_only)}\n")

    with open(args.binary, "rb") as f:
        buf = f.read()
    em = ElfMap(args.binary)
    cs = Cs(CS_ARCH_X86, CS_MODE_64)

    def disasm(va):
        try:
            o = em.va_to_off(va)
            i = next(cs.disasm(buf[o:o + 15], va, count=1))
            return f"{i.mnemonic} {i.op_str}"
        except Exception:
            return "?"

    for title, lst in (
            (f"good-only -- reached by {os.path.basename(args.good)} only "
             f"(the path the failing run missed)", good_only),
            (f"bad-only -- reached by {os.path.basename(args.bad)} only "
             f"(where it went instead)", bad_only)):
        print(f"===== {title}: {len(lst)} =====")
        for va in lst[:args.limit]:
            print(f"  0x{va:x}: {disasm(va)}")
        if len(lst) > args.limit:
            print(f"  ... {len(lst) - args.limit} more (raise --limit)")
        print()


if __name__ == "__main__":
    main()
