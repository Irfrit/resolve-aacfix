"""ELF file-offset <-> virtual-address mapping, derived at runtime.

Every patch offset in this project depends on translating between the on-disk
file offset and the loaded virtual address.  The Resolve binary has four LOAD
segments and the LAST one uses a different delta (0x401000 rather than
0x400000) -- mishandling it silently corrupts every vtable / typeinfo read.
We never hardcode that; we read the program headers and compute per-segment.
"""
from elftools.elf.elffile import ELFFile


class ElfMap:
    def __init__(self, path):
        self.path = path
        self.segs = []  # (file_off, vaddr, filesz, flags)
        with open(path, "rb") as f:
            elf = ELFFile(f)
            self.is_pie = elf.header["e_type"] == "ET_DYN"
            self.machine = elf.header["e_machine"]
            self.entry = elf.header["e_entry"]
            for seg in elf.iter_segments():
                if seg["p_type"] != "PT_LOAD":
                    continue
                self.segs.append((
                    seg["p_offset"], seg["p_vaddr"],
                    seg["p_filesz"], seg["p_flags"],
                ))
        self.segs.sort()

    def off_to_va(self, off):
        for foff, vaddr, filesz, _ in self.segs:
            if foff <= off < foff + filesz:
                return vaddr + (off - foff)
        raise ValueError(f"file offset 0x{off:x} not in any LOAD segment")

    def va_to_off(self, va):
        for foff, vaddr, filesz, _ in self.segs:
            if vaddr <= va < vaddr + filesz:
                return foff + (va - vaddr)
        raise ValueError(f"vaddr 0x{va:x} not in any LOAD segment")

    def exec_ranges(self):
        """(file_off, size, vaddr) for executable LOAD segments -- where code
        signatures are searched."""
        PF_X = 0x1
        return [(fo, sz, va) for fo, va, sz, fl in self.segs if fl & PF_X]

    def ro_ranges(self):
        """Non-executable, non-writable LOAD segments -- where rodata tables
        (e.g. the fourcc->AVCodecID map) live."""
        PF_X, PF_W = 0x1, 0x2
        return [(fo, sz, va) for fo, va, sz, fl in self.segs
                if not (fl & PF_X) and not (fl & PF_W)]

    def describe(self):
        out = []
        for fo, va, sz, fl in self.segs:
            perm = "".join(c if fl & b else "-" for c, b in
                           (("R", 4), ("W", 2), ("X", 1)))
            out.append(f"  off 0x{fo:09x}  va 0x{va:011x}  sz 0x{sz:x}  "
                       f"{perm}  delta 0x{va - fo:x}")
        return "\n".join(out)
