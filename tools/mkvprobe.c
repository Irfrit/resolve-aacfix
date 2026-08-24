/*
 * Debug probe (tagged): log "tag <n>: val=<v>" so several sites can share one
 * build.  Temporary — not shipped.
 */
#include "stdlib.c"

void probe(long tag, long val)
{
    char buf[64];
    int n = snprintf(buf, sizeof buf, "probe tag=%ld val=%ld (0x%lx)\n",
                     tag, val, val);
    int fd = open("/tmp/mkvprobe.log", O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd >= 0) { write(fd, buf, n); close(fd); }
}
