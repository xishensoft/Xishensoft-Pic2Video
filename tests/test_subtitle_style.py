# -*- coding: utf-8 -*-
"""字幕样式：样式字典的洗值、ASS 换算、工程 v2→v3 迁移（不碰 GUI）。"""
import json, os, re, sys
try:                      # 控制台是 GBK，日/韩文本会炸，统一用 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:         # noqa: BLE001
    pass
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import pic2video as p
import subtitle_style as S

fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(extra) if extra else ""))
    if not cond:
        fails.append(name)


class FakeSettings(dict):
    """工程补种 Default 时只用到这几项，别为它去动真的 settings.json。"""

    def __getitem__(self, key):
        return dict.__getitem__(self, key)


# ---------------- 1 样式字典

check("15 个样式键齐全",
      len(S.STYLE_KEYS) == 15 and set(S.STYLE_KEYS) == set(S.DEFAULT_SUBTITLE_STYLE),
      sorted(set(S.STYLE_KEYS) ^ set(S.DEFAULT_SUBTITLE_STYLE)))
check("默认字号 48、居中贴底、微软雅黑",
      S.DEFAULT_SUBTITLE_STYLE["size"] == 48
      and S.DEFAULT_SUBTITLE_STYLE["align"] == 2
      and S.DEFAULT_SUBTITLE_STYLE["font"] == "Microsoft YaHei")

sty = S.clean_style({"size": 9999, "align": 0, "back_opacity": 200,
                     "outline": -5, "primary": "#abc", "font": "  思源黑体  "})
check("越界的值夹住、颜色归一、字体去空白",
      sty["size"] == 160 and sty["align"] == 1 and sty["back_opacity"] == 100
      and sty["outline"] == 0.0 and sty["primary"] == "#AABBCC"
  and sty["font"] == "思源黑体", sty)
check("坏值不炸，退回默认",
      S.clean_style({"size": "大", "primary": "red"})["size"] == 48
      and S.clean_style({"primary": "red"})["primary"] == "#FFFFFF")

ov = S.clean_overrides({"align": 7, "size": 60, "胡说": 1, "primary": "#ff0"})
check("覆盖项只留认识的键",
      ov == {"align": 7, "size": 60, "primary": "#FFFF00"}, ov)
check("覆盖项不是字典就给空",
      S.clean_overrides(None) == {} and S.clean_overrides("x") == {})

merged = S.merge_style(S.DEFAULT_SUBTITLE_STYLE, {"align": 7, "outline": 4})
check("合出来的样式：覆盖生效、其余不动",
      merged["align"] == 7 and merged["outline"] == 4.0 and merged["size"] == 48)

seeded = S.seed_default("思源黑体", 64, {"outline": 3})
check("补种 Default：字体/字号来自设置，其余按默认",
      seeded["font"] == "思源黑体" and seeded["size"] == 64
      and seeded["outline"] == 3.0 and seeded["align"] == 2, seeded)
check("补种时字号越界也夹住", S.seed_default("", 9999)["size"] == 160)


# ---------------- 2 颜色与位置

check("颜色写成 &HAABBGGRR",
      S.ass_colour("#FFFFFF") == "&H00FFFFFF"
      and S.ass_colour("#1E5AA8") == "&H00A85A1E"
      and S.ass_colour("#ff0000", 128) == "&H800000FF",
      (S.ass_colour("#1E5AA8"), S.ass_colour("#ff0000", 128)))
check("底色不透明度换成 ASS 的 alpha",
      (S.back_alpha(0), S.back_alpha(100), S.back_alpha(60)) == (255, 0, 102),
      (S.back_alpha(0), S.back_alpha(100), S.back_alpha(60)))

base = S.DEFAULT_SUBTITLE_STYLE
check("贴底居中：x 在正中、y 从底边往上让 margin_v",
      S.anchor_px(base, 1920, 1080) == (2, 960, 1040),
      S.anchor_px(base, 1920, 1080))
