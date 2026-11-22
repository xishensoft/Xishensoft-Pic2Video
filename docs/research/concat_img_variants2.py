
import os, subprocess, json
TMP = r"E:\deepseek\FM114.5\.scratch\pic2video\probe\tmp"
lf=os.path.join(TMP,"img_concat.txt")
for name, extra in {"vfr":["-fps_mode","vfr"], "passthrough":["-fps_mode","passthrough"], "cfr_then_fpsfilter":["-fps_mode","cfr"]}.items():
    out=os.path.join(TMP,f"img_{name}.mp4")
    cmd=["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",lf,
         "-vf","scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p"]+extra+[
         "-c:v","libx264","-preset","ultrafast","-crf","28",out]
    p=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",errors="replace")
    if p.returncode: print(name,"FAIL",p.stderr[-400:]); continue
    q=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-count_frames",
        "-show_entries","stream=nb_read_frames,duration,avg_frame_rate,r_frame_rate","-of","json",out],capture_output=True,text=True)
    print(name, json.loads(q.stdout)["streams"][0])

