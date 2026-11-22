# -*- coding: utf-8 -*-
"""Probe 6: ffprobe dash audio streams + real download; compare ids; durl audio."""
import json, time, hashlib, urllib.parse, urllib.request, subprocess, os
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
TAB=[46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
MIXIN=[None]
op=urllib.request.build_opener()
op.addheaders=[("User-Agent",UA),("Referer","https://www.bilibili.com/"),("Origin","https://www.bilibili.com")]
def j(url):
    for i in range(3):
        try:
            with op.open(url,timeout=25) as r: return json.loads(r.read().decode("utf-8","replace"))
        except Exception as e: err=repr(e); time.sleep(1)
    return {"code":"exception","message":err}
nav=j("https://api.bilibili.com/x/web-interface/nav")
w=nav["data"]["wbi_img"]; raw=w["img_url"].rsplit("/",1)[-1].split(".")[0]+w["sub_url"].rsplit("/",1)[-1].split(".")[0]
MIXIN[0]="".join(raw[i] for i in TAB)[:32]
def sign(p):
    p=dict(p); p["wts"]=int(time.time())
    qs=urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
    p["w_rid"]=hashlib.md5((qs+MIXIN[0]).encode()).hexdigest()
    return urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
HDR="Referer: https://www.bilibili.com/\r\nUser-Agent: "+UA+"\r\n"
def probe_url(url):
    r=subprocess.run(["ffprobe","-v","error","-headers",HDR,"-show_entries",
        "stream=codec_name,codec_long_name,sample_rate,channels,channel_layout,bit_rate,duration:format=duration,bit_rate,size",
        "-of","json",url],capture_output=True,text=True,encoding="utf-8",errors="replace")
    try: d=json.loads(r.stdout)
    except: return {"err":r.stderr[-300:]}
    st=(d.get("streams") or [{}])[0]; fm=d.get("format") or {}
    return {"codec":st.get("codec_name"),"sample_rate":st.get("sample_rate"),"channels":st.get("channels"),
            "layout":st.get("channel_layout"),"stream_bitrate":st.get("bit_rate"),
            "stream_dur":st.get("duration"),"fmt_dur":fm.get("duration"),"fmt_bitrate":fm.get("bit_rate"),"fmt_size":fm.get("size")}
def wbi_view(bv): return j("https://api.bilibili.com/x/web-interface/wbi/view?"+sign({"bvid":bv}))
def play(bv,cid,fnval=16,**kw):
    p={"bvid":bv,"cid":cid,"fnval":fnval,"qn":127,"fourk":1,"platform":"pc"}; p.update(kw)
    return j("https://api.bilibili.com/x/player/wbi/playurl?"+sign(p))
bvs=["BV1jQst6BEf2","BV1ALs465E2V","BV13qs46FE9C"]
out={}
for bv in bvs:
    v=wbi_view(bv); vd=v.get("data") or {}
    p=play(bv,vd.get("cid")); d=p.get("data") or {}; dash=d.get("dash") or {}
    row={"bvid":bv,"dur":vd.get("duration"),"play_code":p.get("code"),"dash_dur":dash.get("duration"),"audios":[]}
    for a in dash.get("audio") or []:
        u=a.get("baseUrl") or a.get("base_url")
        pr=probe_url(u)
        row["audios"].append({"id":a.get("id"),"bandwidth":a.get("bandwidth"),"mime":a.get("mimeType"),
                              "codecs":a.get("codecs"),"url_host":urllib.parse.urlsplit(u).netloc,"probe":pr})
    # compare first bytes of equal-bandwidth streams
    urls=[(a.get("id"),a.get("baseUrl") or a.get("base_url")) for a in dash.get("audio") or []]
    def head_hash(u,n=262144):
        req=urllib.request.Request(u,headers={"Referer":"https://www.bilibili.com/","User-Agent":UA,"Range":"bytes=0-%d"%(n-1)})
        try:
            with op.open(req,timeout=30) as r: return hashlib.md5(r.read()).hexdigest(), r.status, r.headers.get("Content-Range")
        except Exception as e: return repr(e),None,None
    row["head_hashes"]={str(i):head_hash(u) for i,u in urls}
    # download 30280 (or first) and remux to m4a + wav
    best=max((a for a in dash.get("audio") or []),key=lambda a:a.get("bandwidth") or 0,default=None)
    if best:
        u=best.get("baseUrl") or best.get("base_url")
        dst=r"E:\deepseek\FM114.5\.scratch\pic2video\probe\out_%s.m4a"%bv
        r=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-headers",HDR,"-i",u,"-vn","-c:a","copy",dst],
                         capture_output=True,text=True,encoding="utf-8",errors="replace")
        row["download"]={"id":best.get("id"),"rc":r.returncode,"err":r.stderr[-300:],
                         "size":os.path.getsize(dst) if os.path.exists(dst) else None}
        if r.returncode==0:
            row["download"]["probe"]=probe_url(dst)
        wav=dst[:-4]+".wav"
        r2=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-i",dst,"-ac","1","-ar","16000","-c:a","pcm_s16le",wav],
                          capture_output=True,text=True,encoding="utf-8",errors="replace")
        row["to_wav"]={"rc":r2.returncode,"probe":probe_url(wav) if r2.returncode==0 else None}
    out[bv]=row
    print("done",bv)
# durl quality for one video
bv="BV1bw4t6fET9"; v=wbi_view(bv); vd=v.get("data") or {}
p=play(bv,vd.get("cid"),fnval=1,qn=127)
d=p.get("data") or {}
out["durl_"+bv]={"quality":d.get("quality"),"format":d.get("format"),
                 "durl":[{"size":x.get("size"),"length":x.get("length"),"probe":probe_url(x.get("url"))} for x in (d.get("durl") or [])[:1]]}
open(r"E:\deepseek\FM114.5\.scratch\pic2video\probe\probe6.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=1))
print("WROTE")