check("左上：keypad 7 是贴顶不是贴底",
      S.anchor_px(S.merge_style(base, {"align": 7}), 1920, 1080) == (7, 60, 40),
      S.anchor_px(S.merge_style(base, {"align": 7}), 1920, 1080))
check("正中间：右不加边距、居中不贴顶也不贴底",
      S.anchor_px(S.merge_style(base, {"align": 5}), 1920, 1080) == (5, 960, 540),
      S.anchor_px(S.merge_style(base, {"align": 5}), 1920, 1080))
check("小画面边距按百分比缩",
      S.anchor_px(base, 640, 360) == (2, 320, 347),
      S.anchor_px(base, 640, 360))


# ---------------- 3 字号与样式行

check("Fontsize = em 像素 × line_factor",
      S.ass_fontsize(S.DEFAULT_SUBTITLE_STYLE, 1080, 1.32) == 63
      and S.ass_fontsize(S.DEFAULT_SUBTITLE_STYLE, 540, 1.32) == 32,
      (S.ass_fontsize(base, 1080, 1.32), S.ass_fontsize(base, 540, 1.32)))
check("系数给 0/负数也不炸",
      S.ass_fontsize(base, 1080, 0) == 48 and S.ass_fontsize(base, 1080, -1) == 48)

line = S.ass_style_line("Default", base, 1080, 1.32)
fields = line[len("Style: "):].split(",")
check("Style 行是 ASS 的 23 个字段", len(fields) == 23, len(fields))
check("样式行第 1/2/3 个字段是名字/字体/字号",
      fields[0] == "Default" and fields[1] == "Microsoft YaHei" and fields[2] == "63",
      fields[:3])
boxed = S.ass_style_line("Box", S.merge_style(base, {"back_opacity": 80}), 1080, 1.32)
check("有底色时 BorderStyle=4（3 在 libass 里画的是描边色）",
      fields[15] == "1" and boxed[len("Style: "):].split(",")[15] == "4")

pre = S.dialogue_prefix(base, base, 1920, 1080, 1.32)
check(r"前缀永远带 \an 与 \pos",
      pre.startswith("{\\an2\\pos(960,1040)") and pre.endswith("}"), pre)
pre7 = S.dialogue_prefix(S.merge_style(base, {"align": 7}), base, 1920, 1080, 1.32)
# 字号补偿（\fscx/\fscy）是渲染层的取整补救，不算样式差异，允许出现在前缀里
pre7_core = re.sub(r"\\fscx[0-9.]+\\fscy[0-9.]+", "", pre7)
check(r"只有对齐变了就只写 \an 与 \pos，别的键不重复",
      pre7_core == "{\\an7\\pos(60,40)}", pre7)
pre_red = S.dialogue_prefix(S.merge_style(base, {"primary": "#FF0000"}),
                            base, 1920, 1080, 1.32)
check("换了主色就带 \1c", "\\1c&H000000FF" in pre_red, pre_red)
pre_bold = S.dialogue_prefix(S.merge_style(base, {"bold": True}),
                             base, 1920, 1080, 1.32)
check("加粗写 \b1", "\\b1" in pre_bold, pre_bold)
pre_box = S.dialogue_prefix(S.merge_style(base, {"back_opacity": 60}),
                            base, 1920, 1080, 1.32)
check("单条加底色框写 \4a", "\\4a&H66&" in pre_box, pre_box)
check("正文里的花括号转义",
      S.escape_text("a{b}c") == "a\\{b\\}c", S.escape_text("a{b}c"))


# ---------------- 4 工程：cue 与 v2→v3 迁移

cue = p._cue(1.0, 2.0, "你好", overrides={"align": 7, "胡说": 3})
check("cue 自带 overrides，怪键被洗掉",
      cue["overrides"] == {"align": 7}, cue)
check("老 cue 形态读进来补空 overrides",
      p._clean_cues([{"start": 0, "end": 1, "text": "a"}])[0]["overrides"] == {})

