# -*- coding: utf-8 -*-
"""新铺底/比例的真实出片验证（只打印 ASCII，避开 GBK 控制台）。"""
import os, sys, json, subprocess
sys.path.insert(0, r"E:\deepseek\FM114.5\.scratch\pic2video")
import pic2video as p

D = os.path.join(r"E:\deepseek\FM114.5\.scratch\pic2video", "probe", "testout")
IMG = [os.path.join(D, n) for n in ("land.png", "port.png", "sq.png")]
AUD = os.path.join(D, "tone6.m4a")
BGIMG = os.path.join(D, "bgimg.jpg")
BGVID = os.path.join(D, "bgvid.mp4")


def probe(path):
    out = subprocess.run([p.FFPROBE, "-v", "error", "-print_format", "json",
                          "-show_streams", "-show_format", path],
                         capture_output=True, text=True, encoding="utf-8").stdout
    j = json.loads(out)
    v = [s for s in j["streams"] if s["codec_type"] == "video"][0]
    a = [s for s in j["streams"] if s["codec_type"] == "audio"]
    return dict(w=v["width"], h=v["height"], fps=v["r_frame_rate"],
                pix=v.get("pix_fmt"), frames=v.get("nb_frames"),
                dur=round(float(j["format"]["duration"]), 3),
                audio=(a[0]["codec_name"] + " " + str(a[0].get("sample_rate"))
                       + "Hz " + str(a[0].get("channels")) + "ch") if a else None)


def render_one(name, opts, n=3, per=2.0):
    pr = p.Project(fps=opts.get("fps", 30))
    pr.add_images(IMG[:n])
    p.set_mode(pr, "free")
    for i in range(n):
        pr.set_duration(i, per)
    counts = pr.frame_counts()
    out = os.path.join(D, name)
    p.render(pr.clips, counts, AUD, out, crf=23, preset="ultrafast", **opts)
    print("OK", name, json.dumps(probe(out), ensure_ascii=False))


cases = [
    ("out_219_color.mp4", dict(w=2520, h=1080, fit="color", pad_color="#1b3a5c"), 3, 2.0),
    ("out_34_own_blur.mp4", dict(w=1080, h=1440, fit="own"), 3, 2.0),
    ("out_34_own_noblur.mp4", dict(w=1080, h=1440, fit="own", bg_blur=False), 3, 2.0),
    ("out_img_blur.mp4", dict(w=1280, h=720, fit="image", bg_path=BGIMG, bg_blur=True), 3, 2.0),
    ("out_img_noblur.mp4", dict(w=1280, h=720, fit="image", bg_path=BGIMG, bg_blur=False), 3, 2.0),
    ("out_vidbg.mp4", dict(w=1280, h=720, fit="video", bg_path=BGVID, bg_blur=False), 3, 2.0),
    ("out_custom_1000x1000.mp4", dict(w=1000, h=1000, fit="cover"), 3, 2.0),
]
for name, opts, n, per in cases:
    render_one(name, opts, n, per)
print("DONE")
