"""Tests for the ELF geometry layer.

Every patch offset in this project is derived from these translations, and the
one thing that makes Resolve unusual is worth restating: its four LOAD segments
do NOT share a single vaddr-minus-offset delta -- the last one uses 0x401000
where the others use 0x400000.  An earlier generation of this code assumed one
base and silently produced wrong addresses for every vtable it reported.  So the
tests below check per-segment translation rather than a global base.

Run: python -m pytest tests/   (PYTHONPATH=.:vendor/pylibs)
"""
import shutil

import pytest

from aacpatch.elfmap import ElfMap

# /bin/bash is a PIE with several LOAD segments and is present everywhere we run.
TARGET = shutil.which("bash") or "/bin/bash"


@pytest.fixture(scope="module")
def em():
    return ElfMap(TARGET)


def test_has_load_segments(em):
    assert em.segs, "no PT_LOAD segments parsed"
    assert em.machine == "EM_X86_64"


def test_round_trip_every_segment(em):
    # Round-trip the first, middle and last byte of each segment: an off-by-one
    # at a segment boundary is exactly the bug that would map a patch site into
    # the neighbouring segment.
    for off, _va, size, _flags in _segments(em):
        for probe in (0, size // 2, size - 1):
            o = off + probe
            assert em.va_to_off(em.off_to_va(o)) == o


def test_per_segment_deltas_are_independent(em):
    # Not "all deltas are equal" -- that is exactly what must NOT be assumed.
    for off, va, size, _flags in _segments(em):
        assert em.off_to_va(off) == va
        assert em.off_to_va(off + size - 1) == va + size - 1


def test_out_of_range_is_an_error(em):
    with pytest.raises(ValueError):
        em.off_to_va(1 << 60)
    with pytest.raises(ValueError):
        em.va_to_off(1 << 60)


def test_exec_and_ro_ranges_are_disjoint_and_within_segments(em):
    ex = em.exec_ranges()
    assert ex, "no executable ranges"
    all_spans = {(o, s) for o, s, _v in ex} | {(o, s) for o, s, _v in em.ro_ranges()}
    assert len(all_spans) == len(ex) + len(em.ro_ranges())
    for off, size, va in ex:
        assert em.off_to_va(off) == va
        assert size > 0


def _segments(em):
    """ElfMap stores segments as tuples; normalise to (off, va, size, flags)."""
    out = []
    for seg in em.segs:
        off, va, size, flags = seg[0], seg[1], seg[2], seg[3]
        if size:
            out.append((off, va, size, flags))
    return out