olds = {"version": 2, "clips": [], "subtitles": {
    "path": "a.srt",
    "cues": [{"start": 0.0, "end": 1.0, "text": "旧字幕", "style": ""}]}}
old = p.Project.from_doc(olds)
check("v2 工程读进来：样式表空、还没补种",
      old.styles == {} and old._styles_seeded is False
      and old.default_style == "Default", (old.styles, old.default_style))
check("v2 工程的字幕条目补上 overrides",
      old.cues[0]["overrides"] == {} and old.cues[0]["text"] == "旧字幕")

done = old.seed_default_style(FakeSettings(subtitle_font="思源黑体", subtitle_size=64,
                                          subtitle_style={"outline": 4}))
check("补种 Default：字体字号来自设置",
      done and old.styles["Default"]["font"] == "思源黑体"
      and old.styles["Default"]["size"] == 64
      and old.styles["Default"]["outline"] == 4.0, old.styles)
check("补过就不再补第二遍",
      old.seed_default_style(FakeSettings(subtitle_font="别的")) is False
      and old.styles["Default"]["font"] == "思源黑体")

doc = old.to_doc()
check("存盘写 v3，样式表与默认样式名都在",
      doc["version"] == 3
      and doc["subtitles"]["styles"]["Default"]["font"] == "思源黑体"
      and doc["subtitles"]["default_style"] == "Default", doc["version"])
back = p.Project.from_doc(json.loads(json.dumps(doc)))
check("样式表随工程往返",
      back.styles["Default"]["size"] == 64 and back._styles_seeded is True
      and back.default_style == "Default")

check("新工程还没样式表（等窗口按设置补种）",
      p.Project().styles == {} and p.Project()._styles_seeded is False)
check("坏样式表读成空的，不炸",
      p.Project.from_doc({"version": 3, "clips": [],
                          "subtitles": {"styles": [1, 2], "default_style": 7}}).styles == {})
check("样式表里的坏条目被洗掉",
      p.Project.from_doc({"version": 3, "clips": [], "subtitles": {
          "styles": {"A": {"size": 9999, "胡说": 1}, "B": 5}}}).styles
      == {"A": S.clean_style({"size": 160})})
check("default_style 指不到表里就退回 Default",
      p.Project.from_doc({"version": 3, "clips": [], "subtitles": {
          "styles": {"A": {}}, "default_style": "Nope"}}).default_style == "Default")


# ---------------- 5 烧录用 .ass

cues = [{"start": 0.5, "end": 2.0, "text": "第一句", "style": "",
         "overrides": {}},
        {"start": 2.0, "end": 3.0, "text": "第二句", "style": "Big",
         "overrides": {"primary": "#FF0000"}}]
styles = {"Default": S.DEFAULT_SUBTITLE_STYLE,
          "Big": S.merge_style(S.DEFAULT_SUBTITLE_STYLE, {"size": 72, "align": 8})}
ass = p.format_ass(cues, w=1920, h=1080, styles=styles, default_style="Default")
lines = ass.splitlines()
style_lines = [ln for ln in lines if ln.startswith("Style:")]
dial = [ln for ln in lines if ln.startswith("Dialogue:")]
check("两条样式写两行 Style，每行 23 个字段",
      len(style_lines) == 2
      and all(len(ln[len("Style: "):].split(",")) == 23 for ln in style_lines),
      style_lines)
check(r"Dialogue 引用样式名并带 \an+\pos",
      dial[0].startswith("Dialogue: 0,0:00:00.50,0:00:02.00,Default,{")
      and "\\an2\\pos(960,1040)" in dial[0]
      and "\\an8\\pos(960," in dial[1], dial)
check("样式自己的字号进了 Style 行（72 按 1080 算）",
      [ln for ln in style_lines if ln.startswith("Style: Big")][0].split(",")[2] == "72")
check("单条覆盖的红色写进这一条的 Dialogue",
      "\\1c&H000000FF" in dial[1] and "\\1c" not in dial[0], dial[1])
