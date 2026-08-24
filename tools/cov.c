/*
 * Basic-block coverage trampoline (differential tracing).
 *
 * Marks every instrumented control-flow site's address in a live, file-backed
 * mmap'd bitmap (so coverage survives even if the process is force-quit).  Run
 * once importing AC3, once importing AAC, then diff the two bitmaps: blocks AC3
 * reaches that AAC does not are the divergence points = the MKV AAC gates.
 *
 * Bitmap covers [BASE, BASE+SPAN); 1 bit per byte-address (addr-BASE).
 * Output file: /tmp/cov.bin (rename between runs).
 */
#include "stdlib.c"

#define BASE  0x5b00000UL
#define SPAN  0x200000UL          /* 2 MB region: covers the MKV/audio code */

static volatile unsigned char *BM = 0;
static mutex_t mutex = MUTEX_INITIALIZER;
static int inited = 0;

static void init(void)
{
    mutex_lock(&mutex);
    if (!inited) {
        inited = 1;
        int fd = open("/tmp/cov.bin", O_RDWR | O_CREAT, 0644);
        if (fd >= 0) {
            ftruncate(fd, SPAN / 8);
            void *m = mmap(0, SPAN / 8, PROT_READ | PROT_WRITE,
                           MAP_SHARED, fd, 0);
            if (m != (void *)-1) BM = (unsigned char *)m;
            close(fd);
        }
    }
    mutex_unlock(&mutex);
}

void mark(const void *addr)
{
    if (!inited) init();
    if (!BM) return;
    unsigned long off = (unsigned long)addr - BASE;
    if (off < SPAN) BM[off >> 3] |= (unsigned char)(1u << (off & 7));
}
