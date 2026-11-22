
# -*- coding: utf-8 -*-
"""Probe 7: multi-P cid, VIP bangumi ep, charging/paid UGC, durl audio stream."""
import json, time, hashlib, urllib.parse, urllib.request, http.cookiejar, sys

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
MIXIN = None
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.addheaders = [("User-Agent", UA), ("Referer", "https://www.bilibili.com/"), ("Origin", "https://www.bilibili.com")]

def fetch(url, tries=3):
    last=None
    for i in range(tries):
        try:
            with op.open(url, timeout=25) as r:
                return json.loads(r.read().decode("utf-8","replace"))
        except Exception as e:
            last=repr(e); time.sleep(1+i)
    return {"code":"exception","message":last}

def load_mixin():
    global MIXIN
    nav = fetch("https://api.bilibili.com/x/web-interface/nav")
    w = nav["data"]["wbi_img"]
    raw = w["img_url"].rsplit("/",1)[-1].split(".")[0] + w["sub_url"].rsplit("/",1)[-1].split(".")[0]
    MIXIN = "".join(raw[i] for i in TAB)[:32]
    return nav.get("code")

def sign(p):
    p={k:v for k,v in p.items() if v is not None}
    p["wts"]=int(time.time())
    qs=urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
    p["w_rid"]=hashlib.md5((qs+MIXIN).encode()).hexdigest()
    return urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))

def audio_summary(p):
    d=p.get("data") or {}
    dash=d.get("dash") or {}
    return {"code":p.get("code"),"message":p.get("message"),
            "quality":d.get("quality"),"accept_quality":d.get("accept_quality"),
            "duration":dash.get("duration"),"timelength":d.get("timelength"),
            "audio":[{"id":a.get("id"),"bw":a.get("bandwidth"),"sr":a.get("sampleRate")} for a in (dash.get("audio") or [])],
            "has_durl":bool(d.get("durl")),
            "durl":[{"size":x.get("size"),"length":x.get("length"),"host":urllib.parse.urlsplit(x.get("url","")).netloc} for x in (d.get("durl") or [])],
            "result_keys":list((p.get("result") or {}).keys())[:12]}

out={}
out["nav_code"]=load_mixin()

# 1) find multi-P video from ranking
rank = fetch("https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all")
multi=None
for v in (rank.get("data") or {}).get("list") or []:
    if (v.get("videos") or 1) > 1:
        multi={"bvid":v.get("bvid"),"title":v.get("title"),"videos":v.get("videos")}
        break
out["multi_candidate"]=multi
if multi:
    bv=multi["bvid"]
    view=fetch("https://api.bilibili.com/x/web-interface/wbi/view?"+sign({"bvid":bv}))
    vd=view.get("data") or {}
    pages=[{"page":x.get("page"),"cid":x.get("cid"),"part":x.get("part"),"dur":x.get("duration")} for x in (vd.get("pages") or [])]
    out["multi_view"]={"code":view.get("code"),"bvid":bv,"videos":vd.get("videos"),"cid":vd.get("cid"),"pages":pages[:4]}
    if len(pages)>1:
        p2=pages[1]
        pl=fetch("https://api.bilibili.com/x/player/wbi/playurl?"+sign({"bvid":bv,"cid":p2["cid"],"fnval":16,"qn":127,"platform":"pc","fnver":0,"fourk":1}))
        out["multi_p2_playurl"]=audio_summary(pl)
        out["multi_p2_playurl"]["requested_cid"]=p2["cid"]
        pl1=fetch("https://api.bilibili.com/x/player/wbi/playurl?"+sign({"bvid":bv,"cid":pages[0]["cid"],"fnval":16,"qn":127,"platform":"pc","fnver":0,"fourk":1}))
        out["multi_p1_playurl"]=audio_summary(pl1)
        out["multi_p1_playurl"]["requested_cid"]=pages[0]["cid"]

# 2) bangumi rank -> season -> VIP ep
br = fetch("https://api.bilibili.com/pgc/web/rank/list?day=3&season_type=1")
seasons=[]
for it in ((br.get("result") or {}).get("list") or [])[:6]:
    seasons.append({"season_id":it.get("season_id"),"title":it.get("title"),"badge":it.get("badge"),"new_ep":(it.get("new_ep") or {}).get("index_show")})
