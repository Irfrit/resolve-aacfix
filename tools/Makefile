# Debugging / reverse-engineering tools.  None of this is needed to install the
# AAC fix -- see the repo root for that.
#
#   make trace.so     LD_PRELOAD FFmpeg API tracer (needs FFmpeg dev headers)
#   make aacfix.so    the historical esds->ASC shim (superseded; see legacy/)
#   make cov mkvprobe e9patch trampolines (needs vendor/e9patch built)
#
ROOT      := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
E9DIR     ?= $(ROOT)/vendor/e9patch
CFLAGS    ?= -O2 -Wall -fPIC
FFMPEG_CFLAGS := $(shell pkg-config --cflags libavcodec libavformat 2>/dev/null)

.PHONY: all shims trampolines clean
all: shims
shims: trace.so
trampolines: cov mkvprobe

trace.so: trace.c
	$(CC) $(CFLAGS) $(FFMPEG_CFLAGS) -shared -o $@ $< -ldl

aacfix.so: legacy/aacfix.c
	$(CC) $(CFLAGS) $(FFMPEG_CFLAGS) -shared -o $@ $< -ldl

# e9patch trampolines: compiled by e9compile.sh, which needs a full e9patch
# checkout in $(E9DIR) (scripts/dev-setup.sh e9patch builds one).  They are
# freestanding PIEs, not libraries -- e9tool splices them into the target.
cov mkvprobe: %: %.c
	@test -x $(E9DIR)/e9compile.sh || \
	  { echo "no e9patch checkout at $(E9DIR); run scripts/dev-setup.sh e9patch"; exit 1; }
	cd $(E9DIR) && ./e9compile.sh $(abspath $<)
	mv -f $(E9DIR)/$@ $@
	rm -f $(E9DIR)/$@.o

clean:
	rm -f trace.so aacfix.so cov mkvprobe *.o
