# -*- coding: utf-8 -*-
"""Probe 3: failure boundaries - bad BV, bangumi, live, paid/preview."""
import json, time, hashlib, urllib.parse, urllib.request
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
TAB=[46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
MIXIN=[None]
op=urllib.request.build_opener()
op.addheaders=[("User-Agent",UA),("Referer","https://www.bilibili.com/"),("Origin","https://www.bilibili.com")]
def fetch(url,tries=2):
    global err
    err=None
    for i in range(tries):
        try:
            with op.open(url,timeout=20) as r: return json.loads(r.read().decode("utf-8","replace"))
        except Exception as e: err=repr(e); time.sleep(1)
    return {"code":"exception","message":err}
nav=fetch("https://api.bilibili.com/x/web-interface/nav")
w=nav["data"]["wbi_img"]; raw=w["img_url"].rsplit("/",1)[-1].split(".")[0]+w["sub_url"].rsplit("/",1)[-1].split(".")[0]
MIXIN[0]="".join(raw[i] for i in TAB)[:32]
def sign(p):
    p=dict(p); p["wts"]=int(time.time())
    qs=urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
    p["w_rid"]=hashlib.md5((qs+MIXIN[0]).encode()).hexdigest()
    return urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
def brief(d):
    x=d.get("data")
    if not isinstance(x,dict): x={}
    return {"code":d.get("code"),"message":d.get("message"),
            "data_type":type(x).__name__ if x else "empty","data_keys":list(x.keys())[:16],
            "quality":x.get("quality"),"has_dash":bool(x.get("dash")),"has_durl":bool(x.get("durl")),
            "is_preview":x.get("is_preview"),"durl_len":[z.get("length") for z in (x.get("durl") or [])][:3]}
cases={}
for bv in ("BV1ab411c7mD","BV1xx411c7mZ","BV1Aa4y1A7Aa","BV1bw4t6fET9"):
    v=fetch("https://api.bilibili.com/x/web-interface/wbi/view?"+sign({"bvid":bv}))
    cases["view_"+bv]=brief(v)
    cid=(v.get("data") or {}).get("cid") if isinstance(v.get("data"),dict) else None
    if cid: cases["play_"+bv]=brief(fetch("https://api.bilibili.com/x/player/wbi/playurl?"+sign({"bvid":bv,"cid":cid,"fnval":16,"qn":127})))
# pagelist without wbi
cases["pagelist_BV1bw4t6fET9"]=fetch("https://api.bilibili.com/x/player/pagelist?bvid=BV1bw4t6fET9")
# bangumi ranking to find live season ids
rank=fetch("https://api.bilibili.com/pgc/season/rank/web/list?season_type=1&day=3")
cases["pgc_rank"]= {"code":rank.get("code"),"message":rank.get("message"),"n":len(((rank.get("data") or {}).get("list") or []))}
lst=((rank.get("data") or {}).get("list") or [])[:3]
ids=[]
for it in lst:
    ids.append({"title":(it.get("title") or "")[:20],"season_id":it.get("season_id"),"url":it.get("url")})
cases["pgc_rank_items"]=ids
for sid in [i.get("season_id") for i in ids if i.get("season_id")]:
    s=fetch("https://api.bilibili.com/pgc/view/web/season?season_id=%s"%sid)
    sd=s.get("data") or {}
    eps=(sd.get("episodes") or [])[:2]
    cases["pgc_season_%s"%sid]={"code":s.get("code"),"message":s.get("message"),
        "title":sd.get("title"),"season_type":sd.get("season_type"),"episodes":[{"id":e.get("id"),"aid":e.get("aid"),"cid":e.get("cid"),"badge":e.get("badge"),"pay":e.get("pay"),"duration":e.get("duration")} for e in eps]}
    if eps:
        ep=eps[0]
        cases["pgc_play_ep_%s"%ep.get("id")]=brief(fetch("https://api.bilibili.com/pgc/player/web/playurl?ep_id=%s&fnval=16&qn=127"%ep.get("id")))
# live
for room in (1,6,21452505):
    cases["live_play_%s"%room]=brief(fetch("https://api.live.bilibili.com/room/v1/Room/playUrl?cid=%s&quality=4&platform=web"%room))
# search charging-exclusive
cases["search_charging"]=brief(fetch("https://api.bilibili.com/x/web-interface/wbi/search/type?"+sign({"search_type":"video","keyword":"充电专属","page":1})))
open(r"E:\deepseek\FM114.5\.scratch\pic2video\probe\probe3.json","w",encoding="utf-8").write(json.dumps(cases,ensure_ascii=False,indent=1))
print("WROTE cases", len(cases))