check("整份 .ass 里搜不到 force_style", "force_style" not in ass)
check("正文花括号转义，不被当排版指令",
      "a\\{b\\}c" in p.format_ass([{"start": 0, "end": 1, "text": "a{b}c"}],
                                 w=1920, h=1080))

plain = p.format_ass(cues, w=1920, h=1080)
check("不给样式表就现造一份 Default", plain.count("Style: Default,") == 1)
check("不认识的样式名退回 Default",
      "Dialogue: 0,0:00:00.50,0:00:02.00,Default," in plain, plain.splitlines()[-2])
check("字号系数可以按字体查（给函数）",
      ",63," in p.format_ass([{"start": 0, "end": 1, "text": "a"}], w=1920, h=1080,
                             factor=lambda fam: 1.32))

tmpf = p._write_ass(cues, 1920, 1080, styles=styles,
                    factors={"Microsoft YaHei": 1.32})
try:
    written = open(tmpf, encoding="utf-8").read()
finally:
    os.remove(tmpf)
check("_write_ass 落盘的就是这份 .ass",
      "Style: Big," in written and "{\\an2\\pos" in written)

# ---- 阶段 2：预览与烧录对齐用到的换算 -------------------------------------
check("字号补偿：1080p 下的取整零头补回来",
      S.font_scale(base, 1080, 1.32) == 100.6, S.font_scale(base, 1080, 1.32))
check("字号补偿：540p 下是 99（Fontsize 取整后偏大 1%）",
      S.font_scale(base, 540, 1.32) == 99.0, S.font_scale(base, 540, 1.32))
check("字号补偿：不传系数（1.0）就不用补",
      (S.font_scale(base, 540, 1.0), S.font_scale(base, 1080, 1.0)) == (100.0, 100.0),
      (S.font_scale(base, 540, 1.0), S.font_scale(base, 1080, 1.0)))
check("底色不透明度：Qt 的 alpha 与 ASS 的 AA 正好相反",
      S.paint_alpha(100) == 255 and S.paint_alpha(0) == 0
      and S.paint_alpha(80) == 255 - S.back_alpha(80),
      (S.paint_alpha(100), S.paint_alpha(0), S.back_alpha(80)))
check("字号按画面高度换算", (S.em_px(base, 1080), S.em_px(base, 540)) == (48, 24),
      (S.em_px(base, 1080), S.em_px(base, 540)))

_a2 = S.anchor_px(base, 1920, 1080)
_a5 = S.anchor_px(S.merge_style(base, {"align": 5}), 1920, 1080)
_a7 = S.anchor_px(S.merge_style(base, {"align": 7}), 1920, 1080)
check("底部居中定位点贴在底边距上", _a2[0] == 2 and _a2[2] > 1000, _a2)
check("中间一档的定位点在画面中心", _a5[2] == 540, _a5)
check("左上角定位点落在左边距/上边距", _a7[1] < 200 and _a7[2] < 200, _a7)
_half = S.anchor_px(base, 960, 540)
check("换分辨率后定位点按比例走",
      abs(_half[2] - _a2[2] / 2.0) <= 1 and abs(_half[1] - _a2[1] / 2.0) <= 1, (_a2, _half))

_p540 = S.dialogue_prefix(base, base, 960, 540, 1.32)
check("540p 的 Dialogue 前缀带缩放补偿", "\\fscx99\\fscy99" in _p540, _p540)
_p1080 = S.dialogue_prefix(base, base, 1920, 1080, 1.32)
check("1080p 的 Dialogue 前缀也补 0.6% 的零头",
      "\\fscx100.6\\fscy100.6" in _p1080, _p1080)
_pnone = S.dialogue_prefix(base, base, 1920, 1080, 1.0)
check("系数 1.0 时不写缩放指令", "\\fscx" not in _pnone, _pnone)



print("")
if fails:
    print("FAILED %d: %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("ALL PASS")
