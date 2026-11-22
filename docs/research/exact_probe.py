
import os, subprocess, json, math, time
TMP=r"E:\deepseek\FM114.5\.scratch\pic2video\probe\tmp"
A,B,C=[os.path.join(TMP,x) for x in ("a.jpg","b.jpg","c.jpg")]
FPS=30
# fractional durations to expose rounding drift
durs=[2.13, 2.57, 1.31]
srcs=[("a.jpg",durs[0]),("b.jpg",durs[1]),("c.jpg",durs[2])]
N=len(srcs)
parts=[]
for i,(name,d) in enumerate(srcs):
    n=max(1,round(d*FPS))
    parts.append(f"[{i}:v]split=2[bg{i}][fg{i}];"
        f"[bg{i}]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,gblur=sigma=40,eq=brightness=-0.08[bgx{i}];"
        f"[fg{i}]scale=1920:1080:force_original_aspect_ratio=decrease[fgx{i}];"
        f"[bgx{i}][fgx{i}]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p,trim=start_frame=0:end_frame={n},setpts=PTS-STARTPTS[v{i}]")
parts.append("".join(f"[v{i}]" for i in range(N))+f"concat=n={N}:v=1:a=0[vout]")
total=sum(round(d*FPS) for d in durs)
parts.append(f"[{N}:a]atrim=0:{total/FPS:.3f},asetpts=N/SR/TB[aout]")
cmd=["ffmpeg","-y","-hide_banner","-loglevel","error"]
for name,d in srcs: cmd += ["-loop","1","-framerate","30","-t",f"{d:.2f}","-i",os.path.join(TMP,name)]
cmd += ["-i",os.path.join(TMP,"bgm.mp3"),"-filter_complex",";".join(parts),"-map","[vout]","-map","[aout]",
        "-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k",
        "-ar","48000","-ac","2","-movflags","+faststart",os.path.join(TMP,"sample_exact.mp4")]
t=time.perf_counter(); p=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",errors="replace"); dt=time.perf_counter()-t
print("rc",p.returncode,"%.2fs"%dt,"expected frames",total, "%.4fs"%(total/FPS))
if p.returncode: print(p.stderr[-1000:])
q=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-count_frames","-show_entries","stream=nb_read_frames,duration -of json".replace(" -of json",""),"-of","json",os.path.join(TMP,"sample_exact.mp4")],capture_output=True,text=True)
q=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-count_frames","-show_entries","stream=nb_read_frames,duration","-of","json",os.path.join(TMP,"sample_exact.mp4")],capture_output=True,text=True)
print(json.loads(q.stdout)["streams"][0])
q=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration,size","-of","json",os.path.join(TMP,"sample_exact.mp4")],capture_output=True,text=True)
print(json.loads(q.stdout)["format"])

