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
check("简洁模式隐藏进阶组", 'add_sep("音乐", normal=True)' in _src
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
    _cover_ret = p.grab_cover(_out_ab, _cover_out, 0.2, 1.0)
    check("封面图生成了", os.path.exists(_cover_out)
          and os.path.getsize(_cover_out) > 500,
          os.path.getsize(_cover_out) if os.path.exists(_cover_out) else -1)
    check("封面接口把路径交回来", _cover_ret == _cover_out, _cover_ret)
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


# ---------------- 59 音频：每段增益 / 音量包络 / 波形缓存 ----------------
_ac59 = p.AudioClip("x.m4a", 3.0)
check("音频默认没有增益", _ac59.gain_db == 0.0 and _ac59.points == [])
_ac59.gain_db = p.clamp_gain_db(99)
check("增益上限夹到 +12 dB", _ac59.gain_db == 12.0, _ac59.gain_db)
check("增益下限夹到 -24 dB", p.clamp_gain_db(-99) == -24.0)
check("增益读不出来当 0", p.clamp_gain_db("abc") == 0.0 and p.clamp_gain_db(None) == 0.0)
check("0 dB 就是原样", abs(p.audio_level(0) - 1.0) < 1e-9)
check("-6 dB 约一半", abs(p.audio_level(-6) - 0.50119) < 0.01, p.audio_level(-6))
check("+6 dB 约两倍", abs(p.audio_level(6) - 1.99526) < 0.02, p.audio_level(6))
check("离谱增益按夹后的算", p.audio_level(100) == p.audio_level(12))

_pts59 = p.clean_env_points([(2.0, 0.4), (0.5, 1.5), (-1, 0.5), (2.0, 0.2),
                             ("x", 1), (3.0, "y"), (0.5, 0.8)])
check("包络点会排序", [t for t, g in _pts59] == sorted(t for t, g in _pts59), _pts59)
check("包络点夹在 0..1", all(0.0 <= g <= 1.0 for t, g in _pts59), _pts59)
check("同一时刻只留最后一个", abs(_pts59[-1][1] - 0.2) < 1e-9, _pts59)
check("负时间抬回 0", min(t for t, g in _pts59) >= 0.0)
check("坏包络点丢掉不炸", p.clean_env_points(None) == [] and p.clean_env_points("oops") == [])

check("没包络点就不出表达式", p.envelope_expr([]) == "" and p.envelope_expr(None) == "")
_ex59 = p.envelope_expr([(0.0, 1.0), (2.0, 0.2)])
check("包络表达式是 volume eval=frame", _ex59.startswith("volume=volume='") and "eval=frame" in _ex59, _ex59)
check("包络表达式按时间分段", _ex59.count("if(lt(t,") >= 2, _ex59)
check("包络表达式带斜率", "*(t-0.000)" in _ex59, _ex59)
_ex59b = p.envelope_expr([(1.0, 1.0), (2.0, 0.5)], base=0.5, t0=1.5)
check("偏移时间轴用 (t-1.500)", "(t-1.500)" in _ex59b and "eval=frame" in _ex59b, _ex59b)
check("包络乘上增益底数", "0.50000" in _ex59b, _ex59b)

check("没有增益也没有包络就不加滤镜", p.audio_fx(0.0, []) == "")
check("只有增益就一个 volume", p.audio_fx(-6.0, []) == "volume=0.50119", p.audio_fx(-6.0, []))
check("包络配增益走表达式", "eval=frame" in p.audio_fx(-6.0, [(0.0, 1.0), (1.0, 0.5)]))

_ac59b = p.AudioClip(TONE, 6.0, 1.0, 1.0, False, -6.0, [(0.0, 1.0), (2.0, 0.4)])
check("AudioClip 存住增益与包络",
      _ac59b.gain_db == -6.0 and len(_ac59b.points) == 2, _ac59b.points)

# 工程文件往返
_pj59 = p.Project()
_pj59.add_images([IMG[0]])
_pj59.audios = [p.AudioClip(TONE, 6.0, 0.0, 1.0, gap=True, gain_db=-4.5,
                            points=[(0.0, 1.0), (3.0, 0.25)])]
_doc59 = _pj59.to_doc()
check("工程文件里音频带增益", _doc59["audios"][0]["gain_db"] == -4.5)
check("工程文件里包络是列表的列表",
      _doc59["audios"][0]["points"] == [[0.0, 1.0], [3.0, 0.25]],
      _doc59["audios"][0]["points"])
_rt59 = p.Project.from_doc(json.loads(json.dumps(_doc59)))
check("工程往返保住增益与包络",
      _rt59.audios[0].gain_db == -4.5 and _rt59.audios[0].points == [(0.0, 1.0), (3.0, 0.25)],
      _rt59.audios[0].points)
_old59 = p.Project.from_doc({"version": 1, "clips": [], "audios": [
    {"path": "a.m4a", "dur": 2.0, "start": 0.0, "volume": 1.0, "gap": True}]})
check("老工程读成没有增益与包络", _old59.audios[0].gain_db == 0.0 and _old59.audios[0].points == [])
_bad59 = p.Project.from_doc({"version": 2, "clips": [], "audios": [
    {"path": "a.m4a", "dur": 2.0, "gain_db": 99, "points": [[1.0, 2.0], "junk"]}]})
check("坏增益坏包络都洗掉",
      _bad59.audios[0].gain_db == 12.0 and _bad59.audios[0].points == [(1.0, 1.0)],
      (_bad59.audios[0].gain_db, _bad59.audios[0].points))

# 滤镜串
_pj59g = p.Project(); _pj59g.add_images([IMG[0], IMG[1]])
_c59 = _pj59g.frame_counts()
_g59 = p.build_graph(_c59, 4.0, 320, 180, fps=30, clips=_pj59g.clips,
                     bgm_gain=-6.0)
check("增益进了 bgm 那一路", "volume=0.50119" in _g59, _g59[-300:])
_g59b = p.build_graph(_c59, 4.0, 320, 180, fps=30, clips=_pj59g.clips,
                      bgm_points=[(0.0, 1.0), (2.0, 0.2)])
check("包络进了 bgm 那一路", "eval=frame" in _g59b and "if(lt(t," in _g59b)
_g59c = p.build_graph(_c59, 4.0, 320, 180, fps=30, clips=_pj59g.clips,
                      extras=[{"index": 99, "start": 1.0, "dur": 2.0,
                               "volume": 1.0, "gain_db": 0.0,
                               "points": [(0.0, 1.0), (1.0, 0.3)]}])
check("额外音频的包络用输出时间轴", "(t-1.000)" in _g59c, _g59c[-400:])

def _mean59(mp4, ss, t):
    out = subprocess.run(
        [p.FFMPEG, "-v", "info", "-ss", str(ss), "-t", str(t), "-i", mp4,
         "-vn", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    line = [ln for ln in out.splitlines() if "mean_volume" in ln]
    if not line:
        return None
    return float(line[0].split("mean_volume:")[1].split("dB")[0])

if not p.missing_tools() and os.path.isfile(TONE):
    _outs59 = {}
    for tag, kw in (("plain", {}),
                    ("quiet", {"gain_db": -12.0}),
                    ("env", {"gain_db": 0.0,
                             "points": [(0.0, 1.0), (1.5, 1.0), (3.0, 0.02)]})):
        au = p.AudioClip(TONE, 3.0, 0.0, 1.0, gap=True,
                         gain_db=kw.get("gain_db", 0.0),
                         points=kw.get("points"))
        dst = os.path.join(tmp, "audio_" + tag + ".mp4")
        p.render([p.Clip(IMG[0], 3.0)], [90], [au], dst, w=160, h=88, fps=30,
                 crf=35, preset="ultrafast", fade_in=0.0, fade_out=0.0)
        _outs59[tag] = dst
    _plain59 = _mean59(_outs59["plain"], 0.2, 2.4)
    _quiet59 = _mean59(_outs59["quiet"], 0.2, 2.4)
    check("每段 -12 dB 真的变小了",
          _plain59 is not None and _quiet59 is not None and _plain59 - _quiet59 > 8,
          (_plain59, _quiet59))
    _early59 = _mean59(_outs59["env"], 0.2, 0.8)
    _late59 = _mean59(_outs59["env"], 2.5, 0.4)
    _plainlate59 = _mean59(_outs59["plain"], 2.5, 0.4)
    check("包络让尾巴变轻",
          _early59 is not None and _late59 is not None
          and _early59 - _late59 > 8,
          (_early59, _late59, _plainlate59))

# 波形缓存
if os.path.isfile(TONE):
    _pk59 = p.audio_peaks(TONE)
    check("波形峰值解出来了", len(_pk59) > 100, len(_pk59))
    check("峰值都在 0..1", all(0.0 <= v <= 1.0 for v in _pk59))
    _cache59 = p._peaks_cache_path(TONE)
    check("峰值缓存写下来了", os.path.isfile(_cache59), _cache59)
    _orig59 = p._decode_peaks
    p._decode_peaks = lambda path: [1.0] * 7      # 命中缓存就不该再解码
    try:
        _again59 = p.audio_peaks(TONE)
    finally:
        p._decode_peaks = _orig59
    check("第二次读的是缓存", _again59 == _pk59, len(_again59))
    _copy59 = os.path.join(tmp, "copy" + os.path.splitext(TONE)[1])
    with open(TONE, "rb") as _fh:
        _bytes59 = _fh.read()
    with open(_copy59, "wb") as _fh:
        _fh.write(_bytes59)
    _k59a = p.wave_key(_copy59)
    _pk59a = p.audio_peaks(_copy59)
    check("换了文件是另一份缓存",
          _pk59a == _pk59 and p.wave_key(_copy59) == _k59a, (len(_pk59a), _k59a))
    import time as _time59
    os.utime(_copy59, (_time59.time() + 5, _time59.time() + 5))
    check("改了修改时间就算失效", p.wave_key(_copy59) != _k59a)
    _pk59b = p.audio_peaks(_copy59)
    check("失效后重算还是对的", _pk59b == _pk59, len(_pk59b))
    check("文件不在就没有波形", p.audio_peaks(os.path.join(tmp, "nope.m4a")) == [])

# ---------------- 60 音频：界面接线与词条 ----------------
_src60 = open(os.path.join(ROOT, "pic2video.py"), encoding="utf-8").read()
check("时间线有包络改动的信号", "envelopeChanged = pyqtSignal(int)" in _src60)
check("时间线能算包络点的坐标", "def _env_xy(" in _src60 and "def _env_from_pos(" in _src60)
check("时间线能命中包络点", "def _hit_env(" in _src60 and "def _x_at(" in _src60)
check("时间线有波形缓存", "def _wave(" in _src60 and "audio_peaks(" in _src60)
check("波形按 WAVE_RATE 画",
      "WAVE_RATE" in _src60 and "peaks[idx] / wmax * half" in _src60)
check("Alt + 点音频块加包络点",
      "AltModifier" in _src60 and "clean_env_points(list(a.points)" in _src60)
check("拖动包络点会改值和重排",
      'kind == "env"' in _src60 and "a.points[j] = (t, g)" in _src60)
check("右键包络点就删掉", "a.points.pop(ej)" in _src60)
check("窗口接上包络改动",
      "self.timeline.envelopeChanged.connect(self.on_envelope_changed)" in _src60)
check("音频菜单有增益与清空包络",
      'tr("增益…")' in _src60 and 'tr("清空包络")' in _src60)
check("三个音频处理器都在",
      all(("def " + _n + "(self") in _src60
          for _n in ("on_ask_gain", "on_clear_envelope",
                     "on_envelope_changed")))
check("音频轨的底边不低于顶边+16",
      "return top, max(top + 16, self.height() - 4)" in _src60)
check("音频行按轨道高度分",
      "row = max(10.0, (bottom - top) / n)" in _src60)
check("增益对话框带 dB 后缀", 'suffix=tr(" dB")' in _src60)
check("数字对话框能带后缀",
      'suffix: str = ""' in _src60
      and 'self.spin.setSuffix(suffix or tr(" 秒"))' in _src60)

_c60_keys = ("增益…", "增益", "这条音频的增益（dB，0 就是原样）：", " dB",
             "清空包络", "音量包络")
check("音频词条四语齐全",
      all(_k in tbl for _k in _c60_keys for tbl in _i18n.values()),
      [k for k in _c60_keys if not all(k in tbl for tbl in _i18n.values())])


# ---------------- 61 资源包：模型层 ----------------
import hashlib as _h61, zipfile as _z61


def _sha61(b):
    return _h61.sha256(b).hexdigest()


def _man61(kind, name, version, files):
    return {"name": name, "version": version, "kind": kind,
            "entries": [{"path": r, "sha256": _sha61(d)}
                        for r, d in files.items()]}


def _zip61(path, man, files, extra=None):
    z = _z61.ZipFile(path, "w")
    z.writestr("manifest.json", _j61.dumps(man, ensure_ascii=False))
    for r, d in files.items():
        z.writestr(r, d)
    for r, d in (extra or {}).items():
        z.writestr(r, d)
    z.close()
    return path


import json as _j61
_CUBE61 = (b"TITLE \"T\"\nLUT_3D_SIZE 2\n"
           b"0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n")

check("三类资源包都有中文名",
      p.ASSET_KINDS == ("font", "lut", "transition")
      and all(k in p.ASSET_KIND_LABELS for k in p.ASSET_KINDS),
      p.ASSET_KIND_LABELS)
check("资源包目录在数据目录的 assets 下",
      p.assets_root().replace("\\", "/").endswith("/assets")
      and p.asset_dir("lut").replace("\\", "/").endswith("/assets/luts"),
      (p.assets_root(), p.asset_dir("lut")))
check("包扩展名白名单",
      ".ttf" in p.ASSET_EXTS["font"] and ".otf" in p.ASSET_EXTS["font"]
      and ".cube" in p.ASSET_EXTS["lut"]
      and ".json" in p.ASSET_EXTS["transition"], p.ASSET_EXTS)
for _bad61 in ("", "/abs/x.cube", "C:/x.cube", "a/../b.cube", "a/./b.cube",
               "a//b.cube", "a\nb.cube"):
    try:
        p._asset_rel(_bad61)
        _ok61 = False
    except p.AssetError as _e61:
        _ok61 = _e61.reason == "unsafe-path"
    check("拒绝不安全路径 %r" % _bad61, _ok61)
check("正常相对路径能过", p._asset_rel("sub\\a.cube") == "sub/a.cube")
check("转场预设：认名字与时长",
      p.parse_transition_preset({"name": "柔和", "xfade": "slideleft",
                                 "duration": 0.5})
      == {"name": "柔和", "xfade": "slideleft", "duration": 0.5})
check("转场预设：时长夹到范围",
      p.parse_transition_preset({"name": "x", "xfade": "fade",
                                 "duration": 99})["duration"] == p.XFADE_MAX)
try:
    p.parse_transition_preset({"name": "x", "xfade": "nope"})
    _ok61b = False
except p.AssetError as _e61b:
    _ok61b = _e61b.reason == "bad-preset"
check("转场预设：认不出的转场名不收", _ok61b)
try:
    p.parse_transition_preset({"name": "", "xfade": "fade"})
    _ok61c = False
except p.AssetError as _e61c:
    _ok61c = _e61c.reason == "bad-preset"
check("转场预设：名字空着不收", _ok61c)

with tempfile.TemporaryDirectory() as _td61:
    _root61 = os.path.join(_td61, "assets")
    _files61 = {"probe.cube": _CUBE61}
    _zp61 = _zip61(os.path.join(_td61, "lut.zip"),
                   _man61("lut", "Probe LUT", "1.0", _files61), _files61)
    _info61 = p.inspect_asset_zip(_zp61)
    check("读包：认出类型/名字/版本",
          (_info61["kind"], _info61["name"], _info61["version"])
          == ("lut", "Probe LUT", "1.0"))
    check("读包：文件内容与清单对上",
          list(_info61["files"]) == ["probe.cube"]
          and _info61["files"]["probe.cube"] == _CUBE61)
    _pkg61 = p.install_asset_zip(_zp61, _root61)
    check("装包：落进 assets/luts 下",
          os.path.dirname(_pkg61["path"]).replace("\\", "/")
          .endswith("/assets/luts"), _pkg61["path"])
    check("装包：文件真写下去了",
          os.path.exists(os.path.join(_pkg61["path"], "probe.cube")))
    check("装包：清单也写下去了，还带 sha256",
          (lambda: (lambda man: man["entries"][0]["sha256"] == _sha61(_CUBE61))(
              _j61.load(open(os.path.join(_pkg61["path"], "manifest.json"),
                             encoding="utf-8"))))())
    _lst61 = p.installed_assets("lut", _root61)
    check("已装列表只有这一包",
          len(_lst61) == 1 and _lst61[0]["name"] == "Probe LUT")
    check("同类不同类的列表分开",
          p.installed_assets("font", _root61) == []
          and len(p.installed_asset_packages(_root61)) == 1)
    try:
        p.install_asset_zip(_zp61, _root61)
        _ok61d = False
    except p.AssetError as _e61d:
        _ok61d = _e61d.reason == "exists"
    check("同一版本再装一次被拒", _ok61d)
    _files61b = {"probe2.cube": _CUBE61}
    _zp61b = _zip61(os.path.join(_td61, "lut2.zip"),
                    _man61("lut", "Probe LUT", "2.0", _files61b), _files61b)
    _pkg61b = p.install_asset_zip(_zp61b, _root61)
    check("新版本覆盖同名包",
          len(p.installed_assets("lut", _root61)) == 1
          and p.installed_assets("lut", _root61)[0]["version"] == "2.0")
    check("覆盖后旧文件不在了",
          not os.path.exists(os.path.join(_pkg61b["path"], "probe.cube")))
    check("覆盖不留 .old 目录",
          not any(n.endswith(".old") for n in
                  os.listdir(os.path.join(_root61, "luts"))))
    check("装好的 LUT 出现在下拉源里",
          [os.path.basename(x) for x in p.asset_luts(_root61)]
          == ["probe2.cube"])
    check("all_luts 合并目录与资源包",
          len(p.all_luts("", _root61)) == 1)
    check("删除资源包", p.remove_asset_package(_pkg61b, _root61)
          and p.installed_asset_packages(_root61) == [])
    check("删完删不掉第二次",
          p.remove_asset_package(_pkg61b, _root61) is False)

    _preset_files = {"soft.json": _j61.dumps(
        {"name": "柔和推移", "xfade": "slideleft", "duration": 0.5},
        ensure_ascii=False).encode("utf-8")}
    _zpt = _zip61(os.path.join(_td61, "tr.zip"),
                  _man61("transition", "Probe Tr", "1", _preset_files),
                  _preset_files)
    p.install_asset_zip(_zpt, _root61)
    _tp61 = p.asset_transition_presets(_root61)
    check("转场预设包读得出名字/转场/时长",
          len(_tp61) == 1 and _tp61[0]["name"] == "柔和推移"
          and _tp61[0]["xfade"] == "slideleft"
          and abs(_tp61[0]["duration"] - 0.5) < 1e-9, _tp61)
    _badpreset = {"bad.json": b'{"name": "x", "xfade": "no-such"}'}
    _zpb = _zip61(os.path.join(_td61, "trbad.zip"),
                  _man61("transition", "Bad Tr", "1", _badpreset), _badpreset)
    try:
        p.inspect_asset_zip(_zpb)
        _ok61e = False
    except p.AssetError as _e61e:
        _ok61e = _e61e.reason == "bad-preset"
    check("转场包里写了不存在的转场名就不收", _ok61e)

    _tsrc = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                         "Fonts", "arial.ttf")
    if os.path.exists(_tsrc):
        _fam61 = p.font_family_of(_tsrc)
        check("字体包：能读出 family", _fam61 == "Arial", _fam61)
        _ffiles = {"arial.ttf": open(_tsrc, "rb").read()}
        _zpf = _zip61(os.path.join(_td61, "font.zip"),
                      _man61("font", "Probe Font", "1", _ffiles), _ffiles)
        _pf61 = p.install_asset_zip(_zpf, _root61)
        check("字体包：装好就进字体表",
              p.asset_fonts(_root61).get(_fam61)
              == os.path.join(_pf61["path"], "arial.ttf"))
        check("字体包：烧字幕的字体目录指向包目录",
              p.font_lookup_dir(_fam61, _root61) == _pf61["path"])
        check("字体包：系统字体回落到系统目录",
              p.font_lookup_dir("No Such Family", _root61)
              == p.default_font_dir())
        _proj61 = p.Project()
        _proj61.clips = [p.Clip(IMG[0], 2.0)]
        check("删包前能算出「字幕字体」在用",
              p.asset_usage(_proj61, _pf61, _fam61) == [p.tr("字幕字体")],
              p.asset_usage(_proj61, _pf61, _fam61))

    _lutpath = os.path.join(_pkg61["path"], "probe.cube")
    _proj61b = p.Project()
    _proj61b.clips = [p.Clip(IMG[0], 2.0, lut=_lutpath.replace("probe.cube",
                                                               "probe2.cube"))]
    check("删包前能算出「第 n 段的 LUT」在用",
          p.asset_usage(_proj61b, _pkg61b) == [p.tr("第 {n} 段的 LUT", n=1)],
          p.asset_usage(_proj61b, _pkg61b))

    # 每条拒绝路径
    def _reject(name, man, files, extra=None, patch=None):
        path = _zip61(os.path.join(_td61, "r%d.zip" % len(fails)), man, files,
                      extra)
        old = {}
        for k, v in (patch or {}).items():
            old[k] = getattr(p, k)
            setattr(p, k, v)
        try:
            p.inspect_asset_zip(path)
            got = ""
        except p.AssetError as exc:
            got = exc.reason
        finally:
            for k, v in old.items():
                setattr(p, k, v)
        return got

    try:
        p.inspect_asset_zip(os.path.join(_td61, "nope.zip"))
        _r61 = ""
    except p.AssetError as _e61f:
        _r61 = _e61f.reason
    check("拒绝：文件不在了", _r61 == "missing")
    _notzip = os.path.join(_td61, "not.zip")
    open(_notzip, "wb").write(b"not a zip at all")
    try:
        p.inspect_asset_zip(_notzip)
        _r61 = ""
    except p.AssetError as _e61g:
        _r61 = _e61g.reason
    check("拒绝：不是 zip", _r61 == "bad-zip")
    check("拒绝：条目太多", _reject("x", _man61("lut", "n", "1", {}), {},
                                  extra={"f%d.cube" % i: b"x" for i in range(70)},
                                  patch={"ASSET_MAX_ENTRIES": 4})
          == "too-many")
    check("拒绝：解压太大", _reject("x", _man61("lut", "n", "1",
                                                {"a.cube": _CUBE61}),
                                  {"a.cube": _CUBE61},
                                  patch={"ASSET_MAX_BYTES": 8}) == "too-big")
    check("拒绝：没清单", _reject("x", {}, {"a.cube": _CUBE61}) == "bad-manifest")
    check("拒绝：清单不是对象",
          _reject("x", [], {"a.cube": _CUBE61}) == "bad-manifest")
    check("拒绝：类型不在白名单",
          _reject("x", _man61("video", "n", "1", {"a.cube": _CUBE61}),
                  {"a.cube": _CUBE61}) == "bad-manifest")
    check("拒绝：扩展名与类型不符",
          _reject("x", _man61("lut", "n", "1", {"a.txt": b"x"}), {"a.txt": b"x"})
          == "bad-entry")
    check("拒绝：清单里的文件不在包里",
          _reject("x", _man61("lut", "n", "1", {"a.cube": _CUBE61}), {})
          == "missing-entry")
    check("拒绝：包里多出没声明的文件",
          _reject("x", _man61("lut", "n", "1", {"a.cube": _CUBE61}),
                  {"a.cube": _CUBE61}, extra={"junk.txt": b"x"})
          == "bad-manifest")
    _badman = _man61("lut", "n", "1", {"a.cube": _CUBE61})
    _badman["entries"][0]["sha256"] = "0" * 64
    check("拒绝：文件校验对不上",
          _reject("x", _badman, {"a.cube": _CUBE61}) == "hash-mismatch")

    _sym = _zip61(os.path.join(_td61, "sym.zip"),
                  _man61("lut", "n", "1", {"l.cube": b""}), {"l.cube": b""})
    _zr = _z61.ZipFile(_sym, "a")
    _zi = _z61.ZipInfo("link.cube")
    _zi.external_attr = (0o120777 << 16)
    _zr.writestr(_zi, b"")
    _zr.close()
    try:
        p.inspect_asset_zip(_sym)
        _r61 = ""
    except p.AssetError as _e61h:
        _r61 = _e61h.reason
    check("拒绝：符号链接", _r61 == "symlink")

    _exep = os.path.join(_td61, "exe.zip")
    _zr = _z61.ZipFile(_exep, "w")
    _zr.writestr("manifest.json", _j61.dumps(_man61("lut", "n", "1",
                                                    {"a.cube": _CUBE61})))
    _zr.writestr("a.cube", _CUBE61)
    _zi = _z61.ZipInfo("run.exe")
    _zi.external_attr = (0o100755 << 16)
    _zr.writestr(_zi, b"MZ")
    _zr.close()
    try:
        p.inspect_asset_zip(_exep)
        _r61 = ""
    except p.AssetError as _e61i:
        _r61 = _e61i.reason
    check("拒绝：可执行文件", _r61 in ("executable", "bad-manifest"))

    for _reason61 in ("missing", "bad-zip", "too-many", "too-big",
                      "unsafe-path", "symlink", "executable", "bad-manifest",
                      "bad-entry", "missing-entry", "hash-mismatch",
                      "bad-preset", "exists", "io"):
        _txt61 = p.asset_error_text(p.AssetError(_reason61))
        check("错误提示 %s 有人话" % _reason61,
              _txt61.startswith(p.tr("装不进去："))
              and _txt61 != p.tr("装不进去：") + p.tr("认不出来的问题"), _txt61)

# ---------------- 62 资源包：界面接线与词条 ----------------
_src62 = open(os.path.join(ROOT, "pic2video.py"), encoding="utf-8").read()
check("设置页有「资源包」一栏",
      'add_sep("资源包"' in _src62 and "self.list_assets" in _src62)
check("资源包区有导入/删除按钮",
      'tr("导入 zip…")' in _src62 and 'tr("删除所选")' in _src62)
check("导入走 zip 文件框",
      'tr("资源包 (*.zip)")' in _src62 and "def _import_asset(" in _src62)
check("导入失败说人话",
      "asset_error_text(" in _src62 and 'tr("资源包没装上")' in _src62)
check("删除要问、删完提示在用",
      "def _remove_asset(" in _src62 and 'tr("删掉资源包")' in _src62
      and "asset_usage(" in _src62)
check("设置对话框拿得到工程",
      "def __init__(self, settings: Settings, parent=None, project=None)"
      in _src62 and "self.project = project" in _src62)
check("启动时注册资源字体", "register_asset_fonts()" in _src62)
check("资源字体注册是模块级函数", "\ndef register_asset_fonts(" in _src62)
check("字幕烧录用资源字体目录",
      'subtitle_fonts=font_lookup_dir(s["subtitle_font"])' in _src62)
check("调色下拉合并资源 LUT", "luts = all_luts(lut_dir)" in _src62)
check("转场菜单有资源包预设",
      'tr("{name}（资源包）", name=preset["name"])' in _src62
      and "def on_set_transition_preset(" in _src62)
check("资源包预设按内置转场名套",
      'name if name in XFADE_NAMES else "fade"' in _src62
      and 'preset["xfade"], preset["duration"]' in _src62)

_c62_keys = ("字体", "LUT", "转场预设", "资源包", "已装资源包", "导入 zip…",
             "删除所选", "选资源包", "资源包 (*.zip)", "资源包没装上",
             "资源包装好了", "删掉资源包", "删掉", "算了", "删不掉",
             "资源包已经删掉", "字幕字体", "{name}（资源包）",
             "装不进去：", "认不出来的问题", "第 {n} 段的 LUT",
             "找不到这个 zip 文件", "文件校验对不上（sha256），包可能坏了")
check("资源包词条四语齐全",
      all(_k in tbl for _k in _c62_keys for tbl in _i18n.values()),
      [k for k in _c62_keys if not all(k in tbl for tbl in _i18n.values())])

# ---------------- 63 资源包：真拿去渲染 ----------------
_BLACK61 = (b"TITLE \"go black\"\nLUT_3D_SIZE 2\n" +
            b"0 0 0\n" * 8)
with tempfile.TemporaryDirectory() as _td63:
    _root63 = os.path.join(_td63, "assets")
    _lutf = {"black.cube": _BLACK61}
    _zpl = _zip61(os.path.join(_td63, "black.zip"),
                  _man61("lut", "Black", "1", _lutf), _lutf)
    _pl63 = p.install_asset_zip(_zpl, _root63)
    _lutfile = os.path.join(_pl63["path"], "black.cube")
    _pr63 = p.Project(fps=10)
    _pr63.clips = [p.Clip(IMG[0], 1.0, lut=_lutfile)]
    _out63 = os.path.join(_td63, "lut.mp4")
    p.render(_pr63.clips, _pr63.frame_counts(), _pr63.audios, _out63,
             w=160, h=90, fps=10, crf=30, preset="ultrafast",
             fade_in=0, fade_out=0, transition="none")
    _raw = subprocess.run([p.FFMPEG, "-v", "error", "-i", _out63,
                           "-frames:v", "1", "-f", "rawvideo",
                           "-pix_fmt", "rgb24", "-"],
                          capture_output=True).stdout
    check("资源包里的 LUT 真套上了（整帧变黑）",
          len(_raw) > 100 and max(_raw) < 40, max(_raw) if _raw else -1)

    _tsrc63 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                           "Fonts", "arial.ttf")
    if os.path.exists(_tsrc63):
        _ff63 = {"arial.ttf": open(_tsrc63, "rb").read()}
        _zpf63 = _zip61(os.path.join(_td63, "font.zip"),
                        _man61("font", "F", "1", _ff63), _ff63)
        _pf63 = p.install_asset_zip(_zpf63, _root63)
        _fam63 = p.font_family_of(os.path.join(_pf63["path"], "arial.ttf"))
        _cues63 = [{"start": 0.1, "end": 0.9, "text": "Hello pack"}]
        _out63b = os.path.join(_td63, "font.mp4")
        p.render(_pr63.clips, _pr63.frame_counts(), [], _out63b,
                 w=160, h=90, fps=10, crf=30, preset="ultrafast",
                 fade_in=0, fade_out=0, transition="none",
                 subtitles=_cues63, subtitle_font=_fam63,
                 subtitle_fonts=p.font_lookup_dir(_fam63, _root63))
        check("资源包字体能烧字幕", os.path.getsize(_out63b) > 500,
              os.path.getsize(_out63b))

    _pr63b = p.Project(fps=10)
    _pr63b.clips = [p.Clip(IMG[0], 1.0), p.Clip(IMG[1], 1.0)]
    _pr63b.clips[0].xfade = True
    _pr63b.clips[0].xfade_name = "slideleft"
    _pr63b.clips[0].xfade_dur = 0.2
    _out63c = os.path.join(_td63, "tr.mp4")
    p.render(_pr63b.clips, _pr63b.frame_counts(), [], _out63c,
             w=160, h=90, fps=10, crf=30, preset="ultrafast",
             fade_in=0, fade_out=0, transition="none")
    check("资源包预设选的转场能出片", os.path.getsize(_out63c) > 500,
          os.path.getsize(_out63c))

# ---------------- 64 预览控制条：质量档位 / 倍速 / 接线 ----------------
check("预览质量四档", p.PREVIEW_QUALITY == {"fast": 640, "normal": 960,
                                        "sharp": 1280, "full": 0},
      p.PREVIEW_QUALITY)
check("档位顺序固定",
      p.PREVIEW_QUALITY_ORDER == ("fast", "normal", "sharp", "full"),
      p.PREVIEW_QUALITY_ORDER)
check("每档都有中文名",
      set(p.PREVIEW_QUALITY_LABELS) == set(p.PREVIEW_QUALITY_ORDER)
      and all(p.PREVIEW_QUALITY_LABELS[q] for q in p.PREVIEW_QUALITY_ORDER),
      p.PREVIEW_QUALITY_LABELS)
check("倍速档位", p.PREVIEW_SPEEDS == (0.5, 0.75, 1.0, 1.25, 1.5, 2.0),
      p.PREVIEW_SPEEDS)

_sp64 = os.path.join(tmp, "settings_preview.json")
_s64 = p.Settings(_sp64)
check("默认预览质量是标准", _s64["preview_quality"] == "normal",
      _s64["preview_quality"])
check("默认倍速 1 倍", _s64["preview_speed"] == 1.0, _s64["preview_speed"])
_s64.update(width=1920, height=1080)
check("1080p 流畅档 640x360", _s64.preview_size("fast") == (640, 360),
      _s64.preview_size("fast"))
check("1080p 标准档 960x540", _s64.preview_size("normal") == (960, 540),
      _s64.preview_size("normal"))
check("1080p 清晰档 1280x720", _s64.preview_size("sharp") == (1280, 720),
      _s64.preview_size("sharp"))
check("原尺寸档跟成片一样大", _s64.preview_size("full") == (1920, 1080),
      _s64.preview_size("full"))
_s64.update(width=1080, height=1920)
check("竖屏按最长边缩", _s64.preview_size("normal") == (540, 960),
      _s64.preview_size("normal"))
_s64.update(width=640, height=360)
check("小成片不放大", _s64.preview_size("normal") == (640, 360),
      _s64.preview_size("normal"))
_s64.update(width=2000, height=800)
check("超宽画面按长边缩", _s64.preview_size("sharp") == (1280, 512),
      _s64.preview_size("sharp"))
_s64.update(preview_quality="huge")
check("认不出的质量档退回标准", _s64["preview_quality"] == "normal",
      _s64["preview_quality"])
_s64.update(preview_speed=3.0)
check("不支持的倍速退回 1 倍", _s64["preview_speed"] == 1.0,
      _s64["preview_speed"])
_s64.update(preview_speed=1.5)
check("支持的倍速留得住", _s64["preview_speed"] == 1.5, _s64["preview_speed"])

check("控制条有进度条", "self.slider_pos = SeekSlider(" in _src)
check("控制条有播放按钮", 'self.btn_play = QPushButton("▶")' in _src)
check("控制条有质量下拉", "self.cmb_quality = QComboBox()" in _src)
check("进度条接上总长", "durationChanged.connect(self.on_duration)" in _src)
check("播放状态接了按钮图标",
      "playbackStateChanged.connect(self.on_play_state)" in _src)
check("倍速真的设给播放器", "self.player.setPlaybackRate(rate)" in _src)
check("有快进快退", "def on_skip(self, delta: float):" in _src)
check("拖动结束才真跳", "def on_scrub_end(self):" in _src)
check("样片尺寸取所选清晰度",
      's.preview_size(s["preview_quality"])' in _src)

_qss64 = TH.build_qss(TH.palette("dark", TH.DEFAULT_ACCENT))
check("进度条有滑杆样式", "QSlider::handle:horizontal" in _qss64
      and "QSlider::sub-page:horizontal" in _qss64)

_c64_keys = ["播放 / 暂停（空格）", "播放（空格）", "暂停（空格）", "后退 5 秒",
             "前进 5 秒", "拖动跳到任意位置",
             "预览倍速：只影响播放，不影响成片",
             "预览清晰度：只影响样片，不影响成片",
             "预览倍速：{n}×", "预览质量：{q}", "流畅", "清晰", "原尺寸"]
check("控制条词条四语齐全",
      all(_k in tbl for _k in _c64_keys for tbl in _i18n.values()),
      [k for k in _c64_keys if not all(k in tbl for tbl in _i18n.values())])

# ---------------- 65 实时合成：时间映射 / 画面 / 转场 / 画中画
from PyQt6.QtGui import QImage as _QImg, QColor as _QCol
_scd = tempfile.mkdtemp(prefix="p2v_scene_")
def _solid(name, color, w=160, h=90):
    path = os.path.join(_scd, name)
    img = _QImg(w, h, _QImg.Format.Format_RGB32)
    img.fill(_QCol(color))
    img.save(path, "PNG")
    return path
_red, _blue, _green = _solid("red.png", "#ff0000"), \
    _solid("blue.png", "#0000ff"), _solid("green.png", "#00ff00", 60, 60)

def _scene(clips, counts=None, **kw):
    opts = dict(w=160, h=90, fit="own", bg_blur=False, transition="none",
                transition_sec=0.3, xfade_sec=0.3, motion="none",
                crop_focus="center", subtitle_size=20,
                subtitle_font="Microsoft YaHei")
    opts.update(kw)
    if counts is None:
        counts = [int(round(c.dur * 30)) for c in clips]
    return p.Scene(clips, counts, 30, opts)

def _px(sc, t, x=80, y=45):
    c = sc.frame_at(t).pixelColor(x, y)
    return (c.red(), c.green(), c.blue())

_sc65 = _scene([p.Clip(_red, 1.0), p.Clip(_blue, 1.0)])
check("Scene 总长按帧数算", abs(_sc65.total - 2.0) < 1e-9, _sc65.total)
check("Scene 段起点正确", [round(s, 3) for s in _sc65.starts] == [0.0, 1.0],
      _sc65.starts)
check("硬切前段是红的", _px(_sc65, 0.1) == (255, 0, 0), _px(_sc65, 0.1))
check("硬切后段是蓝的", _px(_sc65, 1.4) == (0, 0, 255), _px(_sc65, 1.4))
check("超出总长取最后一帧", _px(_sc65, 99.0)[2] == 255)
check("负时间取第一帧", _px(_sc65, -5.0) == (255, 0, 0))
_f = _sc65.frame_at(0.1)
check("画面尺寸等于预览参数", (_f.width(), _f.height()) == (160, 90),
      (_f.width(), _f.height()))

_sx = _scene([p.Clip(_red, 1.0, xfade=True, xfade_name="fade", xfade_dur=0.4),
              p.Clip(_blue, 1.0)])
check("交叉淡化重叠帧数", _sx.overlaps == [12], _sx.overlaps)
check("交叉淡化后段起点提前", abs(_sx.starts[1] - 0.8) < 1e-9, _sx.starts)
check("淡化起点还是纯红", _px(_sx, 0.79) == (255, 0, 0), _px(_sx, 0.79))
_mid = _px(_sx, 1.0)
check("淡化中点是红蓝各半", abs(_mid[0] - 128) <= 3 and abs(_mid[2] - 128) <= 3,
      _mid)
check("淡化结尾基本是蓝", _px(_sx, 1.19)[2] > 200, _px(_sx, 1.19))
check("淡化不改总长", abs(_sx.total - 2.0) < 1e-9, _sx.total)

_sf = _scene([p.Clip(_red, 1.0, fade=True), p.Clip(_blue, 1.0)],
             transition_sec=0.3)
check("自己开淡入：开头是黑的", _px(_sf, 0.02)[0] < 40, _px(_sf, 0.02))
check("自己开淡入：中段正常", _px(_sf, 0.5) == (255, 0, 0))
check("自己开淡入：结尾又黑", _px(_sf, 0.98)[0] < 40, _px(_sf, 0.98))

_sx_names = ["wipeleft", "wiperight", "wipeup", "wipedown", "slideleft",
             "slideright", "circleopen", "circleclose", "fadeblack",
             "pixelize", "radial"]
def _xf(name):
    return _scene([p.Clip(_red, 1.0, xfade=True, xfade_name=name,
                          xfade_dur=0.4), p.Clip(_blue, 1.0)])

_wl = _xf("wipeleft")
check("向左擦除：左边还是旧画面", _px(_wl, 1.0, 20, 45) == (255, 0, 0))
check("向左擦除：右边已是新画面", _px(_wl, 1.0, 140, 45) == (0, 0, 255))
_wr = _xf("wiperight")
check("向右擦除方向相反", _px(_wr, 1.0, 20, 45) == (0, 0, 255)
      and _px(_wr, 1.0, 140, 45) == (255, 0, 0))
_wu = _xf("wipeup")
check("向上擦除自下而上", _px(_wu, 1.0, 80, 10) == (255, 0, 0)
      and _px(_wu, 1.0, 80, 80) == (0, 0, 255))
_wd = _xf("wipedown")
check("向下擦除自上而下", _px(_wd, 1.0, 80, 10) == (0, 0, 255)
      and _px(_wd, 1.0, 80, 80) == (255, 0, 0))
_sl = _xf("slideleft")
check("向左推移不留黑边", _px(_sl, 1.0, 20, 45) == (255, 0, 0)
      and _px(_sl, 1.0, 140, 45) == (0, 0, 255))
_sr = _xf("slideright")
check("向右推移不留黑边", _px(_sr, 1.0, 20, 45) == (0, 0, 255)
      and _px(_sr, 1.0, 140, 45) == (255, 0, 0))
_co = _xf("circleopen")
check("圆形展开：中间已是新画面", _px(_co, 1.0, 80, 45) == (0, 0, 255))
check("圆形展开：四角还是旧画面", _px(_co, 1.0, 4, 4) == (255, 0, 0))
_cc = _xf("circleclose")
check("圆形收拢：中间还是旧画面", _px(_cc, 1.0, 80, 45) == (255, 0, 0))
check("圆形收拢：四角已是新画面", _px(_cc, 1.0, 4, 4) == (0, 0, 255))
_fb = _xf("fadeblack")
check("黑场过渡中途接近全黑", max(_px(_fb, 1.0)) < 12, _px(_fb, 1.0))
_pz = _xf("pixelize")
check("马赛克转场中途是混色", 60 <= _px(_pz, 1.0)[0] <= 200
      and 60 <= _px(_pz, 1.0)[2] <= 200, _px(_pz, 1.0))
_rd = _xf("radial")
check("径向转场左右不同", _px(_rd, 1.0, 20, 45) != _px(_rd, 1.0, 140, 45),
      (_px(_rd, 1.0, 20, 45), _px(_rd, 1.0, 140, 45)))
check("12 个转场名都能画出来",
      all(_xf(n).frame_at(1.0).width() == 160 for n in p.XFADE_NAMES))

_ov65 = p.Overlay(_green, 0.5, 1.5, scale=50, anchor="center", opacity=100)
_sov = p.Scene([p.Clip(_red, 2.0)], [60], 30,
               dict(w=160, h=90, fit="own", bg_blur=False,
                    transition="none", subtitle_size=20),
               overlays=p.overlay_specs([_ov65], 0, 2.0))
check("画中画没到点不出现", _px(_sov, 0.2) == (255, 0, 0), _px(_sov, 0.2))
check("画中画到点压在上面", _px(_sov, 1.0) == (0, 255, 0), _px(_sov, 1.0))
check("画中画过了点消失", _px(_sov, 1.8) == (255, 0, 0), _px(_sov, 1.8))

_sm = _scene([p.Clip(_red, 1.0)], motion="in")
check("推近从 1.0 开始", abs(_sm._zoom(0, 0.0) - 1.0) < 1e-9, _sm._zoom(0, 0.0))
check("推近到结尾变大", _sm._zoom(0, 0.99) > 1.0 + p.MOTION_AMOUNT * 0.9,
      _sm._zoom(0, 0.99))
check("镜头拉远从大开始", abs(_scene([p.Clip(_red, 1.0)], motion="out")
                            ._zoom(0, 0.0) - (1.0 + p.MOTION_AMOUNT)) < 1e-6)

_sc65.reconfigure([p.Clip(_red, 1.0), p.Clip(_blue, 1.0), p.Clip(_red, 1.0)],
                  [30, 30, 30], 30,
                  dict(w=160, h=90, fit="own", bg_blur=False,
                       transition="none", subtitle_size=20))
check("重建后总长变 3 秒", abs(_sc65.total - 3.0) < 1e-9, _sc65.total)
check("重建后解码过的图还留着", len(_sc65._imgs) == 2, len(_sc65._imgs))

check("字幕去 ASS 标签", p._plain_cue("{pos(1,2)}你好") == ["你好"],
      p._plain_cue("{pos(1,2)}你好"))
check("字幕 \\N 拆行", p._plain_cue("上\\N下") == ["上", "下"],
      p._plain_cue("上\\N下"))
check("空字幕给一条空行", p._plain_cue("") == [""])

# ---------------- 66 音轨主时钟：只混音频
_ag = p.build_audio_graph(6.0, timeline=[(True, 3.0), (False, 1.5), (True, 1.5)],
                          volume=0.5, fade_in=0.3, fade_out=0.3)
check("音频图最后落到 aout", _ag.endswith("[aout]"), _ag[-40:])
check("音频图按片头片尾补静音", "anullsrc" in _ag
      and "concat=n=3:v=0:a=1" in _ag)
check("音频图带淡入淡出", "afade=t=in" in _ag and "afade=t=out" in _ag)
check("音频图带整段音量", "volume=0.500" in _ag)
check("没有额外音轨就不 amix", "amix=" not in _ag)
_ag2 = p.build_audio_graph(4.0, timeline=[(True, 4.0)],
                           extras=[{"index": 1, "start": 1.0, "dur": 2.0,
                                    "volume": 0.5}])
check("多轨走 amix 并延时", "amix=inputs=2" in _ag2
      and "adelay=1000|1000" in _ag2, _ag2[-120:])
_span66 = p.audio_timeline([p.Clip(IMG[0], 1.0), p.Clip(IMG[0], 1.0)],
                           [30, 30], 30, 2.0)
check("音轨时间线按段切", len(_span66) == 2
      and abs(sum(d for _, d in _span66) - 2.0) < 0.06, _span66)
if p.missing_tools():
    print("  (没 ffmpeg，跳过音轨实测)")
else:
    _mixdir = tempfile.mkdtemp(prefix="p2v_mix_")
    _aout66 = os.path.join(_mixdir, "mix.m4a")
    _pr66 = p.Project()
    _pr66.add_images([IMG[0], IMG[1]])
    _pr66.clips[0].dur = 1.0
    _pr66.clips[1].dur = 1.0
    _pr66.fps = 30
    _pr66.audios = [p.AudioClip(TONE, 6.0, 0.0, 1.0, gap=True)]
    p.render_audio_mix(_pr66.clips, _pr66.frame_counts(), _pr66.audios,
                       _aout66, fps=30)
    _d66 = subprocess.run([p.FFPROBE, "-v", "error", "-show_entries",
                           "format=duration", "-of",
                           "default=nw=1:nk=1", _aout66],
                          capture_output=True, text=True).stdout.strip()
    check("音轨时长跟成片一致", abs(float(_d66 or 0) - 2.0) < 0.05, _d66)
    _c66 = subprocess.run([p.FFPROBE, "-v", "error", "-select_streams", "a:0",
                           "-show_entries", "stream=codec_name", "-of",
                           "default=nw=1:nk=1", _aout66],
                          capture_output=True, text=True).stdout.strip()
    check("音轨是 aac", _c66 == "aac", _c66)
    _sil66 = os.path.join(_mixdir, "silent.m4a")
    p.render_audio_mix(_pr66.clips, _pr66.frame_counts(), [], _sil66, fps=30)
    check("没音乐也有一条静音轨当钟",
          os.path.exists(_sil66) and os.path.getsize(_sil66) > 200)

# ---------------- 67 实时预览接线
check("预览换成自绘画布", "self.video = PreviewCanvas()" in _src
      and "class PreviewCanvas(QWidget)" in _src)
check("播放器不再挂视频输出", "setVideoOutput" not in _src)
check("播放时按播放器位置逐帧画", "def _tick_frame" in _src
      and "self._draw_frame(self.player.position() / 1000.0)" in _src)
check("改参数立刻重建场景", "def _refresh_scene" in _src
      and "self.scene.reconfigure(" in _src)
check("预览不再渲整片", "preview_{self.preview_seq}.mp4" not in _src,
      "还留着整片预览路径")
check("音轨单独混成主时钟", "class AudioMixWorker(QThread)" in _src
      and "def render_audio_mix" in _src)
check("音轨有指纹防抖", "def _audio_signature" in _src
      and "self._mix_timer.start()" in _src)
check("拖动进度条实时出帧", "self._render_at(cur)" in _src)
check("画面按时间现画", "self.scene.frame_at(t)" in _src)
check("预览词条四语齐全", all(k in tbl for k in (
      "实时合成", "预览：实时合成", "预览已更新（实时合成）",
      "预览音频失败", "音频混音失败") for tbl in _i18n.values()),
      [k for k in ("实时合成", "预览：实时合成") if not all(
          k in tbl for tbl in _i18n.values())])

# ---------------- 68 视频片段：按时间出帧（片头片尾）
if p.missing_tools():
    print("  (没 ffmpeg，跳过视频片段实测)")
else:
    _vdir = tempfile.mkdtemp(prefix="p2v_vid_")
    _vpath = os.path.join(_vdir, "clip.mp4")
    subprocess.run([p.FFMPEG, "-y", "-nostdin", "-loglevel", "error",
                    "-f", "lavfi", "-i",
                    "testsrc=size=160x90:rate=30:duration=2",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264",
                    "-preset", "ultrafast", _vpath], check=False)
    _vclip = p.Clip(_vpath, 1.0, kind="intro")
    _sv68 = p.Scene([_vclip, p.Clip(_red, 1.0)], [30, 30], 30,
                    dict(w=160, h=90, fit="own", bg_blur=False,
                         transition="none", subtitle_size=20))
    _vf1 = _sv68.frame_at(0.1)
    _vf2 = _sv68.frame_at(0.9)
    check("视频片段能出帧", not _vf1.isNull() and _vf1.width() == 160)
    check("视频两帧画面不同", _vf1.cacheKey() != _vf2.cacheKey())
    _vf3 = _sv68.frame_at(0.2)
    check("视频往回跳也能出帧", not _vf3.isNull() and _vf3.width() == 160)
    _sv68.close()
    check("关掉场景会放掉视频管道", not _sv68._readers)

# 54 切段 / 改时长后自动收转场（免得导出时才报「转场放不下」）
_pj54 = p.Project()
_pj54.clips = [p.Clip(IMG[0], 2.0, xfade=True, xfade_name="wipeleft",
                       xfade_dur=1.5), p.Clip(IMG[1], 2.0)]
_n54 = _pj54.fit_transitions()
check("转场超过可用的一半就缩到一半",
      _n54 == 1 and abs(_pj54.clips[0].xfade_dur - 1.0) < 1e-9,
      (_n54, _pj54.clips[0].xfade_dur))
_pj54.clips[1].dur = 0.1
_n54 = _pj54.fit_transitions()
check("连最短转场都塞不下就让开",
      _n54 == 1 and not _pj54.clips[0].xfade
      and _pj54.clips[0].xfade_name == ""
      and _pj54.clips[0].xfade_dur is None,
      (_pj54.clips[0].xfade, _pj54.clips[0].xfade_name))
_pj54b = p.Project()
_pj54b.clips = [p.Clip(IMG[0], 3.0, xfade=True, xfade_name="radial",
                       xfade_dur=0.8), p.Clip(IMG[1], 3.0)]
_pj54b.split_at(0.5)
check("切一刀顺手收转场（0.5 秒的段只放 0.25）",
      abs(_pj54b.clips[0].xfade_dur - 0.25) < 1e-9,
      _pj54b.clips[0].xfade_dur)
_pj54c = p.Project()
_pj54c.mode = "free"
_pj54c.clips = [p.Clip(IMG[0], 2.0, xfade=True, xfade_name="fade",
                       xfade_dur=0.8), p.Clip(IMG[1], 2.0)]
_pj54c.set_duration(0, 0.6)
check("改短一张也顺手收",
      _pj54c.clips[0].xfade_dur <= 0.3 + 1e-9, _pj54c.clips[0].xfade_dur)
_pj54d = p.Project()
_pj54d.clips = [p.Clip(IMG[0], 2.0, xfade=True, xfade_name="fade"),
                p.Clip(IMG[1], 2.0)]
check("跟随全局时长的转场不被动",
      _pj54d.fit_transitions() == 0 and _pj54d.clips[0].xfade_dur is None,
      _pj54d.clips[0].xfade_dur)

# ---------------- 69 进度条点击跳转 + 设置保存不再崩
# (1) 纯几何：鼠标 x → 数值。以前 pageStep=0 导致点滑轨毫无反应。
check("滑轨中点取一半", p.slider_value_from_x(217, 0, 434, 12, 0, 3000) == 1500,
      p.slider_value_from_x(217, 0, 434, 12, 0, 3000))
check("滑轨 75% 取 2268", p.slider_value_from_x(325, 0, 434, 12, 0, 3000) == 2268,
      p.slider_value_from_x(325, 0, 434, 12, 0, 3000))
check("点到最左是 0", p.slider_value_from_x(0, 0, 434, 12, 0, 3000) == 0)
check("点到最右是最大值", p.slider_value_from_x(434, 0, 434, 12, 0, 3000) == 3000)
check("超出边界夹住", p.slider_value_from_x(9999, 0, 434, 12, 0, 3000) == 3000)
check("反向滑轨取反", p.slider_value_from_x(325, 0, 434, 12, 0, 3000, True) == 732,
      p.slider_value_from_x(325, 0, 434, 12, 0, 3000, True))
check("滑轨太窄不炸", p.slider_value_from_x(10, 0, 8, 12, 0, 3000) in (0, 3000))
check("区间为空不炸", p.slider_value_from_x(10, 0, 0, 0, 5, 5) == 5)
check("非零下界也按比例", p.slider_value_from_x(217, 0, 434, 12, 100, 200) == 150,
      p.slider_value_from_x(217, 0, 434, 12, 100, 200))

# (2) 接线：进度条用 SeekSlider，鼠标事件自己算
check("进度条换成 SeekSlider", "self.slider_pos = SeekSlider(Qt.Orientation.Horizontal)" in _src)
check("SeekSlider 有按下处理", "class SeekSlider(QSlider):" in _src and
      "def mousePressEvent(self, ev):" in _src)
check("SeekSlider 用纯几何换算", "return slider_value_from_x(" in _src)
check("拖动也走同一套换算", _src.count("self._value_at(ev.position().x())") >= 3,
      _src.count("self._value_at(ev.position().x())"))

# (3) 设置保存这条路上 log 的占位符与实参必须对得上。
#     线上真的崩过：on_settings 里 log("...%s" % (...6 个值)) 少一个占位符，
#     TypeError 把后面 apply_theme() 全吞了，于是"切主题不生效还看不出错"。
import ast as _ast
import re as _re
_src_tree = _ast.parse(_src)
_fmt_bad = []
for _node in _ast.walk(_src_tree):
    if not (isinstance(_node, _ast.Call) and isinstance(_node.func, _ast.Name)
            and _node.func.id in ("log", "print")):
        continue
    for _arg in _node.args:
        if not (isinstance(_arg, _ast.BinOp)
                and isinstance(_arg.op, _ast.Mod)
                and isinstance(_arg.left, _ast.Constant)
                and isinstance(_arg.left.value, str)):
            continue
        _n = len(_re.findall(r"%(?!%)[-+ #0]*\d*(?:\.\d+)?[sdfrxegc]",
                             _arg.left.value))
        if isinstance(_arg.right, _ast.Tuple):
            _m = len(_arg.right.elts)
        elif isinstance(_arg.right, _ast.Dict):
            continue
        else:
            _m = 1
        if _n != _m:
            _fmt_bad.append((_node.lineno, _n, _m))
check("log/print 的 %s 个数与实参一致", not _fmt_bad, _fmt_bad)
check("设置保存的日志行有 6 个占位符",
      _src.count('log("settings saved lang=%s ui=%s theme=%s ratio=%s size=%sx%s"') == 1)

# ---------------- 70 撤回要把设置引起的界面状态也拉回来
def _func_body(name, nxt):
    a = _src.index("def %s(" % name)
    b = _src.index("def %s(" % nxt, a)
    return _src[a:b]

_undo_body = _func_body("_history_apply", "on_undo")
check("撤回后会重新绑快捷键（否则设置改回去但键还旧的）",
      "self._bind_shortcuts()" in _undo_body)
check("撤回后换语言/主题仍然生效", "self.apply_theme()" in _undo_body
      and "self.retranslate()" in _undo_body)
check("撤回后素材列表跟着刷新（走 retranslate 里的 _refresh_all）",
      "self._refresh_all()" in _func_body("retranslate", "_player_retranslate"))

# ---------------- 71 用起来顺手的修补（多选 / 方向键 / 批量重定位…）
_sel_body = _func_body("_selected_rows", "_select_rows")
check("列表多选：SelectionMode 是 ExtendedSelection",
      "SelectionMode.ExtendedSelection" in _src)
check("列表选中一改就同步时间线", "itemSelectionChanged.connect(self.on_selection_changed)"
      in _src)
check("选中行先看列表选中、再回落到当前行",
      "self.list.selectedItems()" in _sel_body and "currentRow()" in _sel_body)
check("没选中时返回空表而不是 [-1]", "if row >= 0" in _sel_body)
_sync_body = _func_body("_sync_selection", "on_selection_changed")
check("同步选中走 _selected_rows 并设时间线",
      "self.timeline.set_selection(self._selected_rows())" in _sync_body)
_pick_body = _func_body("on_timeline_pick", "on_timeline_resize")
check("时间线上点选也会回头高亮列表", "self._select_rows(rows)" in _pick_body)
check("时间线多选时不再被 setCurrentRow 缩成一行",
      "NoUpdate" in _func_body("_select_rows", "_sync_selection"))

_rm_body = _func_body("on_remove", "on_edit_dur")
check("删除按选中的那几行走，不再只删当前行",
      "rows = self._selected_rows()" in _rm_body
      and "self.project.remove(rows)" in _rm_body
      and "currentRow" not in _rm_body)
_mv_body = _func_body("on_move", "on_remove")
check("上移/下移支持整块挪",
      "for i in order:" in _mv_body and "reversed(rows)" in _mv_body)
check("挪完把选中还原到新位置",
      "self._select_rows(" in _mv_body)
_ed_body = _func_body("on_edit_dur", "on_rows_moved")
check("双击改时长支持多选一起改",
      "for i in rows:" in _ed_body and "选中的 {n} 张一起改（秒）：" in _ed_body)
check("多选改时长有专门的标题", "改选中图片的时长" in _ed_body)

check("音乐链接输入框回车就取音轨",
      "self.edit_bgm.returnPressed.connect(self.start_fetch)" in _src)

_ev_body = _func_body("eventFilter", "on_scrub_start")
check("方向键可步进播放头", "Qt.Key.Key_Left" in _ev_body
      and "Qt.Key.Key_Right" in _ev_body)
check("Shift+方向键一秒、否则一帧",
      "ShiftModifier" in _ev_body and '1.0 / max(1.0, float(self.settings["fps"]))'
      in _ev_body)
check("Home / End 跳首尾",
      "Qt.Key.Key_Home" in _ev_body and "_export_total()" in _ev_body)
check("输入框/下拉框/列表里不抢方向键",
      "QLineEdit, QTextEdit, QPlainTextEdit," in _ev_body
      and "QAbstractSpinBox, QComboBox," in _ev_body)
check("模态框打开时不插手", "QApplication.activeModalWidget()" in _ev_body)
check("右键菜单弹出时不抢方向键", "QApplication.activePopupWidget()" in _ev_body)
check("全应用装了这个过滤器",
      "QApplication.instance().installEventFilter(self)" in _src)

check("时间线块上带文件名并按宽度截断",
      'os.path.basename(clip.path or "")' in _src
      and "elidedText(" in _src)

_rl_body = _func_body("_relink_missing", "on_save_project")
check("多个素材丢了可以先一起选文件夹",
      "一起重新定位" in _rl_body and "_relink_from_folder" in _rl_body)
_fd_body = _func_body("_relink_from_folder", "_relink_missing")
check("按文件名在文件夹里找替身",
      "os.path.join(folder, os.path.basename(old))" in _fd_body)

_ux_keys = ["改选中图片的时长", "选中的 {n} 张一起改（秒）：", "一起重新定位",
            "选文件夹…", "一个个来", "选这些素材现在所在的文件夹"]
check("新词条四语齐全",
      all(k in _i18n.get(lang, {}) for k in _ux_keys
          for lang in ("zh-TW", "en", "ja", "ko")),
      [k for k in _ux_keys if any(k not in _i18n.get(l, {}) for l in
                                  ("zh-TW", "en", "ja", "ko"))])
check("批量重定位的说明句子四语齐全",
      all("有 {n} 个素材不见了。要是它们还在同一个文件夹里，可以一次选文件夹，按文件名自动对上。"
          in _i18n.get(l, {}) for l in ("zh-TW", "en", "ja", "ko")))

# ---------------- 72 拖拽重排要保住每段的设置
_pj = p.Project()
for _i in range(3):
    _pj.clips.append(p.Clip(os.path.join(tmp, "r%d.png" % _i), 2.0 + _i))
_pj.clips[0].fade = True
_pj.clips[0].xfade = True
_pj.clips[0].xfade_name = "wipeleft"
_pj.clips[0].xfade_dur = 0.5
_pj.clips[0].src_in, _pj.clips[0].src_out = 1.0, 4.0
_pj.clips[1].kind = "intro"
_objs = list(_pj.clips)
check("reorder_clips 接受一个排列", _pj.reorder_clips([2, 0, 1]) is True)
check("重排后顺序就是给的顺序", _pj.clips == [_objs[2], _objs[0], _objs[1]])
check("重排后调色/转场/淡入淡出还在",
      _pj.clips[1].fade and _pj.clips[1].xfade
      and _pj.clips[1].xfade_name == "wipeleft"
      and abs(_pj.clips[1].xfade_dur - 0.5) < 1e-9)
check("重排后入出点还在",
      abs(_pj.clips[1].src_in - 1.0) < 1e-9
      and abs(_pj.clips[1].src_out - 4.0) < 1e-9)
check("重排后片头标记还在", _pj.clips[2].kind == "intro")
check("重排后时长没被归一化改掉",
      [round(c.dur, 3) for c in _pj.clips] == [4.0, 2.0, 3.0])
check("不是排列就拒绝（重复下标）", _pj.reorder_clips([0, 0, 1]) is False)
check("不是排列就拒绝（长度不对）", _pj.reorder_clips([0, 1]) is False)
check("不是排列就拒绝（越界）", _pj.reorder_clips([0, 1, 9]) is False)
_before = list(_pj.clips)
_pj.reorder_clips([0, 0, 1])
check("拒绝时一段都不动", _pj.clips == _before)
_old = p.Project()
_old.clips = [p.Clip(os.path.join(tmp, "o%d.png" % _i), 1.0) for _i in range(2)]
_old.reorder([(os.path.join(tmp, "o1.png"), 1.0),
              (os.path.join(tmp, "o0.png"), 1.0)])
check("老的 reorder 还留着当兜底",
      [os.path.basename(c.path) for c in _old.clips] == ["o1.png", "o0.png"])

check("列表项记着原始下标",
      "it.setData(Qt.ItemDataRole.UserRole + 2, i)" in _src)
_moved_body = _func_body("on_rows_moved", "on_row_changed")
check("拖拽重排按原始下标找回 Clip",
      "UserRole + 2" in _moved_body and "reorder_clips(iids)" in _moved_body)
check("认不出下标时才退回重建",
      "self.project.reorder(pairs)" in _moved_body)
check("重排后把选中还原回新位置",
      "iids.index(k)" in _moved_body)

# ---------------- 73 时间线磁吸 / 音频裁头尾 / 刀片切开 / 涟漪删除
_v, _h = p.snap_value(3.0, [1.0, 3.0, 5.0], 0.2)
check("磁吸：正好落在边上", _v == 3.0 and _h == 3.0)
_v, _h = p.snap_value(3.1, [1.0, 3.0], 0.2)
check("磁吸：容差内吸过去", abs(_v - 3.0) < 1e-9 and _h == 3.0)
_v, _h = p.snap_value(3.4, [1.0, 3.0], 0.2)
check("磁吸：容差外不动", abs(_v - 3.4) < 1e-9 and _h is None)
_v, _h = p.snap_value(1.0, [], 0.2)
check("磁吸：没有目标就原样返回", _v == 1.0 and _h is None)
_v, _h = p.snap_value(2.0, [1.0, 2.5], 0.0)
check("磁吸：容差 0 只吸正好的", _h is None)
_v, _h = p.snap_value(2.4, [2.0, 2.6], 0.5)
check("磁吸：挑最近的那个", _h == 2.6)

_pj = p.Project()
_pj.clips.append(p.Clip(IMG[0], 3.0))
_pj.clips.append(p.Clip(IMG[1], 3.0))
_pj.audios.append(p.AudioClip(TONE, 2.0, 1.0))
_pj.overlays.append(p.Overlay(IMG[2], 0.5, 1.5))
_cand = p.snap_candidates(_pj, 2.0)
check("吸附点含首尾", 0.0 in _cand and 3.0 in _cand and 6.0 in _cand, _cand)
check("吸附点含播放头", 2.0 in _cand)
check("吸附点含音频首尾", 1.0 in _cand and 3.0 in _cand)
check("吸附点含画中画首尾", 0.5 in _cand and 1.5 in _cand)
check("吸附点有序且不重复", _cand == sorted(set(_cand)))
check("吸附点能再补几个", 4.5 in p.snap_candidates(_pj, 2.0, extra=(4.5,)))

_a = p.AudioClip(TONE, 4.0, 1.0, 1.0, False, 0.0,
                 [[0.0, 1.0], [1.0, 0.6], [3.0, 0.2]], 0.5)
check("音频默认从源的第 0 秒开始", p.AudioClip(TONE, 2.0).src_in == 0.0)
check("音频记得裁头位置", abs(_a.src_in - 0.5) < 1e-9)
_pj2 = p.Project()
_pj2.clips.append(p.Clip(IMG[0], 2.0))
_pj2.audios.append(_a)
_doc = _pj2.to_doc()
check("工程文件里写着裁头位置",
      abs(_doc["audios"][0]["src_in"] - 0.5) < 1e-9)
_back = p.Project.from_doc(json.loads(json.dumps(_doc)))
check("读回来裁头位置还在", abs(_back.audios[0].src_in - 0.5) < 1e-9)
_olddoc = _pj2.to_doc()
del _olddoc["audios"][0]["src_in"]
check("老工程没这个字段就当 0",
      p.Project.from_doc(_olddoc).audios[0].src_in == 0.0)
_g = p.build_audio_graph(
    6.0, timeline=[(True, 6.0)],
    extras=[{"index": 1, "start": 0.5, "dur": 2.0, "src_in": 2.0,
             "volume": 1.0, "gain_db": 0.0, "points": None}])
check("裁头之后从源的 2 秒开始取", "atrim=start=2.000:end=4.000" in _g)

_split = p.Project()
_split.clips.append(p.Clip(IMG[0], 6.0))
_split.audios.append(p.AudioClip(TONE, 4.0, 1.0, 1.0, False, 0.0,
                                 [[0.0, 1.0], [1.0, 0.6], [3.0, 0.2]], 0.5))
check("音频能在 2 秒处切开", _split.split_audio(0, 2.0) is True)
check("切开后变成两条", len(_split.audios) == 2)
_left, _right = _split.audios
check("左条到切开点为止",
      abs(_left.dur - 1.0) < 1e-9 and abs(_left.start - 1.0) < 1e-9)
check("右条接着往后",
      abs(_right.start - 2.0) < 1e-9 and abs(_right.dur - 3.0) < 1e-9)
check("右条从源的更后面开始", abs(_right.src_in - 1.5) < 1e-9)
check("包络点按切开点分开",
      [round(x[0], 3) for x in _left.points] == [0.0, 1.0]
      and [round(x[0], 3) for x in _right.points] == [0.0, 2.0],
      [_left.points, _right.points])
_bad = p.Project()
_bad.clips.append(p.Clip(IMG[0], 6.0))
_bad.audios.append(p.AudioClip(TONE, 4.0, 1.0))
check("太靠开头切不了", _bad.split_audio(0, 1.02) is False)
check("太靠结尾切不了", _bad.split_audio(0, 4.98) is False)
check("越界切不了", _bad.split_audio(9, 3.0) is False)
_gap = p.Project()
_gap.clips.append(p.Clip(IMG[0], 6.0))
_gap.audios.append(p.AudioClip(TONE, 2.0, 0.0, 1.0, True))
check("空白占位不能切", _gap.split_audio(0, 1.0) is False)
_rm = p.Project()
_rm.clips.append(p.Clip(IMG[0], 10.0))
_rm.audios.append(p.AudioClip(TONE, 2.0, 0.0))
_rm.audios.append(p.AudioClip(TONE, 1.0, 5.0))
_rm.audios.append(p.AudioClip(TONE, 1.0, 8.0))
_rm.remove_audio([0], ripple=True)
check("涟漪删除把后面的音频往前挪",
      [round(a.start, 3) for a in _rm.audios] == [3.0, 6.0],
      [a.start for a in _rm.audios])
_rm2 = p.Project()
_rm2.clips.append(p.Clip(IMG[0], 10.0))
_rm2.audios.append(p.AudioClip(TONE, 2.0, 0.0))
_rm2.audios.append(p.AudioClip(TONE, 1.0, 5.0))
_rm2.remove_audio([0])
check("不平移时后面的音频不动",
      [round(a.start, 3) for a in _rm2.audios] == [5.0])

for _pat, _name in [
        ("cut = pyqtSignal(str, int, float)", "时间线有切开信号"),
        ("audioTrimmed = pyqtSignal(int, str, float)", "时间线有裁音频信号"),
        ("audioTrimDone = pyqtSignal(int)", "松手才记历史"),
        ("def set_snap", "时间线有磁吸开关"),
        ("def set_razor", "时间线有切割模式"),
        ("def _snap_time", "时间线会算吸附"),
        ("def _hit_audio_edge", "音频块左右边能命中"),
        ("if self.snap_guide is not None:", "吸附时画参考线"),
        ("Qt.PenStyle.DashLine", "参考线是虚线"),
        ("atrimL", "音频左边能拖"),
        ("atrimR", "音频右边能拖"),
        ("if self.razor:", "切割模式先看自己在不在"),
        ("self.btn_snap = btn(tr(\"磁吸\")", "工具条有磁吸按钮"),
        ("self.btn_razor = btn(tr(\"切割\")", "工具条有切割按钮"),
        ("def on_toggle_snap", "磁吸按钮有处理函数"),
        ("def on_toggle_razor", "切割按钮有处理函数"),
        ("def on_cut", "切开有处理函数"),
        ("def on_audio_trim", "裁音频有处理函数"),
        ("def on_audio_trim_done", "裁完记历史"),
        ("def on_remove_audio_ripple", "音频能涟漪删除"),
        ("self.project.remove_audio(rows, ripple=True)", "涟漪删除调对了接口"),
        ("audioTrimmed.connect(self.on_audio_trim)", "裁音频接上了"),
        ("audioTrimDone.connect(self.on_audio_trim_done)", "裁完接上了"),
        ("self.timeline.cut.connect(self.on_cut)", "切开接上了"),
        ("self.timeline.set_snap(bool(self.settings[\"snap_on\"]))", "启动时恢复磁吸开关"),
        ("if key == Qt.Key.Key_Escape and self.timeline.razor:", "Esc 退出切割模式"),
]:
    check(_name, _pat in _src)

_snap_keys = ["磁吸", "吸附：拖动时贴住附近的边（Alt 临时关）", "切割",
              "切割模式：点哪块切哪块（Esc 退出）", "磁吸已开", "磁吸已关",
              "切割模式已开：点哪块切哪块，Esc 退出", "切割模式已关",
              "这儿切不了：离段首或段尾太近", "已切开", "切开", "裁音频",
              "这条音频 {a} → {b} 秒", "删除并前移",
              "删掉这条音频，后面的音频整体往前挪", "删音频并前移"]
check("磁吸/切割的词条四语齐全",
      all(k in _i18n.get(lang, {}) for k in _snap_keys
          for lang in ("zh-TW", "en", "ja", "ko")),
      [k for k in _snap_keys if any(k not in _i18n.get(l, {})
                                    for l in ("zh-TW", "en", "ja", "ko"))])


check("磁吸/切割按钮走 toggled，程序里改选中也能生效",
      'def btn(text, slot, tip="", signal="clicked")' in _src
      and '"toggled"' in _src)

# ---------------- 74 时间线缩放 / 横向滚动
check("1 倍时窗口就是整片", abs(p.timeline_span(8.0, 1.0) - 8.0) < 1e-9)
check("放大 2 倍只看一半", abs(p.timeline_span(8.0, 2.0) - 4.0) < 1e-9)
check("放大 4 倍只看四分之一", abs(p.timeline_span(8.0, 4.0) - 2.0) < 1e-9)
check("缩放倍数夹在 1..64 之间",
      abs(p.timeline_span(8.0, 0.2) - 8.0) < 1e-9
      and abs(p.timeline_span(8.0, 1000.0)
              - 8.0 / p.TIMELINE_ZOOM_MAX) < 1e-9)
check("空片子不给负窗口", p.timeline_span(0.0, 4.0) == 0.0)
check("窗口起点夹在片子里",
      abs(p.timeline_clamp_start(3.0, 8.0, 4.0) - 3.0) < 1e-9
      and abs(p.timeline_clamp_start(6.0, 8.0, 4.0) - 4.0) < 1e-9
      and p.timeline_clamp_start(-2.0, 8.0, 4.0) == 0.0)
check("窗口比片子长就从头看", p.timeline_clamp_start(3.0, 8.0, 8.0) == 0.0)
check("时间换算成像素",
      abs(p.timeline_x(4.0, 2.0, 4.0, 400) - 200.0) < 1e-9
      and abs(p.timeline_x(2.0, 2.0, 4.0, 400)) < 1e-9
      and abs(p.timeline_x(1.0, 2.0, 4.0, 400) + 100.0) < 1e-9)
check("像素换算回时间并夹在片内",
      abs(p.timeline_time(200, 2.0, 4.0, 400, 8.0) - 4.0) < 1e-9
      and p.timeline_time(-400, 2.0, 4.0, 400, 8.0) == 0.0
      and abs(p.timeline_time(1000, 2.0, 4.0, 400, 8.0) - 8.0) < 1e-9)
check("缩放时锚点不动",
      abs(p.timeline_zoom_start(2.0, 4.0, 4.0, 2.0, 8.0) - 3.0) < 1e-9)
_ns74 = p.timeline_zoom_start(2.0, 4.0, 4.0, 2.0, 8.0)
check("放大后鼠标下的时刻还在原来的像素",
      abs(p.timeline_x(4.0, _ns74, 2.0, 400) - 200.0) < 1e-9)
check("缩回整片时从头看",
      p.timeline_zoom_start(2.0, 4.0, 4.0, 8.0, 8.0) == 0.0)
check("缩小时起点不会超出右端",
      abs(p.timeline_zoom_start(4.0, 4.0, 8.0, 6.0, 8.0) - 2.0) < 1e-9)

_tl_src = _src[_src.index("class Timeline(QWidget):"):]
_tl_src = _tl_src[:_tl_src.index("class ColorWheel(QWidget):")]
check("时间线有缩放状态与窗口起点",
      "self.zoom = 1.0" in _tl_src and "self.view_start = 0.0" in _tl_src)
check("时间线把缩放/滚动变化发出来",
      "viewChanged = pyqtSignal(float, float)" in _tl_src
      and "self.viewChanged.emit(self.view_start" in _tl_src)
_zoom_body = _tl_src[_tl_src.index("def _view_span(self) -> float:"):
                     _tl_src.index("def _wave(self, path: str):")]
check("缩放/滚动都走纯函数算窗口",
      "timeline_span(self._total(), self.zoom)" in _zoom_body
      and "timeline_clamp_start(" in _zoom_body
      and "timeline_zoom_start(" in _zoom_body)
check("缩放有上下限、锚点默认播放头",
      "TIMELINE_ZOOM_MIN" in _zoom_body and "TIMELINE_ZOOM_MAX" in _zoom_body
      and "anchor_t = self.play_pos" in _zoom_body)
check("播放头走出窗口时窗口跟上",
      "def ensure_visible" in _zoom_body and "self.set_view_start(t" in _zoom_body)
_zw_body = _src[_src.index("def wheelEvent"):
                _src.index("class ColorWheel(QWidget):")]
check("Ctrl+滚轮缩放、以鼠标位置为中心",
      "Qt.KeyboardModifier.ControlModifier" in _zw_body
      and "self.zoom_at(x, dy > 0)" in _zw_body)
check("Shift+滚轮/空白处横向滚动",
      "self.scroll_by(-dy / 120.0" in _zw_body
      and "Qt.KeyboardModifier.ShiftModifier" in _zw_body)
check("音频块上普通滚轮仍是调音量",
      "self.audioVolume.emit(k, step)" in _zw_body)
check("各段像素位置按可见窗口算",
      "timeline_x(x, start, span, self.width())" in _tl_src
      and "timeline_x(max(0.0, a.start), start, span," in _tl_src
      and "timeline_x(max(0.0, o.start), start, span," in _tl_src)
check("拖动改长度的增量按可见窗口换算",
      "(x - x0) * self._view_span() / max(1, self.width())" in _tl_src)
check("窗口外的边也能算准（不夹范围）",
      "def _raw_time" in _tl_src and "self._raw_time(xs0 if kind" in _tl_src)
check("播放头/吸附线只画在窗口里",
      "if 0 <= px <= self.width():" in _tl_src
      and "if 0 <= gx <= self.width():" in _tl_src)
check("画之前先把窗口拉回片子范围",
      "self.clamp_view()" in _tl_src)

_sync_body = _func_body("_sync_tl_scroll", "on_tl_scroll")
check("下面有横向滚动条，范围按缩放算",
      "self.tl_scroll.setRange(0, max(0, tot_ms - span_ms))" in _sync_body
      and "self.tl_scroll.setPageStep(span_ms)" in _sync_body)
check("滚动条拖动就挪窗口",
      "self.timeline.set_view_start(value / 1000.0)" in _src)
check("时间线自己缩放时滚动条跟着对齐",
      "self.timeline.viewChanged.connect(self._on_tl_view)" in _src
      and "def _on_tl_view(self, start, span):" in _src)
check("刷新后重新算滚动条范围", "self._sync_tl_scroll()" in _src[
      _src.index("def _refresh_all(self):"):_src.index("def _refresh_all(self):") + 1200])
check("清晰度/缩放变化不影响成片",
      "self.btn_fit.clicked.connect(self.on_tl_fit)" in _src
      and "def on_tl_fit(self):" in _src
      and "self.timeline.reset_view()" in _src)
_ev74 = _func_body("eventFilter", "on_scrub_start")
check("Ctrl+0 回到全片、Ctrl+= / Ctrl+- 缩放",
      "Qt.Key.Key_0" in _ev74 and "self.timeline.reset_view()" in _ev74
      and "self.on_tl_zoom(" in _ev74)
check("播放时播放头走出窗口自动跟", "self.timeline.ensure_visible(" in _src)
check("滚动条/缩放按钮都在界面上",
      "self.tl_scroll = QScrollBar(Qt.Orientation.Horizontal)" in _src
      and "self.lbl_zoom = QLabel(tr(\"全片\"))" in _src
      and "self.btn_fit = QPushButton(tr(\"适应窗口\"))" in _src)

check("放大后块上的文字贴到可见区左边、按可见宽度截断",
      "tx = max(int(x0) + 6, 6)" in _tl_src
      and "tx = max(int(x0) + 5, 5)" in _tl_src
      and "if x1 - x0 > 26 and x1 > 0 and x0 < self.width():" in _tl_src)

_zoom_keys = ["全片", "适应窗口",
              "时间线横向滚动：拖这里看后半段；Ctrl+滚轮缩放",
              "时间线缩放：Ctrl+滚轮，Ctrl+0 回到全片",
              "时间线缩回全片（Ctrl+0）"]
check("缩放/滚动词条四语齐全",
      all(k in _i18n.get(lang, {}) for k in _zoom_keys
          for lang in ("zh-TW", "en", "ja", "ko")),
      [k for k in _zoom_keys if any(k not in _i18n.get(l, {})
                                    for l in ("zh-TW", "en", "ja", "ko"))])

# ---------------- 75 界面三档：简洁 / 标准 / 专业
check("三档就这三个", p.UI_TIERS == ("simple", "normal", "pro"), p.UI_TIERS)
check("三档有中文名",
      p.UI_TIER_LABELS == {"simple": "简洁", "normal": "标准",
                           "pro": "专业"}, p.UI_TIER_LABELS)
check("认不出的档位按简洁办",
      [p.ui_tier(x) for x in (None, "", "Simple", "x", 3)]
      == ["simple"] * 5, [p.ui_tier(x) for x in (None, "", "x")])
check("三档各自保留",
      [p.ui_tier(x) for x in ("simple", "normal", "pro")]
      == ["simple", "normal", "pro"])
_simple = set(p.ui_toolbar("simple"))
check("简洁模式只剩四件事",
      _simple == {"add", "export", "mode", "more"}, _simple)
check("简洁模式没有剪辑工具",
      not (_simple & {"snap", "razor", "overlay", "settings", "project",
                      "undo", "redo", "up", "down", "remove"}), _simple)
_normal = set(p.ui_toolbar("normal"))
check("标准模式有日常剪辑那几件",
      {"project", "add", "overlay", "snap", "razor", "undo", "redo",
       "settings", "export", "mode"} <= _normal, _normal)
check("标准模式不露排序/节奏/刷新",
      not (_normal & {"up", "down", "remove", "rhythm", "total",
                      "refresh", "more"}), _normal)
_pro = set(p.ui_toolbar("pro"))
check("专业模式全给",
      {"up", "down", "remove", "rhythm", "total", "refresh"} <= _pro
      and len(_pro) == 16, sorted(_pro))
check("不认识的档位按简洁办",
      p.ui_toolbar("wat") == p.ui_toolbar("simple"))
check("标准以上才算进阶",
      [p.ui_advanced(x) for x in ("simple", "normal", "pro", "wat")]
      == [False, True, True, False])
_s75 = p.Settings(os.path.join(tmp, "s75.json"))
check("默认界面是简洁", _s75["ui"] == "simple", _s75["ui"])
_s75.update(ui="normal")
check("标准档能存住", _s75["ui"] == "normal", _s75["ui"])
_s75.update(ui="wat")
check("坏值回落简洁", _s75["ui"] == "simple", _s75["ui"])
check("设置对话框认三档",
      'self.tier = ui_tier(settings["ui"])' in _src
      and 'self.daily = self.tier != "simple"' in _src)
check("藏字段分两堆", '_hide_fields = {"normal": [], "pro": []}' in _src)
check("标准项都挂了 normal=True", _src.count("normal=True") >= 20,
      _src.count("normal=True"))
check("整组也能分档",
      'add_sep("音乐", normal=True)' in _src
      and 'add_sep("节奏与动效", normal=True)' in _src)
check("简洁档不显示专业组",
      'if g == "pro" or (g == "normal" and not self.daily):' in _src)
check("模式按钮变成菜单",
      "self.btn_mode.setPopupMode(" in _src
      and "self.btn_mode.setMenu(self._build_ui_menu())" in _src)
check("⋯ 里有工程和设置",
      "self.btn_more.setMenu(self._build_more_menu())" in _src
      and "def _build_more_menu" in _src)
check("可见性照名单执行",
      "allowed = set(ui_toolbar(tier))" in _src
      and "for name, w_ in widgets.items():" in _src)
check("简洁档折掉画中画轨", "advanced or bool(self.project.overlays)" in _src)
check("左栏按档位露页",
      "self.left_tabs.setTabVisible(1, advanced)" in _src
      and "self.left_tabs.setTabVisible(2, advanced or has_subs)" in _src)
check("倍速/清晰度是进阶项",
      "self.cmb_speed.setVisible(advanced)" in _src
      and "self.cmb_quality.setVisible(advanced)" in _src)
check("时间线缩放条按档位",
      "def _sync_tl_bar" in _src
      and 'ui_advanced(self.settings["ui"])' in _src)
check("切档立即生效并记住",
      "def on_set_ui" in _src and "self.settings.save()" in _src
      and "def on_toggle_mode" not in _src)
check("时间线能按需折叠轨",
      "def set_rows" in _src and "if not self.show_overlay:" in _src
      and "if not self.show_audio:" in _src)
check("没显示时命中测试不接",
      "if not self.show_overlay:" in _src
      and "_hit_overlay" in _src and "_hit_audio" in _src)
_tier_keys = ["标准", "更多：工程与设置",
              "界面模式：简洁只做三件事，标准日常剪辑，专业全部参数",
              "{name}模式"]
check("三档词条四语齐全",
      all(k in _i18n.get(lang, {}) for k in _tier_keys
          for lang in ("zh-TW", "en", "ja", "ko")),
      [k for k in _tier_keys
       if any(k not in _i18n.get(l) for l in ("zh-TW", "en", "ja", "ko"))])

# ---------------- 76 配音页：设置、接线与词条
_s76 = p.Settings(os.path.join(tmp, "settings_tts.json"))
check("配音默认走 Edge", _s76["tts_provider"] == "edge")
check("配音默认音色是晓晓", _s76["tts_voice"] == "zh-CN-XiaoxiaoNeural")
check("配音默认原速", _s76["tts_rate"] == 0)
check("配音默认段间停 0.6 秒", abs(float(_s76["tts_gap"]) - 0.6) < 1e-6)
check("配音默认开清理", _s76["tts_cleanup"] is True)
check("key 默认是空表", _s76["tts_keys"] == {})

_s76b = p.Settings(os.path.join(tmp, "settings_tts2.json"))
_s76b.update(tts_provider="nope", tts_rate=999, tts_gap=-3, tts_cleanup="x",
             tts_keys={"gemini": "  g-key  ", "azure": 5, "evil": "z"})
check("认不出的服务商回落 edge", _s76b["tts_provider"] == "edge")
check("语速夹到上限", _s76b["tts_rate"] == p.tts_engine.TTS_MAX_RATE)
check("段间停顿夹到 0", _s76b["tts_gap"] == 0.0)
check("清理开关非 bool 回 True", _s76b["tts_cleanup"] is True)
check("key 只收认识的、去空白",
      _s76b["tts_keys"] == {"gemini": "g-key"}, _s76b["tts_keys"])

check("配音工作线程在", "class TtsWorker(QThread)" in _src)
check("工作线程把 Event 交给引擎", "cancel=self._stop, **self.kwargs)" in _src)
check("工作线程调 synth_plan", "tts_engine.synth_plan(" in _src)
check("左栏有配音页", 'addTab(voice_page, tr("配音"))' in _src)
check("配音页只在标准以上出现",
      "self.left_tabs.setTabVisible(3, advanced)" in _src)
check("配音页有开始/取消/试听", all(x in _src for x in (
    "self.btn_tts_start", "self.btn_tts_cancel", "self.btn_tts_audition")))
check("配音页有提供方与音色下拉",
      "self.cmb_tts_provider" in _src and "self.cmb_tts_voice" in _src)
check("合成就写旁白", "set_narration(self.project" in _src)
check("合完切跟旁白模式", "set_narration(" in _src)
check("设置页有四个 key 输入框", all(x in _src for x in (
    "self.edit_key_gemini", "self.edit_key_azure", "self.edit_key_openai")))
check("设置页存 key", "tts_keys={" in _src.replace(" ", ""))
check("设置页有代理与停顿",
      "self.edit_tts_proxy" in _src and "self.spin_tts_gap" in _src)
check("配音页词条四语齐全",
      all(k in _i18n.get(lang, {}) for k in (
          "配音", "配音服务", "音色", "语速", "开始合成", "试听",
          "还没有旁白", "旁白好了：{dur}", "配音失败")
          for lang in ("zh-TW", "en", "ja", "ko")),
      [k for k in ("配音", "配音服务", "音色", "语速", "开始合成", "试听")
       if any(k not in _i18n.get(l) for l in ("zh-TW", "en", "ja", "ko"))])
check("提供方名字有译文",
      all(k in _i18n.get(lang, {}) for k in (
          "Edge（免费）", "Azure 语音", "OpenAI 兼容端点")
          for lang in ("zh-TW", "en", "ja", "ko")))


# ---------------- 77 配音清理接线（T12 音频清理与响度）
check("配音把清理开关传下去", 'cleanup=bool(s["tts_cleanup"])' in _src)
check("合成完显示响度", '已清理，响度 {lu} LUFS' in _src and 'res.get("cleaned")' in _src)
check("清理默认是开的", p.Settings(os.path.join(tmp, "settings_clean.json"))["tts_cleanup"] is True)
check("清理词条四语齐", all("（已清理，响度 {lu} LUFS）" in _i18n[L] for L in ("zh-TW", "en", "ja", "ko")),
      {L: _i18n[L].get("（已清理，响度 {lu} LUFS）") for L in ("en", "ja")})



# ---------------- 78 Gemini 配音接线
_eng_src78 = open(p.tts_engine.__file__, encoding="utf-8").read()
check("Gemini 音色表来自引擎",
      "TTS_VOICES = tts_engine.TTS_VOICES" in _src
      and '"gemini": list(GEMINI_VOICES)' in _eng_src78)
check("Gemini 语速框会禁用",
      'self.spin_tts_rate.setEnabled(provider != "gemini")' in _src)
check("Gemini 没语速时给提示", "Gemini 没有语速参数：想快就自己改文稿" in _src)
check("配音页会提示填 key",
      "要自己的 key：填在「设置 → 配音」里。" in _src)
check("引擎有 Gemini 模型名", "gemini-3.8-flash-tts" in
      open(os.path.join(ROOT, "tts_engine.py"), encoding="utf-8").read())
check("需要 key 的四家里 Gemini 其一",
      p.tts_engine.TTS_PROVIDERS["gemini"]["needs_key"] is True)
check("Gemini 词条四语齐",
      all("Gemini 没有语速参数：想快就自己改文稿" in _i18n[L]
          for L in ("zh-TW", "en", "ja", "ko")))



# ---------------- 79 对话模式（主讲 / 听友）
_s79 = p.Settings(os.path.join(tmp, "settings_dlg.json"))
check("对话音色 B 有默认值", _s79["tts_voice_b"] == "zh-CN-YunjianNeural", _s79["tts_voice_b"])
check("字幕前缀默认关", _s79["tts_prefix"] is False)
_s79["tts_voice_b"] = "   "
_s79["tts_prefix"] = "yes"
_s79 = p.Settings(os.path.join(tmp, "settings_dlg.json"))
_s79.update(tts_voice_b="   ", tts_prefix="yes")
_s79.save()
_s79 = p.Settings(os.path.join(tmp, "settings_dlg.json"))
check("空音色 B 回落默认", _s79["tts_voice_b"] == "zh-CN-YunjianNeural", _s79["tts_voice_b"])
check("前缀非布尔回落关", _s79["tts_prefix"] is False)
check("对话轮次解析在引擎里", callable(getattr(p.tts_engine, "parse_dialogue", None)))
check("对话计划在引擎里", callable(getattr(p.tts_engine, "dialogue_plan", None)))
check("轮间停顿是 0.35", abs(float(p.tts_engine.DIALOGUE_GAP) - 0.35) < 1e-6)
for _needle, _why in (
        ('self.cmb_tts_voice_b = QComboBox()', "配音页有音色 B 下拉"),
        ('vform.addRow(tr("音色 B"), self.cmb_tts_voice_b)', "音色 B 进表单"),
        ('self.chk_tts_prefix = QCheckBox(tr("字幕带「主讲：」这样的前缀"))', "有字幕前缀开关"),
        ('def _dialogue_turns(self) -> list:', "有对话检测"),
        ('tts_engine.parse_dialogue(self._script_text())', "检测走引擎解析"),
        ('def _show_dialogue(self, on: bool):', "没对话稿就藏起 B 音色"),
        ('tts_engine.dialogue_plan(text, provider)', "合成用对话计划"),
        ('kw["voices"] = {"主讲": self._tts_voice(),', "按角色分音色"),
        ('kw["gap"] = float(tts_engine.DIALOGUE_GAP)', "对话用 0.35 秒间隔"),
        ('kw["cue_prefix"] = bool(self.chk_tts_prefix.isChecked())', "前缀跟随开关"),
        ('tts_voice_b=self._tts_voice_b(),', "记住音色 B"),
        ('tts_prefix=bool(self.chk_tts_prefix.isChecked()),', "记住前缀开关")):
    check(_why, _needle in _src, _needle)
for _key, _tr in (("音色 B", "Voice B"),
                  ("字幕前缀", "Caption prefix"),
                  ("对话稿里「听友」用这个音色", "guest voice"),
                  ("字幕带「主讲：」这样的前缀", "Prefix captions")):
    _row = _i18n["en"].get(_key)
    check("对话词条四语齐 " + _key,
          bool(_row) and _tr.lower() in _row.lower(), _row)



# ---------------- 80 Azure 语音 / OpenAI 兼容端点接线
_s80 = p.Settings(os.path.join(tmp, "settings_az.json"))
check("模型名默认空", _s80["tts_model"] == "")
_s80.update(tts_model="  FunAudioLLM/CosyVoice2-0.5B  ")
_s80.save()
_s80 = p.Settings(os.path.join(tmp, "settings_az.json"))
check("模型名会去空白",
      _s80["tts_model"] == "FunAudioLLM/CosyVoice2-0.5B", _s80["tts_model"])
check("Azure 合成在引擎里", callable(getattr(p.tts_engine, "synth_azure", None)))
check("OpenAI 兼容合成在引擎里", callable(getattr(p.tts_engine, "synth_openai", None)))
check("SSML 生成在引擎里", callable(getattr(p.tts_engine, "azure_ssml", None)))
check("请求体生成在引擎里", callable(getattr(p.tts_engine, "openai_body", None)))
check("四家都在派发表里",
      p.tts_engine.synth_for("azure") is p.tts_engine.synth_azure
      and p.tts_engine.synth_for("openai") is p.tts_engine.synth_openai)
_eng_src80 = open(p.tts_engine.__file__, encoding="utf-8").read()
for _needle, _where, _why in (
        ('"azure": synth_azure', _eng_src80, "派发表有 Azure"),
        ('"openai": synth_openai', _eng_src80, "派发表有 OpenAI 兼容"),
        ('self.edit_tts_model = QLineEdit(settings["tts_model"])', _src, "设置页有模型名"),
        ('add_row("模型名", self.edit_tts_model, normal=True)', _src, "模型名进表单"),
        ('tts_model=self.edit_tts_model.text().strip(),', _src, "保存模型名"),
        ('extra={"model": str(s["tts_model"]),', _src, "模型名传给引擎"),
        ('"region": str(s["tts_azure_region"])', _src, "区域传给引擎"),
        ('**(extra or {})', _eng_src80, "引擎收 extra")):
    check(_why, _needle in _where, _needle)
for _key, _en in (("模型名", "Model"),
                  ("例如 FunAudioLLM/CosyVoice2-0.5B（留空 = tts-1）", "tts-1")):
    check("模型名词条四语齐 " + _key,
          bool(_i18n["en"].get(_key)) and _en in _i18n["en"][_key],
          _i18n["en"].get(_key))



# ---------------- 81 切服务商：音色表跟着换 / 地址不串味
for _needle, _why in (
        ('self.cmb_tts_provider.currentIndexChanged.connect(',
         "换服务商走 currentIndexChanged（程序化切换也生效）"),
        ("voices = TTS_VOICES.get(provider) or []", "音色表按服务商取"),
        ("elif wanted and wanted not in known:", "旧服务商音色名不硬留着"),
        ("TTS_VOICES = tts_engine.TTS_VOICES", "音色表在引擎里一份"),
        ('url = AZURE_ENDPOINT % region    # base_url 只属于 OpenAI 兼容端点，别串味',
         "Azure 不用 OpenAI 的地址"),
        ("root = GEMINI_API_ROOT    # base_url 只属于 OpenAI 兼容端点，别串味",
         "Gemini 不用 OpenAI 的地址")):
    _where = _eng_src80 if _why.startswith(("Azure", "Gemini")) else _src
    check(_why, _needle in _where, _needle)
check("引擎音色表 Azure 4 个", len(p.tts_engine.TTS_VOICES["azure"]) == 4)
check("引擎音色表 OpenAI 6 个", len(p.tts_engine.TTS_VOICES["openai"]) == 6)
check("界面音色表就是引擎那份",
      p.TTS_VOICES is p.tts_engine.TTS_VOICES)



# ---------------- 82 旁白位置：写清楚 + 能打开 / 另存
for _needle, _why in (
        ('self.btn_narr_open = QPushButton(tr("打开旁白文件夹"))',
         "配音页有打开文件夹按钮"),
        ('self.btn_narr_save = QPushButton(tr("旁白另存为…"))',
         "配音页有另存按钮"),
        ('self.btn_narr_open.clicked.connect(self.on_open_narration)',
         "打开按钮接线"),
        ('self.btn_narr_save.clicked.connect(self.on_save_narration_as)',
         "另存按钮接线"),
        ('subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])',
         "打开时定位到文件"),
        ('set_narration(self.project, picked, float(p.narration_dur),',
         "另存后工程改用新文件"),
        ('path=p.narration_path))', "界面写出完整文件路径"),
        ('"旁白已生成，已放进时间线「音频」轨：{name}"',
         "状态栏说清旁白进了音频轨"),
        ('self.lbl_narration.setTextInteractionFlags(',
         "旁白路径能选中复制")):
    check(_why, _needle in _src, _needle)
_eng_narr = open(p.tts_engine.__file__, encoding="utf-8").read()
check("旁白写在 tts/ 根下，文件名带内容指纹",
      'out_path = os.path.join(cache_dir(root), "narration_%s.m4a" % key[:10])'
      in _eng_narr and '"narration.m4a"' not in _eng_narr,
      "narration_%s" in _eng_narr)
for _key in ("打开旁白文件夹", "旁白另存为…", "把旁白存到你自己选的地方，工程跟着改用这份",
             "旁白没存下去", "旁白已存到：{path}",
             "旁白已生成，已放进时间线「音频」轨：{name}", "音频 (*.m4a)"):
    check("旁白位置词条四语齐 " + _key,
          all(_i18n[_l].get(_key) for _l in ("zh-TW", "en", "ja", "ko")),
          {_l: _i18n[_l].get(_key) for _l in ("zh-TW", "en", "ja", "ko")})

# ---------------- 83 东西放哪：BGM / 资源包 / 封面都写出完整路径
_media = p.persistent_media_dir()
check("程序自己取的素材有固定目录",
      os.path.isdir(_media) and os.path.abspath(_media).startswith(
          os.path.abspath(p.components.data_dir())), _media)
check("不再往临时目录里存 BGM", "FetchWorker(url, self.tmpdir" not in _src)
check("取音源落在固定目录", "FetchWorker(url, persistent_media_dir()" in _src)
check("BGM 文件名带链接指纹，不覆盖上一条",
      'bgm_%s.m4a" % stamp' in _src and 'hashlib.sha1(' in _src)
check("取完 BGM 写出完整路径",
      'tr("BGM 已就绪：{total} · 文件在：{path}"' in _src
      and 'path=path)' in _src)
check("BGM 路径也挂在输入框 tooltip 上",
      'self.edit_bgm.setToolTip(tr("当前 BGM：{path}", path=path))' in _src)
check("资源包装完告诉用户装在哪",
      'tr("{kind}「{name}」已经装进资源目录：\\n{path}"' in _src
      and 'path=asset_dir(pkg["kind"])' in _src)
check("设置页能打开资源文件夹",
      'self.btn_asset_open.clicked.connect(self._open_assets)' in _src
      and 'def _open_assets(self):' in _src
      and 'subprocess.Popen(["explorer", os.path.normpath(folder)])' in _src)
check("导出后状态栏给出封面完整路径",
      'tr("（封面：{name}）", name=cover)' in _src
      and 'name=os.path.basename(cover)' not in _src)
check("导出完成对话框也写封面路径",
      'body += tr("\\n\\n封面：\\n{path}", path=cover)' in _src)
check("封面接口把路径交回来", "    return out_path" in _src)
check("存完工程状态栏写出完整路径",
      'tr("工程已保存：{path}", path=path)' in _src)
check("封面成功时 SHOT 也打印路径", 'print("COVER_OK:", cover' in _src)
for _key in ("BGM 已就绪：{total} · 文件在：{path}", "当前 BGM：{path}",
             "打开资源文件夹", "在资源管理器里看这些包装在哪",
             "{kind}「{name}」已经装进资源目录：\n{path}", "\n\n封面：\n{path}",
             "工程已保存：{path}"):
    check("路径提示词条四语齐 " + _key,
          all(_i18n[_l].get(_key) for _l in ("zh-TW", "en", "ja", "ko")),
          {_l: _i18n[_l].get(_key) for _l in ("zh-TW", "en", "ja", "ko")})

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL -> {fails}")
sys.exit(1 if fails else 0)
