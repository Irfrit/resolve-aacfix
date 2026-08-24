#!/usr/bin/env python3
"""Functionally verify a set of AAC-enabled FFmpeg libs by loading them.

Reading FFmpeg's codec tables out of the ELF with `nm` does not work: the
AVCodec/AVInputFormat structs have hidden visibility, so none of them are
dynamic symbols.  The only honest check is to dlopen the libraries and ask them
the same questions Resolve will -- which is what this does, via ctypes.

    ffmpeg-inventory.py DIR       # exit 0 if the libs are what we expect
"""
import ctypes
import os
import sys

AV_CODEC_ID_AAC = 0x15002      # 86018
AV_CODEC_ID_AC3 = 0x15003      # 86019
AV_CODEC_ID_AVS3DA = 86119     # the id Blackmagic used, read out of their build

GREEN, RED, RST = "\033[32m", "\033[31m", "\033[0m"


class Checks:
    def __init__(self):
        self.failed = False

    def want(self, cond, label):
        if cond:
            print(f"  {GREEN}ok{RST}    {label}")
        else:
            print(f"  {RED}FAIL{RST}  {label}", file=sys.stderr)
            self.failed = True


def load(d):
    """Load the four libs in dependency order, by absolute path so their
    $ORIGIN RUNPATH resolves siblings in DIR rather than the system copies."""
    libs = {}
    for name, soname in (("avutil", "libavutil.so.58.2.100"),
                         ("swscale", "libswscale.so.7.1.100"),
                         ("avcodec", "libavcodec.so.60.3.100"),
                         ("avformat", "libavformat.so.60.3.100")):
        path = os.path.join(d, soname)
        if not os.path.exists(path):
            sys.exit(f"missing {soname} in {d}")
        libs[name] = ctypes.CDLL(path)
    return libs


def iter_names(lib, fn):
    """Walk one of FFmpeg's av_*_iterate lists, collecting .name (field 0)."""
    it = getattr(lib, fn)
    it.restype = ctypes.c_void_p
    it.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    opaque = ctypes.c_void_p(None)
    out = []
    while True:
        p = it(ctypes.byref(opaque))
        if not p:
            return out
        name_ptr = ctypes.cast(p, ctypes.POINTER(ctypes.c_void_p))[0]
        out.append(ctypes.cast(ctypes.c_void_p(name_ptr), ctypes.c_char_p)
                   .value.decode())


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    d = os.path.abspath(argv[1])
    libs = load(d)
    c, f = libs["avcodec"], libs["avformat"]
    for fn in ("avcodec_find_decoder", "avcodec_find_encoder",
               "avcodec_find_decoder_by_name", "avcodec_descriptor_get"):
        getattr(c, fn).restype = ctypes.c_void_p

    k = Checks()

    # --- AAC is back.  This is the whole point: without it the binary patch
    # routes audio to a decoder that does not exist.
    k.want(c.avcodec_find_decoder(AV_CODEC_ID_AAC), "AAC decoder present")
    k.want(c.avcodec_find_encoder(AV_CODEC_ID_AAC), "AAC encoder present (native)")
    for n in ("aac", "aac_fixed", "aac_latm"):
        k.want(c.avcodec_find_decoder_by_name(n.encode()), f"decoder '{n}' present")

    # --- AC-3 stays exactly as Blackmagic had it: decoder on, encoder off.
    # Re-enabling the encoder would change what the shipped product can output.
    k.want(c.avcodec_find_decoder(AV_CODEC_ID_AC3), "AC-3 decoder present")
    k.want(not c.avcodec_find_encoder(AV_CODEC_ID_AC3),
           "AC-3 encoder absent (matches Blackmagic)")

    demuxers = iter_names(f, "av_demuxer_iterate")
    muxers = iter_names(f, "av_muxer_iterate")
    k.want("ac3" not in demuxers, "AC-3 demuxer absent (matches Blackmagic)")
    k.want("ac3" not in muxers, "AC-3 muxer absent (matches Blackmagic)")

    # --- Blackmagic's AV3A / "Audio Vivid" support, backported to the 6.0 API.
    # We replace all four libs, so if we drop this Resolve loses a format it
    # shipped with -- and nothing else would notice until a user opened one.
    k.want("av3a" in demuxers, "av3a demuxer present (AV3A backport applied)")
    desc = c.avcodec_descriptor_get(AV_CODEC_ID_AVS3DA)
    name = (ctypes.cast(ctypes.cast(desc + 8, ctypes.POINTER(ctypes.c_void_p))[0],
                        ctypes.c_char_p).value.decode() if desc else None)
    k.want(name == "avs3p3_3d_audio",
           f"AV_CODEC_ID_AVS3DA ({AV_CODEC_ID_AVS3DA}) = 'avs3p3_3d_audio' "
           f"(got {name!r})")

    # --- AAC demux/mux/parse came back with the decoder
    k.want("aac" in demuxers, "raw AAC (ADTS) demuxer present")
    k.want("adts" in muxers, "ADTS muxer present")

    return 1 if k.failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
