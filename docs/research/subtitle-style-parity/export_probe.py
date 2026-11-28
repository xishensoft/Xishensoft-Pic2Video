# -*- coding: utf-8 -*-
"""阶段 4 的导出实测：另存的 .ass 拖回 ffmpeg 能不能烧出样式。

跑法（仓库根）：
    python docs/research/subtitle-style-parity/export_probe.py
产出两个截图：export_ass_burn.png（默认样式的第一条）、
export_ass_burn_styled.png（带 \\pos / 颜色 / 底色框覆盖的第二条）。
"""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, ROOT)
import pic2video as p
import subtitle_style as S


def main():
    cues = [p._cue(0.0, 1.0, "导出的 .ass 能烧"),
            p._cue(1.2, 3.0, "第二句带样式", "",
                   {"size": 72, "primary": "#FFD24D",
                    "back_color": "#1E5AA8", "back_opacity": 100})]
    styles = {"Default": S.seed_default("Microsoft YaHei", 48)}
    text = p.subtitle_export_texts(cues, styles, "Default", 1920, 1080)[".ass"]
    fd, path = tempfile.mkstemp(suffix=".ass")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    flt = p.ass_filter(path, "Microsoft YaHei", 48,
                       p.default_font_dir()) + ",format=rgb24"
    try:
        for t, name in ((0.3, "export_ass_burn.png"),
                        (1.6, "export_ass_burn_styled.png")):
            out = os.path.join(HERE, name)
            r = subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi",
                                "-i", "color=c=0x202020:s=1920x1080:d=4",
                                "-vf", flt, "-ss", str(t), "-frames:v", "1", out],
                               capture_output=True, text=True)
            print(name, "exit", r.returncode)
            if r.returncode != 0:
                print(r.stderr[-400:])
                return 1
    finally:
        os.remove(path)
    print("force_style in filter:", "force_style" in flt)
    return 0


if __name__ == "__main__":
    sys.exit(main())