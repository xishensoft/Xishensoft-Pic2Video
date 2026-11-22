# -*- coding: utf-8 -*-
"""Probe 2: sample popular videos, map dash audio availability/bandwidth without login."""
import json, time, hashlib, urllib.parse, urllib.request, http.cookiejar, sys
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
MIXIN=[None]
op = urllib.request.build_opener()
op.addheaders=[("User-Agent",UA),("Referer","https://www.bilibili.com/"),("Origin","https://www.bilibili.com")]
def fetch(url, tries=3):
    for i in range(tries):
        try:
            with op.open(url, timeout=20) as r: return json.loads(r.read().decode("utf-8","replace"))
        except Exception as e:
            err=repr(e); time.sleep(2)
    return {"code":"exception","message":err}
def load():
    nav=fetch("https://api.bilibili.com/x/web-interface/nav")
    w=nav["data"]["wbi_img"]; raw=w["img_url"].rsplit("/",1)[-1].split(".")[0]+w["sub_url"].rsplit("/",1)[-1].split(".")[0]
    MIXIN[0]="".join(raw[i] for i in TAB)[:32]
def sign(p):
    p=dict(p); p["wts"]=int(time.time())
    qs=urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
    p["w_rid"]=hashlib.md5((qs+MIXIN[0]).encode()).hexdigest()
    return urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
load()
rank=fetch("https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all")
items=(rank.get("data") or {}).get("list") or []
print("rank_code",rank.get("code"),"n",len(items))
rows=[]
for it in items[:18]:
    bv=it.get("bvid")
    v=fetch("https://api.bilibili.com/x/web-interface/wbi/view?"+sign({"bvid":bv}))
    vd=v.get("data") or {}
    if v.get("code")!=0:
        rows.append({"bvid":bv,"view_code":v.get("code"),"view_msg":v.get("message")}); continue
    cid=vd.get("cid")
    p=fetch("https://api.bilibili.com/x/player/wbi/playurl?"+sign({"bvid":bv,"cid":cid,"fnval":16,"qn":127,"fourk":1,"platform":"pc"}))
    d=p.get("data") or {}; dash=d.get("dash") or {}
    rows.append({"bvid":bv,"title":(vd.get("title") or "")[:28],"duration":vd.get("duration"),"videos":vd.get("videos"),
                 "code":p.get("code"),"msg":p.get("message"),"quality":d.get("quality"),
                 "accept":d.get("accept_quality"),
                 "audio":[(a.get("id"),a.get("bandwidth"),a.get("codecs"),a.get("mimeType")) for a in dash.get("audio") or []],
                 "dash_video_ids":[x.get("id") for x in dash.get("video") or []]})
    time.sleep(0.4)
from collections import Counter
bw=Counter(); ids=Counter(); missing=0
for r in rows:
    if "audio" not in r: continue
    if not r["audio"]: missing+=1
    for a in r["audio"]:
        ids[a[0]]+=1; bw[(a[0],a[1])]+=1
open(r"E:\\deepseek\\FM114.5\\.scratch\\pic2video\\probe\\probe2.json","w",encoding="utf-8").write(json.dumps({"rows":rows,"id_counts":dict(ids),"bw_pairs":[[k[0],k[1],v] for k,v in bw.items()],"no_audio":missing},ensure_ascii=False,indent=1))
print("WROTE", len(rows), "rows; ids", dict(ids))