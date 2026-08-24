#!/usr/bin/env python3
"""Walk RTTI to find a C++ class's typeinfo and vtable in a stripped binary.

    scan/cls.py 20IOQuickTimeAudioDecoder 15IOMKVAudioDecoder

Takes the *mangled* type name (what `typeid(T).name()` returns -- length prefix
plus identifier, e.g. `18IOAudioFFMPEGCodec`) and follows the standard Itanium
ABI chain:

    "18IOAudioFFMPEGCodec\\0"        the typeinfo-name string
      <- pointer to it                _ZTI...  (typeinfo object; name is at +8)
        <- pointer to the typeinfo    _ZTV...  (vtable; virtuals start at +16)

Resolve is stripped, so RTTI is the only structural handle on its class
hierarchy -- this is how IOQuickTimeAudioDecoder, IOMKVAudioDecoder,
IOADTSAudioDecoder and IOAudioFFMPEGCodec were located in the first place, and
it is the first thing to run when adding support for another container's
decoder class.

Note the chain has false positives: any 8-byte value that happens to equal an
address is reported.  Cross-check a candidate vtable by disassembling the
function at +16 and confirming it looks like a destructor.
"""
import argparse

from _common import add_binary_arg, open_target


def find_all(buf, needle, limit):
    out, i = [], 0
    while len(out) < limit:
        i = buf.find(needle, i)
        if i < 0:
            return out
        out.append(i)
        i += 1
    return out


def main():
    ap = add_binary_arg(argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter))
    ap.add_argument("names", nargs="+",
                    help="mangled type names, e.g. 18IOAudioFFMPEGCodec")
    ap.add_argument("--limit", type=int, default=10,
                    help="max hits per level (default 10)")
    args = ap.parse_args()

    buf, em = open_target(args.binary)

    def ptrs_to(va, limit):
        return find_all(buf, va.to_bytes(8, "little"), limit)

    for cname in args.names:
        print(f"\n=== {cname} ===")
        for off in find_all(buf, cname.encode() + b"\x00", args.limit):
            try:
                sv = em.off_to_va(off)
            except ValueError:
                continue          # string outside any LOAD segment
            print(f" typeinfo-name string @ vaddr 0x{sv:x}")
            for p in ptrs_to(sv, args.limit):
                # _ZTI layout: [vptr to type_info's own vtable][name ptr]
                ti_off = p - 8
                ti_va = em.off_to_va(ti_off)
                print(f"   referenced by ptr @0x{em.off_to_va(p):x}"
                      f"  => typeinfo @ vaddr 0x{ti_va:x}")
                for q in ptrs_to(ti_va, args.limit * 2):
                    # _ZTV layout: [offset-to-top][typeinfo ptr][virtuals...]
                    vt_va = em.off_to_va(q - 8)
                    print(f"      typeinfo ref @0x{em.off_to_va(q):x}"
                          f" => vtable @ vaddr 0x{vt_va:x}"
                          f" (virtuals at 0x{vt_va + 16:x})")


if __name__ == "__main__":
    main()
