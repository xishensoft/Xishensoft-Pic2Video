# -*- coding: utf-8 -*-
"""竖版图放进 16:9 画布，专门看背景区域（左右留白）的对照。"""
import os, sys, subprocess
sys.path.insert(0, r"E:\deepseek\FM114.5\.scratch\pic2video")
import pic2video as p

D = os.path.join(r"E:\deepseek\FM114.5\.scratch\pic2video", "probe", "testout")
IMG = [os.path.join(D, n) for n in ("port.png", "land.png")]
AUD = os.path.join(D, "tone6.m4a")
BGIMG = os.path.join(D, "bgimg.jpg")
BGVID = os.path.join(D, "bgvid.mp4")

def run(name, **opts):
    pr = p.Project()
    pr.add_images(IMG)
    p.set_mode(pr, "free")
    pr.set_duration(0, 3.0); pr.set_duration(1, 3.0)
    out = os.path.join(D, name)
    p.render(pr.clips, pr.frame_counts(), AUD, out, w=1280, h=720, crf=23,
             preset="ultrafast", **opts)
    return out

def corner(path, x=4, y=4):
    r = subprocess.run([p.FFMPEG, "-v", "error", "-ss", "1", "-i", path,
                        "-frames:v", "1", "-vf", f"crop=1:1:{x}:{y},format=rgb24",
                        "-f", "rawvideo", "-"], capture_output=True)
    b = r.stdout
    return "#%02x%02x%02x" % (b[0], b[1], b[2])

cases = [
    ("bg_own_blur.mp4",   dict(fit="own")),
    ("bg_own_noblur.mp4", dict(fit="own", bg_blur=False)),
    ("bg_color.mp4",      dict(fit="color", pad_color="#1b3a5c")),
    ("bg_img_blur.mp4",   dict(fit="image", bg_path=BGIMG)),
    ("bg_img_noblur.mp4", dict(fit="image", bg_path=BGIMG, bg_blur=False)),
    ("bg_video.mp4",      dict(fit="video", bg_path=BGVID, bg_blur=False)),
]
thumbs = []
for name, opts in cases:
    out = run(name, **opts)
    print(name, "left-corner", corner(out))
    t = os.path.join(D, "th_" + name.replace(".mp4", ".png"))
    subprocess.run([p.FFMPEG, "-y", "-v", "error", "-ss", "1", "-i", out,
                    "-frames:v", "1", "-vf", "scale=640:360", t], check=True)
    thumbs.append(t)

rows = []
for i in range(0, 6, 3):
    row = os.path.join(D, f"row{i//3}.png")
    subprocess.run([p.FFMPEG, "-y", "-v", "error", "-i", thumbs[i], "-i", thumbs[i+1],
                    "-i", thumbs[i+2], "-filter_complex", "[0][1][2]hstack=3", row],
                   check=True)
    rows.append(row)
subprocess.run([p.FFMPEG, "-y", "-v", "error", "-i", rows[0], "-i", rows[1],
                "-filter_complex", "[0][1]vstack=2", os.path.join(D, "montage.png")],
               check=True)
print("montage", os.path.join(D, "montage.png"))
