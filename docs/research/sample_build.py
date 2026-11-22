
import os, subprocess, time, json, sys
from PIL import Image, ImageDraw, ImageFont

PROBE = r"E:\deepseek\FM114.5\.scratch\pic2video\probe"
TMP = os.path.join(PROBE, "tmp")
os.makedirs(TMP, exist_ok=True)

def font(size):
    for p in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\arialbd.ttf"]:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except Exception: pass
    return ImageFont.load_default()

def mk(path, w, h, text, color):
    im = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(im)
    for y in range(h):
        d.line([(0,y),(w,y)], fill=(max(color[0]-y*60//h,0), max(color[1]-y*40//h,0), color[2]))
    f = font(min(w,h)//5)
    bbox = d.textbbox((0,0), text, font=f)
    d.text(((w-(bbox[2]-bbox[0]))/2, (h-(bbox[3]-bbox[1]))/2), text, font=f, fill="white")
    im.save(path, quality=92)
    return path

A = mk(os.path.join(TMP,"a.jpg"), 1920,1080, "A", (30,60,120))
B = mk(os.path.join(TMP,"b.jpg"), 1920,1080, "B", (140,40,40))
C = mk(os.path.join(TMP,"c.jpg"), 1080,1920, "C", (20,110,70))
WAV = os.path.join(TMP,"bgm.mp3")
subprocess.run(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","lavfi","-i","sine=frequency=440:duration=6","-c:a","libmp3lame","-b:a","192k",WAV], check=True)

def run(cmd):
    t=time.perf_counter()
    p=subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    dt=time.perf_counter()-t
    return dt, p

def probe(path):
    p=subprocess.run(["ffprobe","-v","error","-print_format","json","-show_format","-show_streams",path],capture_output=True,text=True,encoding="utf-8")
    j=json.loads(p.stdout); st=j["streams"]
    v=[s for s in st if s["codec_type"]=="video"][0]
    a=[s for s in st if s["codec_type"]=="audio"]
    return dict(dur=float(j["format"]["duration"]), size=int(j["format"]["size"]),
                vcodec=v["codec_name"], w=v["width"], h=v["height"], fps=v["r_frame_rate"],
                frames=v.get("nb_frames"), acodec=(a[0]["codec_name"] if a else None),
                adur=(float(a[0]["duration"]) if a and "duration" in a[0] else None))

D=[2.0,2.5,1.5]
# ---------- path A: single filter_complex ----------
parts=[]
for i,(img,d) in enumerate(zip([A,B,C],D)):
    parts.append(f"[{i}:v]split=2[bg{i}][fg{i}]")
    parts.append(f"[bg{i}]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,gblur=sigma=40,eq=brightness=-0.08[bgx{i}]")
    parts.append(f"[fg{i}]scale=1920:1080:force_original_aspect_ratio=decrease[fgx{i}]")
    parts.append(f"[bgx{i}][fgx{i}]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p[v{i}]")
parts.append("[v0][v1][v2]concat=n=3:v=1:a=0[vout]")
parts.append("[3:a]atrim=0:6,asetpts=N/SR/TB[aout]")
fc=";".join(parts)
outA=os.path.join(TMP,"sample_filter.mp4")
cmdA=["ffmpeg","-y","-hide_banner","-loglevel","error",
      "-loop","1","-t",str(D[0]),"-i",A,
      "-loop","1","-t",str(D[1]),"-i",B,
      "-loop","1","-t",str(D[2]),"-i",C,
      "-i",WAV,
      "-filter_complex",fc,"-map","[vout]","-map","[aout]",
      "-c:v","libx264","-preset","veryfast","-crf","20","-r","30","-pix_fmt","yuv420p",
      "-c:a","aac","-b:a","192k","-movflags","+faststart","-shortest",outA]
dtA,pA=run(cmdA)
print("PATH A filter_complex rc=",pA.returncode,"time=%.2fs"%dtA)
if pA.returncode: print(pA.stderr[-3000:])
else: print(" A:", probe(outA))

# ---------- path B: per-image encode + concat demuxer ----------
listfile=os.path.join(TMP,"concat.txt")
lines=[]
tB=0
for i,(img,d) in enumerate(zip([A,B,C],D)):
    seg=os.path.join(TMP,f"seg{i}.mp4")
    fcseg=(f"[0:v]split=2[bg][fg];"
           f"[bg]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,gblur=sigma=40,eq=brightness=-0.08[bgx];"
           f"[fg]scale=1920:1080:force_original_aspect_ratio=decrease[fgx];"
           f"[bgx][fgx]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p[v]")
    cmd=["ffmpeg","-y","-hide_banner","-loglevel","error","-loop","1","-t",str(d),"-i",img,
         "-filter_complex",fcseg,"-map","[v]","-c:v","libx264","-preset","veryfast","-crf","20",
         "-r","30","-pix_fmt","yuv420p","-an",seg]
    dt,p=run(cmd); tB+=dt
    if p.returncode: print("seg fail",p.stderr[-1500:])
    lines.append("file '%s'"%seg.replace("\\","/"))
open(listfile,"w",encoding="utf-8",newline="\n").write("\n".join(lines)+"\n")
outB=os.path.join(TMP,"sample_concat.mp4")
cmd=["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",listfile,
     "-i",WAV,"-map","0:v","-map","1:a","-c:v","copy","-c:a","aac","-b:a","192k",
     "-movflags","+faststart","-shortest",outB]
dt,p=run(cmd); tB+=dt
print("PATH B concat rc=",p.returncode,"time=%.2fs"%tB)
if p.returncode: print(p.stderr[-2000:])
else: print(" B:", probe(outB))
print("SIZES A/B:", os.path.getsize(outA), os.path.getsize(outB))

