import json, subprocess, sys, os
sys.path.insert(0, r"E:\deepseek\FM114.5\.scratch\pic2video")
import pic2video as p
D = os.path.join(r"E:\deepseek\FM114.5\.scratch\pic2video", "probe", "testout")
out = subprocess.run([p.FFPROBE, "-v", "error", "-print_format", "json", "-show_streams",
                      "-show_format", os.path.join(D, "cli_219.mp4")], capture_output=True, text=True).stdout
j = json.loads(out)
v = [s for s in j["streams"] if s["codec_type"] == "video"][0]
print("cli_219", v["width"], v["height"], j["format"]["duration"], v.get("pix_fmt"))
out = subprocess.run([p.FFPROBE, "-v", "error", "-print_format", "json", "-show_streams",
                      "-show_format", os.path.join(D, "shot_export.mp4")], capture_output=True, text=True).stdout
j = json.loads(out)
v = [s for s in j["streams"] if s["codec_type"] == "video"][0]
print("shot_export", v["width"], v["height"], v.get("nb_frames"), j["format"]["duration"])
s = p.Settings()
print("keys", len(s.data))
print("ratio/w/h", s["ratio"], s["width"], s["height"], "fit", s["fit"], "bg_blur", s["bg_blur"], "pad", s["pad_color"])
