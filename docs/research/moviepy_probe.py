
import os, sys, time, json, subprocess
sys.path.insert(0, r"E:\deepseek\FM114.5\.scratch\pic2video\probe\pylibs")
from PIL import Image, ImageFilter, ImageEnhance
from moviepy import ImageClip, concatenate_videoclips, AudioFileClip
import imageio_ffmpeg
print("moviepy ffmpeg exe:", imageio_ffmpeg.get_ffmpeg_exe())
print("ffmpeg version:", imageio_ffmpeg.get_ffmpeg_version())

TMP=r"E:\deepseek\FM114.5\.scratch\pic2video\probe\tmp"
items=[("a.jpg",2.0),("b.jpg",2.5),("c.jpg",1.5)]
W,H=1920,1080
prep=[]
for name,d in items:
    im=Image.open(os.path.join(TMP,name)).convert("RGB")
    # background: cover + blur + darken
    s=max(W/im.width, H/im.height)
    bg=im.resize((round(im.width*s),round(im.height*s)), Image.LANCZOS)
    left=(bg.width-W)//2; top=(bg.height-H)//2
    bg=bg.crop((left,top,left+W,top+H)).filter(ImageFilter.GaussianBlur(40))
    bg=ImageEnhance.Brightness(bg).enhance(0.82)
    # foreground: contain, centered
    s2=min(W/im.width, H/im.height)
    fg=im.resize((round(im.width*s2),round(im.height*s2)), Image.LANCZOS)
    bg.paste(fg, ((W-fg.width)//2,(H-fg.height)//2))
    p=os.path.join(TMP,"mv_"+name.replace(".jpg",".png")); bg.save(p); prep.append((p,d))
print("prepped", prep)

t=time.perf_counter()
clips=[ImageClip(p).with_duration(d) for p,d in prep]
video=concatenate_videoclips(clips, method="compose")
audio=AudioFileClip(os.path.join(TMP,"bgm.mp3"))
video=video.with_audio(audio).with_duration(audio.duration)
out=os.path.join(TMP,"sample_moviepy.mp4")
video.write_videofile(out, fps=30, codec="libx264", audio_codec="aac", preset="veryfast",
                      ffmpeg_params=["-crf","20","-pix_fmt","yuv420p"], logger=None)
dt=time.perf_counter()-t
q=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-count_frames","-show_entries","stream=nb_read_frames,duration,width,height,pix_fmt","-of","json",out],capture_output=True,text=True)
print("MOVIEPY secs %.2f"%dt, "size", os.path.getsize(out), json.loads(q.stdout)["streams"][0])

