# -*- coding: utf-8 -*-
"""Probe 1: no-cookie nav/view/playurl (fnval=16 dash, fnval=1 durl)."""
import json, time, hashlib, urllib.parse, urllib.request, http.cookiejar, sys

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
MIXIN = None

def make_opener(use_cookies):
    cj = http.cookiejar.CookieJar() if use_cookies else None
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj)) if cj else urllib.request.build_opener()
    op.addheaders = [("User-Agent", UA), ("Referer", "https://www.bilibili.com/"), ("Origin", "https://www.bilibili.com")]
    return op

def fetch(op, url, tries=3):
    last = None
    for i in range(tries):
        try:
            with op.open(url, timeout=20) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = repr(e); time.sleep(1 + i)
    return {"code": "exception", "message": last}

def load_mixin(op):
    global MIXIN
    nav = fetch(op, "https://api.bilibili.com/x/web-interface/nav")
    wbi = nav["data"]["wbi_img"]
    raw = wbi["img_url"].rsplit("/",1)[-1].split(".")[0] + wbi["sub_url"].rsplit("/",1)[-1].split(".")[0]
    MIXIN = "".join(raw[i] for i in TAB)[:32]
    return {"nav_code": nav.get("code"), "isLogin": nav.get("data",{}).get("isLogin")}

def sign(p):
    p = {k: v for k, v in p.items() if v is not None}
    p["wts"] = int(time.time())
    qs = urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))
    p["w_rid"] = hashlib.md5((qs+MIXIN).encode()).hexdigest()
    return urllib.parse.urlencode(sorted((k,str(v)) for k,v in p.items()))

def summarize_play(p):
    d = p.get("data") or {}
    out = {"code": p.get("code"), "message": p.get("message"),
           "quality": d.get("quality"), "format": d.get("format"),
           "accept_quality": d.get("accept_quality"), "accept_desc": d.get("accept_description"),
           "timelength": d.get("timelength"), "has_dash": bool(d.get("dash")), "has_durl": bool(d.get("durl"))}
    dash = d.get("dash") or {}
    if dash:
        out["duration_s"] = dash.get("duration")
        out["audio"] = [{"id":a.get("id"),"baseUrl_host":urllib.parse.urlsplit(a.get("baseUrl","")).netloc,
                         "bandwidth":a.get("bandwidth"),"codecs":a.get("codecs"),
                         "mimeType":a.get("mimeType"),"channels":a.get("channels"),
                         "sampleRate":a.get("sampleRate"),"has_backup":bool(a.get("backupUrl"))} for a in dash.get("audio") or []]
        out["video_ids"] = [v.get("id") for v in dash.get("video") or []]
    if d.get("durl"):
        out["durl"] = [{"order":x.get("order"),"size":x.get("size"),"length":x.get("length"),
                        "url_host":urllib.parse.urlsplit(x.get("url","")).netloc} for x in d["durl"]]
    return out

bv = sys.argv[1] if len(sys.argv) > 1 else "BV1bw4t6fET9"
report = {}
for mode in ("raw", "anon"):
    op = make_opener(mode == "anon")
    r = {"load_mixin": load_mixin(op)}
    view = fetch(op, "https://api.bilibili.com/x/web-interface/wbi/view?" + sign({"bvid": bv}))
    vd = view.get("data") or {}
    r["view"] = {"code": view.get("code"), "message": view.get("message"),
                 "title": vd.get("title"), "cid": vd.get("cid"), "aid": vd.get("aid"),
                 "duration": vd.get("duration"), "videos": vd.get("videos"),
                 "pages": [{"page":x.get("page"),"cid":x.get("cid"),"part":x.get("part"),"duration":x.get("duration")} for x in (vd.get("pages") or [])],
                 "rights": {k: (vd.get("rights") or {}).get(k) for k in ("pay","ugc_pay","is_charging_arc","arc_pay","free_watch","pay_free_watch")}}
    cid = vd.get("cid")
    if cid:
        for label, params in [
            ("dash_pc", {"bvid":bv,"cid":cid,"fnval":16,"qn":127,"fourk":1,"platform":"pc"}),
            ("dash_html5", {"bvid":bv,"cid":cid,"fnval":16,"qn":127,"platform":"html5"}),
            ("durl_pc", {"bvid":bv,"cid":cid,"fnval":1,"qn":127,"platform":"pc"}),
            ("durl_html5", {"bvid":bv,"cid":cid,"fnval":1,"qn":32,"platform":"html5"}),
        ]:
            r[label] = summarize_play(fetch(op, "https://api.bilibili.com/x/player/wbi/playurl?" + sign(params)))
    report[mode] = r

print(json.dumps(report, ensure_ascii=False, indent=1))
