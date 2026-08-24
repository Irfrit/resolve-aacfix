# The trampoline

`aacadd.c` is the actual fix — every AAC gate in `bin/resolve` is undone by one
of the functions in this file. It is compiled by e9patch's `e9compile.sh` into a
freestanding PIE, and `e9tool` splices calls to it into the target binary.

It is **not** a library and not a program. It has no libc, no relocations and no
SIMD (all three would break e9tool's call instrumentation); `#include "stdlib.c"`
pulls in e9patch's own freestanding runtime, which its authors placed under the
MIT license by explicit exception.

## The additive rule

Every function here either acts on AAC or returns 0. Returning 0 means e9tool's
`if CALL goto` form falls through and the **original instruction runs unchanged**.
That is the entire safety argument for this project: nothing upstream — `NONE`,
AC-3, FLAC, ALAC, MP3, PCM, AV3A — can be affected by code that never fires for
them.

If you add a function here, keep that property. It is also the condition under
which trampoline rewriting is reliable at all
(see [`../../docs/reverse-engineering.md`](../../docs/reverse-engineering.md#5-why-additive-and-why-e9patch)).

## What each function does

| function | gate | container |
|---|---|---|
| `redirect(val, wanted, target)` | 1, 3a, 3b, and the MKV dispatch | both |
| `build5(map, insert_range)` | 2 — rebuilds the fourcc→AVCodecID map with 5 rows | both |
| `aac_esds_fix(rsp)` | 4 — trims an `esds` blob down to the bare `AudioSpecificConfig` | both |
| `mkv_undrop(matched, rax, proceed)` | un-drops `A_AAC` at the CodecID parser | Matroska |
| `mkv_asc(rsp, r12, asc_parser, finalize)` | parses `CodecPrivate` as an ASC instead of frame-probing | Matroska |

`redirect` is deliberately generic: it is used at four different sites with
different registers and different comparison values. Addresses and target
addresses are **not** compiled in — they are resolved per build by the signature
engine and passed as arguments by `aacpatch/additive.py`, which is what makes the
same trampoline binary work across Resolve versions.

## Building

Normally you don't: `scripts/dev-setup.sh e9patch` builds e9patch and compiles
this in one step, and release tarballs ship the result as `vendor/aacadd`. By
hand:

```sh
cd vendor/e9patch && ./e9compile.sh ../../src/trampoline/aacadd.c
```

`scripts/check-trampoline.sh` asserts the exact set of exported symbols. That
check exists because a stale prebuilt trampoline — exporting a symbol whose
source had been deleted — once went unnoticed for a while, which meant the
shipped binary could not be rebuilt from the shipped source.
