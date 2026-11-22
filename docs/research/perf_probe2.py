
import os, subprocess, time, json
TMP=r"E:\deepseek\FM114.5\.scratch\pic2video\probe\tmp"
A,B,C=[os.path.join(TMP,x) for x in ("a.jpg","b.jpg","c.jpg")]
FPS=30
def run(cmd):
    t=time.perf_counter(); p=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",errors="replace")
    return time.perf_counter()-t,p
def vinfo(path):
    q=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-count_frames","-show_entries","stream=nb_read_frames,duration","-of","json",path],capture_output=True,text=True)
    return json.loads(q.stdout)["streams"][0]
def chain(i):
    return (f"[{i}:v]split=2[bg{i}][fg{i}];"
            f"[bg{i}]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,gblur=sigma=40,eq=brightness=-0.08[bgx{i}];"
            f"[fg{i}]scale=1920:1080:force_original_aspect_ratio=decrease[fgx{i}];"
            f"[bgx{i}][fgx{i}]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p[v{i}]")

# long bgm 60s
WAV60=os.path.join(TMP,"bgm60.mp3")
subprocess.run(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","lavfi","-i","sine=frequency=330:duration=60","-c:a","libmp3lame","-b:a","192k",WAV60],check=True)

# check option existence
h=subprocess.run(["ffmpeg","-hide_banner","-h","full"],capture_output=True,text=True,encoding="utf-8",errors="replace").stdout
print("has filter_complex_script:", "-filter_complex_script" in h)
print("has -/filter_complex:", "-/filter_complex" in h or "/filter_complex" in h)
for line in h.splitlines():
    if "filter_complex" in line or line.strip().startswith("-/"):
        print("OPT:",line.strip()[:150])

# 60s benchmark, 20 images x3s
srcs=[A,B,C,A,B,C,A,B,C,A,B,C,A,B,C,A,B,C,A,C]; N=len(srcs)
parts=[chain(i) for i in range(N)]
parts.append("".join(f"[v{i}]" for i in range(N))+f"concat=n={N}:v=1:a=0[vout]")
parts.append(f"[{N}:a]atrim=0:60,asetpts=N/SR/TB[aout]")
cmd=["ffmpeg","-y","-hide_banner","-loglevel","error"]
for s in srcs: cmd += ["-loop","1","-framerate","30","-t","3","-i",s]
cmd += ["-i",WAV60,"-filter_complex",";".join(parts),"-map","[vout]","-map","[aout]",
        "-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k",
        "-movflags","+faststart","-t","60",os.path.join(TMP,"bench60.mp4")]
print("cmd chars:",len(subprocess.list2cmdline(cmd)))
dt,p=run(cmd); out=os.path.join(TMP,"bench60.mp4")
print("BENCH60 rc",p.returncode,"%.2fs"%dt,"size",os.path.getsize(out) if p.returncode==0 else "-")
print(" bench", vinfo(out) if p.returncode==0 else p.stderr[-800:])

