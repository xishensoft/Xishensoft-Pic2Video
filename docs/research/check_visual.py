# -*- coding: utf-8 -*-
import os, sys, json, subprocess
sys.path.insert(0, r"E:\deepseek\FM114.5\.scratch\pic2video")
import pic2video as p

D = os.path.join(r"E:\deepseek\FM114.5\.scratch\pic2video", "probe", "testout")
files = ["out_219_color.mp4", "out_34_own_blur.mp4", "out_34_own_noblur.mp4",
         "out_img_blur.mp4", "out_img_noblur.mp4", "out_vidbg.mp4",
         "out_custom_1000x1000.mp4"]

def corner(path, x=2, y=2):
    r = subprocess.run([p.FFMPEG, "-v", "error", "-ss", "1", "-i", path,
                        "-frames:v", "1", "-vf", f"crop=1:1:{x}:{y},format=rgb24",
                        "-f", "rawvideo", "-"], capture_output=True)
    b = r.stdout
    return "#%02x%02x%02x" % (b[0], b[1], b[2]) if len(b) >= 3 else None

def mid(path):
    r = subprocess.run([p.FFMPEG, "-v", "error", "-ss", "1", "-i", path,
                        "-frames:v", "1", "-vf", "scale=1:1,format=rgb24",
                        "-f", "rawvideo", "-"], capture_output=True)
    b = r.stdout
    return "#%02x%02x%02x" % (b[0], b[1], b[2]) if len(b) >= 3 else None

for f in files:
    print(f, "corner", corner(os.path.join(D, f)), "avg", mid(os.path.join(D, f)))

# 拼一张对照图：上排 blur/noblur（3:4 竖版，缩高等高），下排 背景图模糊与否
thumbs = []
for f in ("out_34_own_blur.mp4", "out_34_own_noblur.mp4", "out_219_color.mp4",
          "out_img_blur.mp4", "out_img_noblur.mp4", "out_vidbg.mp4"):
    out = os.path.join(D, "thumb_" + f.replace(".mp4", ".png"))
    subprocess.run([p.FFMPEG, "-y", "-v", "error", "-ss", "1", "-i", os.path.join(D, f),
                    "-frames:v", "1", "-vf", "scale=-2:360", out], check=True)
    thumbs.append(out)
row1 = os.path.join(D, "m1.png"); row2 = os.path.join(D, "m2.png")
subprocess.run([p.FFMPEG, "-y", "-v", "error", "-i", thumbs[0], "-i", thumbs[1], "-i", thumbs[2],
                "-filter_complex", "[0][1][2]hstack=3", row1], check=True)
subprocess.run([p.FFMPEG, "-y", "-v", "error", "-i", thumbs[3], "-i", thumbs[4], "-i", thumbs[5],
                "-filter_complex", "[0][1][2]hstack=3", row2], check=True)
subprocess.run([p.FFMPEG, "-y", "-v", "error", "-i", row1, "-i", row2,
                "-filter_complex", "[0][1]vstack=2", os.path.join(D, "montage.png")], check=True)
print("montage", os.path.join(D, "montage.png"))
