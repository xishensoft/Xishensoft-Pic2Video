
import os, subprocess, time, json
TMP = r"E:\deepseek\FM114.5\.scratch\pic2video\probe\tmp"
A,B,C = [os.path.join(TMP,x) for x in ("a.jpg","b.jpg","c.jpg")]
WAV = os.path.join(TMP,"bgm.mp3")
D=[2.0,2.5,1.5]; FPS=30

def run(cmd):
    t=time.perf_counter(); p=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",errors="replace")
    return time.perf_counter()-t,p

def vinfo(path):
    p=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-count_frames",
        "-show_entries","stream=nb_read_frames,duration,start_time","-of","json",path],capture_output=True,text=True)
    s=json.loads(p.stdout)["streams"][0]
    return s

# path B2: exact frame counts, then concat copy, then mux audio
lines=[]; tt=0
for i,(img,d) in enumerate(zip([A,B,C],D)):
    n=round(d*FPS)
    seg=os.path.join(TMP,f"b2_seg{i}.mp4")
    fc=(f"[0:v]split=2[bg][fg];"
        f"[bg]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,gblur=sigma=40,eq=brightness=-0.08[bgx];"
        f"[fg]scale=1920:1080:force_original_aspect_ratio=decrease[fgx];"
        f"[bgx][fgx]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p[v]")
    cmd=["ffmpeg","-y","-hide_banner","-loglevel","error","-loop","1","-framerate",str(FPS),"-i",img,
         "-filter_complex",fc,"-map","[v]","-frames:v",str(n),"-c:v","libx264","-preset","veryfast","-crf","20",
         "-pix_fmt","yuv420p","-an",seg]
    dt,p=run(cmd); tt+=dt
    if p.returncode: print("seg fail",p.stderr[-800:])
    print(f"seg{i} want={n}", vinfo(seg))
    lines.append("file '%s'"%seg.replace("\\","/"))
lf=os.path.join(TMP,"b2_concat.txt"); open(lf,"w",encoding="utf-8",newline="\n").write("\n".join(lines)+"\n")
out=os.path.join(TMP,"sample_concat2.mp4")
cmd=["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",lf,"-i",WAV,
     "-map","0:v","-map","1:a","-c:v","copy","-c:a","aac","-b:a","192k","-movflags","+faststart","-shortest",out]
dt,p=run(cmd); tt+=dt
print("B2 rc",p.returncode,"time %.2f"%tt)
if p.returncode: print(p.stderr[-1500:])
else:
    print("B2 video",vinfo(out))
    s=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",out],capture_output=True,text=True).stdout.strip()
    print("B2 format dur",s,"size",os.path.getsize(out))

# path C: concat demuxer directly on images with duration directives (no per-image filter)
lf2=os.path.join(TMP,"img_concat.txt")
open(lf2,"w",encoding="utf-8",newline="\n").write(
 "file '%s'\nduration 2\nfile '%s'\nduration 2.5\nfile '%s'\nduration 1.5\nfile '%s'\n" % (A.replace("\\","/"),B.replace("\\","/"),C.replace("\\","/"),C.replace("\\","/")))
outC=os.path.join(TMP,"sample_imgconcat.mp4")
cmdC=["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",lf2,"-i",WAV,
      "-vf","scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p",
      "-map","0:v","-map","1:a","-c:v","libx264","-preset","veryfast","-crf","20","-c:a","aac","-b:a","192k",
      "-shortest","-movflags","+faststart",outC]
dt,p=run(cmdC)
print("C rc",p.returncode,"time %.2f"%dt)
if p.returncode: print(p.stderr[-1200:])
else: print("C video",vinfo(outC), "size", os.path.getsize(outC))

