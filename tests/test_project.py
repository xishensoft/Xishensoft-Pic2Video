# -*- coding: utf-8 -*-
"""时间线/帧数/设置/filtergraph 的单元检查（不碰 GUI；只碰 ffmpeg 的地方用错误路径）。"""
import json, os, sys, random, subprocess, tempfile
try:                      # 控制台是 GBK，日/韩文本会炸，统一用 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:         # noqa: BLE001
    pass
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, ROOT)
import pic2video as p
import theme as TH
import audio_source as A

IMG = [os.path.join(FIXTURES, "land.png"),
       os.path.join(FIXTURES, "port.png"),
       os.path.join(FIXTURES, "sq.png")]
fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(extra) if extra else ""))
    if not cond:
        fails.append(name)

def mk(n=3):
    pr = p.Project()
    pr.add_images(IMG[:1] * n)
    return pr

# 1 跟 BGM 均分
pr = mk(3); pr.audio_dur = 7.0; pr.normalize()
check("bgm 均分 7/3", abs(sum(c.dur for c in pr.clips) - 7.0) < 1e-6 and
      all(abs(c.dur - 7/3) < 1e-6 for c in pr.clips), [round(c.dur, 3) for c in pr.clips])

# 2 手改一张，其余让位
pr.set_duration(0, 4.0)
check("改一张总长不变", abs(sum(c.dur for c in pr.clips) - 7.0) < 1e-6)
check("改一张其余等比", abs(pr.clips[1].dur - 1.5) < 1e-6, [round(c.dur, 3) for c in pr.clips])

# 3 极小值被夹住
pr.set_duration(1, 0.01)
check("最小 0.2 夹住", pr.clips[1].dur >= p.MIN_DUR - 1e-9 and abs(sum(c.dur for c in pr.clips) - 7) < 1e-6)

# 4 极大值被夹住
pr.set_duration(0, 100)
check("最大值夹住", abs(sum(c.dur for c in pr.clips) - 7) < 1e-6 and
      all(c.dur >= p.MIN_DUR - 1e-9 for c in pr.clips))

# 5 自设总长
pr2 = mk(3); p.set_mode(pr2, "total"); pr2.total_setting = 10.0; pr2.normalize()
check("自设总长 10", abs(sum(c.dur for c in pr2.clips) - 10) < 1e-6, pr2.mode)

# 6 逐张秒数
pr3 = mk(3); p.set_mode(pr3, "free")
for i in range(3): pr3.set_duration(i, 2.0)
check("逐张自由 6s", abs(pr3.total() - 6) < 1e-9, pr3.total())
check("逐张帧数 180", sum(pr3.frame_counts()) == 180, pr3.frame_counts())

# 7 增删保持总长
pr4 = mk(2); pr4.audio_dur = 9.0; pr4.normalize()
pr4.add_images([IMG[0]])
check("加图后总长不变", abs(sum(c.dur for c in pr4.clips) - 9) < 1e-6)
pr4.remove([0])
check("删图后总长不变", abs(sum(c.dur for c in pr4.clips) - 9) < 1e-6)

# 8 帧数总和对齐 round(总时长*fps)，且每段 >= 1
random.seed(4)
bad = 0
for fps in (30, 60):
    for _ in range(200):
        n = random.randint(1, 40)
        pr5 = p.Project(fps=fps); pr5.add_images(IMG[:1] * n); pr5.mode = "free"
        for c in pr5.clips: c.dur = random.uniform(0.2, 8.0)
        cnt = pr5.frame_counts()
        if sum(cnt) != round(sum(c.dur for c in pr5.clips) * fps) or min(cnt) < 1:
            bad += 1
check("随机 400 组帧数对齐(30/60fps)", bad == 0, bad)

# 9 排序
pr6 = mk(3); pr6.clips[0].dur = 1; pr6.clips[1].dur = 2; pr6.clips[2].dur = 3
pr6.reorder([(c.path, c.dur) for c in reversed(pr6.clips)])
check("reorder 反转", [round(c.dur) for c in pr6.clips] == [3, 2, 1], [c.dur for c in pr6.clips])

# 10 排序后归一化仍守恒
pr6.audio_dur = 12.0; pr6.normalize()
check("reorder 后归一化", abs(sum(c.dur for c in pr6.clips) - 12) < 1e-6)

# 11 比例档位与预设尺寸
want = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080),
        "4:5": (1080, 1350), "4:3": (1440, 1080), "21:9": (2520, 1080),
        "3:4": (1080, 1440), "2:3": (1080, 1620)}
bad = [r for r, sz in want.items() if p.preset_size(r) != sz]
check("八档比例预设尺寸", not bad, [(r, p.preset_size(r)) for r in bad])
check("自定义比例无预设", p.preset_size("自定义") is None)
check("RATIOS 含自定义与三档新比例",
      "自定义" in p.RATIOS and all(r in p.RATIOS for r in ("21:9", "3:4", "2:3")), list(p.RATIOS))

# 12 设置：默认 / 持久化 / 坏文件兜底 / 非法值
tmp = tempfile.mkdtemp(prefix="p2v_set_")
sp = os.path.join(tmp, "settings.json")
s = p.Settings(sp)
check("设置默认 1920x1080", s.size() == (1920, 1080), s.size())
check("默认预览 960x540", s.preview_size() == (960, 540), s.preview_size())
check("默认铺底 own + 背景模糊", s["fit"] == "own" and s["bg_blur"] is True, (s["fit"], s["bg_blur"]))
s.update(ratio="9:16", width=1080, height=1920); s.save()
s2 = p.Settings(sp)
check("设置持久化竖屏", s2["ratio"] == "9:16" and s2.size() == (1080, 1920), s2.size())
check("竖屏预览 540x960", s2.preview_size() == (540, 960), s2.preview_size())
s2.update(ratio="自定义", width=2000, height=800)
check("自定义宽高手填", s2.size() == (2000, 800) and s2.preview_size() == (960, 384), s2.preview_size())
with open(sp, "w", encoding="utf-8") as fh:
    fh.write("{ not json")
s3 = p.Settings(sp)
check("坏设置回落默认", s3["ratio"] == "16:9" and s3.size() == (1920, 1080), s3.size())
s3.update(fit="blur", fps=999, width=17, height=10000, transition="x", fade_in=-3,
          pad_color="red", bg_blur="yes", ratio="11:11")
check("非法值兜底", s3["fit"] == "own" and s3["fps"] == 60 and s3["width"] == 16 and
      s3["height"] == 4096 and s3["transition"] == "none" and s3["fade_in"] == 0.0 and
      s3["pad_color"] == "#000000" and s3["bg_blur"] is True and s3["ratio"] == "16:9",
      s3.data)
s3.update(fit="black")
check("老配置 black→color", s3["fit"] == "color", s3["fit"])
s3.update(fit="cover", pad_color="#00ff00", bg_path="x.png")
s3.save()
s4 = p.Settings(sp); s4.update(quality="高")
check("画质映射 crf18", s4.crf() == 18)
check("颜色与背景路径持久化", s4["pad_color"] == "#00ff00" and s4["fit"] == "cover"
      and s4["bg_path"] == "x.png", (s4["pad_color"], s4["fit"], s4["bg_path"]))

# 13 filtergraph：多种铺底 / 背景输入 / 帧率 / 转场
g = p.build_graph([90, 90], 6.0, 1280, 720, fps=60, fit="cover")
check("cover 不 split 只裁切", g.count("split=2") == 0 and "crop=1280:720" in g and "fps=60" in g)
g = p.build_graph([90, 90], 6.0, 720, 1280, fit="color", pad_color="#00ff00")
check("color 用 pad 与自定颜色", "pad=720:1280" in g and "color=0x00ff00" in g and "gblur" not in g)
g = p.build_graph([90, 90], 6.0, fit="own")
check("own 默认 split+overlay+gblur", "split=2" in g and "overlay=" in g and "gblur" in g)
g = p.build_graph([90, 90], 6.0, fit="own", bg_blur=False)
check("own 关模糊无 gblur", "split=2" in g and "gblur" not in g)
g = p.build_graph([90, 90], 6.0, fit="image", bg_index=2)
check("image 背景链", "bgx0" in g and "[2:v]scale" in g and "trim=start=0.0000" in g
      and "shortest=1" in g and "gblur" in g)
g = p.build_graph([90, 90], 6.0, fit="image", bg_index=2, bg_blur=False)
check("image 关模糊无 gblur", "bgx0" in g and "gblur" not in g)
g = p.build_graph([90, 90], 6.0, fit="video", bg_index=2, audio_index=3)
check("video 背景 + 音频下标", "[3:a]atrim=start=0.000:end=6.000" in g
      and "bgx1" in g, g[-120:])
g = p.build_graph([90, 90], 6.0, transition="fade", transition_sec=0.3)
check("fade 转场进图", g.count("fade=t=in:st=0:d=0.300") == 2 and "fade=t=out" in g)
check("硬切不带转场", "fade=t=in:st=0:d=0.300" not in p.build_graph([90], 3.0))

# 14 背景缺失/格式不符要报错而不是跑一半
clip = p.Clip(IMG[0], 3.0)
try:
    p.render([clip], [90], None, os.path.join(tmp, "x.mp4"), fit="image", bg_path="")
    check("背景缺失报错", False)
except p.RenderError as e:
    check("背景缺失报错", "背景文件不存在" in str(e), str(e))
notimg = os.path.join(tmp, "a.m4a")
open(notimg, "wb").close()
try:
    p.render([clip], [90], None, os.path.join(tmp, "x.mp4"), fit="image", bg_path=notimg)
    check("背景图格式报错", False)
except p.RenderError as e:
    check("背景图格式报错", "格式不支持" in str(e), str(e))

# 15 多语言：词条完整、回落、系统语言映射、内部值不被翻译
import ast
import i18n as I

src = open(os.path.join(ROOT, "pic2video.py"), "r", encoding="utf-8").read()
keys = set()
for node in ast.walk(ast.parse(src)):
    if not isinstance(node, ast.Call):
        continue
    fn = node.func
    name = getattr(fn, "id", None) or getattr(fn, "attr", None)
    if name not in ("tr", "add_row"):
        continue
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        keys.add(node.args[0].value)

base = set(I.TR["en"])
check("语言表键集合一致", all(set(v) == base for v in I.TR.values()),
      {k: len(v) for k, v in I.TR.items()})
missing = sorted(k for k in keys if k not in base)
check("tr()/add_row() 的中文都有 4 种译文", not missing, missing[:8])
check("词条数量", len(base) >= 140, len(base))

I.set_locale("en")
check("英文查得到", I.tr("加图片") == "Add images", I.tr("加图片"))
check("英文带占位符", I.tr("已加 {n} 张图", n=3) == "Added 3 image(s)")
I.set_locale("zh-CN")
check("简体直接回原文", I.tr("加图片") == "加图片")
I.set_locale("ja")
check("日文查得到", I.tr("加图片") == "画像を追加", I.tr("加图片"))
check("缺词条回落中文", I.tr("这是个不存在的词条") == "这是个不存在的词条")
I.set_locale("fr")
check("非法语言回落简体", I.current_locale() == "zh-CN" and I.tr("加图片") == "加图片")

check("LANGID 0x0804 简体", I.locale_from_langid(0x0804) == "zh-CN")
check("LANGID 0x1004 新加坡简体", I.locale_from_langid(0x1004) == "zh-CN")
check("LANGID 0x0404 繁體", I.locale_from_langid(0x0404) == "zh-TW")
check("LANGID 0x0C04 繁體", I.locale_from_langid(0x0C04) == "zh-TW")
check("LANGID 0x0411 日本語", I.locale_from_langid(0x0411) == "ja")
check("LANGID 0x0412 한국어", I.locale_from_langid(0x0412) == "ko")
check("LANGID 0x0409 English", I.locale_from_langid(0x0409) == "en")
check("LANGID 其它 → English", I.locale_from_langid(0x0407) == "en")

I.set_locale("ko")
check("内部值不受翻译影响", "标准" in p.QUALITY and "自定义" in p.RATIOS
      and p.QUALITY["标准"] == 20)
check("名字表仍是内部值", p.FIT_LABELS["own"] == "原图铺底" and p.MODE_LABELS["bgm"] == "跟 BGM")
check("显示用 label 被翻译", p.fit_label("own") == "원본 이미지 배경" and p.mode_label("bgm") == "BGM 길이")
I.set_locale("zh-CN")

s5 = p.Settings(os.path.join(tmp, "s5.json"))
s5.update(lang="fr", ui="crazy", theme="purple", accent="blue")
check("非法语言/模式/主题/主色回默认",
      s5["lang"] == "auto" and s5["ui"] == "simple"
      and s5["theme"] == "auto" and s5["accent"] == TH.DEFAULT_ACCENT,
      (s5["lang"], s5["ui"], s5["theme"], s5["accent"]))
s7 = p.Settings(os.path.join(tmp, "s7.json"))
check("首次打开默认简洁模式",
      s7["ui"] == "simple" and s7["theme"] == "auto", (s7["ui"], s7["theme"]))
s5.update(lang="ja", ui="simple")
s5.save()
s6 = p.Settings(os.path.join(tmp, "s5.json"))
check("语言与模式会记住", s6["lang"] == "ja" and s6["ui"] == "simple", (s6["lang"], s6["ui"]))

# 16 主题：深浅两套、对撞色、QSS 里真的带上色
check("主题三档", TH.THEMES == ("auto", "light", "dark"), TH.THEMES)
light = TH.palette("light", TH.DEFAULT_ACCENT)
dark = TH.palette("dark", TH.DEFAULT_ACCENT)
check("调色板 22 个键", len(light) == 22 and set(light) == set(dark), sorted(light))
check("深浅底色不同", light["window"] != dark["window"], (light["window"], dark["window"]))
check("深色底更暗", TH.luminance(dark["window"]) < TH.luminance(light["window"]),
      (TH.luminance(dark["window"]), TH.luminance(light["window"])))
check("浅底深字/深底浅字", light["text"] == "#161a21" or TH.luminance(light["text"]) < 0.3,
      light["text"])
check("深底浅字", TH.luminance(dark["text"]) > 0.7, dark["text"])
check("对比度够用", TH.contrast(light["text"], light["window"]) > 4.5
      and TH.contrast(dark["text"], dark["window"]) > 4.5)


