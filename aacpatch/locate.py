"""Locate every AAC patch site in a Resolve binary by signature.

Reports, per site: found / one-hit / capstone-verified, and the resolved file
offset + virtual address.  Writes nothing.  Both the QuickTime group (always
required) and the Matroska group (used only with --mkv) are reported.

This is the first thing to run against an unfamiliar build: it says exactly
which signature stopped matching, which is the whole diagnosis when a new
Resolve release breaks the patcher.  The patcher consumes the same signatures,
so if this reports every site LOCATED/ORIGINAL, `aacpatch.additive` will resolve
its addresses too.

    python -m aacpatch.locate /opt/resolve/bin/resolve
"""
import sys
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

from .elfmap import ElfMap
from .sites import SIGNATURES, MKV_SIGNATURES

GREEN, RED, YEL, DIM, RST = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def locate_all(path):
    """Locate every signature in both groups.  Returns (ElfMap, [result...]).

    Each result carries the signature, its group, the resolved file offset and
    vaddr, and a state:
      MISS       pattern not found, or found more than once (ambiguous)
      BADVERIFY  found, but the instruction there is not what the site expects
      ORIGINAL   static-swap site holding its pre-patch bytes
      PATCHED    site already carries the patched bytes
      UNEXPECTED found, but the edit bytes are neither original nor patched
      LOCATED    found; nothing to check (located-only / reference sites)
    """
    with open(path, "rb") as f:
        buf = f.read()
    em = ElfMap(path)
    cs = Cs(CS_ARCH_X86, CS_MODE_64)
    results = []
    for group, sigs in (("quicktime", SIGNATURES), ("matroska", MKV_SIGNATURES)):
        for sig in sigs:
            results.append(_check(sig, group, buf, em, cs))
    return em, results


def _check(sig, group, buf, em, cs):
    r = {"sig": sig, "group": group, "off": None, "va": None,
         "state": None, "note": ""}
    try:
        off = sig.locate(buf, em)
    except LookupError as e:
        r["state"] = "MISS"
        r["note"] = str(e).split(": ", 1)[-1]
        return r
    r["off"], r["va"] = off, em.off_to_va(off)
    # capstone verification, if the site declared one
    if sig.verify and not sig.verify(cs, off, em, buf):
        r["state"] = "BADVERIFY"
        return r
    # for static-swap sites, confirm the current bytes are orig or new
    if sig.orig:
        cur = buf[off + sig.edit_off: off + sig.edit_off + len(sig.orig)]
        if cur == sig.orig:
            r["state"] = "ORIGINAL"
        elif sig.new and cur == sig.new:
            r["state"] = "PATCHED"
        else:
            r["state"] = "UNEXPECTED"
            r["note"] = f"edit bytes = {cur.hex(' ')}"
    elif sig.patched_test is not None:
        r["state"] = "PATCHED" if sig.patched_test(buf, off) else "LOCATED"
    else:
        r["state"] = "LOCATED"        # located; state not checked
    return r


def main(argv):
    if len(argv) != 2:
        print(f"usage: {argv[0].split('/')[-1]} <resolve-binary>")
        return 2
    path = argv[1]
    em, results = locate_all(path)

    print(f"target : {path}")
    print(f"type   : {'PIE' if em.is_pie else 'non-PIE'} {em.machine} "
          f"entry 0x{em.entry:x}")
    print("LOAD segments:")
    print(em.describe())
    print()

    colors = {"ORIGINAL": GREEN, "PATCHED": YEL, "LOCATED": GREEN,
              "UNEXPECTED": RED, "BADVERIFY": RED, "MISS": RED}
    titles = {"quicktime": "QuickTime (MP4/MOV/M4A) -- always required",
              "matroska":  "Matroska (.mkv) -- used with --mkv"}
    ok = True
    group = None
    for r in results:
        if r["group"] != group:
            group = r["group"]
            print(f"{titles[group]}:")
        s = r["state"]
        col = colors.get(s, RST)
        loc = (f"file 0x{r['off']:x}  va 0x{r['va']:x}"
               if r["off"] is not None else "-")
        line = f"  [{col}{s:9}{RST}] {r['sig'].name:18} {loc}"
        if r["note"]:
            line += f"  {DIM}{r['note']}{RST}"
        print(line)
        print(f"                {DIM}{r['sig'].desc}{RST}")
        if s in ("MISS", "BADVERIFY", "UNEXPECTED"):
            ok = False

    print()
    print(f"{GREEN if ok else RED}"
          f"{'ALL SITES LOCATED' if ok else 'SOME SITES FAILED — refusing to patch'}"
          f"{RST}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
