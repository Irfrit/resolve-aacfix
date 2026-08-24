#!/usr/bin/env python3
"""Count 4-byte immediates in the executable segments.

    scan/imm.py                       # the AVCodecID constants, by default
    scan/imm.py 0x15002 0x61616320    # any values

Blunt but effective: AAC's AVCodecID is 0x15002, so every `cmp reg,0x15002` in
the binary shows up here.  Comparing those counts between the Linux build and
the macOS build (which still has AAC) is what showed macOS has a *dedicated*
AAC extradata path -- 15 such sites in its text, none of them at the gate3
region that Linux and macOS share byte-for-byte.

Defaults cover the audio codec IDs that matter here; the fourcc constants are
worth passing explicitly when hunting dispatch tables ('aac ' = 0x61616320).
"""
import argparse

from _common import add_binary_arg, open_target, exec_spans, require_numpy

DEFAULTS = [("MP2 ", 0x15000), ("MP3 ", 0x15001), ("AAC ", 0x15002),
            ("AC3 ", 0x15003), ("FLAC", 0x1500C), ("ALAC", 0x15010)]


def find_imm(buf, em, val, chunk=1 << 24):
    np = require_numpy()
    tgt = np.frombuffer(val.to_bytes(4, "little"), dtype=np.uint8)
    out = []
    for off, size, _va in exec_spans(em):
        p, end = off, off + size
        while p < end:
            n = min(chunk, end - p)
            window = buf[p:p + n + 4]
            if len(window) < 8:
                break
            a = np.frombuffer(window, dtype=np.uint8)
            m = len(window) - 4
            hit = ((a[0:m] == tgt[0]) & (a[1:m + 1] == tgt[1]) &
                   (a[2:m + 2] == tgt[2]) & (a[3:m + 3] == tgt[3]))
            out.extend(int(p + i) for i in np.nonzero(hit)[0])
            p += n
    return out


def main():
    ap = add_binary_arg(argparse.ArgumentParser(description=__doc__))
    ap.add_argument("values", nargs="*", help="32-bit values (hex)")
    ap.add_argument("--limit", type=int, default=30,
                   help="max hits to print per value (default 30)")
    args = ap.parse_args()

    wanted = ([(v, int(v, 16)) for v in args.values] if args.values
              else [(n, v) for n, v in DEFAULTS])
    buf, em = open_target(args.binary)
    for name, v in wanted:
        hits = find_imm(buf, em, v)
        print(f"{name} 0x{v:x}: {len(hits)} occurrence(s)")
        for o in hits[:args.limit]:
            print(f"    file 0x{o:x} -> vaddr 0x{em.off_to_va(o):x}")


if __name__ == "__main__":
    main()
