# -*- coding: utf-8 -*-
"""Probe 5: bangumi (result key), pgc playurl, region/paid attempts, cheese course."""
import json, time, hashlib, urllib.parse, urllib.request
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
TAB=[46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
MIXIN=[None]
op=urllib.request.build_opener()
op.addheaders=[("User-Agent",UA),("Referer","https://www.bilibili.com/"),("Origin","https://www.bilibili.com")]
def j(url):
    try:
        with op.open(url,timeout=20) as r: return json.loads(r.read().decode("utf-8","replace"))
    except Exception as e: return {"code":"exception","message":repr(e)}
nav=j("https://api.bilibili.com/x/web-interface/nav")
w=nav["data"]["wbi_img"]; raw=w["img_url"].rsplit("/",1)[-1].split(".")[0]+w["sub_url"].rsplit("/",1)[-1].split(".")[0]
MIXIN[0]="".join(raw[i] for i in TAB)[:32]
def sign(p):
    p=dict(p); p["wts"]=int(time.time())
    qs=urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
    p["w_rid"]=hashlib.md5((qs+MIXIN[0]).encode()).hexdigest()
    return urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
def playbrief(d):
    x=d.get("result") if isinstance(d.get("result"),dict) else d.get("data")
    if not isinstance(x,dict): x={}
    dash=x.get("dash") or {}
    return {"code":d.get("code"),"message":d.get("message"),
            "keys":list(x.keys())[:18],"quality":x.get("quality"),
            "has_dash":bool(dash),"dash_duration":dash.get("duration"),
            "audio":[(a.get("id"),a.get("bandwidth")) for a in dash.get("audio") or []],
            "has_durl":bool(x.get("durl")),"durl_len":[z.get("length") for z in (x.get("durl") or [])][:3],
            "is_preview":x.get("is_preview"),"result_type":x.get("result") if isinstance(x.get("result"),str) else None}
out={}
s=j("https://api.bilibili.com/pgc/view/web/season?season_id=33415")
r=s.get("result") or {}
eps=r.get("episodes") or []
out["season"]={"code":s.get("code"),"title":r.get("title"),"season_type":r.get("season_type"),
               "areas":[a.get("name") for a in r.get("areas") or []],
               "rights":{k:r.get("rights",{}).get(k) for k in ("is_vip","is_pay","allow_download","allow_dm","area_limit")},
               "ep_count":len(eps),
               "eps":[{"id":e.get("id"),"cid":e.get("cid"),"aid":e.get("aid"),"badge":e.get("badge"),"pay":e.get("pay"),"duration":e.get("duration")} for e in eps[:3]]}
if eps:
    ep=eps[0]
    out["pgc_play_ep16"]=playbrief(j("https://api.bilibili.com/pgc/player/web/playurl?ep_id=%s&fnval=16&qn=127"%ep.get("id")))
    out["pgc_play_ep1"]=playbrief(j("https://api.bilibili.com/pgc/player/web/playurl?ep_id=%s&fnval=1&qn=32&platform=html5"%ep.get("id")))
    out["ugc_play_ep"]=playbrief(j("https://api.bilibili.com/x/player/wbi/playurl?"+sign({"ep_id":ep.get("id"),"fnval":16,"qn":127})))
# find a region-limited bangumi via pgc search
for kw in ("进击的巨人","Lycoris Recoil","莉可丽丝"):
    sr=j("https://api.bilibili.com/pgc/season/search/list?keyword="+urllib.parse.quote(kw)+"&page=1&season_type=1")
    items=((sr.get("result") or {}).get("list") or [])[:3]
    out["pgc_search_"+kw]={"code":sr.get("code"),"msg":sr.get("message"),
        "items":[{"title":x.get("title"),"season_id":x.get("season_id"),"areas":x.get("areas")} for x in items]}
    if items:
        sid=items[0].get("season_id")
        s2=j("https://api.bilibili.com/pgc/view/web/season?season_id=%s"%sid)
        r2=s2.get("result") or {}
        e2=(r2.get("episodes") or [])
        out["region_season_%s"%sid]={"code":s2.get("code"),"title":r2.get("title"),
            "areas":[a.get("name") for a in r2.get("areas") or []],
            "rights":{k:r2.get("rights",{}).get(k) for k in ("area_limit","is_vip","is_pay")},
            "first_ep":e2[0].get("id") if e2 else None}
        if e2:
            out["region_play_%s"%sid]=playbrief(j("https://api.bilibili.com/pgc/player/web/playurl?ep_id=%s&fnval=16&qn=127"%e2[0].get("id")))
# cheese / pugv
for sid in (101, 100, 1):
    out["pugv_season_%s"%sid]=playbrief(j("https://api.bilibili.com/pugv/view/web/season?season_id=%s"%sid))
open(r"E:\deepseek\FM114.5\.scratch\pic2video\probe\probe5.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=1))
print("ok",len(out))
