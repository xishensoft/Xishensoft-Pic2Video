
import subprocess, os, sys
from PIL import Image
rec = os.path.join(os.environ["TEMP"], "p2v_rec2", "rec_all.mp4")
tmp = os.path.join(os.environ["TEMP"], "p2v_rec2", "lum")
os.makedirs(tmp, exist_ok=True)
for t in [x/2 for x in range(12, 27)]:
    f = os.path.join(tmp, "f.png")
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-ss",str(t),"-i",rec,
                    "-frames:v","1","-vf","crop=900:560:470:100","-y",f], check=True)
    im = Image.open(f).convert("L")
    px = list(im.getdata())
    print("t=%.1f mean=%.1f" % (t, sum(px)/len(px)))
