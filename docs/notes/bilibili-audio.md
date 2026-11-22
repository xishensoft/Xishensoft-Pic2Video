# B 站音轨直取：无登录最高音质与边界

> 实测时间：2026-11-22（Asia/Shanghai）。**全程无登录、无 Cookie**，出口 IP 在中国大陆；Python 3.12.5 / ffmpeg、ffprobe 9.0.2；原生 Windows PowerShell。
> 本文结论以**实测**为准，文档只用于解释字段含义。探针脚本与原始 JSON 全部在 `.scratch/pic2video/probe/`。

## 0. 结论速览

1. **不需要任何 Cookie**：UA + Referer 就能拿到 dash 音频（30280/30232/30216），连 nav 的 -101（未登录）都不影响，wbi_img 照常返回。
2. 30280 名义 192K，**实测 87–320 kbps**，全部 AAC-LC 48 kHz 立体声，流时长与视频/dash.duration 一致（误差 < 1 s）。
3. 音轨**不受未登录画质压制影响**：未登录画质被压到 720P（quality=64），但音频三条 id 与带宽跟登录态样本一致，30280 没有被降成 64K。
4. **真陷阱是会员/付费番剧**：接口返回 code 0，但 is_preview=1、没有 dash、只有 durl，而 durl 是**试看片段**（实测 180s / 360s / 600s 不等）。不判断就会把 6 分钟试看当正片。
5. 最佳策略：**fnval=16 取 dash.audio 直下 m4s**（几 MB）；没有 dash 时才退回 durl 下整段 mp4 再抽音轨（音轨质量≈30280，但流量 55–130 MB）。
6. 要 30250（杜比）/30251（Hi-Res FLAC）必须大会员，无登录时 dash.flac.audio 为 null，只有 display 标记。**对本项目（转 16k 单声道）完全不值得**。

---

## 1. 实测环境与复现命令

```powershell
# 环境
python --version           # Python 3.12.5
ffmpeg -version            # 9.0.2
# 探针（每个都会写同名 .json 结果）
$env:PYTHONIOENCODING='utf-8'
python .scratch\pic2video\probe\probe1.py     # raw vs 匿名 cookie 对比
python .scratch\pic2video\probe\probe6.py     # dash/durl 下载 + ffprobe 实测
python .scratch\pic2video\probe\probe7.py     # 多 P、番剧 VIP 集
python .scratch\pic2video\probe\probe10.py    # 短链、av2bv、m4s 无 Referer
python .scratch\pic2video\probe\test_bili_audio.py   # 参考实现 11 个用例
```

---

## 2. 接口链路 (a)

### 2.1 取 cid：wbi/view 与 pagelist

| 端点 | 参数 | 实测 |
| --- | --- | --- |
| `x/web-interface/wbi/view` | bvid / aid（+ wbi 签名） | code 0，返回 title、cid、pages[]、duration、rights |
| `x/web-interface/view` | bvid / aid（**无需签名**） | 同样 code 0，可作为兜底 |
| `x/player/pagelist` | bvid（无需签名） | data[] 每项含 cid/page/part/duration |

关键点（实测）：

- **分 P 必须用 pages[p-1].cid**。`view?bvid=...&p=2` 不会切换 cid（cid 恒为第 1P，只有 part 字段随 p 变）。
- 单 P 视频 pages 也只有 1 项；p 越界要自己判断（接口不报错）。
- 实测样本：BV1NDsS6UE8D（69 P）P1 cid=43721230307 / 399 s，P2 cid=43719068751 / 670 s；playurl 用 P2 的 cid 拿到的 dash.duration=670，正确。

### 2.2 playurl：fnval=16（dash）vs fnval=1（durl）

同一视频 BV1bw4t6fET9（1981 s，单 P）四种请求对照：

| 请求 | 返回结构 | 实测结果 |
| --- | --- | --- |
| `fnval=16&qn=127&platform=pc` | dash | quality=64(720P)，accept_quality=[120,112,80,64,32,16]，dash.duration=1981；audio 3 条 |
| `fnval=16&platform=html5` | **无 dash，直接 durl** | quality=64，单段 130,968,736 B |
| `fnval=1&qn=127&platform=pc` | durl | quality=64，单段 130,968,736 B |
| `fnval=1&qn=32&platform=html5` | durl | quality=16(360P)，55,470,452 B |

结论：

- dash 音频是**独立 m4s**，只下音频即可；durl 是**整段 mp4**（h264 + aac 复用），必须下整片再抽音轨。
- **platform=html5 只给 MP4**（无 dash），且 html5 + qn=32 会被压到 360P。要 dash 就传 platform=pc。
- 无登录时 durl 不返回"独立音频"，只有带画面的整段 mp4。

### 2.3 dash.audio 实测明细

