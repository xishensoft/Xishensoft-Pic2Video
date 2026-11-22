# -*- coding: utf-8 -*-
"""自定义背景图：锐利背景 + 模糊开关，直接看左右留白条。"""
import os, sys, subprocess
sys.path.insert(0, r"E:\deepseek\FM114.5\.scratch\pic2video")
import pic2video as p

D = os.path.join(r"E:\deepseek\FM114.5\.scratch\pic2video", "probe", "testout")
BGS = os.path.join(D, "bgsharp.png")
AUD = os.path.join(D, "tone6.m4a")
port = os.path.join(D, "port.png")

def run(name, **opts):
    pr = p.Project()
    pr.add_images([port])
    p.set_mode(pr, "free")
    pr.set_duration(0, 3.0)
    out = os.path.join(D, name)
    p.render(pr.clips, pr.frame_counts(), AUD, out, w=1280, h=720, crf=20,
             preset="ultrafast", **opts)
    return out

def crop(path, x, y, w, h, out):
    subprocess.run([p.FFMPEG, "-y", "-v", "error", "-ss", "1", "-i", path,
                    "-frames:v", "1", "-vf",
                    f"crop={w}:{h}:{x}:{y},scale=480:-2", out], check=True)

a = run("bg_sharp_blur.mp4", fit="image", bg_path=BGS)
b = run("bg_sharp_noblur.mp4", fit="image", bg_path=BGS, bg_blur=False)
ca = os.path.join(D, "crop_sharp_blur.png")
cb = os.path.join(D, "crop_sharp_noblur.png")
crop(a, 0, 0, 320, 720, ca)
crop(b, 0, 0, 320, 720, cb)
subprocess.run([p.FFMPEG, "-y", "-v", "error", "-i", ca, "-i", cb,
                "-filter_complex", "[0][1]hstack=2", os.path.join(D, "montage_sharp.png")],
               check=True)
print("ok montage_sharp.png")
