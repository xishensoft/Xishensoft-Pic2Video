# -*- coding: utf-8 -*-
"""时间线/帧数/设置/filtergraph 的单元检查（不碰 GUI；只碰 ffmpeg 的地方用错误路径）。"""
import os, sys, random, subprocess, tempfile
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
check("exe 里 print 有落处", "sys.stdout = fh" in _src and "sys.stderr = fh" in _src)
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
check("文件框非系统", "class FileDialog" in _src
      and "DontUseNativeDialog" in _src)
check("不再用系统弹窗", "QMessageBox(" not in _src
      and "QProgressDialog" not in _src and "QInputDialog" not in _src)
check("文件框箭头跟主题", "_arrow_icon" in _src
      and "toParentButton" in _src and "backButton" in _src)
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
      and "def on_toggle_xfade" in _src)
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

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL -> {fails}")
sys.exit(1 if fails else 0)
