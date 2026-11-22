
import os, subprocess, time, json, math
TMP=r"E:\deepseek\FM114.5\.scratch\pic2video\probe\tmp"
A,B,C=[os.path.join(TMP,x) for x in ("a.jpg","b.jpg","c.jpg")]
WAV=os.path.join(TMP,"bgm.mp3")
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

# 1) images looped to fill audio: 2 images x 2s = 4s, audio 6s -> repeat list 2x, trim to 6s
seq=[A,B,A,B]
parts=[chain(i) for i in range(len(seq))]
parts.append("".join(f"[v{i}]" for i in range(len(seq)))+f"concat=n={len(seq)}:v=1:a=0,trim=0:6,setpts=PTS-STARTPTS[vout]")
parts.append("[4:a]atrim=0:6,asetpts=N/SR/TB[aout]")
cmd=["ffmpeg","-y","-hide_banner","-loglevel","error"]
for p in seq: cmd += ["-loop","1","-framerate","30","-t","2","-i",p]
cmd += ["-i",WAV,"-filter_complex",";".join(parts),"-map","[vout]","-map","[aout]",
        "-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k",
        "-movflags","+faststart","-shortest",os.path.join(TMP,"sample_loopfill.mp4")]
dt,p=run(cmd)
print("LOOPFILL rc",p.returncode,"%.2fs"%dt)
print(" loopfill", vinfo(os.path.join(TMP,"sample_loopfill.mp4")) if p.returncode==0 else p.stderr[-800:])

# 2) 60s / 20 images benchmark with filter_complex
N=20; per=3.0
imgs=[A,B,C][:1]*0
srcs=[A,B,C,A,B,C,A,B,C,A,B,C,A,B,C,A,B,C,A,C]
parts=[chain(i) for i in range(N)]
parts.append("".join(f"[v{i}]" for i in range(N))+f"concat=n={N}:v=1:a=0[vout]")
parts.append("[20:a]atrim=0:60,asetpts=N/SR/TB[aout]")
cmd=["ffmpeg","-y","-hide_banner","-loglevel","error"]
for s in srcs: cmd += ["-loop","1","-framerate","30","-t","3","-i",s]
cmd += ["-i",WAV,"-filter_complex",";".join(parts),"-map","[vout]","-map","[aout]",
        "-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k",
        "-movflags","+faststart","-shortest",os.path.join(TMP,"bench60.mp4")]
print("cmd length 20 imgs:", len(subprocess.list2cmdline(cmd)))
dt,p=run(cmd)
out=os.path.join(TMP,"bench60.mp4")
print("BENCH60 rc",p.returncode,"%.2fs"%dt, "size", os.path.getsize(out) if p.returncode==0 else "-")
if p.returncode==0: print(" bench", vinfo(out))
else: print(p.stderr[-1000:])

# 3) filter_complex_script variant on 3 images
fc=";".join([chain(0),chain(1),chain(2),"[v0][v1][v2]concat=n=3:v=1:a=0[vout]","[3:a]atrim=0:6,asetpts=N/SR/TB[aout]"])
fcf=os.path.join(TMP,"fc.txt"); open(fcf,"w",encoding="utf-8",newline="\n").write(fc)
cmd=["ffmpeg","-y","-hide_banner","-loglevel","error","-loop","1","-t","2","-i",A,"-loop","1","-t","2.5","-i",B,"-loop","1","-t","1.5","-i",C,"-i",WAV,
     "-filter_complex_script",fcf,"-map","[vout]","-map","[aout]","-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k","-shortest",os.path.join(TMP,"sample_script.mp4")]
dt,p=run(cmd)
print("FC_SCRIPT rc",p.returncode,"%.2fs"%dt)
print(" script", vinfo(os.path.join(TMP,"sample_script.mp4")) if p.returncode==0 else p.stderr[-600:])

# 4) frame at t=5s (portrait C with blur bg)
subprocess.run(["ffmpeg","-y","-hide_banner","-loglevel","error","-ss","5","-i",os.path.join(TMP,"sample_filter.mp4"),"-frames:v","1",os.path.join(r"E:\deepseek\FM114.5\.scratch\pic2video\probe","sample_frame5.png")],check=True)
print("frame5 saved")

