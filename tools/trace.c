#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <dlfcn.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>

static FILE *L;
static pthread_mutex_t M = PTHREAD_MUTEX_INITIALIZER;
static void ini(void){
  if(L) return;
  pthread_mutex_lock(&M);
  if(!L){ const char*p=getenv("AVTRACE_LOG"); L=fopen(p?p:"/tmp/avtrace.log","a");
          if(L) setvbuf(L,NULL,_IOLBF,0); }
  pthread_mutex_unlock(&M);
}
#define LOG(...) do{ ini(); if(L){ fprintf(L,"[%d] ",(int)getpid()); fprintf(L,__VA_ARGS__); } }while(0)

#define REAL(f) static typeof(f) *r_##f; if(!r_##f) r_##f=dlsym(RTLD_NEXT,#f)

static const char* cn(enum AVCodecID id){
  const AVCodecDescriptor*d=avcodec_descriptor_get(id);
  return d&&d->name?d->name:"?";
}

const AVCodec *avcodec_find_decoder(enum AVCodecID id){
  REAL(avcodec_find_decoder);
  const AVCodec*c=r_avcodec_find_decoder(id);
  LOG("find_decoder(id=%d %s) -> %s\n", id, cn(id), c?c->name:"NULL");
  return c;
}
const AVCodec *avcodec_find_decoder_by_name(const char*n){
  REAL(avcodec_find_decoder_by_name);
  const AVCodec*c=r_avcodec_find_decoder_by_name(n);
  LOG("find_decoder_by_name(%s) -> %s\n", n, c?c->name:"NULL");
  return c;
}
int avcodec_open2(AVCodecContext*a,const AVCodec*c,AVDictionary**o){
  REAL(avcodec_open2);
  int id = a?a->codec_id:-1;
  int rc = r_avcodec_open2(a,c,o);
  LOG("open2(codec_id=%d %s, codec=%s, ch=%d sr=%d fmt=%d prof=%d extradata=%d) -> %d\n",
      id, cn(id), c?c->name:"NULL",
      a?a->ch_layout.nb_channels:-1, a?a->sample_rate:-1, a?a->sample_fmt:-1,
      a?a->profile:-1, a?a->extradata_size:-1, rc);
  if (a && a->extradata && a->extradata_size > 0) {
    char hex[1024]; int n = a->extradata_size; if (n > 300) n = 300;
    for (int i=0;i<n;i++) sprintf(hex+i*3, "%02x ", a->extradata[i]);
    LOG("   extradata[%d]: %s\n", a->extradata_size, hex);
  }
  return rc;
}
int avcodec_send_packet(AVCodecContext*a,const AVPacket*p){
  REAL(avcodec_send_packet);
  int rc=r_avcodec_send_packet(a,p);
  if(rc<0){ char e[128]; av_strerror(rc,e,sizeof e);
    LOG("send_packet(%s, size=%d) -> %d (%s)\n", cn(a?a->codec_id:0), p?p->size:-1, rc, e); }
  return rc;
}
int avcodec_receive_frame(AVCodecContext*a,AVFrame*f){
  REAL(avcodec_receive_frame);
  int rc=r_avcodec_receive_frame(a,f);
  if(rc<0 && rc!=AVERROR(EAGAIN) && rc!=AVERROR_EOF){ char e[128]; av_strerror(rc,e,sizeof e);
    LOG("receive_frame(%s) -> %d (%s)\n", cn(a?a->codec_id:0), rc, e); }
  return rc;
}
int av_find_best_stream(AVFormatContext*ic,enum AVMediaType t,int w,int rel,const AVCodec**d,int fl){
  REAL(av_find_best_stream);
  int rc=r_av_find_best_stream(ic,t,w,rel,d,fl);
  LOG("find_best_stream(type=%d) -> %d (dec=%s)\n", t, rc, (d&&*d)?(*d)->name:"none");
  return rc;
}
int avformat_find_stream_info(AVFormatContext*ic,AVDictionary**o){
  REAL(avformat_find_stream_info);
  int rc=r_avformat_find_stream_info(ic,o);
  LOG("find_stream_info(%s) -> %d, nb_streams=%u\n", ic&&ic->url?ic->url:"?", rc, ic?ic->nb_streams:0);
  if(ic) for(unsigned i=0;i<ic->nb_streams;i++){
    AVCodecParameters*cp=ic->streams[i]->codecpar;
    LOG("   stream[%u] type=%d codec_id=%d (%s) tag=0x%08x prof=%d ch=%d sr=%d\n",
        i, cp->codec_type, cp->codec_id, cn(cp->codec_id), cp->codec_tag,
        cp->profile, cp->ch_layout.nb_channels, cp->sample_rate);
  }
  return rc;
}