| 视频 | 时长 | 30280 bw | 30232 bw | 30216 bw | 30280 实测码率 |
| --- | --- | --- | --- | --- | --- |
| BV1jQst6BEf2 | 22 s | 232254 | 112929 | 66358 | 230233 bps |
| BV1ALs465E2V | 190 s | 180678 | 90184 | 65708 | — |
| BV13qs46FE9C | 662 s | 100988 | 100988 | （未记录） | — |
| BV1bw4t6fET9 | 1981 s | 88662 | 88662 | 65677 | 86986 bps |
| 番剧 ep323085（免费集） | 1494 s | 323176 | 134243 | 68752 | 320005 bps |

排行榜前 18 个热门视频采样：**全部 code 0、quality=64**，每条都有 30216/30232/30280 三条，30280 带宽范围 100,988–232,254，**没有出现 30250/30251**（那是有大会员才有的条目）。

两条容易误解的事实：

1. **同 bandwidth 的 30280 与 30232 是同一文件别名**。BV13qs46FE9C 两条都是 100988，首 256 KB 的 md5 完全相同（`ac6e0a216d8e99e1b578392da900151e`）；bandwidth 不同则内容不同。
2. **30280 不等于 192K**。它是"音轨槽位 id"，实际码率由 UP 主上传的源决定，实测低到 87 kbps 也有。选流应按 bandwidth 取最大，id 只做同码率时的次序（脚本里就是这么做的）。

### 2.4 是否需要 Cookie / 签名

- `x/web-interface/nav` 无登录返回 code **-101**（账号未登录）、isLogin=false，但 **data.wbi_img 正常返回**——WBI 签名照常可用。
- `x/player/wbi/playurl` **不带 wbi 签名**（只有 bvid/cid/fnval/qn/platform）也能 code 0；旧 `x/player/playurl` 无签名同样 code 0。说明当前 -352 风控不是必现。但**仍建议签名**（成本极低，见第 6 节），以免某天被风控。
- m4s 直链**不带 Referer/UA 也能 206 拉到数据**（无头 curl 取 0–1023 字节，206 + 1024 字节）；仍建议带 UA + Referer，防以后收紧。
- url 有效期约 120 分钟（文档），过期重新调 playurl 即可。

---

## 3. 地址解析 (b)

### 3.1 b23.tv 短链

| 情况 | 实测 |
| --- | --- |
| 有效短码 `https://b23.tv/BV1bw4t6fET9` | HTTP **302**，Location: `https://www.bilibili.com/video/BV1bw4t6fET9` |
| 失效短码 `https://b23.tv/H4FAqk6` | HTTP **200** + Content-Type: application/json + body `{"code":-404,"message":"啥都木有","ttl":1}`，响应头带 `Bili-Status-Code: -404` |

所以解析必须**先看 3xx 的 Location，没有 3xx 再按 JSON 的 code 报错**；只看状态码会把失效短链当成功。

### 3.2 分 P

`?p=2` 从 query 里取，映射到 pages[1].cid。也支持 b23 短链跳转后带 p 的形式。

### 3.3 av 号 → BV 号

两种都可行：

1. **直接调 API**：`x/web-interface/view?aid=117562180376697` → code 0，data.bvid=BV1bw4t6fET9，data.cid=43344332203。一次请求同时拿到 bvid 和 cid，**推荐**。
2. **本地算法**（不想多一次请求时）：码表 `FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf`，XOR=23442827791579，`tmp=(1<<51|aid)^XOR`，58 进制从右往左填入 `BV1000000000`，最后交换 bytes[3]<->bytes[9]、bytes[4]<->bytes[7]。已用 probe_av2bv.py 验证：av170001→BV17x411w7KC、av2→BV1xx411c7mD、av117562180376697→BV1bw4t6fET9，与 API 一致。

---

## 4. 失败边界与错误码映射 (c)

| 场景 | 实测返回 | 建议给用户的中文提示 |
| --- | --- | --- |
| 正常 UGC | code 0 + dash | — |
| BV 不存在 | view code **-404** "啥都木有" | 视频不存在或已被删除 |
| 已删除 / 仅自己可见 / 审核中 | view code **62002** "稿件不可见" | 稿件不可见（已删除/仅自己可见/审核中） |
| 坏 cid（1、99999999999） | playurl code **-404** | 同上；重取 cid |
| 不可见稿件的 playurl | code **-404**（不是 62002） | 同上 |
| 番剧免费集 | pgc playurl code 0，is_preview=0，有 dash，quality=32(480P) | 正常 |
| **番剧/影视会员集** | code 0，**is_preview=1**，无 dash，只有 durl，durl.length=试看长度 | 试看片段，完整音轨需大会员 |
| 非法/不存在 ep_id | code **-404** | 剧集不存在 |
| 直播 | live playUrl code 0，durl 的 length=0 | 直播没有点播音轨，不支持 |
| 地区限制 | 文档 **-688** 地理区域限制 | 该内容在当前地区不可观看 |
| 版权限制 | 文档 **-689** 版权限制 | 该内容有版权限制 |
| 风控 | **-352** 风控校验失败 | 请求被拦截，稍后重试 |
| 充电专属 | **87007**（本次未找到真实样本，见下） | 充电专属视频，需充电后观看 |
| 付费视频 | **87008**（同上） | 付费视频，需购买后观看 |
| 参数错 | **-400** | 链接不是有效的视频地址 |

