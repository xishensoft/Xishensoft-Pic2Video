# -*- coding: utf-8 -*-
"""bili_audio.py - 从任意 B 站视频/番剧地址取最高音质音轨（仅标准库 + ffmpeg）。

用法:
    from bili_audio import bili_audio, BiliError
    bili_audio("https://www.bilibili.com/video/BV1bw4t6fET9", "out.m4a")
"""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile, time
import urllib.error, urllib.parse, urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REFERER = "https://www.bilibili.com/"
WBI_TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,
           39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,
           6,63,57,62,11,36,20,34,44,52]
AUDIO_PRIORITY = (30280, 30232, 30216, 30250, 30251)
_MIXIN = {"key": None, "ts": 0}

# 用户可见的中文错误提示
ERROR_MSG = {
    -404: "视频不存在或已被删除",
    62002: "稿件不可见（已删除/仅自己可见/审核中）",
    62004: "稿件审核中",
    -403: "没有访问权限（可能是付费/充电专属内容）",
    -400: "请求参数有误，链接可能不是有效的视频地址",
    -352: "请求被 B 站风控拦截，请稍后重试或降低频率",
    -412: "请求被风控拦截（IP 层面），请稍后再试",
    -688: "该内容有地区限制，当前网络无法观看",
    -689: "该内容有版权限制，无法观看",
    87007: "充电专属视频，需要给 UP 主充电后才能观看",
    87008: "付费视频，需要购买后才能观看",
    -10403: "该内容在当前地区不可观看",
    "preview": "这是会员/付费内容的试看片段，完整音轨需要大会员账号",
    "live": "直播流没有点播音轨，本工具只支持点播视频/番剧",
    "cheese": "付费课程需要登录且已购买，本工具暂不支持",
    "network": "网络请求失败，请检查网络或稍后重试",
    "ffmpeg": "ffmpeg 调用失败",
    "badjson": "B 站返回了非 JSON 内容（可能被风控或需要验证）",
}

class BiliError(RuntimeError):
    def __init__(self, code, message="", detail=""):
        self.code, self.api_message, self.detail = code, message, detail
        tip = ERROR_MSG.get(code) or ERROR_MSG.get(int(code) if str(code).lstrip("-").isdigit() else None)
        if tip is None:
            tip = "B 站接口返回错误（code=%s, message=%s）" % (code, message)
        super().__init__(tip + (("：" + detail) if detail else ""))
        self.user_message = tip

def _opener():
    op = urllib.request.build_opener()
    op.addheaders = [("User-Agent", UA), ("Referer", REFERER)]
    return op

OP = _opener()