def hue_of(h):
    r, g, b = [int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        return 0.0
    if mx == r:
        hh = ((g - b) / d) % 6
    elif mx == g:
        hh = (b - r) / d + 2
    else:
        hh = (r - g) / d + 4
    return hh * 60.0


for c in ("#3b82f6", "#e11d48", "#22c55e", "#f59e0b"):
    diff = abs(hue_of(TH.complementary(c)) - hue_of(c)) % 360
    diff = min(diff, 360 - diff)
    check(f"对撞色是色环 180° {c}", 175 <= diff <= 185, round(diff, 1))

pal = TH.palette("dark", "#e11d48")
qss = TH.build_qss(pal)
check("QSS 里有主色", pal["accent"].lower() in qss.lower(), pal["accent"])
check("QSS 里有对撞色", pal["accent2"].lower() in qss.lower(), pal["accent2"])
check("时间线颜色齐", set(TH.timeline_colors(pal)) >=
      {"bg", "empty", "piece_a", "piece_b", "piece_text", "playhead", "selected_edge"})
check("auto 解成浅色或深色", TH.resolve("auto") in ("light", "dark"), TH.resolve("auto"))
pale = TH.readable_accent("#fef9c3", False)
check("浅主色被压暗", TH.luminance(pale) < TH.luminance("#fef9c3"), pale)
deep = TH.readable_accent("#0a2a66", True)
check("深主色被提亮", TH.luminance(deep) > TH.luminance("#0a2a66"), deep)
for th_name, key in (("light", light), ("dark", dark)):
    check(f"{th_name} 按钮文字压在主色上看得清",
          TH.contrast(key["accent_fg"], key["accent"]) >= 4.5,
          round(TH.contrast(key["accent_fg"], key["accent"]), 2))
    check(f"{th_name} 正文压在窗口底上看得清",
          TH.contrast(key["text"], key["window"]) >= 7.0,
          round(TH.contrast(key["text"], key["window"]), 2))

# 17 音源判定
check("B 站链接", A.kind_of("https://www.bilibili.com/video/BV13qs46FE9C") == "bili")
check("B 站短链", A.kind_of("https://b23.tv/abcd123") == "bili")
check("裸 BV 号", A.kind_of("BV13qs46FE9C") == "bili")
check("av 号", A.kind_of("https://www.bilibili.com/video/av170001") == "bili")
check("抖音", A.kind_of("https://www.douyin.com/video/7300000000000000000") == "web")
check("抖音短链", A.kind_of("https://v.douyin.com/iAbCdEf/") == "web")
check("YouTube", A.kind_of("https://youtu.be/dQw4w9WgXcQ") == "web")
TONE = os.path.join(FIXTURES, "tone6.m4a")
check("本地音频", A.kind_of(TONE) == "local", TONE)
fake_mp4 = os.path.join(tmp, "clip.mp4")
open(fake_mp4, "wb").close()
check("本地视频", A.kind_of(fake_mp4) == "local")
check("普通网页链接走 yt-dlp", A.kind_of("https://example.com/v/123") == "web")
check("ytdlp_ready 返回 bool", isinstance(A.ytdlp_ready(), bool), A.ytdlp_ready())

if os.path.exists(TONE):
    got, kind = A.fetch_audio(TONE, os.path.join(tmp, "bgm_local.m4a"))
    check("本地音频直接可用", os.path.exists(got) and kind == "local", (got, kind))
try:
    A.fetch_audio("", os.path.join(tmp, "x.m4a"))
    check("空来源报错", False)
except A.SourceError as e:
    check("空来源报错", "先给个链接" in e.message, e.message)

# 18 依赖自检 / 坏图预检
check("missing_tools 返回 list", isinstance(p.missing_tools(), list), p.missing_tools())
check("好图能读", p.image_ok(IMG[0]) is True)
check("别的图也能读", all(p.image_ok(x) for x in IMG))
check("不存在的图读不出", p.image_ok(os.path.join(FIXTURES, "nope.png")) is False)
check("音频不算图片", p.image_ok(TONE) is False)
try:
    p.render([p.Clip(os.path.join(FIXTURES, "nope.png"), 1.0)], [30],
             None, os.path.join(tmp, "never.mp4"))
    check("坏图在渲染前就报错", False)
except p.RenderError as e:
    check("坏图在渲染前就报错", "nope.png" in str(e), str(e))
except Exception as e:   # noqa: BLE001
    check("坏图在渲染前就报错", False, f"{type(e).__name__}: {e}")

# 18b 拖文件夹 / 拖文件
fold = os.path.join(tmp, "photos")
os.makedirs(os.path.join(fold, "sub"), exist_ok=True)
for nm in ("b.png", "a.png", "c.png"):
    open(os.path.join(fold, nm), "wb").close()
got = p.expand_paths([fold, IMG[0]])
check("文件夹展开成里面的文件", len(got) == 4, got)
check("文件夹里按名字排序", [os.path.basename(x) for x in got[:3]] == ["a.png", "b.png", "c.png"], got[:3])
check("单个文件原样留着", got[3] == IMG[0], got[3])
check("不存在的路径也留着交给后面报错", p.expand_paths([os.path.join(tmp, "nope.bin")]) == [os.path.join(tmp, "nope.bin")])
check("空输入得到空列表", p.expand_paths([]) == [])

# 19 进度/导出的新文案在四种语言里都能格式化
import i18n as I
for lang in ("zh-TW", "en", "ja", "ko"):
    I.set_locale(lang)
    msg = I.tr("正在导出… {pct}% · 剩余约 {left}", pct=40, left="0:12")
    check(f"{lang} 导出进度文案", "40" in msg and "0:12" in msg, msg)
    check(f"{lang} 取消文案", bool(I.tr("已取消")) and "已取消" in I.TR[lang], I.tr("已取消"))
    check(f"{lang} 正在取消文案", bool(I.tr("正在取消…")), I.tr("正在取消…"))
    check(f"{lang} 关窗确认文案", len(I.tr("视频还在导出，现在关掉会丢掉这次的结果。")) > 4)
I.set_locale("zh-CN")

# 20 画面动效（Ken Burns）
check("动效默认静止", p.Settings()["motion"] == "none", p.Settings()["motion"])
_s = p.Settings()
_s.data["motion"] = "crazy"
_s._sanitize()
check("非法动效回落静止", _s["motion"] == "none", _s["motion"])
check("四种动效都在", p.MOTIONS == ("none", "in", "out", "inout"), p.MOTIONS)
check("四种动效都有文案", all(k in p.MOTION_LABELS for k in p.MOTIONS))

g_none = p.build_graph([30], 1.0, 320, 180)
check("静止时没有 zoompan", "zoompan" not in g_none)
g_in = p.build_graph([30], 1.0, 320, 180, motion="in")
check("推近有 zoompan", "zoompan" in g_in, g_in[-160:])
check("推近放大 6%", "1+0.0600*on/29" in g_in, g_in[-200:])
g_out = p.build_graph([30], 1.0, 320, 180, motion="out")
check("拉远从放大回到原样", "1+0.0600*(1-on/29)" in g_out, g_out[-200:])
g_alt = p.build_graph([30, 30], 2.0, 320, 180, motion="inout")
check("交替：一张推近", "1+0.0600*on/29" in g_alt)
check("交替：一张拉远", "1+0.0600*(1-on/29)" in g_alt)
check("单帧不做动效", "zoompan" not in p.build_graph([1], 0.03, 320, 180, motion="in"))
check("动效不改变总帧数", p.build_graph([45, 15], 2.0, 320, 180, motion="in").count("zoompan") == 2)

if p.missing_tools():
    print("  (没 ffmpeg，跳过动效出片验证)")
else:
    out_zoom = os.path.join(tmp, "zoom.mp4")
    p.render([p.Clip(IMG[0], 1.0)], [30], None, out_zoom, w=320, h=180,
             fps=30, motion="in")
    probe = subprocess.run(
        [p.FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", out_zoom],
        capture_output=True, text=True)
    n = probe.stdout.strip().rstrip(",")
    check("动效出片帧数不变", n == "30", (n, probe.stderr[-200:]))

# 21 背景音乐音量
check("音量默认 100%", p.Settings()["music_volume"] == 1.0, p.Settings()["music_volume"])
_sv = p.Settings()
_sv.data["music_volume"] = 9.0
_sv._sanitize()
check("音量上限 2 倍", _sv["music_volume"] == 2.0, _sv["music_volume"])
_sv.data["music_volume"] = -3
_sv._sanitize()
check("音量下限 0", _sv["music_volume"] == 0.0, _sv["music_volume"])
g_v = p.build_graph([30], 1.0, 320, 180, volume=0.5)
check("半音量写进 graph", ",volume=0.500" in g_v, g_v[-120:])
check("原音量不加滤镜", "volume=" not in p.build_graph([30], 1.0, 320, 180))

if p.missing_tools():
    print("  (没 ffmpeg，跳过音量出片验证)")
else:
    mean = {}
    for vol in (1.0, 0.5):
        dst = os.path.join(tmp, f"vol_{vol}.mp4")
        p.render([p.Clip(IMG[0], 1.0)], [30], TONE, dst, w=320, h=180,
                 fps=30, volume=vol)
        det = subprocess.run(
            [p.FFMPEG, "-hide_banner", "-i", dst, "-af", "volumedetect",
             "-f", "null", "-"], capture_output=True, text=True)
        line = [ln for ln in det.stderr.splitlines() if "mean_volume" in ln]
        mean[vol] = float(line[0].split("mean_volume:")[1].split("dB")[0])
    check("半音量比原音量低约 6dB", 4.5 <= (mean[1.0] - mean[0.5]) <= 7.5,
          mean)

# 22 裁切位置
check("裁切默认居中", p.Settings()["crop_focus"] == "center")
_sc = p.Settings()
_sc.data["crop_focus"] = "weird"
_sc._sanitize()
check("非法裁切位置回落居中", _sc["crop_focus"] == "center", _sc["crop_focus"])
check("居中不写偏移", "crop=320:180" in p.build_graph([30], 1.0, 320, 180, fit="cover")
      and "(ih-180)" not in p.build_graph([30], 1.0, 320, 180, fit="cover"))
g_top = p.build_graph([30], 1.0, 320, 180, fit="cover", crop_focus="top")
check("偏上从顶部裁", "(ih-180)*0.000" in g_top, g_top[:160])
g_bot = p.build_graph([30], 1.0, 320, 180, fit="cover", crop_focus="bottom")
check("偏下从底部裁", "(ih-180)*1.000" in g_bot, g_bot[:160])

if p.missing_tools():
    print("  (没 ffmpeg，跳过裁切位置出片验证)")
else:
    # 一张上红下绿的竖图：偏上裁只剩红，偏下裁只剩绿
    tall = os.path.join(tmp, "tall.png")
    subprocess.run(
        [p.FFMPEG, "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=400x400:d=1",
         "-f", "lavfi", "-i", "color=c=green:s=400x400:d=1",
         "-filter_complex", "vstack=inputs=2", "-frames:v", "1", tall],
        capture_output=True)

    def center_rgb(mp4, name):
        frame = os.path.join(tmp, name + ".png")
        subprocess.run([p.FFMPEG, "-v", "error", "-y", "-i", mp4,
                        "-frames:v", "1", frame], capture_output=True)
        raw = subprocess.run(
            [p.FFMPEG, "-v", "error", "-i", frame,
             "-vf", "crop=1:1:160:90", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return tuple(raw[:3])

    for focus, which in (("top", "红"), ("bottom", "绿")):
        dst = os.path.join(tmp, f"focus_{focus}.mp4")
        p.render([p.Clip(tall, 1.0)], [30], None, dst, w=320, h=180,
                 fps=30, fit="cover", crop_focus=focus)
        r, g, b = center_rgb(dst, f"focus_{focus}")
        ok = (r > g) if which == "红" else (g > r)
        check(f"裁切{focus}留住{which}", ok, (r, g, b))


# 23 背景音乐截取
check("音乐起点默认 0", p.Settings.DEFAULTS["music_start"] == 0.0)
check("音乐长度默认整首", p.Settings.DEFAULTS["music_len"] == 0.0)
_pr = mk(3); _pr.audio_dur = 60.0
check("音乐长度=0 用整首", abs(_pr.music_length() - 60.0) < 1e-6)
_pr.audio_start = 10.0
check("起点吃掉开头", abs(_pr.music_length() - 50.0) < 1e-6, _pr.music_length())
_pr.audio_len = 20.0
check("长度截断", abs(_pr.music_length() - 20.0) < 1e-6, _pr.music_length())
_pr.audio_start = 45.0; _pr.audio_len = 20.0
check("超出剩余长度时夹住", abs(_pr.music_length() - 15.0) < 1e-6,
      _pr.music_length())
_pr.audio_start = 999.0; _pr.audio_len = 0.0
check("起点超过总长也有下限", _pr.music_length() >= 0.1, _pr.music_length())
_pr.audio_start = 10.0; _pr.audio_len = 20.0
_pr.mode = "bgm"; _pr.normalize()
check("跟 BGM 模式总长=截取长度", abs(_pr.total() - 20.0) < 1e-6, _pr.total())
_g = p.build_graph([30], 1.0, 320, 180, music_start=12.5)
check("音频链从 music_start 开始", "atrim=start=12.500:end=13.500" in _g,
      [ln for ln in _g.split(";") if "atrim" in ln][:1])
_g0 = p.build_graph([30], 1.0, 320, 180)
check("默认从 0 开始", "atrim=start=0.000:end=1.000" in _g0)
_sc = p.Settings()
_sc.data["music_start"] = -5
_sc.data["music_len"] = 1e9
_sc._sanitize()
check("非法音乐起点被夹住", _sc["music_start"] == 0.0, _sc["music_start"])
check("音乐长度夹在上限内", _sc["music_len"] == 36000.0, _sc["music_len"])

# 24 导出时是否弹保存框
check("默认导出时先问", p.Settings.DEFAULTS["ask_export_dir"] is True)
_sc = p.Settings()
_sc.data["ask_export_dir"] = "yes"
_sc._sanitize()
check("非法值回落先问", _sc["ask_export_dir"] is True)
_sc.data["ask_export_dir"] = False
_sc._sanitize()
check("关掉后保持关掉", _sc["ask_export_dir"] is False)

# 25 设置窗口按组分隔（不建窗口，只查源码里的分组标题与分隔线实现）
_src = open(os.path.join(ROOT, "pic2video.py"), encoding="utf-8").read()
_seps = [s for s in ("外观", "导出", "画面", "音乐", "节奏与动效", "其它")
         if f'add_sep("{s}"' in _src]
check("设置分六组", len(_seps) == 6, _seps)
check("分隔线控件", 'setObjectName("sep")' in _src)
check("简洁模式隐藏空组", 'add_sep("音乐", pro=True)' in _src
      and 'add_sep("其它", pro=True)' in _src)
check("专业模式分页显示", "QTabWidget" in _src and "tabs.addTab" in _src)
check("简洁模式不分页", "if self.pro:" in _src)
check("每组一个表单", "self._groups[text] = form" in _src)

# 26 预设配色（各视频站品牌色）
check("预设配色够多", len(TH.PRESETS) >= 8, len(TH.PRESETS))
check("预设全是大写无关的小写十六进制",
      all(raw == raw.lower() and p.is_hex_color(raw) for _, raw in TH.PRESETS))
check("预设名不重复", len({n for n, _ in TH.PRESETS}) == len(TH.PRESETS))
for _name, _hex in TH.PRESETS:
    _comp = TH.complementary(_hex)
    check(f"预设对撞色不同于原色 {_name}", _comp.lower() != _hex.lower(), _comp)
    for _th in ("light", "dark"):
        _pal = TH.palette(_th, _hex)
        check(f"预设按钮文字看得清 {_name} {_th}",
              TH.contrast(_pal["accent_fg"], _pal["accent"]) >= 4.5,
              round(TH.contrast(_pal["accent_fg"], _pal["accent"]), 2))
_sc = p.Settings()
_sc.data["accent"] = TH.PRESETS[1][1]
_sc._sanitize()
check("预设主色能存下来", _sc["accent"] == TH.PRESETS[1][1], _sc["accent"])
_sc.data["accent"] = "fk-7299"
_sc._sanitize()
check("坏主色回落默认", _sc["accent"] == TH.DEFAULT_ACCENT, _sc["accent"])
check("预设下拉在设置里", "cmb_preset" in _src and "theme.PRESETS" in _src)

# 27 exe（窗口程序、没有控制台）里起子进程的规矩：
#     继承了无效句柄会让 ffmpeg 崩掉整个进程（实测 0xC0000409 闪退）
check("ffmpeg 带 -nostdin", '"-nostdin"' in _src)
check("子进程不继承 stdin", _src.count("stdin=subprocess.DEVNULL") >= 3,
      _src.count("stdin=subprocess.DEVNULL"))
check("子进程免开黑窗口", "CREATION_NO_WINDOW = getattr(" in _src
      and _src.count("creationflags=CREATION_NO_WINDOW") >= 3)
check("exe 里 print 有落处", "if sys.stdout is None:" in _src
      and "sys.stderr = fh" in _src)
check("exe 里崩溃写日志", "faulthandler.enable(file=fh" in _src)

# 28 成片要写明颜色范围/色度，否则播放器把 limited 当 full 解释 -> 发灰发暗
_g = p.build_graph([60, 60], 4.0, 640, 360, fps=30, fit="cover", fade_in=0.0,
                    fade_out=0.0, transition="none", transition_sec=0.3)
check("filtergraph 结尾写颜色标记",
      "setparams=range=tv:colorspace=bt709" in _g, _g[-90:])
check("颜色标记在 concat 之后",
      _g.index("concat=n=2") < _g.index("setparams"), "order")
check("出片命令仍带 -color_range tv", '"-color_range", "tv"' in _src)
_range_out = os.path.join(tempfile.gettempdir(), "p2v_test_range.mp4")
_aud = os.path.join(FIXTURES, "tone6.m4a")
if os.path.isfile(_aud):
    _pr = p.Project(); _pr.mode = "free"; _pr.add_images([IMG[0]], 1.0)
    p.render(_pr.clips, _pr.frame_counts(), _aud, _range_out, w=320, h=180,
             crf=35, preset="ultrafast", fps=30, fit="cover", fade_in=0.0,
             fade_out=0.0, transition="none", transition_sec=0.3)
    _probe = subprocess.run(
        [p.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=color_range,color_space,color_primaries,color_transfer",
         "-of", "default=nw=1", _range_out],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    _txt = _probe.stdout or ""
    check("成片 ffprobe 报 tv 范围", "color_range=tv" in _txt, _txt.replace("\n", " "))
    check("成片 ffprobe 报 bt709", "color_space=bt709" in _txt
          and "color_primaries=bt709" in _txt, _txt.replace("\n", " "))
    try:
        os.remove(_range_out)
    except OSError:
        pass
else:
    check("成片 ffprobe 报 tv 范围", False, "缺 fixtures/tone6.m4a")

# 29 自绘边框：所有窗口都不走系统边框，对话框/文件框统一自绘
check("主窗口自绘边框", "FramelessWindowHint" in _src
      and "setObjectName(\"frame\")" in _src)
check("nativeEvent 不调 super", "super().nativeEvent" not in _src)
check("nativeEvent 走 hit_test",
      "hit = hit_test(self, bar, event_type, message, resize=False)" in _src)
check("标题栏自己画", "class TitleBar" in _src
      and 'setObjectName("winbtn")' in _src and "def sync_max" in _src)
check("弹窗自绘", "class FramelessDialog" in _src and "class MsgBox" in _src
      and "class NumberDialog" in _src and "class ProgressDialog" in _src)
check("文件框换回系统原生", "QFileDialog.getOpenFileNames" in _src
      and "QFileDialog.getSaveFileName" in _src
      and "DontUseNativeDialog" not in _src)
check("不再用系统弹窗", "QMessageBox(" not in _src
      and "QProgressDialog" not in _src and "QInputDialog" not in _src)
check("素材列表能接外部拖入", "drop_files = pyqtSignal(list, int, str)" in _src
      and "def _hit(self, pos)" in _src and "1.0 / 3" in _src
      and "2.0 / 3" in _src)
check("Qt 自带文案本地化", "QTranslator" in _src
      and 'qt_tr.load("qtbase_"' in _src and "apply_qt_translator" in _src)
check("样式贴到全局", "app_.setStyleSheet" in _src
      and "app_.setPalette(app_palette" in _src)

# 30 窗口大小 / 片头片尾 / 交叉淡化 / 多条音频 / 时间线右键
check("窗口模式三档", p.WIN_MODES == ("remember", "max", "fixed"), p.WIN_MODES)
_sc = p.Settings()
_sc.data["win_mode"] = "weird"
_sc._sanitize()
check("窗口模式非法回落记住上次", _sc["win_mode"] == "remember", _sc["win_mode"])
_sc.data["win_fixed_w"] = 10
_sc.data["win_fixed_h"] = 99999
_sc._sanitize()
check("固定宽高夹住", (_sc["win_fixed_w"], _sc["win_fixed_h"]) == (800, 8000),
      (_sc["win_fixed_w"], _sc["win_fixed_h"]))
check("片头片尾默认空", p.Settings.DEFAULTS["intro_path"] == ""
      and p.Settings.DEFAULTS["outro_path"] == "")
check("启动窗口分支齐全", '"max"' in _src and '"fixed"' in _src
      and "maximize_to_screen()" in _src)
check("自绘窗口自己记最大化状态", "def is_maxed" in _src
      and "def toggle_maximum" in _src
      and "def restore_down" in _src
      and "self._maxed = True" in _src)
check("最大化不靠 Qt 的 isMaximized", "win.isMaximized()" not in _src
      and "self.isMaximized()" not in _src)

pr = p.Project(); pr.mode = "free"
pr.audio_dur = 30.0
pr.add_images([IMG[0]], 2.0)
check("没设片头片尾就只加图片", [c.kind for c in pr.clips] == ["image"],
      [c.kind for c in pr.clips])

# 片头片尾需要真视频文件
if p.missing_tools():
    check("片头片尾入片", False, "缺 ffmpeg")
else:
    intro = os.path.join(tmp, "intro.mp4")
    outro = os.path.join(tmp, "outro.mp4")
    for path, color, dur in ((intro, "red", 1.0), (outro, "blue", 2.0)):
        subprocess.run([p.FFMPEG, "-v", "error", "-y", "-f", "lavfi",
                        "-i", f"color=c={color}:s=320x180:d={dur}:r=30",
                        "-pix_fmt", "yuv420p", path], capture_output=True)
    pj = p.Project(); pj.mode = "free"; pj.audio_dur = 30.0
    pj.add_images([IMG[0]], 2.0, intro=intro, outro=outro)
    check("第一张图自动补片头片尾",
          [c.kind for c in pj.clips] == ["intro", "image", "outro"],
          [c.kind for c in pj.clips])
    check("片头时长跟视频", abs(pj.intro().dur - 1.0) < 0.2, pj.intro().dur)
    check("片尾时长跟视频", abs(pj.outro().dur - 2.0) < 0.2, pj.outro().dur)
    check("固定段合计", abs(pj.fixed_span() - (pj.intro().dur + pj.outro().dur)) < 1e-6,
          pj.fixed_span())
    pj.add_images([IMG[1]], 2.0)
    check("后来的图插在片尾前",
          [c.kind for c in pj.clips] == ["intro", "image", "image", "outro"],
          [c.kind for c in pj.clips])
    _span = pj.fixed_span()
    pj.set_duration(1, 5.0)
    check("改图片不动片头片尾", abs(pj.fixed_span() - _span) < 1e-6)
    pj.remove([0])
    check("片头也能删（不锁死在中间）",
          [c.kind for c in pj.clips] == ["image", "image", "outro"],
          [c.kind for c in pj.clips])

    # 跟 BGM：总长 = 音乐完整长度 + 片头片尾
    pb = p.Project(); pb.mode = "bgm"
    pb.audio_dur = 40.0
    pb.add_images([IMG[0]], 2.0, intro=intro, outro=outro)
    pb.normalize()
    want = pb.music_length() + pb.fixed_span()
    check("跟 BGM 时总长含片头片尾", abs(pb.total() - want) < 1e-6,
          (pb.total(), want))
    imgs = pb.images()
    check("图片段吃满剩下的时长",
          abs(sum(c.dur for c in imgs) - pb.music_length()) < 1e-6,
          sum(c.dur for c in imgs))

    # 真出片：片头不放 BGM、图片段放、片尾不放
    tone = os.path.join(FIXTURES, "tone6.m4a")
    if os.path.isfile(tone):
        pk = p.Project(); pk.mode = "free"; pk.audio_dur = 6.0
        pk.add_images([IMG[0]], 1.0, intro=intro, outro=outro)
        pk.normalize()
        dst = os.path.join(tmp, "intro_outro.mp4")
        p.render(pk.clips, pk.frame_counts(), tone, dst, w=320, h=180,
                 crf=35, preset="ultrafast", fps=30, fit="cover",
                 fade_in=0.0, fade_out=0.0, transition="none")
        dur = p.probe_duration(dst)
        check("片头片尾出片总长对", abs(dur - pk.total()) < 0.15,
              (dur, pk.total()))

        def seg_mean(mp4, ss, t):
            out = subprocess.run(
                [p.FFMPEG, "-v", "info", "-ss", str(ss), "-t", str(t),
                 "-i", mp4, "-vn", "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace").stderr
            line = [ln for ln in out.splitlines() if "mean_volume" in ln]
            if not line:
                return None            # 整段纯静音，ffmpeg 干脆不给数
            return float(line[0].split("mean_volume:")[1].split("dB")[0])

        intro_db = seg_mean(dst, 0.2, 0.5)
        body_db = seg_mean(dst, 1.2, 0.6)
        check("片头段没有 BGM", intro_db is None or intro_db < -70,
              (intro_db, body_db, pk.total(), dur))
        check("图片段有 BGM", body_db is not None and body_db > -40,
              (intro_db, body_db, pk.total(), dur))

        # 多条音频叠在一起
        pm = p.Project(); pm.mode = "free"; pm.audio_dur = 2.0
        pm.add_images([IMG[0]], 2.0)
        pm.normalize()
        pm.audios.append(p.AudioClip(tone, 6.0, 0.0, 1.0, gap=False))
        dst2 = os.path.join(tmp, "extra_audio.mp4")
        p.render(pm.clips, pm.frame_counts(), list(pm.audios), dst2,
                 w=320, h=180, crf=35, preset="ultrafast", fps=30,
                 fit="cover", fade_in=0.0, fade_out=0.0, transition="none")
        check("第二条音频也能混进成片",
              os.path.isfile(dst2) and abs(p.probe_duration(dst2) - 2.0) < 0.15,
              p.probe_duration(dst2))
        _gx = p.build_graph([30, 30], 2.0, 320, 180, fps=30,
                            clips=[p.Clip(IMG[0], 1.0), p.Clip(IMG[1], 1.0)],
                            timeline=[(True, 1.0), (True, 1.0)],
                            extras=[{"index": 3, "start": 0.5,
                                     "dur": 1.0, "volume": 0.5}])
        check("额外音频走 amix 且带延迟/音量",
              "amix=inputs=2:normalize=0" in _gx and "adelay=500|500" in _gx
              and "volume=0.500" in _gx, _gx[-260:])

# 交叉淡化：总长守恒，两侧各挤一半
_cf = [p.Clip(IMG[0], 2.0), p.Clip(IMG[1], 2.0)]
_cf[0].xfade = True
_c2, _ov = p._xfade_plan(_cf, [60, 60], 30, 0.3)
_half = min(int(round(0.3 * 30 / 2.0)), 30)
check("交叉淡化各补一半帧", _c2 == [60 + _half, 60 + _half] and _ov == [2 * _half],
      (_c2, _ov))
check("交叉淡化总帧不变", sum(_c2) - 2 * _half == 120, (_c2, _ov))
_c3, _ov3 = p._xfade_plan([p.Clip(IMG[0], 2.0), p.Clip(IMG[1], 2.0)],
                          [60, 60], 30, 0.3)
check("没勾就不交叉", _ov3 == [0] and _c3 == [60, 60], (_c3, _ov3))
check("兜底：没有 clips 也不炸", p._xfade_plan(None, [30], 30, 0.3) == ([30], []))
_gxf = p.build_graph([30, 30], 2.0, 320, 180, fps=30, clips=_cf,
                     timeline=[(True, 1.0), (True, 1.0)])
check("graph 用 xfade 合并", "xfade=transition=fade" in _gxf, _gxf[-200:])

# 右键菜单 / 时间线多选 / 滚轮音量 的接线
check("时间线支持多选与音频轨", "set_selection" in _src
      and "sel_audio" in _src and "audioVolume" in _src)
check("时间线右键发菜单信号", "def on_timeline_menu" in _src
      and "menu = pyqtSignal" in _src)
check("右键能开关淡入淡出", "def on_toggle_fade" in _src
      and "def on_set_transition" in _src and "def on_set_transition_dur" in _src)
check("右键能统一改音量", "def on_audio_set_volume" in _src
      and "def on_ask_volume" in _src)
check("能加/删额外音频", "def add_audio_extra" in _src
      and "def on_add_audio" in _src and "def on_remove_audio" in _src)
_aw = p.AudioClip(os.path.join(FIXTURES, "tone6.m4a"), 6.0, 0.0, 1.0, gap=False)
check("音频元素带名字", _aw.name.endswith("tone6.m4a"), _aw.name)
check("音量改到 0..2 之间", "max(0.0, min(2.0, value))" in _src
      and "max(0.0, min(2.0, a.volume + (delta or 0.0)))" in _src)
check("滚轮一步 5 个百分点", "audioVolume.emit" in _src and "0.05" in _src)
check("色带能拖播放头", "def mouseMoveEvent" in _src and "seeked.emit" in _src)
check("色带能拖着换位置", "moved.emit" in _src and "audioMoved.emit" in _src)

# 31 错误日志：闪退了得查得出原因（用户 2026-11-23 说记不清，靠日志兜住）
check("启动就开日志", "_start_log()" in _src and "_startup_crash_log" not in _src)
check("日志有路径与轮转", "def log_path" in _src and "LOG_MAX" in _src
      and 'path + ".1"' in _src)
check("Qt 消息也进日志", "qInstallMessageHandler" in _src)
check("设置里有错误日志入口", 'add_row("错误日志"' in _src
      and "def _open_log" in _src)
_log_dir = tempfile.mkdtemp(prefix="p2v_log_")
_old_local = os.environ.get("LOCALAPPDATA")
os.environ["LOCALAPPDATA"] = _log_dir
try:
    p._log_fh = None
    p._start_log()
    p.log("hello-log")
    try:
        raise ValueError("boom-test")
    except ValueError:
        p._log_traceback("sys", *sys.exc_info()[1:])
    _lp = p.log_path()
    _txt = open(_lp, encoding="utf-8", errors="replace").read()
    check("日志落到 LOCALAPPDATA", _lp.startswith(_log_dir), _lp)
    check("日志记了版本与动作", "Pic2Video" in _txt and "hello-log" in _txt)
    check("未捕获异常带 traceback", "uncaught sys" in _txt
          and "boom-test" in _txt and "ValueError" in _txt)
    # sys.excepthook 是三个参数；写成两参时真崩了只会报 "Error in sys.excepthook"，
    # 把原始栈一起吞掉（本轮实测踩过）
    _tb = None
    try:
        raise KeyError("three-arg")
    except KeyError:
        _tb = sys.exc_info()[2]
        p.sys.excepthook(KeyError, sys.exc_info()[1], _tb)
    _txt2 = open(_lp, encoding="utf-8", errors="replace").read()
    check("excepthook 是三参数签名", "three-arg" in _txt2 and "KeyError" in _txt2)
    with open(_lp, "a", encoding="utf-8") as _fh:
        _fh.write("x" * (p.LOG_MAX + 10))
    p._log_fh.close()
    p._start_log()
    check("超过上限会轮转", os.path.exists(_lp + ".1"))
finally:
    try:
        if p._log_fh:
            p._log_fh.close()
    except Exception:
        pass
    p._log_fh = None
    if _old_local is None:
        os.environ.pop("LOCALAPPDATA", None)
    else:
        os.environ["LOCALAPPDATA"] = _old_local
    import shutil as _sh
    _sh.rmtree(_log_dir, ignore_errors=True)

# ---------------- 32 撤回 / 还原
pr = p.Project()
pr.add_images(IMG)
pr.audios.append(p.AudioClip(os.path.join(FIXTURES, "tone6.m4a"), 6.0, 1.5, 0.8))
pr.mode = "total"
pr.total_setting = 9.0
st = pr.to_state()
q = p.Project()
q.load_state(st)
check("快照能还原素材",
      [c.path for c in q.clips] == [x.path for x in pr.clips]
      and abs(q.clips[0].dur - pr.clips[0].dur) < 1e-9)
check("快照能还原音频",
      len(q.audios) == 1 and abs(q.audios[0].volume - 0.8) < 1e-9
      and abs(q.audios[0].start - 1.5) < 1e-9)
check("快照带节奏设置", q.mode == "total" and abs(q.total_setting - 9.0) < 1e-9)
q.load_state({"clips": []})
check("空快照不炸", q.clips == [] and q.audios == [])

src = open(os.path.join(ROOT, "pic2video.py"), encoding="utf-8").read()
check("历史栈有上限", "_HISTORY_MAX = 120" in src)
check("快镜头合并成一步", 'now - last["t"] < 1.2' in src)
check("撤回/还原快捷键", '"Ctrl+Z"' in src and '"Ctrl+Shift+Z"' in src)
check("素材/历史两页", 'self.left_tabs.addTab(self.list, tr("素材"))' in src
      and 'self.left_tabs.addTab(hist_page, tr("历史"))' in src)
check("历史列表能点回去", "def on_history_click" in src
      and "self._history_apply(row)" in src)
check("重放时不再记步骤", "self._hist_lock = True" in src
      and 'self.project.load_state(st["project"])' in src)
check("各操作都记历史", src.count("self._history_push(") >= 14,
      src.count("self._history_push("))

# ---------------- 33 快捷键可自定义
_sd = p.Settings.DEFAULTS["shortcuts"]
check("默认快捷键对齐剪辑软件",
      _sd["add"] == "Ctrl+I" and _sd["export"] == "Ctrl+M"
      and _sd["undo"] == "Ctrl+Z" and _sd["redo"] == "Ctrl+Shift+Z"
      and _sd["play"] == "Space" and _sd["remove"] == "Delete"
      and _sd["preview"] == "F5", _sd)
_tmpd = tempfile.mkdtemp(prefix="p2v_keys_")
_sp = os.path.join(_tmpd, "settings.json")
_s = p.Settings(_sp)
_s.update(shortcuts={"add": "Ctrl+Alt+A", "nope": "Ctrl+Q", "export": ""})
check("坏快捷键回落默认", _s["shortcuts"]["add"] == "Ctrl+Alt+A"
      and _s["shortcuts"]["export"] == "Ctrl+M"
      and "nope" not in _s["shortcuts"], _s["shortcuts"])
_s.save()
_s2 = p.Settings(_sp)
check("自定义快捷键能存住", _s2["shortcuts"]["add"] == "Ctrl+Alt+A")
_s3 = p.Settings(_sp)
_s3.data["shortcuts"] = "not-a-dict"
_s3._sanitize()
check("快捷键写成字符串也不炸",
      set(_s3["shortcuts"]) == set(p.SHORTCUT_ORDER), _s3["shortcuts"])
check("七个动作都有名字",
      all(p.SHORTCUT_LABELS[k] for k in p.SHORTCUT_ORDER))
check("重绑时会清掉旧的", 'for sc in getattr(self, "_shortcuts", [])' in src)
check("按设置绑定快捷键", 'bound.update(self.settings["shortcuts"] or {})' in src)
check("设置里有快捷键页", 'add_sep("快捷键", pro=True)' in src
      and "QKeySequenceEdit(" in src)
check("重复键被拦下", 'tr("快捷键冲突")' in src and "return" in src)
check("能一键恢复默认", "def _reset_shortcuts" in src)


# 34 拖到列表三分区：插前 / 替换 / 插后；文件框换回系统
_pr2 = p.Project(); _pr2.mode = "free"
_pr2.add_images([IMG[0], IMG[1]], 2.0)
_pr2.insert_images(1, [IMG[2]], 2.0)
check("插到中间位置", [os.path.basename(c.path)
                        for c in _pr2.clips] == ["land.png", "sq.png", "port.png"],
      [os.path.basename(c.path) for c in _pr2.clips])
check("插入后总数对", _pr2.image_count() == 3, _pr2.image_count())
_pr2.replace_image(0, IMG[2])
check("替换换掉路径", os.path.basename(_pr2.clips[0].path) == "sq.png")
check("替换不改时长", abs(_pr2.clips[0].dur - 2.0) < 1e-6, _pr2.clips[0].dur)
_pr2.clips[0].fade = True
_pr2.replace_image(0, IMG[1])
check("替换保留淡入淡出", _pr2.clips[0].fade is True)
_pr2.insert_images(999, [IMG[0]], 1.0)
check("越界插到末尾", os.path.basename(_pr2.clips[-1].path) == "land.png"
      and len(_pr2.clips) == 4, len(_pr2.clips))
_pr2.insert_images(-5, [IMG[0]], 1.0)
check("负下标当开头", os.path.basename(_pr2.clips[0].path) == "land.png")
_pv = p.Project(); _pv.mode = "free"
_pv.add_images([IMG[0]], 2.0)
_vid = os.path.join(tempfile.gettempdir(), "p2v_test_outro.mp4")
subprocess.run([p.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                "color=c=red:s=320x180:d=1", "-pix_fmt", "yuv420p", _vid],
               capture_output=True)
if os.path.isfile(_vid):
    _pv.add_video(_vid, "outro")
    _before = len(_pv.clips)
    _pv.replace_image(len(_pv.clips) - 1, IMG[1])
    check("片尾不会被替换掉", len(_pv.clips) == _before
          and _pv.clips[-1].kind == "outro", _pv.clips[-1].kind)
    try:
        os.remove(_vid)
    except OSError:
        pass
else:
    check("片尾不会被替换掉", False, "ffmpeg 生成测试视频失败")
check("外部拖入发三个参数", "drop_files.emit(paths, row, zone)" in src)
check("拖入画落点提示", "def paintEvent(self, ev)" in src and
      "zone == \"replace\"" in src)
check("拖入按图片扩展名过滤", "if ext in IMG_EXTS or os.path.isdir(p)" in src)
check("列表接了拖入信号",
      "self.list.drop_files.connect(self.on_drop_into_list)" in src)
check("三段判定在 ImageList 里",
      "if frac < 1.0 / 3:" in src and "if frac > 2.0 / 3:" in src)
check("系统文件框三件套", "QFileDialog.getExistingDirectory" in src
      and "QFileDialog.getSaveFileName" in src
      and "QFileDialog.getOpenFileNames" in src)
check("保存框自动补后缀", 'name += "." + suffix' in src)

# ---- 35 跟素材总长 + 铺满音频 + 视频循环 ----
print()
_vid2 = os.path.join(tempfile.gettempdir(), "p2v_test_intro.mp4")
subprocess.run([p.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                "color=c=blue:s=320x180:d=1", "-pix_fmt", "yuv420p", _vid2],
               capture_output=True)
check("素材测试视频生成", os.path.isfile(_vid2))
_m = p.Project()
_m.add_images([IMG[0], IMG[1]], 2.0)
_m.add_video(_vid2, "intro")
_m.add_video(_vid2, "outro")
_m.mode = "material"
_m.apply_material()
_fixed = _m.fixed_span()
_per = _fixed / 2.0
check("跟素材总长：每张 = 素材总长 / 图片数",
      abs(_m.clips[_m.clips.index(_m.images()[0])].dur - _per) < 1e-6,
      round(_per, 3))
check("跟素材总长：图片总时长 = 素材总长",
      abs(sum(c.dur for c in _m.images()) - _fixed) < 1e-6)
_cnt = _m.frame_counts()
check("跟素材总长：帧数总和 = 总长 x 帧率",
      sum(_cnt) == int(round(sum(c.dur for c in _m.clips) * p.FPS)))
_nomat = p.Project()
_nomat.add_images([IMG[0], IMG[1]], 2.5)
_nomat.mode = "material"
_nomat.apply_material()
check("跟素材总长：没片头片尾时不动图片",
      all(abs(c.dur - 2.5) < 1e-6 for c in _nomat.images()))
_b = p.Project()
_b.add_images([IMG[0], IMG[1]], 1.0)
_b.add_video(_vid2, "intro")
_b.audio_dur = 6.0
_b.mode = "bgm"
_b.normalize()
_old = _b.total()
_b.set_video_len(0, 2.0)
check("改片头时长：片头变长", abs(_b.clips[0].dur - 2.0) < 1e-6,
      round(_b.clips[0].dur, 3))
check("改片头时长：音乐不被截短，图片让位",
      abs(_b.total() - (_old + 1.0)) < 1e-6
      and abs(_b.images()[0].dur - _b.images()[1].dur) < 1e-6,
      [round(c.dur, 3) for c in _b.clips])
check("改片头时长：传图片下标不动",
      (lambda pr: (pr.set_video_len(1, 5.0), abs(pr.images()[0].dur - _b.images()[0].dur) < 1e-9)[1])(_b))
_fa = p.Project()
_fa.add_images([IMG[0], IMG[1]], 2.0)
_fa.audio_dur = 9.0
_fa.mode = "free"
check("铺满音频：图片时长改成音频时长（逐张模式）",
      (lambda pr: (pr.set_duration(0, 9.0), abs(pr.clips[0].dur - 9.0) < 1e-6)[1])(_fa))
check("铺满音频：菜单项存在",
      "画面铺满这条音频" in src and "def on_fill_audio(" in src)
check("铺满音频：编辑记一步",
      "self._history_push(tr(\"铺满音频\"))" in src)
check("视频要放长时加 stream_loop",
      'cmd += ["-stream_loop", "-1"]   # 要放得比原片长就循环' in src)
check("节奏改成下拉：跟 BGM / 跟素材总长",
      'self.cmb_rhythm.addItem(tr(MODE_LABELS["bgm"]), "bgm")' in src
      and 'self.cmb_rhythm.addItem(tr(MODE_LABELS["material"]), "material")' in src)
check("节奏下拉带 mode 数据", "self.on_mode(self.cmb_rhythm.currentData())" in src)
check("跟素材总长在 MODE_LABELS 里", "material" in p.MODE_LABELS)
_long = p.Project()
_long.add_images([IMG[0]], 1.0)
_long.add_video(_vid2, "intro")
_long.mode = "free"
_long.set_video_len(0, 3.0)
_out2 = os.path.join(tempfile.gettempdir(), "p2v_loop.mp4")
_cnt2 = _long.frame_counts()
p.render(_long.clips, _cnt2, None, _out2, w=320, h=180, fps=30,
         preset="ultrafast", crf=30, fit="cover", transition="none",
         fade_in=0.0, fade_out=0.0, bg_blur=False)
_dur2 = p.probe_duration(_out2)
check("1 秒视频要放 3 秒就循环补满", abs(_dur2 - 4.0) < 0.2,
      round(_dur2, 3))
for _p2 in (_out2, _vid2):
    try:
        os.remove(_p2)
    except OSError:
        pass

# ---------------- 34 工程文件：格式与版本号
_pr34 = p.Project(fps=60)
_pr34.add_images(IMG[:2], 2.0)
_pr34.audios.append(p.AudioClip(os.path.join(FIXTURES, "tone6.m4a"), 6.0, 1.5, 0.8))
_pr34.mode = "total"
_pr34.total_setting = 9.0
_doc34 = _pr34.to_doc()
check("工程格式带版本号", _doc34.get("version") == p.PROJECT_VERSION == 2,
      _doc34.get("version"))
check("工程格式：片段是 dict 不是元组",
      isinstance(_doc34["clips"][0], dict)
      and {"path", "dur", "kind", "fade", "xfade"} <= set(_doc34["clips"][0]))
check("工程格式：音频是 dict 不是元组",
      isinstance(_doc34["audios"][0], dict)
      and {"path", "dur", "start", "volume", "gap"} <= set(_doc34["audios"][0]))
check("工程格式能直接 json 化",
      (lambda d: (json.dumps(d), True)[1])(_doc34))
_back34 = p.Project.from_doc(_doc34)
check("往返：素材与时长一致",
      [c.path for c in _back34.clips] == [c.path for c in _pr34.clips]
      and [round(c.dur, 6) for c in _back34.clips] == [round(c.dur, 6) for c in _pr34.clips])
check("往返：音频一致",
      len(_back34.audios) == 1
      and abs(_back34.audios[0].start - 1.5) < 1e-9
      and abs(_back34.audios[0].volume - 0.8) < 1e-9
      and _back34.audios[0].path == _pr34.audios[0].path)
check("往返：节奏/总长/fps 一致",
      _back34.mode == "total" and abs(_back34.total_setting - 9.0) < 1e-9
      and _back34.fps == 60)

# 缺字段给默认值（老格式/手写文件不该炸）
_loose34 = p.Project.from_doc({"version": 1, "clips": [
    {"path": IMG[0], "dur": 2.0}]})
check("缺字段给默认值",
      len(_loose34.clips) == 1 and _loose34.clips[0].kind == "image"
      and _loose34.clips[0].fade is False and _loose34.clips[0].xfade is False
      and _loose34.mode == "bgm")

def _reason34(doc):
    try:
        p.Project.from_doc(doc)
    except p.ProjectFileError as exc:
        return exc.reason
    return ""

check("版本比软件新时拒绝",
      _reason34({"version": p.PROJECT_VERSION + 1, "clips": []}) == "newer")
check("不是 dict 就拒绝", _reason34(None) == "bad-shape")
check("缺版本号就拒绝", _reason34({"clips": []}) == "bad-shape")
check("clips 不是列表就拒绝",
      _reason34({"version": 1, "clips": "oops"}) == "bad-shape")
check("片段缺 path 就拒绝",
      _reason34({"version": 1, "clips": [{"dur": 1.0}]}) == "bad-shape")
check("撤销快照仍走同一套格式",
      _pr34.to_state()["clips"][0]["path"] == _pr34.clips[0].path
      and _pr34.to_state()["version"] == p.PROJECT_VERSION)

# ---------------- 35 工程文件：存盘 / 读回 / 出错分类
_dir35 = tempfile.mkdtemp(prefix="p2v_doc_")
_path35 = os.path.join(_dir35, "我的工程.p2v")
p.save_project(_pr34, _path35)
check("存盘后文件在", os.path.isfile(_path35))
check("存盘不留 .tmp", not os.path.exists(_path35 + ".tmp"))
_l35 = p.load_project(_path35)
check("读回来内容一致",
      [c.path for c in _l35.clips] == [c.path for c in _pr34.clips]
      and _l35.mode == "total" and abs(_l35.total_setting - 9.0) < 1e-9)
_cn35 = os.path.join(_dir35, "中文素材.png")
open(_cn35, "wb").write(b"x")
_pr35 = p.Project()
_pr35.clips = [p.Clip(_cn35, 2.0)]
_cn_doc35 = os.path.join(_dir35, "中文工程.p2v")
p.save_project(_pr35, _cn_doc35)
_raw35 = open(_cn_doc35, encoding="utf-8").read()
check("存盘带版本号", '"version": 2' in _raw35, _raw35[:40])
check("存盘不转义中文", "中文素材" in _raw35)
check("中文路径能读回", p.load_project(_cn_doc35).clips[0].path == _cn35)

def _reason35(fn):
    try:
        fn()
    except p.ProjectFileError as exc:
        return exc.reason
    return ""

check("打开不存在的文件 -> missing",
      _reason35(lambda: p.load_project(os.path.join(_dir35, "nope.p2v")))
      == "missing")
_bad35 = os.path.join(_dir35, "bad.p2v")
open(_bad35, "w", encoding="utf-8").write("{oops")
check("坏 JSON -> bad-json",
      _reason35(lambda: p.load_project(_bad35)) == "bad-json")
_new35 = os.path.join(_dir35, "newer.p2v")
open(_new35, "w", encoding="utf-8").write(
    json.dumps({"version": p.PROJECT_VERSION + 1, "clips": []}))
check("更新版本的工程 -> newer",
      _reason35(lambda: p.load_project(_new35)) == "newer")
_shape35 = os.path.join(_dir35, "shape.p2v")
open(_shape35, "w", encoding="utf-8").write(json.dumps([1, 2]))
check("内容不是对象 -> bad-shape",
      _reason35(lambda: p.load_project(_shape35)) == "bad-shape")
check("写到不存在的目录 -> write-failed",
      _reason35(lambda: p.save_project(
          _pr34, os.path.join(_dir35, "no", "dir", "x.p2v")))
      == "write-failed")
check("新存的能覆盖旧的",
      (p.save_project(_pr35, _cn_doc35),
       p.load_project(_cn_doc35).clips[0].path == _cn35)[1])

# ---------------- 36 自动保存
_dir36 = tempfile.mkdtemp(prefix="p2v_auto_")
_auto36 = os.path.join(_dir36, "autosave.p2v")
_t36 = {"now": 1000.0}
_saver36 = p.AutoSaver(_auto36, interval=15.0, clock=lambda: _t36["now"])
check("没有改动就不写",
      _saver36.tick(_pr34) is False and not os.path.exists(_auto36))
_saver36.mark_dirty()
_t36["now"] = 1010.0
check("还没到间隔不写",
      _saver36.tick(_pr34) is False and not os.path.exists(_auto36))
_t36["now"] = 1015.0
check("到点就写一份",
      _saver36.tick(_pr34) is True and os.path.isfile(_auto36))
check("写过的能读回",
      p.load_project(_auto36).clips[0].path == _pr34.clips[0].path)
check("写完不重复写", _saver36.tick(_pr34) is False)
check("能认出有残留", p.AutoSaver.restorable(_auto36) is True)
_t36["now"] = 1100.0
_saver36.mark_dirty()
_t36["now"] = 1115.0
check("又改了会再写", _saver36.tick(_pr34) is True)
_saver36.clear()
check("清掉自动保存",
      _saver36.clear() is None and not os.path.exists(_auto36))
check("没残留时不提示", p.AutoSaver.restorable(_auto36) is False)
_bad36 = os.path.join(_dir36, "bad.p2v")
open(_bad36, "w", encoding="utf-8").write("{oops")
check("坏残留不提示", p.AutoSaver.restorable(_bad36) is False)

# ---------------- 37 最近打开的工程
_dir37 = tempfile.mkdtemp(prefix="p2v_recent_")
_sp37 = os.path.join(_dir37, "settings.json")
_s37 = p.Settings(_sp37)
_paths37 = []
for _i in range(10):
    _f37 = os.path.join(_dir37, "工程%d.p2v" % _i)
    open(_f37, "w", encoding="utf-8").write("{}")
    _paths37.append(_f37)
for _f37 in _paths37:
    _s37.push_recent(_f37)
check("最近打开最多 8 条", len(_s37.recent()) == 8, len(_s37.recent()))
check("最近打开：新的在前", _s37.recent()[0] == _paths37[-1])
_s37.push_recent(_paths37[0])
check("最近打开：重开过的置顶且不重复",
      _s37.recent()[0] == _paths37[0]
      and _s37.recent().count(_paths37[0]) == 1
      and len(_s37.recent()) == 8)
os.remove(_paths37[3])
check("最近打开：文件没了就不列", _paths37[3] not in _s37.recent())
check("最近打开：列出来的都还在",
      all(os.path.isfile(x) for x in _s37.recent()))
check("最近打开能跨次启动",
      p.Settings(_sp37).recent()[:1] == [_paths37[0]])
check("最近打开不吃空路径",
      (lambda s: (s.push_recent(""), s.push_recent(None),
                  s.recent()[0] == _paths37[0])[2])(p.Settings(_sp37)))

# ---------------- 38 缺素材
_miss38 = p.Project()
_miss38.audios = [p.AudioClip(os.path.join(_dir35, "nope.m4a"), 5.0)]
_miss38.clips = [p.Clip(IMG[0], 2.0), p.Clip(os.path.join(_dir35, "nope.png"), 2.0)]
check("缺的素材能列全（图片 + 音频）",
      _miss38.missing_media() == [os.path.join(_dir35, "nope.png"),
                                  os.path.join(_dir35, "nope.m4a")],
      _miss38.missing_media())
check("素材都在就没缺",
      p.Project.from_doc({"version": 1, "clips": [
          {"path": IMG[0], "dur": 1.0}]}).missing_media() == [])
check("同一个文件只报一次",
      (lambda pr: (pr.clips.append(p.Clip(pr.clips[-1].path, 1.0)),
                   len(pr.missing_media()))[1])(_miss38) == 2)
check("空工程没有缺素材", p.Project().missing_media() == [])

# ---------------- 39 工程菜单 / 自动保存接线（源码级）
check("工具栏有「工程」入口", "self.btn_project = QToolButton()" in src)
check("工程菜单：新建/打开/保存/另存为",
      'tr("新建工程")' in src and 'tr("打开工程…")' in src
      and 'tr("保存工程")' in src and 'tr("另存为…")' in src)
check("工程菜单有最近打开",
      "self.menu_recent" in src and "def _refresh_recent_menu" in src)
check("Ctrl+N/O/S 都绑上了",
      '"Ctrl+N"' in src and '"Ctrl+O"' in src and '"Ctrl+S"' in src)
check("标题带工程名和改动点",
      "def _refresh_title" in src and 'star = " ●"' in src)
check("关窗前问存不存（保存/不保存/取消）",
      "def _confirm_discard" in src and 'tr("不保存")' in src
      and 'tr("保存")' in src)
check("自动保存定时器",
      "self.autosave_timer" in src and "def _autosave_tick" in src)
check("启动时能恢复自动保存", "def _maybe_restore_autosave" in src)
check("打开工程时缺素材会提示",
      "def _warn_missing" in src and "missing_media()" in src)
check("保存/不保存后清掉自动保存", "self.autosaver.clear()" in src)
check("打开过的记进最近列表", "self.settings.push_recent(" in src)
# ---------------- 40 缺素材重新定位

def _touch(path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("x")


with tempfile.TemporaryDirectory() as td_rel:
    old_a = os.path.join(td_rel, "旧图.png")
    new_a = os.path.join(td_rel, "新图.png")
    other = os.path.join(td_rel, "别的.png")
    old_b = os.path.join(td_rel, "旧曲.mp3")
    new_b = os.path.join(td_rel, "新曲.mp3")
    for f in (new_a, other, new_b):
        _touch(f)

    proj = p.Project(fps=30)
    proj.clips = [p.Clip(old_a, 2.0), p.Clip(other, 2.0)]
    proj.audios = [p.AudioClip(old_b, 10.0)]
    check("重新定位前：旧图旧曲算缺", proj.missing_media() == [old_a, old_b],
          proj.missing_media())
    n = proj.relink_media(old_a, new_a)
    check("重新定位：换掉对上的片段路径", proj.clips[0].path == new_a,
          proj.clips[0].path)
    check("重新定位：返回换了几条", n == 1, n)
    check("重新定位：没对上的片段不动",
          proj.clips[1].path == other, proj.clips[1].path)
    check("重新定位：音频另走一遍", proj.relink_media(old_b, new_b) == 1)
    check("重新定位：音频路径换了", proj.audios[0].path == new_b,
          proj.audios[0].path)
    check("重新定位：全找回来后不再报缺", proj.missing_media() == [],
          proj.missing_media())
    check("重新定位：路径对不上什么都不改",
          proj.relink_media(os.path.join(td_rel, "没有.png"), new_a) == 0)
    proj.clips[0].path = old_a
    check("重新定位：换到还是不存在的文件，照样算缺",
          (proj.relink_media(old_a, os.path.join(td_rel, "也没有.png")) == 1
           and proj.missing_media() == [os.path.join(td_rel, "也没有.png")]),
          proj.missing_media())

src = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pic2video.py"), encoding="utf-8").read()
check("缺素材对话框有「重新定位…」", 'tr("重新定位…")' in src)
check("缺素材对话框有「先不管」", 'tr("先不管")' in src)
check("重新定位走文件选择框", "def _relink_missing" in src
      and "QFileDialog.getOpenFileName" in src)
check("重新定位调 Project.relink_media",
      "self.project.relink_media(old, new)" in src)
# ---------------- 41 入出点：模型与工程文件

def video_clip(dur=10.0, **kw):
    c = p.Clip("v.mp4", dur, "intro", **kw)
    return c


c0 = video_clip()
check("新片段默认 src_in=0", c0.src_in == 0.0, c0.src_in)
check("新片段默认 src_out=None", c0.src_out is None, c0.src_out)
check("没设入出点时 span 等于 dur", c0.span == 10.0, c0.span)
c0.src_in, c0.src_out = 2.0, 5.0
check("设了入出点后 span = out - in", c0.span == 3.0, c0.span)
check("图片段 span 就是 dur", p.Clip("a.png", 2.5).span == 2.5)

c1 = video_clip(2.0, 5.0) if False else video_clip()
c1.src_in, c1.src_out = 2.0, 5.0
c2 = c1.copy()
check("copy 带上入出点", (c2.src_in, c2.src_out) == (2.0, 5.0),
      (c2.src_in, c2.src_out))

proj = p.Project(fps=30)
proj.clips = [video_clip(), p.Clip("a.png", 2.0)]
proj.clips[0].src_in, proj.clips[0].src_out = 2.0, 5.0
check("frame_counts 按入出点算视频帧数", proj.frame_counts() == [90, 60],
      proj.frame_counts())
proj.clips[0].src_in = 0.0
proj.clips[0].src_out = None
check("没入出点时帧数照旧", proj.frame_counts() == [300, 60],
      proj.frame_counts())

proj = p.Project(fps=30)
proj.clips = [video_clip()]
proj.clips[0].src_in = 1.0
proj.set_src_out(0, 99.0)
check("出点超过源时长被夹到结尾", proj.clips[0].src_out == 10.0,
      proj.clips[0].src_out)
proj.set_src_in(0, 50.0)
check("入点不会越过出点（至少留一帧）",
      abs(proj.clips[0].span - 1 / 30) < 1e-6, proj.clips[0].span)
proj.set_src_in(0, -5.0)
check("入点不会小于 0", proj.clips[0].src_in == 0.0, proj.clips[0].src_in)
proj.set_src_out(0, 0.0)
check("出点不会小于入点（至少留一帧）",
      proj.clips[0].span >= 1 / 30 - 1e-9, proj.clips[0].span)

proj = p.Project(fps=30)
proj.clips = [video_clip(), p.Clip("a.png", 2.0)]
proj.clips[0].src_in, proj.clips[0].src_out = 2.0, 5.0
doc = proj.to_doc()
check("工程文件写出入出点",
      doc["clips"][0]["src_in"] == 2.0 and doc["clips"][0]["src_out"] == 5.0,
      doc["clips"][0])
check("图片段不写入出点也能过", "src_in" in doc["clips"][1])

raw = {"version": 1, "fps": 30, "mode": "bgm",
       "clips": [{"path": "v.mp4", "dur": 10.0, "kind": "intro"}]}
old = p.Project.from_doc(raw)
check("老工程缺 src_in 给 0", old.clips[0].src_in == 0.0, old.clips[0].src_in)
check("老工程缺 src_out 给 None", old.clips[0].src_out is None,
      old.clips[0].src_out)
raw = {"version": 1, "fps": 30, "mode": "bgm",
       "clips": [{"path": "v.mp4", "dur": 10.0, "kind": "intro",
                  "src_in": -3, "src_out": 4}]}
fixed = p.Project.from_doc(raw)
check("负入点读成 0", fixed.clips[0].src_in == 0.0, fixed.clips[0].src_in)
raw["clips"][0]["src_out"] = 0
bad = p.Project.from_doc(raw)
check("出点不大于入点就当成到结尾", bad.clips[0].src_out is None,
      bad.clips[0].src_out)
# ---------------- 42 播放头切段

proj = p.Project(fps=30)
proj.clips = [p.Clip("a.png", 4.0), p.Clip("b.png", 6.0)]
check("切段返回成功", proj.split_at(2.0) is True)
check("图片段被切成两段", len(proj.clips) == 3, len(proj.clips))
check("两半时长对得上",
      [round(c.span, 3) for c in proj.clips] == [2.0, 2.0, 6.0],
      [c.span for c in proj.clips])
check("切段后总长不变", abs(proj.total() - 10.0) < 1e-9, proj.total())
check("两半用同一个素材", proj.clips[0].path == proj.clips[1].path == "a.png")

proj = p.Project(fps=30)
proj.clips = [p.Clip("v.mp4", 10.0, "intro", fade=True, xfade=True)]
proj.clips[0].src_in, proj.clips[0].src_out = 2.0, 6.0
check("有入出点的视频也能切", proj.split_at(1.5) is True)
check("视频两段首尾相接",
      (proj.clips[0].src_in, proj.clips[0].src_out,
       proj.clips[1].src_in, proj.clips[1].src_out) == (2.0, 3.5, 3.5, 6.0),
      [(c.src_in, c.src_out) for c in proj.clips])
check("视频切完总长不变", abs(proj.total() - 4.0) < 1e-9, proj.total())
check("切完帧数总和不变",
      sum(proj.frame_counts()) == 120, proj.frame_counts())
check("切段保留类型与淡入淡出",
      all(c.kind == "intro" and c.fade and c.xfade for c in proj.clips),
      [(c.kind, c.fade, c.xfade) for c in proj.clips])

proj = p.Project(fps=30)
proj.clips = [p.Clip("v.mp4", 10.0, "intro")]
proj.clips[0].src_in, proj.clips[0].src_out = 2.0, 6.0
check("段首不足一帧不切", proj.split_at(0.0) is False)
check("段尾不足一帧不切", proj.split_at(4.0) is False)
check("超出总长不切", proj.split_at(99.0) is False)
check("不切时片段数不变", len(proj.clips) == 1, len(proj.clips))

proj = p.Project(fps=30)
proj.clips = [p.Clip("a.png", 3.0)]
proj.split_at(1.0)
check("切完能存能读", p.Project.from_doc(proj.to_doc()).to_doc() == proj.to_doc(),
      proj.to_doc()["clips"])

proj = p.Project(fps=30)
proj.clips = [p.Clip("a.png", 4.0), p.Clip("b.png", 4.0)]
proj.split_at(6.0)
check("切在第二段里也对",
      [round(c.span, 3) for c in proj.clips] == [4.0, 2.0, 2.0],
      [c.span for c in proj.clips])

proj = p.Project(fps=30)
proj.clips = [p.Clip("v.mp4", 10.0, "intro")]
proj.clips[0].src_in, proj.clips[0].src_out = 2.0, 6.0
proj.set_src_out(0, 3.0)
check("拖右边缩到 3 秒", abs(proj.clips[0].span - 1.0) < 1e-9, proj.clips[0].span)
proj.set_src_in(0, 2.5)
check("拖左边留一帧", abs(proj.clips[0].span - 0.5) < 1e-9, proj.clips[0].span)
# ---------------- 43 入出点进 filtergraph

proj = p.Project(fps=30)
proj.clips = [p.Clip("v.mp4", 10.0, "intro")]
proj.clips[0].src_in, proj.clips[0].src_out = 2.0, 6.0
g = p.build_graph(proj.frame_counts(), proj.total(), clips=proj.clips, fps=30)
check("视频链先裁入出点再缩放",
      "trim=start=2.0000:end=6.0000,setpts=PTS-STARTPTS," in g, g[:400])

proj = p.Project(fps=30)
proj.clips = [p.Clip("v.mp4", 10.0, "intro")]
g = p.build_graph(proj.frame_counts(), proj.total(), clips=proj.clips, fps=30)
check("没设入出点就不加 trim=start", "trim=start=" not in g.split("[s0]")[0], g[:400])

proj = p.Project(fps=30)
proj.clips = [p.Clip("v.mp4", 10.0, "intro"), p.Clip("v2.mp4", 10.0, "outro")]
proj.clips[0].src_in, proj.clips[0].src_out = 1.0, 3.0
proj.clips[1].src_in, proj.clips[1].src_out = 4.0, 9.0
g = p.build_graph(proj.frame_counts(), proj.total(), clips=proj.clips, fps=30)
check("片头片尾各裁各的",
      "trim=start=1.0000:end=3.0000" in g and "trim=start=4.0000:end=9.0000" in g,
      g[:600])
check("裁完的段数对得上", proj.frame_counts() == [60, 150], proj.frame_counts())

proj = p.Project(fps=30)
c1 = p.Clip("v.mp4", 10.0, "intro")
c1.src_in, c1.src_out, c1.xfade = 0.0, 3.0, True
c2 = p.Clip("v2.mp4", 10.0, "outro")
c2.src_in, c2.src_out = 2.0, 5.0
proj.clips = [c1, c2]
g = p.build_graph(proj.frame_counts(), proj.total(), clips=proj.clips,
                  fps=30, xfade_sec=0.3)
check("交叉淡化时出点往后多借半截", "trim=start=0.0000:end=3.1333" in g,
      [x for x in g.split(";") if "trim=start=" in x][:1])
check("交叉淡化时入点往前多借半截", "trim=start=1.8667:end=5.0000" in g,
      [x for x in g.split(";") if "trim=start=" in x][1:2])

# ---------------- 44 入出点 / 切段的界面接线

src = open(os.path.join(ROOT, "pic2video.py"), encoding="utf-8").read()
check("时间线能发左右边界拖拽", "trimmed = pyqtSignal(int, str, float)" in src)
check("时间线宽度按 span 算",
      "sum(c.span for c in self.project.clips)" in src)
check("窗口接住边界拖拽", "trimmed.connect(self.on_timeline_trim)" in src)
check("拖拽落到入出点上", "def on_timeline_trim" in src
      and "set_src_in" in src and "set_src_out" in src)
check("有 I / O 快捷键", '("I", self.on_mark_in)' in src
      and '("O", self.on_mark_out)' in src)
check("有 Ctrl+K 切段", '("Ctrl+K", self.on_split_at)' in src)
check("按播放头找所在段", "def _clip_at" in src)
check("切段走 split_at", "self.project.split_at(" in src)
check("时间线标题显示实测长度",
      "dur=f\"{c.span:.1f}\"" in src or "c.span:.1f" in src)

# ---------------- 45 字幕：SRT 解析

srt_src = "\n".join([
    "1",
    "00:00:01,000 --> 00:00:03,500",
    "第一行",
    "第二行",
    "",
    "2",
    "00:00:04,250 --> 00:00:06,000",
    "第二条",
    "",
])
cues = p.parse_srt(srt_src)
check("SRT 解析出两条", len(cues) == 2, cues)
check("SRT 第一条起止时间",
      (cues[0]["start"], cues[0]["end"]) == (1.0, 3.5), cues[0])
check("SRT 多行文字用换行连起来", cues[0]["text"] == "第一行\n第二行",
      cues[0]["text"])
check("SRT 第二条时间", (cues[1]["start"], cues[1]["end"]) == (4.25, 6.0),
      cues[1])
check("SRT 也带空的样式位", cues[0]["style"] == "", cues[0])

cues = p.parse_srt("00:00:00.500 --> 00:00:02.000\r\n只有一句\r\n\r\n\r\n")
check("SRT 可以不写序号、点当毫秒", len(cues) == 1 and cues[0]["start"] == 0.5,
      cues)
check("SRT 去掉行尾回车", cues and cues[0]["text"] == "只有一句", cues)

srt_mixed = "\n".join(["1", "00:00:02,000 --> 00:00:01,000", "倒着的",
                      "", "2", "00:00:05,000 --> 00:00:06,000", "好的"])
check("SRT 时间倒着的丢掉",
      [c["text"] for c in p.parse_srt(srt_mixed)] == ["好的"], srt_mixed)
try:
    p.parse_srt("1\n00:00:02,000 --> 00:00:01,000\n倒着的\n")
    srt_none = ""
except p.SubtitleError as e:
    srt_none = e.reason
check("SRT 一条都不剩报 bad-format", srt_none == "bad-format", srt_none)

try:
    p.parse_srt("这不是字幕\n")
    srt_bad = ""
except p.SubtitleError as e:
    srt_bad = e.reason
check("SRT 认不出报 bad-format", srt_bad == "bad-format", srt_bad)

# ---------------- 46 字幕：ASS 解析与按扩展名读文件

ass_src = "\n".join([
    "[Script Info]",
    "Title: t",
    "",
    "[V4+ Styles]",
    "Format: Name, Fontname, Fontsize",
    "Style: Default,Microsoft YaHei,48",
    "",
    "[Events]",
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
    "Effect, Text",
    "Dialogue: 0,0:00:01.00,0:00:03.50,Default,,0,0,0,,你好，世界",
    "Dialogue: 0,0:00:04.00,0:00:05.00,Title,,0,0,0,,{\\i1}斜体{\\i0}",
    "Comment: 0,0:00:06.00,0:00:07.00,Default,,0,0,0,,不算数",
])
cues = p.parse_ass(ass_src)
check("ASS 只认 Dialogue 行", len(cues) == 2, cues)
check("ASS 文字里的逗号留着", cues[0]["text"] == "你好，世界", cues[0]["text"])
check("ASS 时间按百分秒算", (cues[0]["start"], cues[0]["end"]) == (1.0, 3.5),
      cues[0])
check("ASS 记下样式名", cues[1]["style"] == "Title", cues[1])
check("ASS 去掉花括号里的排版指令", cues[1]["text"] == "斜体", cues[1]["text"])

cues = p.parse_ass("\n".join([
    "[Events]",
    "Format: Layer, Start, End, Style, Text",
    "Dialogue: 0,0:00:01.00,0:00:02.00,Default,上一句\\N下一句",
]))
check("ASS 的 \\N 当换行", cues and cues[0]["text"] == "上一句\n下一句", cues)

try:
    p.parse_ass("[Script Info]\nTitle: x\n")
    ass_bad = ""
except p.SubtitleError as e:
    ass_bad = e.reason
check("ASS 没有 Dialogue 报 bad-format", ass_bad == "bad-format", ass_bad)

sub_dir = tempfile.mkdtemp(prefix="p2v_sub_t_")
srt_path = os.path.join(sub_dir, "a.srt")
with open(srt_path, "w", encoding="utf-8") as fh:
    fh.write(srt_src)
ass_path = os.path.join(sub_dir, "b.ass")
with open(ass_path, "w", encoding="utf-8") as fh:
    fh.write(ass_src)
check("按 .srt 扩展名读 SRT", len(p.load_subtitles(srt_path)) == 2)
check("按 .ass 扩展名读 ASS", len(p.load_subtitles(ass_path)) == 2)

try:
    p.load_subtitles(os.path.join(sub_dir, "nope.srt"))
    sub_missing = ""
except p.SubtitleError as e:
    sub_missing = e.reason
check("字幕文件不存在报 missing", sub_missing == "missing", sub_missing)

try:
    p.load_subtitles(os.path.join(sub_dir, "x.txt"))
    sub_ext = ""
except p.SubtitleError as e:
    sub_ext = e.reason
check("不认识的扩展名报 bad-format", sub_ext == "bad-format", sub_ext)

# ---------------- 47 字幕：生成烧录用 .ass

cues = [{"start": 1.0, "end": 3.5, "text": "第一行\n第二行", "style": ""},
         {"start": 3661.234, "end": 3662.0, "text": "a{b}c", "style": ""}]
ass = p.format_ass(cues, font="Microsoft YaHei", size=48, w=1280, h=720)
check("生成的 .ass 有脚本头", ass.startswith("[Script Info]") and
      "PlayResX: 1280" in ass and "PlayResY: 720" in ass, ass[:80])
check("样式里写了字体与字号",
      "Style: Default,Microsoft YaHei,48," in ass,
      [ln for ln in ass.split("\n") if ln.startswith("Style:")])
check("两条字幕两行 Dialogue",
      sum(1 for ln in ass.split("\n") if ln.startswith("Dialogue:")) == 2)
check("时间写成 时:分:秒.百分秒",
      "Dialogue: 0,0:00:01.00,0:00:03.50,Default,第一行\\N第二行" in ass,
      [ln for ln in ass.split("\n") if ln.startswith("Dialogue:")][0])
check("超过一小时也写得下",
      "0,1:01:01.23,1:01:02.00,Default," in ass,
      [ln for ln in ass.split("\n") if ln.startswith("Dialogue:")][1])
check("花括号转义，不会被当排版指令吃掉", "a\\{b\\}c" in ass,
      [ln for ln in ass.split("\n") if ln.startswith("Dialogue:")][1])
check("没有字幕就只有头，没有 Dialogue",
      "Dialogue:" not in p.format_ass([], font="Arial", size=40))

# ---------------- 48 字幕：滤镜串与挂到输出端

f = p.ass_filter("C:\\tmp\\a b.ass", "Microsoft YaHei", 48)
check("字幕滤镜：正斜杠 + 冒号转义 + 引号",
      f == "subtitles=filename='C\\:/tmp/a b.ass':force_style="
           "'FontName=Microsoft YaHei,FontSize=48'", f)
f2 = p.ass_filter("C:\\tmp\\a.ass", "思源黑体", 42, "C:\\Windows\\Fonts")
check("给了字体目录就带上 fontsdir",
      ":fontsdir='C\\:/Windows/Fonts'" in f2 and "FontName=思源黑体" in f2, f2)

pr = mk(2)
g = p.build_graph(pr.frame_counts(), pr.total(), clips=pr.clips, fps=30,
                  subtitle_file="C:\\tmp\\s.ass", subtitle_font="Arial",
                  subtitle_size=40)
out_chain = [s for s in g.split(";") if "[vout]" in s]
check("字幕只挂一次，并且挂在产出 vout 的那一环",
      g.count("subtitles=") == 1 and len(out_chain) == 1
      and "subtitles=" in out_chain[0], out_chain)
check("字幕接在色彩标记后面", ",subtitles=" in out_chain[0], out_chain[0][-120:])
g2 = p.build_graph(pr.frame_counts(), pr.total(), clips=pr.clips, fps=30)
check("没有字幕时不加滤镜", "subtitles=" not in g2)

# ---------------- 49 字幕：跟着工程文件走

pr = mk(3)
check("新工程没字幕", pr.subtitles_path == "" and pr.cues == [],
      (pr.subtitles_path, pr.cues))

p.set_subtitles(pr, "C:\\subs\\a.srt",
                [{"start": 0.5, "end": 2.0, "text": "你好", "style": ""},
                 {"start": 2.0, "end": 4.0, "text": "第二行\n第二句", "style": "Big"}])
doc = pr.to_doc()
check("工程文档里带字幕（路径 + 内容）",
      isinstance(doc.get("subtitles"), dict)
      and doc["subtitles"].get("path") == "C:\\subs\\a.srt"
      and len(doc["subtitles"].get("cues") or []) == 2, doc.get("subtitles"))
json.dumps(doc)
back = p.Project.from_doc(json.loads(json.dumps(doc)))
check("字幕随工程往返，风格与换行都在",
      back.subtitles_path == "C:\\subs\\a.srt" and len(back.cues) == 2
      and back.cues[1]["text"] == "第二行\n第二句"
      and back.cues[1]["style"] == "Big", back.cues)
check("老工程没有字幕这个键 → 空字幕",
      p.Project.from_doc({"version": 1, "clips": []}).cues == [])

bad = {"version": 1, "clips": [], "subtitles": [1, 2]}
try:
    p.Project.from_doc(bad)
    check("subtitles 不是对象 → bad-shape", False)
except p.ProjectFileError as e:
    check("subtitles 不是对象 → bad-shape", e.reason == "bad-shape", e.reason)

mixed = {"version": 1, "clips": [], "subtitles": {"path": "a.srt", "cues": [
    {"start": 0, "end": 1, "text": "ok"},
    {"start": 1, "end": 1, "text": "end 等于 start 丢掉"},
    {"start": 2, "end": 3},
    "不是对象",
]}}
pr2 = p.Project.from_doc(mixed)
check("坏条目丢掉、好条目留下",
      len(pr2.cues) == 1 and pr2.cues[0]["text"] == "ok", pr2.cues)

p.clear_subtitles(pr2)
check("移除字幕清空路径与内容",
      pr2.subtitles_path == "" and pr2.cues == [], pr2.subtitles_path)

pr3 = mk(2)
before = pr3.frame_counts(), pr3.total()
p.set_subtitles(pr3, "a.srt", [{"start": 0, "end": 99, "text": "跨满全片"}])
check("字幕不参与时长计算", (pr3.frame_counts(), pr3.total()) == before,
      (before, (pr3.frame_counts(), pr3.total())))

# ---------------- 50 字幕：设置默认值与界面接线

s0 = p.Settings.__new__(p.Settings)
s0.data = dict(p.Settings.DEFAULTS)
check("默认字幕字体与字号",
      p.Settings.DEFAULTS.get("subtitle_font") == "Microsoft YaHei"
      and p.Settings.DEFAULTS.get("subtitle_size") == 48,
      (p.Settings.DEFAULTS.get("subtitle_font"),
       p.Settings.DEFAULTS.get("subtitle_size")))
s1 = p.Settings.__new__(p.Settings)
s1.data = dict(p.Settings.DEFAULTS, subtitle_font="  思源黑体  ",
               subtitle_size=9999)
s1._sanitize()
check("字体名清洗后保留、字号夹到上限",
      s1.data["subtitle_font"] == "思源黑体" and s1.data["subtitle_size"] == 120,
      (s1.data.get("subtitle_font"), s1.data.get("subtitle_size")))
s2 = p.Settings.__new__(p.Settings)
s2.data = dict(p.Settings.DEFAULTS, subtitle_font="", subtitle_size=1)
s2._sanitize()
check("空字体回到默认、字号夹到下限",
      s2.data["subtitle_font"] == "Microsoft YaHei"
      and s2.data["subtitle_size"] == 16,
      (s2.data.get("subtitle_font"), s2.data.get("subtitle_size")))

i = src.index("        def _opts(self, preview=False):")
opts_src = src[i:i + 1200]
check("预览与导出都会把字幕交给渲染",
      "subtitles" in opts_src and "project.cues" in opts_src,
      opts_src[:300])
check("字幕字体字号取自设置",
      's["subtitle_font"]' in opts_src and 's["subtitle_size"]' in opts_src,
      opts_src[-260:])
check("界面能导入 / 移除字幕",
      "load_subtitles(" in src and "set_subtitles(" in src
      and "clear_subtitles(" in src)
check("左栏有字幕页", 'left_tabs.addTab(' in src and 'tr("字幕")' in src
      and "self.sub_tabs" in src or 'left_tabs.addTab(sub_page' in src)
check("导入/移除有各自的动作",
      "def on_import_subtitle" in src and "def on_clear_subtitle" in src)
check("导入后刷新字幕页并标记改动",
      "def _refresh_subtitles" in src and "_mark_dirty()" in src)
check("设置页能改字幕字体与字号",
      "字幕字体" in src and "字幕字号" in src
      and 'self.s.update(' in src
      and 'subtitle_font' in src.split('self.s.update(')[1][:4000])

# ---------------- 51 字幕字号按画面高度等比缩放

check("1080p 下字号就是设置值", p.subtitle_px(48, 1080) == 48,
      p.subtitle_px(48, 1080))
check("540p 预览字号减半", p.subtitle_px(48, 540) == 24,
      p.subtitle_px(48, 540))
check("720p 下 48 号约 32 像素", p.subtitle_px(48, 720) == 32,
      p.subtitle_px(48, 720))
check("再小的画面也不低于 8 像素", p.subtitle_px(16, 360) == 8,
      p.subtitle_px(16, 360))
check("字号是整数", isinstance(p.subtitle_px(48, 541), int),
      type(p.subtitle_px(48, 541)))
_j = src.index("        def _opts(self, preview=False):")
check("_opts 里按实际画面高度换算字号",
      "subtitle_px(" in src[_j:_j + 1200], src[_j:_j + 1200][-300:])

check("长 filtergraph 用当前 ffmpeg 认的文件参数",
      "[graph_file_option(), graph_file]" in src
      and p.graph_file_option() in ("-filter_complex_script",
                                    "-/filter_complex"))

# 52 命名转场：12 个内置名 / 逐段时长 / 塞不下要报错
check("内置转场 12 个", len(p.XFADE_NAMES) == 12 and p.XFADE_NAMES[0] == "fade"
      and len(set(p.XFADE_NAMES)) == 12, p.XFADE_NAMES)
check("每个转场都有中文名", all(n in p.XFADE_LABELS for n in p.XFADE_NAMES),
      [n for n in p.XFADE_NAMES if n not in p.XFADE_LABELS])
check("中文名不重样", len(set(p.XFADE_LABELS.values())) == 12,
      list(p.XFADE_LABELS.values()))
def _two(name="", dur=None):
    return [p.Clip(IMG[0], 2.0, xfade=True, xfade_name=name, xfade_dur=dur),
            p.Clip(IMG[1], 2.0)]
_bad_names = []
for _n in p.XFADE_NAMES:
    _cl = _two(_n)
    _c2, _ov = p._xfade_plan(_cl, [60, 60], 30, 0.3)
    _g = p.build_graph([60, 60], 4.0, 320, 180, fps=30, clips=_cl,
                       timeline=[(True, 2.0), (True, 2.0)])
    if ("xfade=transition=%s:" % _n) not in _g or not any(_ov):
        _bad_names.append(_n)
check("12 个转场都能进图", not _bad_names, _bad_names)
_cl = _two("wipeleft", 0.8)
_c2, _ov = p._xfade_plan(_cl, [60, 60], 30, 0.3)
check("逐段时长 0.8 秒盖过全片默认", _ov == [24] and _c2 == [72, 72], (_c2, _ov))
_g = p.build_graph([60, 60], 4.8, 320, 180, fps=30, clips=_cl,
                   timeline=[(True, 2.0), (True, 2.0)])
check("graph 里写逐段名字与时长", "xfade=transition=wipeleft:duration=0.800" in _g,
      _g[-160:])
check("转场错开量按覆盖算", "offset=1.600" in _g, _g[-160:])
check("前一段开的听前一段", p.boundary_xfade(
    [p.Clip(IMG[0], 2.0, xfade=True, xfade_name="wipeleft"),
     p.Clip(IMG[1], 2.0, xfade=True, xfade_name="circleopen")], 0, 0.3)[0]
      == "wipeleft")
check("后一段开的听后一段", p.boundary_xfade(
    [p.Clip(IMG[0], 2.0),
     p.Clip(IMG[1], 2.0, xfade=True, xfade_name="circleopen")], 0, 0.3)[0]
      == "circleopen")
check("都没开就是硬切", p.boundary_xfade(
    [p.Clip(IMG[0], 2.0), p.Clip(IMG[1], 2.0)], 0, 0.3) is None)
check("边界越界不炸", p.boundary_xfade([], 0, 0.3) is None
      and p.boundary_xfade([p.Clip(IMG[0], 2.0)], 0, 0.3) is None)
check("时长空着跟全局", p.boundary_xfade(
    [p.Clip(IMG[0], 2.0, xfade=True), p.Clip(IMG[1], 2.0)], 0, 0.7)[1] == 0.7)
_c = p.Clip(IMG[0], 2.0, xfade=True, xfade_name="nope")
check("认不出的名字回落 fade", _c.xfade_name == ""
      and p.boundary_xfade([_c, p.Clip(IMG[1], 2.0)], 0, 0.3)[0] == "fade")
check("离谱时长夹到上限", p.boundary_xfade(
    [p.Clip(IMG[0], 2.0, xfade=True, xfade_dur=99.0), p.Clip(IMG[1], 2.0)],
    0, 0.3)[1] == p.XFADE_MAX)
_short = p.xfade_short(_two("fade", 0.3), [12, 12], 30, 0.3)
check("塞不下要报出来", len(_short) == 1 and _short[0]["at"] == 0
      and abs(_short[0]["need"] - 0.6) < 1e-9, _short)
check("够长就不报", p.xfade_short(_two("fade", 0.3), [60, 60], 30, 0.3) == [])
_txt = p.xfade_error_text(_short)
check("提示里带段号与秒数", "第 1 段和第 2 段" in _txt and "0.40 / 0.40" in _txt
      and "0.60" in _txt, _txt)
_short_out = os.path.join(tmp, "short_xfade.mp4")
_raised = False
try:
    p.render(_two("fade", 0.3), [12, 12], None, _short_out, w=320, h=180, fps=30,
             xfade_sec=0.3)
except p.RenderError as exc:
    _raised = "转场" in str(exc)
check("塞不下就不出片", _raised and not os.path.exists(_short_out), _raised)
_pr = p.Project()
_pr.add_images([IMG[0], IMG[1]])
_pr.clips[0].xfade = True
_pr.clips[0].xfade_name = "radial"
_pr.clips[0].xfade_dur = 0.5
_doc = _pr.to_doc()
check("工程里存了转场名与时长", _doc["clips"][0]["xfade_name"] == "radial"
      and _doc["clips"][0]["xfade_dur"] == 0.5, _doc["clips"][0])
_back = p.Project.from_doc(json.loads(json.dumps(_doc)))
check("转场能原样读回", _back.clips[0].xfade is True
      and _back.clips[0].xfade_name == "radial"
      and abs(_back.clips[0].xfade_dur - 0.5) < 1e-9,
      (_back.clips[0].xfade_name, _back.clips[0].xfade_dur))
check("硬切段存空名与空时长", _doc["clips"][1]["xfade_name"] == ""
      and _doc["clips"][1]["xfade_dur"] is None, _doc["clips"][1])
_oldp = p.Project.from_doc({"version": 1, "clips": [{"path": IMG[0], "dur": 2.0}]})
check("老工程缺字段 = 空名/None", _oldp.clips[0].xfade_name == ""
      and _oldp.clips[0].xfade_dur is None and _oldp.clips[0].xfade is False)
check("读回时转场时长夹在范围内", p.Project.from_doc(
    {"version": 1, "clips": [{"path": IMG[0], "dur": 2.0, "xfade_dur": 99.0}]
     }).clips[0].xfade_dur == p.XFADE_MAX)
check("右键里有转场与时长子菜单", 'xm = m.addMenu(tr("转场"))' in _src
      and 'dm = xm.addMenu(tr("转场时长"))' in _src)
check("转场菜单列内置名字并给硬切", "for name in XFADE_NAMES:" in _src
      and 'on_set_transition(t, "")' in _src and 'tr("无（硬切）")' in _src)
check("改转场当场查时长", "def _check_transitions" in _src
      and 'MsgBox.warn(self, tr("转场放不下")' in _src)
_i18n = {}
try:
    import i18n as _I18N
    _i18n = {k: _I18N.TR[k] for k in ("zh-TW", "en", "ja", "ko")}
except Exception as exc:      # noqa: BLE001
    print("  (i18n 载入失败：%s)" % exc)
_keys30 = (list(p.XFADE_LABELS.values())
           + ["转场", "转场时长", "无（硬切）", "跟随设置", "{n} 秒", "转场放不下"])
_miss = [(lang, k) for lang, tbl in _i18n.items() for k in _keys30 if k not in tbl]
check("转场词条四语齐全", bool(_i18n) and not _miss, _miss[:5])
if p.missing_tools():
    print("  (没 ffmpeg，跳过转场名实测)")
else:
    _probe = subprocess.run([p.FFMPEG, "-hide_banner", "-h", "filter=xfade"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    _unknown = [n for n in p.XFADE_NAMES if n not in _probe.stdout]
    check("ffmpeg 认这 12 个名字", not _unknown, (_unknown, _probe.stderr[-120:]))

# ---------------- 53 导出预设与封面
check("导出预设正好 5 项", len(p.EXPORT_PRESETS) == 5, list(p.EXPORT_PRESETS))
check("预设顺序表覆盖全部", set(p.EXPORT_PRESET_ORDER) == set(p.EXPORT_PRESETS)
      and len(p.EXPORT_PRESET_ORDER) == 5)
check("每项都有名字和后缀字段",
      all({"label", "w", "h", "fps", "crf", "ab", "suffix"} <= set(v)
          for v in p.EXPORT_PRESETS.values()))
check("通用档不含任何固定值", all(p.EXPORT_PRESETS["generic"][k] is None
                           for k in ("w", "h", "fps", "crf", "ab")))
_plat = [k for k in p.EXPORT_PRESETS if k != "generic"]
check("平台档都有后缀", all(p.EXPORT_PRESETS[k]["suffix"] for k in _plat))
check("宽高要么都有、要么都没有",
      all((p.EXPORT_PRESETS[k]["w"] is None)
          == (p.EXPORT_PRESETS[k]["h"] is None) for k in p.EXPORT_PRESETS))
_odd = []
for _k in _plat:
    _spec = p.EXPORT_PRESETS[_k]
    if _spec["w"] % 2 or _spec["h"] % 2:
        _odd.append((_k, "尺寸不是偶数", _spec["w"], _spec["h"]))
    if _spec["fps"] not in p.EXPORT_FPS:
        _odd.append((_k, "帧率", _spec["fps"]))
    if not 0 <= _spec["crf"] <= 51:
        _odd.append((_k, "crf", _spec["crf"]))
    if not (isinstance(_spec["ab"], str) and _spec["ab"].endswith("k")
            and _spec["ab"][:-1].isdigit()):
        _odd.append((_k, "音频码率", _spec["ab"]))
check("预设表自洽（偶数尺寸 / 帧率 / CRF / 码率）", not _odd, _odd)
check("预设音频码率都在下拉选项里",
      all(p.EXPORT_PRESETS[k]["ab"] in p.EXPORT_AUDIO_RATES for k in _plat))

_base = dict(w=1280, h=720, fps=30, crf=23, ab="160k")
_plan_b = p.export_plan("bilibili", _base)
check("B 站预设：1080p60 / CRF18 / 192k",
      (_plan_b["w"], _plan_b["h"], _plan_b["fps"], _plan_b["crf"],
       _plan_b["ab"]) == (1920, 1080, 60, 18, "192k"), _plan_b)
check("B 站预设带文件名后缀", _plan_b["suffix"] == "_bilibili", _plan_b["suffix"])
_plan_d = p.export_plan("douyin", _base)
check("抖音预设：竖屏 1080×1920@30", (_plan_d["w"], _plan_d["h"],
                                  _plan_d["fps"]) == (1080, 1920, 30), _plan_d)
_plan_g = p.export_plan("generic", _base)
check("通用档沿用设置里的值", (_plan_g["w"], _plan_g["h"], _plan_g["fps"],
                        _plan_g["crf"], _plan_g["ab"], _plan_g["suffix"])
      == (1280, 720, 30, 23, "160k", ""), _plan_g)
_plan_n = p.export_plan("没这个预设", _base)
check("不认识的预设退回通用", _plan_n["preset"] == "generic"
      and (_plan_n["w"], _plan_n["h"]) == (1280, 720), _plan_n)
_plan_e = p.export_plan("generic", dict(w=1281, h=721))
check("宽高自动取偶数", (_plan_e["w"] % 2, _plan_e["h"] % 2) == (0, 0), _plan_e)
_plan_dflt = p.export_plan("generic")
check("不给基准值就用默认", (_plan_dflt["w"], _plan_dflt["h"]) == (1920, 1080)
      and _plan_dflt["fps"] in p.EXPORT_FPS, _plan_dflt)

check("封面时间夹在 [0,0]（没时长就是 0）", p.clamp_cover_time(5, 0) == 0.0)
check("封面时间为负取 0", p.clamp_cover_time(-3, 10) == 0.0)
check("封面时间超过总长取总长", p.clamp_cover_time(99, 10) == 10.0)
check("封面时间在范围内原样保留", p.clamp_cover_time(4.2, 10) == 4.2)
check("封面时间坏值当 0", p.clamp_cover_time("abc", 10) == 0.0)
check("封面时间 NaN 当 0", p.clamp_cover_time(float("nan"), 10) == 0.0)

check("帧率没变就不用换算", p.rescale_counts([60, 60], 30, 30) == [60, 60])
check("30 换 60 帧数翻倍", p.rescale_counts([90, 30], 30, 60) == [180, 60])
check("60 换 30 帧数减半", p.rescale_counts([180, 60], 60, 30) == [90, 30])
check("空时间线还是空的", p.rescale_counts([], 30, 60) == [])
check("极短的段至少留一帧", p.rescale_counts([1], 60, 30) == [1])
_nc = p.rescale_counts([90, 90], 30, 60)
check("换帧率后总秒数不变",
      abs(sum(_nc) / 60.0 - 180 / 30.0) < 0.05, (sum(_nc) / 60.0))
check("导出时按预设帧率换算帧数",
      "rescale_counts(self.project.frame_counts()" in _src)

_cimg = [p.Clip(IMG[0], 3.0, "image")]
check("封面默认取第一张图正中", p.cover_default_time(_cimg, [90], 30) == 1.5,
      p.cover_default_time(_cimg, [90], 30))
_c2 = [p.Clip(IMG[0], 2.0, "image"), p.Clip(IMG[1], 2.0, "image")]
check("两张图：封面落第一张正中", p.cover_default_time(_c2, [60, 60], 30) == 1.0,
      p.cover_default_time(_c2, [60, 60], 30))
_c2x = [p.Clip(IMG[0], 2.0, "image", xfade=True, xfade_name="fade"),
        p.Clip(IMG[1], 2.0, "image", xfade=True, xfade_name="fade")]
check("有转场时按挤短的段算中点",
      abs(p.cover_default_time(_c2x, [60, 60], 30, 0.6) - 0.85) < 1e-6,
      p.cover_default_time(_c2x, [60, 60], 30, 0.6))
_cintro = [p.Clip(IMG[0], 5.0, "intro"), p.Clip(IMG[1], 2.0, "image")]
check("有片头时跳过片头取图片段中点",
      abs(p.cover_default_time(_cintro, [150, 60], 30) - 6.0) < 1e-6,
      p.cover_default_time(_cintro, [150, 60], 30))
check("没有素材返回 0", p.cover_default_time([], [], 30) == 0.0)

check("码率估算是个正数", p.estimate_kbps(_plan_b) > 0, p.estimate_kbps(_plan_b))
check("CRF 越小估的码率越高",
      p.estimate_kbps(dict(_plan_b, crf=18))
      > p.estimate_kbps(dict(_plan_b, crf=28)))
check("分辨率越大估的码率越高",
      p.estimate_kbps(dict(_plan_b, w=3840, h=2160))
      > p.estimate_kbps(dict(_plan_b, w=1280, h=720)))

_pj = p.Project()
_pj.clips = [p.Clip(IMG[0], 3.0)]
_pj.export_preset = "douyin"
_doc_p = _pj.to_doc()
check("工程里存了导出预设", _doc_p.get("export_preset") == "douyin", _doc_p.get("export_preset"))
check("预设能原样读回", p.Project.from_doc(_doc_p).export_preset == "douyin")
_old_pj = p.Project.from_doc({"version": 1, "clips": []})
check("老工程缺预设 = 通用", _old_pj.export_preset == "generic")
check("不认识的预设读回算通用", p.Project.from_doc(
    {"version": 1, "clips": [], "export_preset": "nope"}).export_preset == "generic")
check("预设能过 JSON", json.loads(json.dumps(_doc_p))["export_preset"] == "douyin")

_st = p.Settings(os.path.join(tmp, "settings_defaults.json"))
check("设置默认记住通用预设", _st["export_preset"] == "generic")
check("设置默认不导封面", _st["cover_export"] is False)
check("设置默认封面时间自动", _st["cover_at"] == -1.0)
_st.data["export_preset"] = "taobao"
_st.data["cover_export"] = "yes"
_st.data["cover_at"] = "??? "
_st._sanitize()
check("坏预设洗回通用", _st["export_preset"] == "generic")
check("坏封面开关洗成 False", _st["cover_export"] is False)
check("坏封面时间洗成自动", _st["cover_at"] == -1.0)
_st.data["cover_at"] = 3.5
_st._sanitize()
check("封面时间原样保留", _st["cover_at"] == 3.5)

check("导出框是个对话框类", "class ExportDialog(FramelessDialog)" in _src)
check("导出走预设合并", "ExportDialog.get_plan(" in _src
      and 'plan["suffix"]' in _src and 'opts.update(w=plan["w"]' in _src)
check("封面在导出成功后截", "def _write_cover" in _src
      and "grab_cover(video_path, out" in _src)
check("render 能改音频码率", 'ab="192k"' in _src
      and '"-b:a", (ab if isinstance(ab, str) and ab else "192k")' in _src)
check("记住上次预设与封面开关", '"export_preset"] = plan["preset"]' in _src
      and 'self.settings["cover_export"] = bool(plan.get("cover"))' in _src)

_keys31 = (list(p.EXPORT_PRESETS[k]["label"] for k in p.EXPORT_PRESETS)
           + ["导出", "自定义", "平台预设", "分辨率", "帧率", "画质（CRF）",
              "音频码率", "同时导出封面（同名 .jpg）", "封面时间点",
              "封面没截出来", "封面截不出来：{msg}", "成片不在，封面截不出来",
              "（封面：{name}）"])
_miss31 = [(lang, k) for lang, tbl in _i18n.items()
           for k in _keys31 if k not in tbl]
check("导出预设词条四语齐全", bool(_i18n) and not _miss31, _miss31[:5])

if p.missing_tools():
    print("  (没 ffmpeg，跳过封面与音频码率实测)")
else:
    _out_ab = os.path.join(tmp, "ab_96k.mp4")
    p.render([p.Clip(IMG[0], 1.0)], [30], TONE, _out_ab, w=320, h=180,
             fps=30, crf=30, ab="96k")
    _pr = subprocess.run(
        [p.FFPROBE, "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=bit_rate", "-of", "csv=p=0", _out_ab],
        capture_output=True, text=True)
    try:
        _ab_real = int((_pr.stdout or "0").strip().split(",")[0])
    except ValueError:
        _ab_real = 0
    check("音频码率按预设落进成片", 80000 <= _ab_real <= 120000,
          (_pr.stdout, _pr.stderr[-160:]))
    _cover_out = os.path.join(tmp, "cover.jpg")
    p.grab_cover(_out_ab, _cover_out, 0.2, 1.0)
    check("封面图生成了", os.path.exists(_cover_out)
          and os.path.getsize(_cover_out) > 500,
          os.path.getsize(_cover_out) if os.path.exists(_cover_out) else -1)
    _pc = subprocess.run(
        [p.FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", _cover_out],
        capture_output=True, text=True)
    check("封面就是成片那一帧的尺寸", "320,180" in (_pc.stdout or ""),
          _pc.stdout.strip())

try:
    p.grab_cover(os.path.join(tmp, "nope.mp4"),
                 os.path.join(tmp, "nope.jpg"), 0.0)
    check("成片不在时封面要报错", False)
except p.RenderError as _exc:
    check("成片不在时封面要报错", "成片不在" in str(_exc), str(_exc))

# ---------------- 54 画中画：工程格式（叠加层） ----------------
_ov0 = p.Project()
check("工程格式版本升到 2", _ov0.to_doc()["version"] == 2)
check("新工程没有叠加层", _ov0.to_doc().get("overlays") == [])

_ov0.overlays.append(p.Overlay(IMG[0], 1.0, 4.0))
_doc_ov = _ov0.to_doc()
check("叠加层写进工程", len(_doc_ov["overlays"]) == 1)
_ov_raw = _doc_ov["overlays"][0]
check("叠加层字段齐全",
      set(_ov_raw) == {"path", "start", "end", "scale", "anchor",
                       "dx", "dy", "opacity"}, sorted(_ov_raw))
check("叠加层默认右上角铺四成",
      _ov_raw["anchor"] == "tr" and _ov_raw["scale"] == 40.0
      and _ov_raw["opacity"] == 100.0 and _ov_raw["dx"] == 0.0
      and _ov_raw["dy"] == 0.0, _ov_raw)
check("叠加层能过 JSON", json.loads(json.dumps(_doc_ov))["overlays"][0]["end"] == 4.0)

_ov0.overlays[0].anchor = "bl"
_ov0.overlays[0].scale = 55.0
_ov0.overlays[0].dx = 12.5
_ov0.overlays[0].dy = -8.0
_ov0.overlays[0].opacity = 70.0
_ov_back = p.Project.from_doc(_ov0.to_doc())
check("叠加层原样读回", len(_ov_back.overlays) == 1)
_b0 = _ov_back.overlays[0]
check("叠加层读回每个字段",
      (_b0.path, _b0.start, _b0.end, _b0.scale, _b0.anchor,
       _b0.dx, _b0.dy, _b0.opacity)
      == (IMG[0], 1.0, 4.0, 55.0, "bl", 12.5, -8.0, 70.0),
      (_b0.path, _b0.start, _b0.end, _b0.scale, _b0.anchor,
       _b0.dx, _b0.dy, _b0.opacity))
check("叠加层长度就是进出之差", _b0.span == 3.0, _b0.span)

_old_ov = p.Project.from_doc({"version": 1, "clips": []})
check("老工程没有叠加层也能读", _old_ov.overlays == [])
_bad_ov = p.Project.from_doc({"version": 2, "clips": [], "overlays": [
    {"path": IMG[1], "start": 0, "end": 2},
    {"path": ""},
    {"start": 0, "end": 2},
    "nope"]})
check("坏叠加条目丢掉、好条目留下",
      len(_bad_ov.overlays) == 1 and _bad_ov.overlays[0].path == IMG[1])
_ov_bad_shape = False
try:
    p.Project.from_doc({"version": 2, "clips": [], "overlays": {}})
except p.ProjectFileError as _e:
    _ov_bad_shape = _e.reason == "bad-shape"
check("叠加层不是列表算坏工程", _ov_bad_shape)

_crazy = p.Overlay(IMG[0], -5.0, -1.0, scale=900.0, anchor="middle",
                   dx=-300.0, dy=300.0, opacity=180.0)
check("叠加层起点不早于 0", _crazy.start == 0.0, _crazy.start)
check("叠加层至少留一点时间", _crazy.end > _crazy.start, _crazy.end)
check("缩放夹在 5..100", _crazy.scale == 100.0, _crazy.scale)
check("认不出的位置退回右上", _crazy.anchor == "tr", _crazy.anchor)
check("偏移夹在 -100..100", (_crazy.dx, _crazy.dy) == (-100.0, 100.0),
      (_crazy.dx, _crazy.dy))
check("不透明度夹在 0..100", _crazy.opacity == 100.0, _crazy.opacity)

_ov_missing = p.Project()
_ov_missing.clips = [p.Clip(IMG[0], 2.0)]
_ov_missing.overlays = [p.Overlay(os.path.join(tmp, "gone_overlay.png"),
                                  0.0, 1.0)]
check("缺的叠加素材也进清单", len(_ov_missing.missing_media()) == 1,
      _ov_missing.missing_media())
check("叠加素材能重新定位", _ov_missing.relink_media(
    os.path.join(tmp, "gone_overlay.png"), IMG[0]) == 1
      and _ov_missing.overlays[0].path == IMG[0])

# ---------------- 55 画中画：滤镜串 ----------------
_ovs = [p.Overlay(IMG[0], 1.0, 4.0, scale=40.0, anchor="tr"),
        p.Overlay(IMG[1], 2.0, 8.0, scale=25.0, anchor="bl", dx=10.0,
                  dy=-5.0, opacity=50.0)]
_specs = p.overlay_specs(_ovs, 7, total=10.0)
check("叠加规格条数对", len(_specs) == 2, _specs)
check("叠加输入下标从视频输入数开始",
      [s["index"] for s in _specs] == [7, 8], [s["index"] for s in _specs])
check("右上角锚点换算成 1,0",
      (_specs[0]["ax"], _specs[0]["ay"]) == (1.0, 0.0),
      (_specs[0]["ax"], _specs[0]["ay"]))
check("左下角锚点换算成 0,1",
      (_specs[1]["ax"], _specs[1]["ay"]) == (0.0, 1.0))
check("叠加规格保留进出时间与不透明度",
      (_specs[0]["start"], _specs[0]["end"], _specs[0]["opacity"])
      == (1.0, 4.0, 100.0)
      and (_specs[1]["start"], _specs[1]["end"], _specs[1]["opacity"])
      == (2.0, 8.0, 50.0))
check("叠加超出成片长度的部分被截掉",
      p.overlay_specs([p.Overlay(IMG[0], 9.0, 15.0)], 0, total=10.0)[0]["end"]
      == 10.0)
check("整段都在成片之外的叠加不出现",
      p.overlay_specs([p.Overlay(IMG[0], 12.0, 15.0)], 0, total=10.0) == [])

_gov = p.build_graph([60, 60], 4.0, 1280, 720, fps=30,
                     clips=[p.Clip(IMG[0], 2.0), p.Clip(IMG[1], 2.0)],
                     overlays=_specs)
_ov_line = [ln for ln in _gov.split(";") if "overlay=x=" in ln]
check("叠加层接进滤镜串", len(_ov_line) == 2, len(_ov_line))
check("叠加层用成片宽度四成铺开", "scale=512:-2" in _gov, _gov[:80])
check("缩放后面接 rgba 与透明度",
      "format=rgba,colorchannelmixer=aa=1.000" in _gov
      and "colorchannelmixer=aa=0.500" in _gov)
check("叠加层出现在窗口里",
      "enable='between(t,1.000,4.000)'" in _gov
      and "enable='between(t,2.000,8.000)'" in _gov)
check("右上角贴在右下角表达式上",
      "x=(W-w)*1.000+0.0" in _gov and "y=(H-h)*0.000+0.0" in _gov)
check("偏移按成片百分比加进坐标",
      "x=(W-w)*0.000+128.0" in _gov and "y=(H-h)*1.000-36.0" in _gov)
check("叠加输入按起点挪到时间轴上", "setpts=PTS-STARTPTS+1.000/TB" in _gov)
_vout_line = [ln for ln in _gov.split(";") if "[vout]" in ln][0]
check("叠加在字幕之前、vout 最后一环",
      "overlay=x=" not in _vout_line and "[o1]" in _vout_line, _vout_line)
_gno = p.build_graph([60], 2.0, 1280, 720, fps=30,
                     clips=[p.Clip(IMG[0], 2.0)])
check("没有叠加就不出现 mixerkw", "colorchannelmixer" not in _gno)

# ---------------- 56 画中画：真渲染 ----------------
def _frame_rgb(path, at, w=320, h=180):
    """抽一帧出来当 RGB 字节，方便比某个像素。"""
    pr = subprocess.run(
        [p.FFMPEG, "-v", "error", "-ss", "%.3f" % at, "-i", path,
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True)
    if pr.returncode != 0 or len(pr.stdout) < w * h * 3:
        return None
    return pr.stdout


def _pixel(buf, w, x, y):
    i = (y * w + x) * 3
    return tuple(buf[i:i + 3])


def _close(a, b, tol=10):
    return a is not None and b is not None and all(
        abs(x - y) <= tol for x, y in zip(a, b))


if p.missing_tools():
    print("  (没 ffmpeg，跳过画中画实测)")
else:
    _base_out = os.path.join(tmp, "ov_base.mp4")
    p.render([p.Clip(IMG[0], 3.0)], [90], TONE, _base_out, w=320, h=180,
             fps=30, crf=28)
    _pip_out = os.path.join(tmp, "ov_pip.mp4")
    p.render([p.Clip(IMG[0], 3.0)], [90], TONE, _pip_out, w=320, h=180,
             fps=30, crf=28,
             overlays=[p.Overlay(IMG[1], 0.5, 2.0, scale=40.0, anchor="tr")])
    _fb = _frame_rgb(_base_out, 1.0)
    _fp = _frame_rgb(_pip_out, 1.0)
    _px = (256, 36)          # 右上角那块（x = 80% 宽，y = 20% 高）
    check("画中画真的压在右上角",
          not _close(_pixel(_fb, 320, *_px), _pixel(_fp, 320, *_px)),
          (_pixel(_fb, 320, *_px), _pixel(_fp, 320, *_px)))
    check("画中画只盖住一角，别处不动",
          _close(_pixel(_fb, 320, 20, 160), _pixel(_fp, 320, 20, 160)),
          (_pixel(_fb, 320, 20, 160), _pixel(_fp, 320, 20, 160)))
    _fb2 = _frame_rgb(_base_out, 2.6)
    _fp2 = _frame_rgb(_pip_out, 2.6)
    check("过了退出时间画中画就消失",
          _close(_pixel(_fb2, 320, *_px), _pixel(_fp2, 320, *_px)),
          (_pixel(_fb2, 320, *_px), _pixel(_fp2, 320, *_px)))

    _clear_out = os.path.join(tmp, "ov_clear.mp4")
    p.render([p.Clip(IMG[0], 3.0)], [90], TONE, _clear_out, w=320, h=180,
             fps=30, crf=28,
             overlays=[p.Overlay(IMG[1], 0.5, 2.0, scale=40.0, anchor="tr",
                                 opacity=0.0)])
    _fc = _frame_rgb(_clear_out, 1.0)
    check("不透明度 0 等于看不见",
          _close(_pixel(_fb, 320, *_px), _pixel(_fc, 320, *_px)),
          (_pixel(_fb, 320, *_px), _pixel(_fc, 320, *_px)))

    _dur_pip = subprocess.run(
        [p.FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", _pip_out], capture_output=True, text=True)
    check("画中画不改成片总长",
          abs(float((_dur_pip.stdout or "0").strip() or 0) - 3.0) < 0.15,
          _dur_pip.stdout.strip())

    try:
        p.render([p.Clip(IMG[0], 2.0)], [60], TONE,
                 os.path.join(tmp, "ov_missing.mp4"), w=320, h=180, fps=30,
                 overlays=[p.Overlay(os.path.join(tmp, "nope_pip.png"),
                                     0.0, 1.0)])
        check("叠加素材不在要报错", False)
    except p.RenderError as _e:
        check("叠加素材不在要报错", "叠加" in str(_e), str(_e))

# ---------------- 57 画中画：界面接线与词条
_src57 = open(os.path.join(ROOT, "pic2video.py"), encoding="utf-8").read()
check("时间线有画中画轨",
      "def _overlay_rect" in _src57 and "def _overlay_spans" in _src57)
check("时间线能命中画中画", "def _hit_overlay" in _src57)
check("时间线能拖画中画", '"overlay", k, x, ov.start' in _src57)
check("时间线会报告画中画移动",
      "overlayMoved = pyqtSignal(int, float)" in _src57
      and "self.overlayMoved.emit(idx" in _src57)
check("右键画中画有菜单",
      '"overlay_empty"' in _src57 and "加画中画…" in _src57
      and "编辑画中画…" in _src57 and "删除画中画" in _src57)
check("窗口有画中画按钮", "self.btn_overlay = btn(" in _src57)
check("窗口有加/改/删画中画", all(k in _src57 for k in (
    "def on_add_overlay", "def on_edit_overlay", "def on_remove_overlay",
    "def on_overlay_moved")))
check("窗口接了移动信号",
      "self.timeline.overlayMoved.connect(self.on_overlay_moved)" in _src57)
check("预览与导出都带 overlays",
      "overlays=[o.copy() for o in self.project.overlays]" in _src57)
check("有画中画对话框",
      "class OverlayDialog(FramelessDialog)" in _src57
      and "OverlayDialog.get_overlay(" in _src57)
check("对话框有九宫格与不透明度",
      "OVERLAY_ANCHOR_LABELS" in _src57 and "self.spin_opacity" in _src57)
check("状态栏报画中画层数", "（{n} 层画中画）" in _src57)
for _name in ("画中画", "九宫格位置", "不透明度", "出现时间", "消失时间",
              "水平偏移", "垂直偏移", "缩放", "换素材…", "加画中画…"):
    check("画中画词条四语：" + _name,
          bool(_i18n) and all(_name in tbl for tbl in _i18n.values()))
for _anchor, _lab in (("tl", "左上"), ("tc", "中上"), ("tr", "右上"),
                      ("ml", "左中"), ("mc", "正中"), ("mr", "右中"),
                      ("bl", "左下"), ("bc", "中下"), ("br", "右下")):
    check("九宫格词条：" + _anchor,
          p.OVERLAY_ANCHOR_LABELS.get(_anchor) == _lab
          and all(_lab in tbl for tbl in _i18n.values()))

# ---------------- 58 调色：参数、滤镜串、工程、真渲染、界面 ----------------
_real_lut = os.path.join(tmp, "swap_rb.cube")
_cube_rows = []          # .cube 的行序：红变得最快
for _b in (0.0, 1.0):
    for _g in (0.0, 1.0):
        for _r in (0.0, 1.0):
            _cube_rows.append("%.1f %.1f %.1f" % (_b, _g, _r))
with open(_real_lut, "w", encoding="utf-8") as _fh:
    _fh.write("TITLE \"swap r b\"\nLUT_3D_SIZE 2\n" + "\n".join(_cube_rows) + "\n")

_c58 = p.Clip(IMG[0], 1.0)
check("调色默认等于没调过",
      (_c58.brightness, _c58.contrast, _c58.saturation, _c58.lut)
      == (0.0, 1.0, 1.0, "") and not _c58.has_color)
_c58b = p.Clip(IMG[0], 1.0, brightness=5, contrast=-2, saturation=9)
check("调色参数夹在安全范围里",
      (_c58b.brightness, _c58b.contrast, _c58b.saturation) == (1.0, 0.0, 3.0),
      (_c58b.brightness, _c58b.contrast, _c58b.saturation))
check("调色参数读不出来就用默认值",
      p.Clip(IMG[0], 1.0, brightness=None, contrast="x",
             saturation=float("nan")).brightness == 0.0
      and p.Clip(IMG[0], 1.0, contrast="x").contrast == 1.0)
check("改过色就认得出来",
      p.Clip(IMG[0], 1.0, brightness=0.01).has_color
      and p.Clip(IMG[0], 1.0, lut=_real_lut).has_color
      and not p.Clip(IMG[0], 1.0, contrast=1.0).has_color)
_c58c = p.Clip(IMG[0], 2.0, brightness=0.3, contrast=1.2, saturation=0.8,
               lut=_real_lut)
_c58d = _c58c.copy()
check("复制片段带着调色参数",
      (_c58d.brightness, _c58d.contrast, _c58d.saturation, _c58d.lut)
      == (0.3, 1.2, 0.8, _real_lut))

check("没调过色就不往 graph 里塞滤镜", p.color_filter(0.0, 1.0, 1.0) == "")
check("调色先出 eq", p.color_filter(0.2, 1.0, 1.0)
      == ",eq=brightness=0.200:contrast=1.000:saturation=1.000")
_c58e = p.color_filter(0.0, 1.0, 1.0, "C:\\luts\\my film.cube")
check("LUT 用 file= 且路径转义", "lut3d=file='C\\:/luts/my film.cube'" in _c58e,
      _c58e)
_c58f = p.color_filter(0.1, 1.1, 1.1, _real_lut)
check("eq 在 lut3d 前面",
      _c58f.index("eq=") < _c58f.index("lut3d=")
      and _c58f.startswith(",eq="), _c58f)

_g58 = p.build_graph([90], 3.0, 320, 180, fps=30,
                     clips=[p.Clip(IMG[0], 3.0, brightness=0.3,
                                   lut=_real_lut)])
check("build_graph 里带上 eq 与 lut3d",
      "eq=brightness=0.300" in _g58 and "lut3d=file=" in _g58)
_g58b = p.build_graph([90], 3.0, 320, 180, fps=30, clips=[p.Clip(IMG[0], 3.0)])
check("没调色的段 graph 里没有 eq/lut3d",
      "lut3d=" not in _g58b and "saturation=" not in _g58b)
_g58c = p.build_graph([90, 90], 6.0, 320, 180, fps=30,
                      clips=[p.Clip(IMG[0], 3.0, saturation=1.5),
                             p.Clip(IMG[1], 3.0)])
_eq_seg = [s for s in _g58c.split(";") if s.startswith("[s0]")]
check("调色只挂在改过的那一段上",
      len(_eq_seg) == 1 and "saturation=1.500" in _eq_seg[0]
      and _g58c.count("saturation=") == 1, _eq_seg)

_pj58 = p.Project()
_pj58.clips = [p.Clip(IMG[0], 2.0, brightness=0.25, contrast=1.4,
                      saturation=0.5, lut=_real_lut)]
_doc58 = _pj58.to_doc()
_c0 = _doc58["clips"][0]
check("工程存了每段的调色参数",
      (_c0.get("brightness"), _c0.get("contrast"), _c0.get("saturation"),
       _c0.get("lut")) == (0.25, 1.4, 0.5, _real_lut), _c0)
_pj58b = p.Project.from_doc(_doc58)
check("工程读回来调色不丢",
      (_pj58b.clips[0].brightness, _pj58b.clips[0].contrast,
       _pj58b.clips[0].saturation, _pj58b.clips[0].lut)
      == (0.25, 1.4, 0.5, _real_lut))
check("调色能 json 化", isinstance(json.dumps(_doc58, ensure_ascii=False), str))
_old58 = {"version": 1, "clips": [{"path": IMG[0], "dur": 2.0,
                                  "kind": "image"}], "audios": []}
_pj58c = p.Project.from_doc(_old58)
check("老工程没调色字段就是默认值",
      (_pj58c.clips[0].brightness, _pj58c.clips[0].contrast,
       _pj58c.clips[0].saturation, _pj58c.clips[0].lut) == (0.0, 1.0, 1.0, ""))
_pj58d = p.Project()
_pj58d.clips = [p.Clip(IMG[0], 2.0, lut=os.path.join(tmp, "gone.cube"))]
check("LUT 不在就算缺素材",
      os.path.join(tmp, "gone.cube") in _pj58d.missing_media(),
      _pj58d.missing_media())
check("重新定位能把 LUT 换过去",
      _pj58d.relink_media(os.path.join(tmp, "gone.cube"), _real_lut) == 1
      and _pj58d.clips[0].lut == _real_lut)
check("LUT 目录里的 .cube 能列出来",
      p.list_luts(tmp) == [_real_lut] and p.list_luts("") == []
      and p.list_luts(os.path.join(tmp, "nope")) == [], p.list_luts(tmp))

if p.missing_tools():
    print("  (没 ffmpeg，跳过调色实测)")
else:
    _base58 = os.path.join(tmp, "color_base.mp4")
    p.render([p.Clip(IMG[0], 2.0)], [60], TONE, _base58, w=320, h=180,
             fps=30, crf=28)
    _lut58 = os.path.join(tmp, "color_lut.mp4")
    p.render([p.Clip(IMG[0], 2.0, lut=_real_lut)], [60], TONE, _lut58,
             w=320, h=180, fps=30, crf=28)
    _bright58 = os.path.join(tmp, "color_bright.mp4")
    p.render([p.Clip(IMG[0], 2.0, brightness=0.5)], [60], TONE, _bright58,
             w=320, h=180, fps=30, crf=28)
    _f0 = _frame_rgb(_base58, 0.2)
    _f1 = _frame_rgb(_lut58, 0.2)
    _px0 = _pixel(_f0, 320, 160, 90)
    _px1 = _pixel(_f1, 320, 160, 90)
    check("套了换红蓝的 LUT，画面颜色真的变了",
          not _close(_px0, _px1) and _px1[0] > _px0[0] and _px1[2] < _px0[2],
          (_px0, _px1))
    _f2 = _frame_rgb(_bright58, 0.2)
    _px2 = _pixel(_f2, 320, 160, 90)
    check("调亮以后同一像素更亮",
          sum(_px2) > sum(_px0) + 30, (_px0, _px2))
    try:
        p.render([p.Clip(IMG[0], 2.0,
                         lut=os.path.join(tmp, "no_such.cube"))], [60], TONE,
                 os.path.join(tmp, "color_missing.mp4"), w=320, h=180,
                 fps=30, crf=28)
        check("LUT 不在要报错", False)
    except p.RenderError as _e:
        check("LUT 不在要报错", "LUT" in str(_e), str(_e))

_src58 = open(os.path.join(ROOT, "pic2video.py"), encoding="utf-8").read()
check("有调色对话框", "class ColorDialog(FramelessDialog)" in _src58
      and "def get_values(" in _src58)
check("右键菜单有「调色…」", '"调色…"' in _src58
      and "def on_edit_color" in _src58
      and 'ColorDialog.get_values(self, self.project.clips[rows[0]]' in _src58)
check("调色读设置里的 LUT 目录",
      'self.settings["lut_dir"]' in _src58
      and "lut_dir=self.edit_lut.text().strip()" in _src58)
check("设置页有 LUT 目录",
      '"LUT 目录"' in _src58 and "def _browse_lut" in _src58)
_s58 = p.Settings(os.path.join(tmp, "settings_lut.json"))
check("设置默认没有 LUT 目录", _s58["lut_dir"] == "")
_s58.update(lut_dir=123)
check("LUT 目录不是文字就洗干净", _s58["lut_dir"] == "")
_s58.update(lut_dir="C:\\lut")
check("LUT 目录能存住", _s58["lut_dir"] == "C:\\lut", _s58["lut_dir"])
_c58_keys = ["调色", "调色…", "亮度", "对比度", "饱和度", "重置", "不用",
             "LUT", "LUT 目录", "选 LUT 目录", "{name}（不在目录里）",
             "调色用的 LUT 不在了：{name}",
             "放着 .cube 文件的目录（右键「调色…」时从这个目录里列）",
             "调色只作用在这一段上：先调亮度/对比度/饱和度，再套 LUT。LUT 是 .cube 文件，目录在设置里指定。"]
check("ffmpeg 的帮助读得到", "-filter_complex" in p.ffmpeg_help())
if not p.missing_tools():
    _gp58 = os.path.join(tmp, "optprobe.txt")
    with open(_gp58, "w", encoding="utf-8", newline="\n") as _fh:
        _fh.write("[0:v]null[vout];[1:a]anull[aout]")
    _o58 = os.path.join(tmp, "optprobe.mp4")
    _r58 = subprocess.run(
        [p.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
         "color=c=black:s=64x64:d=0.2", "-f", "lavfi", "-i",
         "anullsrc=r=48000:cl=stereo", p.graph_file_option(), _gp58,
         "-map", "[vout]", "-map", "[aout]", "-t", "0.2",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", _o58],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("滤镜图写文件时当前 ffmpeg 认得那个参数",
          _r58.returncode == 0 and os.path.getsize(_o58) > 0,
          _r58.stderr[-300:])
    _many58 = [p.Clip(IMG[0], 0.2) for _ in range(60)]
    _g58many = p.build_graph([6] * 60, 12.0, 160, 88, fps=30, clips=_many58)
    check("60 段的滤镜图确实超过 6000 字", len(_g58many) > 6000, len(_g58many))
    _o58b = os.path.join(tmp, "many.mp4")
    p.render(_many58, [6] * 60, TONE, _o58b, w=160, h=88, fps=30,
             preset="ultrafast", crf=34)
    check("长滤镜图能真出片", os.path.getsize(_o58b) > 0)

check("调色词条四语齐全",
      all(_k in tbl for _k in _c58_keys for tbl in _i18n.values()),
      [k for k in _c58_keys if not all(k in tbl for tbl in _i18n.values())])

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL -> {fails}")
sys.exit(1 if fails else 0)