out["bangumi_rank"]={"code":br.get("code"),"message":br.get("message"),"seasons":seasons}
out["bangumi_playurls"]=[]
for s in seasons[:3]:
    sd = fetch("https://api.bilibili.com/pgc/view/web/season?season_id=%s" % s["season_id"])
    res = sd.get("result") or {}
    eps = res.get("episodes") or []
    rights = res.get("rights") or {}
    picked=None
    for e in eps:
        if e.get("badge") and ("会员" in e.get("badge") or "VIP" in e.get("badge")):
            picked=e; break
    if not picked and eps:
        picked=eps[-1]
    rec={"season_id":s["season_id"],"title":res.get("title"),
         "areas":[a.get("name") for a in (res.get("areas") or [])],
         "rights":{k:rights.get(k) for k in ("area_limit","allow_download","is_preview","ban_area_show")},
         "ep_count":len(eps),
         "ep_badges":sorted({(e.get("badge") or "") for e in eps}),
         "picked":{"ep_id":(picked or {}).get("id"),"badge":(picked or {}).get("badge"),
                   "title":(picked or {}).get("title"),"long_title":(picked or {}).get("long_title"),
                   "duration":(picked or {}).get("duration")}}
    if picked:
        pl=fetch("https://api.bilibili.com/pgc/player/web/playurl?"+sign({"ep_id":picked.get("id"),"fnval":16,"qn":127,"fnver":0,"fourk":1}))
        rec["playurl"]=audio_summary(pl)
        pl["data"]=pl.get("data") or pl.get("result")
        rec["playurl"]["result_type"]=("result" if pl.get("result") is not None else "data")
        if pl.get("result") is not None:
            r=pl["result"] or {}
            rec["playurl"]["quality"]=r.get("quality"); rec["playurl"]["is_preview"]=r.get("is_preview")
            rec["playurl"]["duration"]=((r.get("dash") or {}).get("duration"))
            rec["playurl"]["audio"]=[{"id":a.get("id"),"bw":a.get("bandwidth"),"sr":a.get("sampleRate")} for a in ((r.get("dash") or {}).get("audio") or [])]
            rec["playurl"]["has_durl"]=bool(r.get("durl"))
            rec["playurl"]["durl"]=[{"size":x.get("size"),"length":x.get("length")} for x in (r.get("durl") or [])]
    out["bangumi_playurls"].append(rec)

# 3) charging/paid UGC via search
sr = fetch("https://api.bilibili.com/x/web-interface/wbi/search/type?"+sign({"search_type":"video","keyword":"充电专属","page":1}))
items=[]
for it in ((sr.get("data") or {}).get("result") or [])[:8]:
    items.append({"bvid":it.get("bvid"),"title":(it.get("title") or "").replace('<em class="keyword">','').replace("</em>","")})
out["charge_search"]={"code":sr.get("code"),"message":sr.get("message"),"items":items}
out["charge_views"]=[]
for it in items[:5]:
    v=fetch("https://api.bilibili.com/x/web-interface/wbi/view?"+sign({"bvid":it["bvid"]}))
    vd=v.get("data") or {}
    rights=vd.get("rights") or {}
    rec={"bvid":it["bvid"],"code":v.get("code"),"message":v.get("message"),
         "rights":{k:rights.get(k) for k in ("pay","ugc_pay","is_charging_arc","arc_pay","pay_free_watch","free_watch")},
         "cid":vd.get("cid"),"duration":vd.get("duration")}
    if vd.get("cid"):
        pl=fetch("https://api.bilibili.com/x/player/wbi/playurl?"+sign({"bvid":it["bvid"],"cid":vd["cid"],"fnval":16,"qn":127,"platform":"pc","fnver":0,"fourk":1}))
        rec["playurl"]=audio_summary(pl)
    out["charge_views"].append(rec)

# 4) durl audio: short video full download then ffprobe (done by caller)
out["durl_probe_bv"]="BV1jQst6BEf2"

with open(r"E:\deepseek\FM114.5\.scratch\pic2video\probe\probe7.json","w",encoding="utf-8") as f:
    json.dump(out,f,ensure_ascii=False,indent=1)
print("ok")