def _get(url, timeout=20, retries=3, headers=None):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with OP.open(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise
            last = e
        except Exception as e:
            last = e
        time.sleep(0.8 * (i + 1))
    raise BiliError("network", "", "网络请求失败: %r" % (last,))

def _get_json(url, **kw):
    body = _get(url, **kw).decode("utf-8", "replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise BiliError("badjson", "", body[:200])

def _wbi_mixin():
    now = time.time()
    if _MIXIN["key"] and now - _MIXIN["ts"] < 3600:
        return _MIXIN["key"]
    nav = _get_json("https://api.bilibili.com/x/web-interface/nav")
    wbi = (nav.get("data") or {}).get("wbi_img")
    if not wbi:
        raise BiliError(nav.get("code"), nav.get("message"), "无法获取 WBI 密钥")
    raw = (wbi["img_url"].rsplit("/", 1)[-1].split(".")[0] +
           wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0])
    _MIXIN["key"] = "".join(raw[i] for i in WBI_TAB)[:32]
    _MIXIN["ts"] = now
    return _MIXIN["key"]

def _wbi_sign(params):
    p = {k: v for k, v in params.items() if v is not None}
    p["wts"] = int(time.time())
    qs = urllib.parse.urlencode(sorted((k, str(v)) for k, v in p.items()))
    p["w_rid"] = hashlib.md5((qs + _wbi_mixin()).encode()).hexdigest()
    return urllib.parse.urlencode(sorted((k, str(v)) for k, v in p.items()))

def _parse_input(url):
    """返回 dict(bvid/aid/ep_id/ss_id/p)。"""
    url = url.strip()
    if re.search(r"live\.bilibili\.com", url):
        raise BiliError("live", "", "直播地址：%s" % url)
    if re.search(r"/cheese/", url):
        raise BiliError("cheese", "", "课程地址：%s" % url)
    if url.startswith("b23.tv") or url.startswith("http://b23.tv") or url.startswith("https://b23.tv"):
        url = _resolve_short(url if "://" in url else "https://" + url)
    u = urllib.parse.urlsplit(url)
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    if m:
        q = urllib.parse.parse_qs(u.query)
        return {"bvid": m.group(1), "p": int(q.get("p", ["1"])[0])}
    m = re.search(r"/video/av(\d+)", url, re.I)
    if m:
        q = urllib.parse.parse_qs(u.query)
        return {"aid": int(m.group(1)), "p": int(q.get("p", ["1"])[0])}
    m = re.search(r"/bangumi/play/(ep|ss)(\d+)", url)
    if m:
        return {"ep_id": int(m.group(2))} if m.group(1) == "ep" else {"ss_id": int(m.group(2))}
    m = re.search(r"^av(\d+)$", url, re.I)
    if m:
        return {"aid": int(m.group(1))}
    raise BiliError(-400, "", "无法识别的链接：%s" % url)

def _resolve_short(url):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None
    op = urllib.request.build_opener(NoRedirect)
    op.addheaders = [("User-Agent", UA)]
    try:
        with op.open(url, timeout=20) as r:
            body = r.read()
            code = r.status
            loc = r.headers.get("Location")
    except urllib.error.HTTPError as e:
        body, code, loc = e.read(), e.code, e.headers.get("Location")
    if code in (301, 302, 303, 307, 308) and loc:
        return loc
    try:
        j = json.loads(body.decode("utf-8", "replace"))
        raise BiliError(j.get("code"), j.get("message"), "短链已失效或被删除")
    except json.JSONDecodeError:
        raise BiliError(-400, "", "短链解析失败（HTTP %s）" % code)

def _select_audio(dash):
    audios = [a for a in (dash or {}).get("audio") or [] if a.get("baseUrl") or a.get("base_url")]
    if not audios:
        return None
    def rank(a):
        aid = a.get("id")
        idx = AUDIO_PRIORITY.index(aid) if aid in AUDIO_PRIORITY else 99
        # 先按实际码率降序，同码率再按 id 优先级（30280 优先）
        return (-(a.get("bandwidth") or 0), idx)
    return sorted(audios, key=rank)[0]

def _download(urls, dest, timeout=30, retries=3):
    last = None
    for url in urls:
        for i in range(retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
                with OP.open(req, timeout=timeout) as r, open(dest, "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                if os.path.getsize(dest) > 0:
                    return dest
            except Exception as e:
                last = e
                time.sleep(0.8 * (i + 1))
    raise BiliError("network", "", "音频下载失败: %r" % (last,))

def _remux(src, dst, wav=False, sample_rate=16000, mono=True):
    ff = shutil.which("ffmpeg")
    if not ff:
        if wav:
            raise BiliError("ffmpeg", "", "需要 ffmpeg 转 wav，但 PATH 中没有")
        shutil.move(src, dst)
        return dst
    cmd = [ff, "-y", "-v", "error", "-i", src]
    if wav:
        cmd += ["-vn", "-ar", str(sample_rate)] + (["-ac", "1"] if mono else []) + ["-c:a", "pcm_s16le", dst]
    else:
        cmd += ["-vn", "-c:a", "copy", dst]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise BiliError("ffmpeg", "", (p.stderr or "")[-400:])
    return dst

def _play_url(params):
    return _get_json("https://api.bilibili.com/x/player/wbi/playurl?" + _wbi_sign(params))

def bili_audio(url, out_path=None, wav=False, timeout=20, retries=3):
    """输入任意 B 站视频/番剧地址，返回本地音频文件路径（默认 .m4a）。"""
    info = _parse_input(url)
    audio = None
    title = None

    eps_dur = None
    if "ep_id" in info or "ss_id" in info:
        ep_id = info.get("ep_id")
        if ep_id is None:
            sd = _get_json("https://api.bilibili.com/pgc/view/web/season?season_id=%d" % info["ss_id"])
            res = sd.get("result") or sd.get("data") or {}
            eps = res.get("episodes") or []
            if not eps:
                raise BiliError(sd.get("code"), sd.get("message"), "该番剧没有可播放的剧集")
            ep_id = eps[0]["id"]
            eps_dur = eps[0].get("duration")
        pl = _get_json("https://api.bilibili.com/pgc/player/web/playurl?" +
                       _wbi_sign({"ep_id": ep_id, "fnval": 16, "qn": 127, "fnver": 0, "fourk": 1}))
        if pl.get("code") != 0:
            raise BiliError(pl.get("code"), pl.get("message"), "ep_id=%s" % ep_id)
        r = pl.get("result") or pl.get("data") or {}
        if r.get("is_preview") == 1:
            prev = round((((r.get("durl") or [{}])[0].get("length") or 0) / 1000.0))
            detail = "试看时长 %s 秒" % prev
            if eps_dur:
                detail += "，剧集本身 %s 秒" % round(eps_dur / 1000.0)
            detail += "；需要大会员 Cookie 才能取完整音轨"
            raise BiliError("preview", "is_preview", detail)
        title = "ep%s" % ep_id
        audio = _select_audio(r.get("dash"))
        if audio is None and r.get("durl"):
            return _durl_to_audio(r, out_path, title, wav, timeout, retries)
    else:
        params = {"bvid": info.get("bvid"), "aid": info.get("aid")}
        v = _get_json("https://api.bilibili.com/x/web-interface/wbi/view?" + _wbi_sign(params))
        if v.get("code") != 0:
            raise BiliError(v.get("code"), v.get("message"))
        vd = v.get("data") or {}
        title = vd.get("title") or info.get("bvid") or ("av%s" % info.get("aid"))
        pages = vd.get("pages") or []
        p = info.get("p", 1)
        if p < 1 or p > max(1, len(pages)):
            raise BiliError(-400, "", "分P参数 p=%d 超出范围（共 %d P）" % (p, len(pages)))
        cid = pages[p - 1]["cid"] if pages else vd.get("cid")
        pl = _play_url({"bvid": vd.get("bvid"), "cid": cid, "fnval": 16, "qn": 127,
                        "platform": "pc", "fnver": 0, "fourk": 1})
        if pl.get("code") != 0:
            raise BiliError(pl.get("code"), pl.get("message"), "cid=%s" % cid)
        d = pl.get("data") or {}
        if d.get("is_preview") == 1:
            raise BiliError("preview", "is_preview", "这是试看片段")
        audio = _select_audio(d.get("dash"))
        if audio is None and d.get("durl"):
            return _durl_to_audio(d, out_path, title, wav, timeout, retries)

    if audio is None:
        raise BiliError(-404, "", "没有可用的音频流")

    ext = "wav" if wav else "m4a"
    if out_path is None:
        safe = re.sub(r'[\\/:*?"<>|]', "_", title)[:80]
        out_path = os.path.join(os.getcwd(), safe + "." + ext)
    tmp = out_path + ".part"
    urls = [audio.get("baseUrl") or audio.get("base_url")] + list(audio.get("backupUrl") or audio.get("backup_url") or [])
    _download([u for u in urls if u], tmp, timeout=timeout, retries=retries)
    _remux(tmp, out_path, wav=wav)
    if os.path.exists(tmp):
        os.remove(tmp)
    return out_path

def _durl_to_audio(d, out_path, title, wav, timeout, retries):
    mp4 = out_path + ".mp4" if out_path else os.path.join(tempfile.gettempdir(), "bili_durl.mp4")
    urls = [x["url"] for x in d.get("durl") or [] if x.get("url")]
    if not urls:
        raise BiliError(-404, "", "没有可用的视频流")
    _download(urls, mp4, timeout=timeout, retries=retries)
    ext = "wav" if wav else "m4a"
    if out_path is None:
        safe = re.sub(r'[\\/:*?"<>|]', "_", title or "bili")[:80]
        out_path = os.path.join(os.getcwd(), safe + "." + ext)
    _remux(mp4, out_path, wav=wav)
    if os.path.exists(mp4):
        os.remove(mp4)
    return out_path