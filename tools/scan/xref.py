#!/usr/bin/env python3
"""Find RIP-relative references to a virtual address.

    scan/xref.py 0x5b56150 [0x...]

Scans every executable segment for a 4-byte displacement that, taken as
RIP-relative, points at the target.  This is the general-purpose "who touches
this?" tool -- it finds `lea`/`mov`/`call [rip+..]` alike, because it matches on
the arithmetic rather than on any opcode.

The reported instruction vaddr is approximate (displacement address minus a
typical 3-byte opcode+modrm prefix); disassemble around it to get the real one.
"""
import argparse

from _common import add_binary_arg, open_target, exec_spans, require_numpy


def xrefs(buf, em, target_va, chunk=1 << 24):
    np = require_numpy()
    out = []
    for off, size, _va in exec_spans(em):
        p, end = off, off + size
        while p < end:
            n = min(chunk, end - p)
            window = buf[p:p + n + 4]
            if len(window) < 8:
                break
            a = np.frombuffer(window, dtype=np.uint8).astype(np.int64)
            m = len(window) - 4
            d = a[0:m] | (a[1:m + 1] << 8) | (a[2:m + 2] << 16) | (a[3:m + 3] << 24)
            d = np.where(d >= 1 << 31, d - (1 << 32), d)          # sign-extend
            # A disp32 at file offset q refers to target_va iff
            #   va(q) + 4 + disp == target_va
            pos = p + np.arange(m, dtype=np.int64)
            # va(q) == q + delta for the segment we are in
            delta = _va - off
            out.extend(int(x) for x in
                       (pos[np.nonzero(d + pos + delta + 4 == target_va)[0]]))
            p += n
    return out


def main():
    ap = add_binary_arg(argparse.ArgumentParser(description=__doc__))
    ap.add_argument("targets", nargs="+", help="target virtual addresses (hex)")
    ap.add_argument("--limit", type=int, default=40,
                    help="max hits to print per target (default 40)")
    args = ap.parse_args()

    buf, em = open_target(args.binary)
    for t in args.targets:
        tv = int(t, 16)
        hits = xrefs(buf, em, tv)
        print(f"target vaddr 0x{tv:x}: {len(hits)} xref(s)")
        for o in hits[:args.limit]:
            print(f"   disp@file 0x{o:x}  (instr vaddr ~0x{em.off_to_va(o) - 3:x})")


if __name__ == "__main__":
    main()
