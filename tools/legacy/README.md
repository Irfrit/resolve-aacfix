# Superseded

`aacfix.c` was the original solution to gate 4. It is an `LD_PRELOAD` shim that
interposes `avcodec_open2`, and when the codec is AAC and the extradata starts
with an MPEG-4 `ES_Descr` tag (`0x03`), walks the descriptor tree and replaces it
with just the `AudioSpecificConfig` FFmpeg actually wants.

It worked, but it meant every launch of Resolve needed a wrapper script setting
`LD_PRELOAD`. That whole requirement is gone: the same descriptor walk now runs
inside the binary as the `aac_esds_fix` trampoline in
`../../src/trampoline/aacadd.c`, applied at the point the config blob is copied.
**The shipped patch needs no `LD_PRELOAD` and no launcher** — run
`/opt/resolve/bin/resolve` normally.

It is kept for two reasons. Its `find_asc()` is the readable version of the
in-binary code, written against the public FFmpeg API with real headers. And it
is a good illustration of a technique worth reusing: when you need a binary
patch, prototype it as an `LD_PRELOAD` shim first, confirm the logic is right,
and only then move it into the binary. Debugging C against a public API is much
cheaper than debugging a freestanding trampoline.

Build it with `make -C .. aacfix.so` if you want it for comparison.
