#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
#include <unistd.h>
#include <pthread.h>
#include <libavcodec/avcodec.h>

static FILE *L; static pthread_mutex_t M = PTHREAD_MUTEX_INITIALIZER;
static void ini(void){ if(L) return; pthread_mutex_lock(&M);
  if(!L){ const char*p=getenv("AACFIX_LOG"); if(p){L=fopen(p,"a"); if(L) setvbuf(L,NULL,_IOLBF,0);} }
  pthread_mutex_unlock(&M); }
#define LOG(...) do{ ini(); if(L){ fprintf(L,"[%d] ",(int)getpid()); fprintf(L,__VA_ARGS__);} }while(0)

/* Walk MPEG-4 descriptors and return the DecSpecificInfo (tag 0x05) payload. */
static const uint8_t* find_asc(const uint8_t*p, int n, int*out_len){
    int i = 0;
    while (i < n) {
        int tag = p[i++];
        int len = 0, cnt = 0, b;
        do { if (i >= n) return NULL; b = p[i++]; len = (len<<7)|(b&0x7f); } while ((b&0x80) && ++cnt < 4);
        if (len < 0 || i + len > n) return NULL;
        if (tag == 0x05) { *out_len = len; return p + i; }
        if (tag == 0x03) {                       /* ES_Descr: descend past ES_ID+flags */
            if (i + 3 > n) return NULL;
            int flags = p[i+2]; int skip = 3;
            if (flags & 0x80) skip += 2;
            if (flags & 0x40) { if (i+skip >= n) return NULL; skip += 1 + p[i+skip]; }
            if (flags & 0x20) skip += 2;
            i += skip; continue;                 /* children follow */
        }
        if (tag == 0x04) { i += 13; continue; }  /* DecoderConfigDescr: children follow */
        i += len;                                /* skip anything else wholesale */
    }
    return NULL;
}

int avcodec_open2(AVCodecContext *a, const AVCodec *c, AVDictionary **o){
    static typeof(avcodec_open2) *real;
    if(!real) real = dlsym(RTLD_NEXT, "avcodec_open2");
    if (a && a->codec_id == AV_CODEC_ID_AAC && a->extradata && a->extradata_size > 5
        && a->extradata[0] == 0x03) {
        int asc_len = 0;
        const uint8_t *asc = find_asc(a->extradata, a->extradata_size, &asc_len);
        if (asc && asc_len > 0 && asc_len < a->extradata_size) {
            uint8_t *nb = av_mallocz(asc_len + AV_INPUT_BUFFER_PADDING_SIZE);
            if (nb) {
                memcpy(nb, asc, asc_len);
                LOG("esds->ASC: %d bytes -> %d bytes (", a->extradata_size, asc_len);
                
                if(L){ for(int i=0;i<asc_len;i++) fprintf(L,"%02x ", nb[i]); fprintf(L,")\n"); }
                av_freep(&a->extradata);
                a->extradata = nb; a->extradata_size = asc_len;
            }
        }
    }
    int rc = real(a,c,o);
    if (a && a->codec_id == AV_CODEC_ID_AAC)
        LOG("open2(aac) ch=%d sr=%d extradata=%d -> %d\n",
            a->ch_layout.nb_channels, a->sample_rate, a->extradata_size, rc);
    return rc;
}