**试看长度实测**（必须按 is_preview 判断，不要只信 duration）：

| 剧集 | 正片时长 | durl 试看 |
| --- | --- | --- |
| 名侦探柯南 ep323835（会员） | 1488 s | 360 s |
| 黑色四叶草 ep114966（会员） | 1434 s | 180 s |
| 蜡笔小新 ep109736（会员） | 424 s | 60 s |
| 电影《水饺皇后》ep1784701 | 7182 s | 360 s |
| 电影《私家侦探》ep1925721 | 6148 s | 360 s |

**关于 87007/87008**：本次用"充电专属""付费视频""仅充电可见"搜了 55 个结果，没有一个稿件 rights 里出现 is_charging_arc / ugc_pay / pay=1（标题写"充电专属"的 BV1qx3965Epc 实际 rights 全 0，playurl 照常返回 30280）。也就是说**没找到真实样本可实测**，这两个码来自社区整理的错误码表，按"可能发生"处理即可。文档里 `is_preview` 甚至写着"作用尚不明确"，我们的实测补上了这个语义。

---

## 5. 音质上限与 Cookie 取舍 (d)

### 5.1 无登录能拿到的上限

- **UGC**：30280 / 30232 / 30216 三条任选，实测最高 232 kbps（30280），AAC 48 kHz 立体声，时长与视频一致。
- **番剧（免费集）**：实测 30280 达 323 kbps / 320,005 bps，同样 48 kHz 立体声。
- **拿不到**：30250（杜比全景声）、30251（Hi-Res 无损 FLAC）。无大会员时 `dash.flac={"display":true,"audio":null}`，即使传 `fnval=272`（含 256 杜比位）也一样 audio=null，dolby={"type":0,"audio":null}。
- 未登录画质被压到 720P，但**音频条目与带宽不受影响**；qn=127/80/64/32/16/6 六种请求返回的 audio 三条 id 与 bandwidth **完全一致**，qn 只改 quality 与 video ids。取音轨时 qn 随便传（建议 127）。

### 5.2 为什么本项目不值得上 Cookie

本项目要把音频转 16 kHz 单声道做节奏/口型对齐，16 kHz 单声道 PCM 的码率是 256 kbps 但**信息量只有 0–8 kHz**，30280 的 192K 立体声降采样绰绰有余。为多拿 30251 无损去折腾 Cookie，收益为零、维护成本不低。

### 5.3 如果哪天真要 Cookie：最简做法与可行性

| 方案 | 可行性（本机实测） |
| --- | --- |
| 用户手动复制 SESSDATA | **推荐**。浏览器 F12 → Application → Cookies → bilibili.com → 复制 SESSDATA，填到配置里 |
| 自动读 Edge/Chrome 的 Cookies 库 | **不可行（标准库）**。本机 Edge 库 202 条 encrypted_value 全部以 0x7632（"v20"）开头，即 Chrome 127+ 的 App-Bound Encryption，解密要走浏览器的 elevation service / COM，纯 Python 标准库做不到；Chrome 的库还被浏览器占用（PermissionError 32） |
| 自动读 Firefox 的 cookies.sqlite | 理论可行（Firefox 的 cookie 库本身不加密），但本机 3 个 profile 里只有 m0qbpvla.default-esr 有 cookies.sqlite，8 条里 0 条 bilibili |
| 用 CDP 让浏览器自己发请求 | 可行但重（要起浏览器），不如手动粘 SESSDATA |

顺带：本机 Edge 的 bilibili cookie 有 17 条（SESSDATA、DedeUserID、bili_jct、buvid3 等），说明用户是登录态——但工具**不应该**去解密浏览器的 cookie 库，这既脆又有隐私风险。

---

## 6. 可复用配方 (e)

`.scratch/pic2video/probe/bili_audio.py`：仅标准库 + 现有 ffmpeg，输入任意 B 站地址，输出本地 m4a（默认）或 16 kHz 单声道 wav。

设计要点：

