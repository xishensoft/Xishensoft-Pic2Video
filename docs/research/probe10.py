
# -*- coding: utf-8 -*-
"""Probe 10: b23 short links, av2bv algo, invalid ep_id, dolby/flac request."""
import json, time, hashlib, urllib.parse, urllib.request, http.cookiejar, subprocess

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

# 1) b23.tv short links via curl (no follow)
def curl_head(u):
    p=subprocess.run(["curl.exe","-sS","-o","NUL","-D","-","--max-time","20","-A",UA,u],capture_output=True,text=True,encoding="utf-8",errors="replace")
    return p.stdout.strip().replace("\r","")
def curl_body(u):
    p=subprocess.run(["curl.exe","-sS","--max-time","20","-A",UA,u],capture_output=True,text=True,encoding="utf-8",errors="replace")
    return p.stdout[:300]
out["b23"]=[]
for u in ("https://b23.tv/H4FAqk6","https://b23.tv/zzzZZZ9","https://b23.tv/BV1bw4t6fET9"):
    out["b23"].append({"url":u,"head":curl_head(u),"body":curl_body(u)})

# 2) correct av2bv algorithm
XOR=23442827791579; MASK=2251799813685247; MAXA=1<<51
B64="FcwAPNKTMug3GV5Lj7EItr0v2oUpK7e5TjyD8Zb4nHqYmR6waxs9QdCk"
def av2bv(aid):
    b=list("BV1000000000")
    tmp=(MAXA|aid)%MAXA
    i=len(b)-1
    while tmp:
        b[i]=B64[tmp%58]; tmp//=58; i-=1
    b[3],b[9]=b[9],b[3]
    b[4],b[7]=b[7],b[4]
    b[0],b[2]=b[2],b[0]
    return "".join(b)
out["av2bv"]=[]
for aid in (170001,2,117562180376697,1,114514):
    v=fetch("https://api.bilibili.com/x/web-interface/view?aid=%d"%aid)
    out["av2bv"].append({"aid":aid,"code":v.get("code"),"api_bvid":(v.get("data") or {}).get("bvid"),"algo":av2bv(aid),"match":(v.get("data") or {}).get("bvid")==av2bv(aid)})

# 3) invalid ep_id
out["ep"]=[]
for ep in (1, 999999999, 323085):
    pl=fetch("https://api.bilibili.com/pgc/player/web/playurl?"+sign({"ep_id":ep,"fnval":16,"qn":64,"fnver":0,"fourk":1}))
    r=pl.get("result") or {}
    out["ep"].append({"ep_id":ep,"code":pl.get("code"),"message":pl.get("message"),
        "has_result":pl.get("result") is not None,"quality":r.get("quality"),
        "is_preview":r.get("is_preview"),"has_dash":bool(r.get("dash")),
        "audio":[a.get("id") for a in ((r.get("dash") or {}).get("audio") or [])],
        "durl_len":[x.get("length") for x in (r.get("durl") or [])]})

# 4) dolby/flac request (fnval = 16|256 = 272) on a flac-flagged video
bv="BV1FkxR6WE58"
v=fetch("https://api.bilibili.com/x/web-interface/wbi/view?"+sign({"bvid":bv}))
cid=(v.get("data") or {}).get("cid")
out["dolby"]={"bvid":bv,"cid":cid}
for fnval in (16,272):
    pl=fetch("https://api.bilibili.com/x/player/wbi/playurl?"+sign({"bvid":bv,"cid":cid,"fnval":fnval,"qn":127,"platform":"pc","fnver":0,"fourk":1}))
    d=pl.get("data") or {}
    dash=d.get("dash") or {}
    out["dolby"]["fnval_%d"%fnval]={"code":pl.get("code"),"quality":d.get("quality"),
        "flac":dash.get("flac"),"dolby":dash.get("dolby"),
        "audio":[{"id":a.get("id"),"bw":a.get("bandwidth"),"mime":a.get("mimeType"),"codecs":a.get("codecs")} for a in (dash.get("audio") or [])]}

# 5) check 30280-only m4a vs durl audio bitrate on same video (already have) - verify m4s URL headers need referer?
pl=fetch("https://api.bilibili.com/x/player/wbi/playurl?"+sign({"bvid":bv,"cid":cid,"fnval":16,"qn":64,"platform":"pc"}))
a=[x for x in ((pl.get("data") or {}).get("dash") or {}).get("audio",[]) if x.get("id")==30280]
if a:
    u=a[0]["baseUrl"]
    p=subprocess.run(["curl.exe","-sS","-o","NUL","-w","%{http_code} %{size_download}","--max-time","25","-r","0-1023",u],capture_output=True,text=True,encoding="utf-8")
    p2=subprocess.run(["curl.exe","-sS","-o","NUL","-w","%{http_code} %{size_download}","--max-time","25","-r","0-1023","-A",UA,"-e","https://www.bilibili.com/",u],capture_output=True,text=True,encoding="utf-8")
    out["referer_test"]={"no_headers":p.stdout,"with_ua_referer":p2.stdout}

with open(r"E:\deepseek\FM114.5\.scratch\pic2video\probe\probe10.json","w",encoding="utf-8") as f:
    json.dump(out,f,ensure_ascii=False,indent=1)
print("ok")
