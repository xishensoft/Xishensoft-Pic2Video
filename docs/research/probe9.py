
# -*- coding: utf-8 -*-
"""Probe 9: av2bv, p param, invalid cid, share short link, unsigned playurl, durl download."""
import json, time, hashlib, urllib.parse, urllib.request, http.cookiejar, subprocess, os

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
TAB=[46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
MIXIN=None
op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
op.addheaders=[("User-Agent",UA),("Referer","https://www.bilibili.com/"),("Origin","https://www.bilibili.com")]

def fetch(url,tries=3):
    last=None
    for i in range(tries):
        try:
            with op.open(url,timeout=30) as r:
                return json.loads(r.read().decode("utf-8","replace"))
        except Exception as e:
            last=repr(e); time.sleep(1+i)
    return {"code":"exception","message":last}

def load_mixin():
    global MIXIN
    nav=fetch("https://api.bilibili.com/x/web-interface/nav")
    w=nav["data"]["wbi_img"]
    raw=w["img_url"].rsplit("/",1)[-1].split(".")[0]+w["sub_url"].rsplit("/",1)[-1].split(".")[0]
    MIXIN="".join(raw[i] for i in TAB)[:32]

def sign(p):
    p={k:v for k,v in p.items() if v is not None}
    p["wts"]=int(time.time())
    qs=urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
    p["w_rid"]=hashlib.md5((qs+MIXIN).encode()).hexdigest()
    return urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))

load_mixin()
out={}

# 1) av2bv algorithm vs API
XOR=23442827791579; MASK=2251799813685247; MAXA=1<<51
B64="FcwAPNKTMug3GV5Lj7EItr0v2oUpK7e5TjyD8Zb4nHqYmR6waxs9QdCk"
def av2bv(aid):
    b=[B64[0]]*12; tmp=(MAXA|aid)%MAXA; i=11
    while tmp:
        b[i]=B64[tmp%58]; tmp//=58; i-=1
    b[0],b[6]=b[6],b[0]; b[3],b[8]=b[8],b[3]
    return "BV1"+ "".join(b[3:])[:9] if False else "BV"+"".join(b)
out["av2bv"]=[]
for aid in (170001, 2, 117562180376697, 1):
    v=fetch("https://api.bilibili.com/x/web-interface/view?aid=%d"%aid)
    out["av2bv"].append({"aid":aid,"code":v.get("code"),"api_bvid":(v.get("data") or {}).get("bvid"),"algo":av2bv(aid)})

# 2) p param
for p in (1,2,3,999):
    v=fetch("https://api.bilibili.com/x/web-interface/wbi/view?"+sign({"bvid":"BV1NDsS6UE8D","p":p}))
    d=v.get("data") or {}
    out.setdefault("p_param",[]).append({"p":p,"code":v.get("code"),"message":v.get("message"),
        "cid":d.get("cid"),"part":next((x.get("part") for x in (d.get("pages") or []) if x.get("page")==p),None)})

# 3) invalid / invisible cid playurl
bv="BV1bw4t6fET9"
out["bad_cid"]=[]
for cid in (1, 99999999999):
    pl=fetch("https://api.bilibili.com/x/player/wbi/playurl?"+sign({"bvid":bv,"cid":cid,"fnval":16,"qn":64,"platform":"pc"}))
    out["bad_cid"].append({"cid":cid,"code":pl.get("code"),"message":pl.get("message"),"data_null":pl.get("data") is None})
pl=fetch("https://api.bilibili.com/x/player/wbi/playurl?"+sign({"bvid":"BV1ab411c7mD","cid":43344332203,"fnval":16,"qn":64,"platform":"pc"}))
out["invisible_bv_playurl"]={"code":pl.get("code"),"message":pl.get("message")}

# 4) unsigned playurl (no wbi)
pl=fetch("https://api.bilibili.com/x/player/playurl?bvid=%s&cid=43344332203&fnval=16&qn=64&platform=pc"%bv)
out["unsigned_old_api"]={"code":pl.get("code"),"message":pl.get("message"),
    "quality":(pl.get("data") or {}).get("quality"),
    "has_dash":bool((pl.get("data") or {}).get("dash"))}
pl=fetch("https://api.bilibili.com/x/player/wbi/playurl?bvid=%s&cid=43344332203&fnval=16&qn=64&platform=pc"%bv)
out["unsigned_wbi_api"]={"code":pl.get("code"),"message":pl.get("message")}

# 5) share click -> short link
out["share"]=[]
for bv2 in ("BV1jQst6BEf2","BV1bw4t6fET9"):
    for url in ("https://api.bilibili.com/x/share/click?build=1&bvid=%s&platform=android&share_channel=copy_link"%bv2,
                "https://api.bilibili.com/x/share/click?build=1&oid=%s&platform=android&share_channel=copy_link"%bv2):
        r=fetch(url)
        out["share"].append({"url":url.split("?")[0],"bvid":bv2,"code":r.get("code"),"message":r.get("message"),
            "short":((r.get("data") or {}).get("short_link") or (r.get("data") or {}).get("short_url"))})

# 6) durl download and ffprobe (short 22s video)
bv3="BV1jQst6BEf2"
v=fetch("https://api.bilibili.com/x/web-interface/wbi/view?"+sign({"bvid":bv3}))
cid=(v.get("data") or {}).get("cid")
pl=fetch("https://api.bilibili.com/x/player/wbi/playurl?"+sign({"bvid":bv3,"cid":cid,"fnval":1,"qn":64,"platform":"pc"}))
d=pl.get("data") or {}
url=(d.get("durl") or [{}])[0].get("url")
out["durl_meta"]={"code":pl.get("code"),"quality":d.get("quality"),"format":d.get("format"),
    "size":[x.get("size") for x in (d.get("durl") or [])],"length":[x.get("length") for x in (d.get("durl") or [])]}
if url:
    path=r"E:\deepseek\FM114.5\.scratch\pic2video\probe\durl_test.mp4"
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Referer":"https://www.bilibili.com/"})
    with op.open(req,timeout=60) as r, open(path,"wb") as f:
        while True:
            chunk=r.read(1<<20)
            if not chunk: break
            f.write(chunk)
    out["durl_download"]={"path":path,"bytes":os.path.getsize(path)}
    pr=subprocess.run(["ffprobe","-v","error","-show_entries","stream=index,codec_type,codec_name,sample_rate,channels,bit_rate,duration","-of","json",path],capture_output=True,text=True,encoding="utf-8")
    out["durl_ffprobe"]=json.loads(pr.stdout or "{}")
    pr2=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration,bit_rate,format_name","-of","json",path],capture_output=True,text=True,encoding="utf-8")
    out["durl_ffprobe_format"]=json.loads(pr2.stdout or "{}")

with open(r"E:\deepseek\FM114.5\.scratch\pic2video\probe\probe9.json","w",encoding="utf-8") as f:
    json.dump(out,f,ensure_ascii=False,indent=1)
print("ok")