1. 解析顺序：b23 短链 → BV / av / ep / ss → 直播与课程直接拒绝。
2. WBI：nav 取 wbi_img → 两个文件名拼接 → 按固定 64 位 TAB 重排取前 32 位做 mixin；参数 sorted urlencode + wts + md5 得 w_rid。mixin 缓存 1 小时。
3. UGC：wbi/view 取 pages → pages[p-1].cid → wbi/playurl(fnval=16, qn=127, platform=pc, fnver=0, fourk=1) → dash.audio 按 (-bandwidth, id 优先级) 选一条 → 下 baseUrl，失败退 backupUrl。
4. 番剧：pgc/player/web/playurl?ep_id= → **先查 is_preview==1 直接报错** → 有 dash 走 dash，否则 durl 下整片再抽音轨。
5. 下载到 `.part`，ffmpeg `-vn -c:a copy` 转 m4a（不重编码），或 `-ar 16000 -ac 1 -c:a pcm_s16le` 转 wav。
6. 错误统一成 BiliError(code, message, detail)，str(e) 就是中文提示；带 3 次重试与 0.8s 递增退避。

```python
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
```

### 用法

```python
from bili_audio import bili_audio, BiliError

bili_audio("https://www.bilibili.com/video/BV1jQst6BEf2", "out.m4a")
bili_audio("https://b23.tv/BV1jQst6BEf2", "out.m4a")
bili_audio("https://www.bilibili.com/video/av117562180376697", "out.m4a")
bili_audio("https://www.bilibili.com/video/BV1NDsS6UE8D?p=2", "p2.m4a")
bili_audio("https://www.bilibili.com/bangumi/play/ep323085", "ep.m4a")
bili_audio("https://www.bilibili.com/bangumi/play/ep323085", "ep.wav", wav=True)

try:
    bili_audio(url)
except BiliError as e:
    print(e)            # 直接就是中文提示
    print(e.code)       # 原始 code，可用于分支
```

### 11 个用例实测结果（testout/results.json）

| 输入 | 结果 |
| --- | --- |
| `/video/BV1jQst6BEf2` | ok，613,864 B / 21.16 s / 230 kbps |
| `b23.tv/BV1jQst6BEf2` | ok，同上（短链解析正确） |
| `/video/av117562180376697` | ok，21,912,246 B / 1980.97 s / 86.99 kbps |
| `/video/BV1NDsS6UE8D?p=2` | ok，14,239,321 B / 669.48 s / 168.64 kbps（正的 P2） |
| `/bangumi/play/ep323085`（免费集） | ok，60,188,869 B / 1493.01 s / 320.0 kbps |
| 同上 + wav | ok，pcm_s16le 16 kHz 单声道 |
| `/bangumi/play/ep323835`（会员集） | 报 preview：试看 360 秒，需大会员 Cookie |
| `/video/BV1ab411c7mD`（不可见） | 报 62002 稿件不可见 |
| `/video/BV1xx411c7mZ`（不存在） | 报 -404 视频不存在 |
| `live.bilibili.com/6` | 报 live：直播没有点播音轨 |
| `?p=999` | 报 -400：分 P 越界（共 1 P） |

产物实测（ffprobe）：全部 aac / 48000 Hz / stereo，时长与 dash.duration、view duration 一致（1980.97 vs 1981、669.48 vs 670、1493.01 vs 1494）。

---

## 7. 出处

- bilibili-API-collect · 视频流（fnval/qn/audio id）：https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/video/videostream_url.md
  （GitHub raw 当时 404，实际读取镜像：https://codeberg.org/mengzhen/bilibili-API-collect/raw/branch/master/docs/video/videostream_url.md）
- 错误码表：https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/errcode.md （镜像 https://codeberg.org/mengzhen/bilibili-API-collect/raw/branch/master/docs/misc/errcode.md）
- b23.tv 短链：https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/b23tv.md
- BV 号算法：https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/bvid_desc.md
- 番剧/影视流：https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/bangumi/videostream_url.md
- 课程流：https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/cheese/videostream_url.md
- 接口实测：`api.bilibili.com/x/web-interface/nav`、`/x/web-interface/wbi/view`、`/x/player/wbi/playurl`、`/pgc/player/web/playurl`、`/pgc/view/web/season`、`/x/player/pagelist`
- Chromium App-Bound Encryption：Chrome 127 起 encrypted_value 以 v20 前缀标记，需经 elevation service 解密（本机 202 条 Edge cookie 全部为 v20，实测无法用标准库解开）
- Mozilla Bugzilla 1992707：Firefox cookie 库明文存储的讨论（决定"Firefox 可读、Chromium 不可读"的判断）

---

## 8. 已知限制 / 后续可做

1. 30250/30251 未取到真实样本（需大会员），排序逻辑已按 bandwidth 兼容。
2. 87007/87008 未实测到真实稿件。
3. 未实现登录（SESSDATA）可选项；如需要，加一个全局 headers 即可。
4. 未做并发分块下载；长番剧单线程约 1–2 MB/s，可接受。