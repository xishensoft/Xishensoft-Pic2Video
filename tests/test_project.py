# -*- coding: utf-8 -*-
"""时间线/帧数/设置/filtergraph 的单元检查（不碰 GUI；只碰 ffmpeg 的地方用错误路径）。"""
import os, sys, random, tempfile
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
check("video 背景 + 音频下标", "[3:a]atrim=0:6.000" in g and "bgx1" in g, g[-120:])
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

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL -> {fails}")
sys.exit(1 if fails else 0)
