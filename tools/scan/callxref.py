#!/usr/bin/env python3
"""Find direct `call` sites targeting a virtual address.

    scan/callxref.py 0x5b56150
    scan/callxref.py --plt avcodec_find_decoder=0x779560 avcodec_open2=0x787410

Narrower than xref.py: it only matches `E8 rel32`, so the hits really are calls.
Pointing it at a PLT stub is how the FFmpeg entry points into Resolve were
enumerated -- e.g. finding that IOAudioFFMPEGCodec's constructor has 9 callers
across several decoder classes, which is why the gate2/gate3 patches are shared
by the QuickTime and Matroska paths.
"""
import argparse

from _common import add_binary_arg, open_target, exec_spans, require_numpy


def call_sites(buf, em, target_va, chunk=1 << 24):
    np = require_numpy()
    out = []
    for off, size, seg_va in exec_spans(em):
        delta = seg_va - off
        p, end = off, off + size
        while p < end:
            n = min(chunk, end - p)
            window = buf[p:p + n + 5]
            if len(window) < 10:
                break
            a = np.frombuffer(window, dtype=np.uint8)
            m = len(window) - 5
            op = a[0:m]
            b = a.astype(np.int64)
            d = b[1:m + 1] | (b[2:m + 2] << 8) | (b[3:m + 3] << 16) | (b[4:m + 4] << 24)
            d = np.where(d >= 1 << 31, d - (1 << 32), d)
            pos = p + np.arange(m, dtype=np.int64)
            # call at file offset q ends at q+5; target = va(q)+5+rel32
            idx = np.nonzero((op == 0xE8) & (d + pos + delta + 5 == target_va))[0]
            out.extend(int(pos[i]) for i in idx)
            p += n
    return out


def main():
    ap = add_binary_arg(argparse.ArgumentParser(description=__doc__))
    ap.add_argument("targets", nargs="+",
                    help="target vaddrs (hex), or name=vaddr pairs")
    args = ap.parse_args()

    buf, em = open_target(args.binary)
    for spec in args.targets:
        name, _, addr = spec.rpartition("=")
        tv = int(addr, 16)
        hits = call_sites(buf, em, tv)
        label = f"{name} " if name else ""
        print(f"{label}0x{tv:x}: {len(hits)} call site(s)")
        for o in hits:
            print(f"    call @ vaddr 0x{em.off_to_va(o):x}")


if __name__ == "__main__":
    main()
