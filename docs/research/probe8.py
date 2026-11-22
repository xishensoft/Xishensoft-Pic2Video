
# -*- coding: utf-8 -*-
"""Probe 8: charging/paid UGC, dolby/flac fields, qn effect, movie season, pugv, cookie DBs."""
import json, time, hashlib, urllib.parse, urllib.request, http.cookiejar, os

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
MIXIN=None
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
op.addheaders=[("User-Agent",UA),("Referer","https://www.bilibili.com/"),("Origin","https://www.bilibili.com")]

def fetch(url,tries=3):
    last=None
    for i in range(tries):
        try:
            with op.open(url,timeout=25) as r:
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
    return nav.get("code")

def sign(p):
    p={k:v for k,v in p.items() if v is not None}
    p["wts"]=int(time.time())
    qs=urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
    p["w_rid"]=hashlib.md5((qs+MIXIN).encode()).hexdigest()
    return urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))

def play(bvid,cid,**kw):
    p={"bvid":bvid,"cid":cid,"fnval":16,"qn":127,"platform":"pc","fnver":0,"fourk":1}
    p.update(kw)
    return fetch("https://api.bilibili.com/x/player/wbi/playurl?"+sign(p))

out={"nav_code":load_mixin()}

# A) charging / paid search, check rights and playurl code
out["paid_search"]=[]
for kw in ("充电专属","付费视频","仅充电可见"):
    sr=fetch("https://api.bilibili.com/x/web-interface/wbi/search/type?"+sign({"search_type":"video","keyword":kw,"page":1}))
    bvs=[(it.get("bvid"),(it.get("title") or "").replace('<em class="keyword">','').replace("</em>","")) for it in ((sr.get("data") or {}).get("result") or [])[:20] if it.get("bvid")]
    hits=[]
    for bv,t in bvs:
        v=fetch("https://api.bilibili.com/x/web-interface/wbi/view?"+sign({"bvid":bv}))
        vd=v.get("data") or {}
        r=vd.get("rights") or {}
        flag={"bvid":bv,"title":t,"code":v.get("code"),
              "is_charging_arc":r.get("is_charging_arc"),"ugc_pay":r.get("ugc_pay"),
              "pay":r.get("pay"),"arc_pay":r.get("arc_pay"),"free_watch":r.get("free_watch"),
              "cid":vd.get("cid"),"duration":vd.get("duration")}
        if r.get("is_charging_arc") or r.get("ugc_pay") or r.get("pay"):
            pl=play(bv,vd.get("cid"))
            flag["playurl"]={"code":pl.get("code"),"message":pl.get("message")}
            d=pl.get("data") or {}
            flag["playurl"]["has_dash"]=bool(d.get("dash")); flag["playurl"]["has_durl"]=bool(d.get("durl"))
            flag["playurl"]["durl_len"]=[x.get("length") for x in (d.get("durl") or [])]
            hits.append(flag)
        elif len(hits)<2 and kw=="充电专属" and len(out["paid_search"])<1 and bv==bvs[0][0]:
            pl=play(bv,vd.get("cid"))
            flag["playurl_anyway"]={"code":pl.get("code"),"message":pl.get("message"),
                "quality":(pl.get("data") or {}).get("quality"),
                "audio":[a.get("id") for a in (((pl.get("data") or {}).get("dash") or {}).get("audio") or [])]}
            hits.append(flag)
    out["paid_search"].append({"kw":kw,"scanned":len(bvs),"hits":hits})

