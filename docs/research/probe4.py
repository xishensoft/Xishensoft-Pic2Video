# -*- coding: utf-8 -*-
import json, time, hashlib, urllib.parse, urllib.request
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
TAB=[46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
MIXIN=[None]
op=urllib.request.build_opener()
op.addheaders=[("User-Agent",UA),("Referer","https://www.bilibili.com/"),("Origin","https://www.bilibili.com")]
def get(url):
    try:
        with op.open(url,timeout=20) as r: return r.read().decode("utf-8","replace")
    except Exception as e: return "ERR "+repr(e)
def j(url):
    t=get(url)
    try: return json.loads(t)
    except: return {"raw":t[:400]}
nav=j("https://api.bilibili.com/x/web-interface/nav")
w=nav["data"]["wbi_img"]; raw=w["img_url"].rsplit("/",1)[-1].split(".")[0]+w["sub_url"].rsplit("/",1)[-1].split(".")[0]
MIXIN[0]="".join(raw[i] for i in TAB)[:32]
def sign(p):
    p=dict(p); p["wts"]=int(time.time())
    qs=urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
    p["w_rid"]=hashlib.md5((qs+MIXIN[0]).encode()).hexdigest()
    return urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
out={}
out["season_raw"]=get("https://api.bilibili.com/pgc/view/web/season?season_id=33415")[:600]
out["season_ep_raw"]=get("https://api.bilibili.com/pgc/view/web/season?ep_id=33415")[:300]
sr=j("https://api.bilibili.com/x/web-interface/wbi/search/type?"+sign({"search_type":"video","keyword":"充电专属","page":1}))
res=((sr.get("data") or {}).get("result") or [])[:12]
out["search_items"]=[{"bvid":x.get("bvid"),"title":x.get("title"),"author":x.get("author"),"description":(x.get("description") or "")[:40]} for x in res]
# check each for charging/paid rights
checks=[]
for x in res[:8]:
    bv=x.get("bvid")
    v=j("https://api.bilibili.com/x/web-interface/wbi/view?"+sign({"bvid":bv}))
    vd=v.get("data") or {}
    rights={k:vd.get("rights",{}).get(k) for k in ("pay","ugc_pay","is_charging_arc","arc_pay","free_watch","pay_free_watch")} if isinstance(vd,dict) else {}
    cid=vd.get("cid") if isinstance(vd,dict) else None
    p=j("https://api.bilibili.com/x/player/wbi/playurl?"+sign({"bvid":bv,"cid":cid,"fnval":16,"qn":127})) if cid else {}
    checks.append({"bvid":bv,"view_code":v.get("code"),"view_msg":v.get("message"),"rights":rights,
                   "play_code":p.get("code"),"play_msg":p.get("message"),
                   "play_data_keys":list((p.get("data") or {}).keys()) if isinstance(p.get("data"),dict) else None,
                   "dash_dur":((p.get("data") or {}).get("dash") or {}).get("duration") if isinstance(p.get("data"),dict) else None,
                   "video_dur":vd.get("duration") if isinstance(vd,dict) else None})
out["checks"]=checks
open(r"E:\deepseek\FM114.5\.scratch\pic2video\probe\probe4.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=1))
print("ok")
