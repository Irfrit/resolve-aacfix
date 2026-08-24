"""Signature-based site location for version-independent patching.

A Signature locates ONE patch site by a masked byte pattern.  Bytes that move
between Resolve builds (RIP-relative disp32, absolute imm32 pointers into
rodata/data) are wildcarded with the mask so the pattern keys only on the
version-invariant opcodes and immediates (fourcc constants, AVCodecID values,
entry counts).

Location contract, enforced on every run:
  * the masked pattern must match EXACTLY ONCE inside the searched segments,
    otherwise the site is ambiguous and we refuse to patch;
  * an optional capstone `verify` callback re-checks the decoded instruction(s)
    at the hit so a coincidental byte match can't slip through.
"""
import re
from dataclasses import dataclass, field
from typing import Callable, Optional


def parse_pattern(text):
    """'41 81 ff ?? ?? ?? ?? 0f 84' -> (pattern_bytes, mask_bytes).

    A token is two hex nibbles, or '??' / 'XX' for a wildcard byte, or a
    'xx' with a single '?' nibble for a half-wildcard (rare; whole-byte is
    the norm).  Whitespace between tokens is ignored.
    """
    pat, mask = bytearray(), bytearray()
    for tok in text.split():
        tok = tok.strip()
        if tok in ("??", "XX", "xx"):
            pat.append(0x00)
            mask.append(0x00)
        elif re.fullmatch(r"[0-9a-fA-F]{2}", tok):
            pat.append(int(tok, 16))
            mask.append(0xFF)
        else:
            raise ValueError(f"bad pattern token: {tok!r}")
    return bytes(pat), bytes(mask)


def _find_all_masked(buf, pat, mask, base_off):
    """Yield absolute file offsets where masked pat matches in buf.

    Uses the first fully-fixed (mask==0xFF) byte-run as an anchor for a fast
    bytes.find scan, then verifies the full mask.  This keeps a multi-hundred-MB
    segment scan tractable without a regex over the whole buffer.
    """
    # pick the longest run of fixed bytes as the anchor
    best_start, best_len, cur_start, cur_len = 0, 0, None, 0
    for i, m in enumerate(mask):
        if m == 0xFF:
            if cur_start is None:
                cur_start, cur_len = i, 1
            else:
                cur_len += 1
            if cur_len > best_len:
                best_start, best_len = cur_start, cur_len
        else:
            cur_start, cur_len = None, 0
    if best_len == 0:
        raise ValueError("pattern has no fixed anchor byte")

    anchor = pat[best_start:best_start + best_len]
    plen = len(pat)
    pos = 0
    while True:
        hit = buf.find(anchor, pos)
        if hit < 0:
            return
        start = hit - best_start
        pos = hit + 1
        if start < 0 or start + plen > len(buf):
            continue
        window = buf[start:start + plen]
        if all((window[j] & mask[j]) == (pat[j] & mask[j]) for j in range(plen)):
            yield base_off + start


@dataclass
class Signature:
    name: str
    pattern: str                       # masked hex, see parse_pattern
    where: str = "exec"                # 'exec' or 'ro'
    edit_off: int = 0                  # byte offset from match start to the edit
    orig: bytes = b""                  # expected bytes at edit_off (pre-patch)
    new: bytes = b""                   # replacement bytes (equal length)
    verify: Optional[Callable] = None  # (cs, match_off, elfmap, buf) -> bool
    # (buf, match_off) -> bool: True if this site is already patched. Used by the
    # locator to report computed sites (which have no static orig/new) as
    # PATCHED vs LOCATED.
    patched_test: Optional[Callable] = None
    desc: str = ""
    # kind drives what the applier does with the site:
    #   static   -> fixed equal-length orig->new swap
    #   computed -> replacement derived from the decoded instruction (patch.py)
    #   additive -> Phase 3 (e9patch), not part of the destructive set
    #   ref      -> located only, to feed a computed site (never written)
    kind: str = "static"
    _pm: tuple = field(default=None, repr=False)

    def __post_init__(self):
        self._pm = parse_pattern(self.pattern)
        if self.orig and self.new and len(self.orig) != len(self.new):
            raise ValueError(f"{self.name}: orig/new length differ "
                             "(in-place edits must be equal length)")

    def locate(self, buf, elfmap):
        """Return the single match file offset, or raise on 0 / >1 matches."""
        pat, mask = self._pm
        ranges = (elfmap.exec_ranges() if self.where == "exec"
                  else elfmap.ro_ranges())
        hits = []
        for foff, size, _va in ranges:
            seg = buf[foff:foff + size]
            hits.extend(_find_all_masked(seg, pat, mask, foff))
            if len(hits) > 1:
                break
        if len(hits) == 0:
            raise LookupError(f"{self.name}: pattern not found")
        if len(hits) > 1:
            raise LookupError(f"{self.name}: ambiguous, {len(hits)} matches "
                              f"at {[hex(h) for h in hits]}")
        off = hits[0]
        # Re-check the decoded instruction at the hit, so a coincidental byte
        # match can't slip through.  This used to be documented but never run
        # (only the diagnostic CLI called it), which meant the verifiers on the
        # gate3a/gate3b sites were dead code in the code path that writes
        # binaries.  Run it here so every caller gets it.
        if self.verify is not None:
            from capstone import Cs, CS_ARCH_X86, CS_MODE_64
            cs = Cs(CS_ARCH_X86, CS_MODE_64)
            if not self.verify(cs, off, elfmap, buf):
                raise LookupError(f"{self.name}: match at {off:#x} failed "
                                  "instruction verification")
        return off