# B) dolby/flac fields: search Hi-Res music video
sr=fetch("https://api.bilibili.com/x/web-interface/wbi/search/type?"+sign({"search_type":"video","keyword":"Hi-Res 无损 音乐","page":1}))
out["hires"]=[]
for it in ((sr.get("data") or {}).get("result") or [])[:4]:
    bv=it.get("bvid")
    if not bv: continue
    v=fetch("https://api.bilibili.com/x/web-interface/wbi/view?"+sign({"bvid":bv}))
    vd=v.get("data") or {}
    if not vd.get("cid"): continue
    pl=play(bv,vd["cid"])
    d=pl.get("data") or {}
    dash=d.get("dash") or {}
    out["hires"].append({"bvid":bv,"title":(it.get("title") or "")[:40],"code":pl.get("code"),
        "quality":d.get("quality"),
        "flac":(dash.get("flac") or {}).get("display"),
        "dolby_type":((dash.get("dolby") or {}).get("type")),
        "dolby_audio":[a.get("id") for a in ((dash.get("dolby") or {}).get("audio") or [])],
        "audio":[{"id":a.get("id"),"bw":a.get("bandwidth")} for a in (dash.get("audio") or [])]})

# C) qn effect on audio list (same cid)
bv="BV1jQst6BEf2"
v=fetch("https://api.bilibili.com/x/web-interface/wbi/view?"+sign({"bvid":bv}))
cid=(v.get("data") or {}).get("cid")
out["qn_effect"]={"bvid":bv,"cid":cid,"runs":[]}
for qn in (127,80,64,32,16,6):
    pl=play(bv,cid,qn=qn,fnval=16)
    d=pl.get("data") or {}
    out["qn_effect"]["runs"].append({"qn":qn,"quality":d.get("quality"),
        "audio":[{"id":a.get("id"),"bw":a.get("bandwidth")} for a in (((d.get("dash") or {}).get("audio")) or [])],
        "video_ids":[x.get("id") for x in (((d.get("dash") or {}).get("video")) or [])]})

# D) movie season playurl (pgc rank season_type=2)
mr=fetch("https://api.bilibili.com/pgc/web/rank/list?day=3&season_type=2")
mres=(mr.get("result") or {}).get("list") or []
out["movie_rank"]={"code":mr.get("code"),"n":len(mres),
    "first":[{"season_id":x.get("season_id"),"title":x.get("title"),"badge":x.get("badge")} for x in mres[:3]]}
out["movie_playurl"]=[]
for it in mres[:2]:
    sd=fetch("https://api.bilibili.com/pgc/view/web/season?season_id=%s"%it["season_id"])
    res=sd.get("result") or {}
    eps=res.get("episodes") or []
    if not eps: continue
    ep=eps[0]
    pl=fetch("https://api.bilibili.com/pgc/player/web/playurl?"+sign({"ep_id":ep.get("id"),"fnval":16,"qn":127,"fnver":0,"fourk":1}))
    r=pl.get("result") or {}
    out["movie_playurl"].append({"season_id":it["season_id"],"title":res.get("title"),
        "ep_id":ep.get("id"),"ep_duration":ep.get("duration"),"badge":ep.get("badge"),
        "code":pl.get("code"),"message":pl.get("message"),
        "is_preview":r.get("is_preview"),"quality":r.get("quality"),
        "dash_duration":((r.get("dash") or {}).get("duration")),
        "audio":[{"id":a.get("id"),"bw":a.get("bandwidth")} for a in ((r.get("dash") or {}).get("audio") or [])],
        "durl_len":[x.get("length") for x in (r.get("durl") or [])],
        "durl_size":[x.get("size") for x in (r.get("durl") or [])]})

# E) pugv course playurl - find a course via search
cs=fetch("https://api.bilibili.com/x/web-interface/wbi/search/type?"+sign({"search_type":"video","keyword":"课程 付费 教程","page":1}))
out["pugv"]={"note":"search-based course lookup skipped; endpoint probed separately"}

# F) browser cookie DB presence
paths={
 "chrome":os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Network\Cookies"),
 "edge":os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Network\Cookies"),
 "firefox":os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles"),
}
out["cookie_dbs"]={k:{"exists":os.path.exists(v),"path":v} for k,v in paths.items()}
if os.path.isdir(paths["firefox"]):
    out["cookie_dbs"]["firefox_profiles"]=os.listdir(paths["firefox"])

with open(r"E:\deepseek\FM114.5\.scratch\pic2video\probe\probe8.json","w",encoding="utf-8") as f:
    json.dump(out,f,ensure_ascii=False,indent=1)
print("ok")
