
import os, sys, json, threading, http.server, socketserver, urllib.request, time
OUT=r"E:\deepseek\FM114.5\.scratch\pic2video\probe"; TMP=os.path.join(OUT,"tmp")
SAMPLE=os.path.join(TMP,"sample_filter.mp4")

# --- HTTP server range test (python -m http.server) ---
os.chdir(TMP)
srv=socketserver.TCPServer(("127.0.0.1",0), http.server.SimpleHTTPRequestHandler)
port=srv.server_address[1]
threading.Thread(target=srv.serve_forever,daemon=True).start()
req=urllib.request.Request(f"http://127.0.0.1:{port}/sample_filter.mp4", headers={"Range":"bytes=0-1023"})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        info={"status":r.status,"content_range":r.headers.get("Content-Range"),"len":len(r.read())}
except Exception as e:
    info={"err":repr(e)}
print("HTTP range probe:", json.dumps(info))
print("HTTP server:", sys.version.split()[0], "SimpleHTTPRequestHandler")
srv.shutdown()

# --- Qt: decode frames through QVideoSink ---
from PyQt6.QtWidgets import QApplication
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink
from PyQt6.QtCore import QUrl, QTimer
app=QApplication(sys.argv)
player=QMediaPlayer(); sink=QVideoSink(); player.setVideoSink(sink); player.setAudioOutput(QAudioOutput())
stats={"frames":0,"sizes":set(),"saved":None}
def on_frame(f):
    if not f.isValid(): return
    stats["frames"]+=1
    img=f.toImage()
    if img.isNull(): return
    stats["sizes"].add((img.width(),img.height()))
    if stats["frames"]==30:
        p=os.path.join(OUT,"qt_frame30.png"); img.save(p); stats["saved"]=p
sink.videoFrameChanged.connect(on_frame)
player.setSource(QUrl.fromLocalFile(SAMPLE)); player.play()
def done():
    print("Qt decode:", json.dumps({"frames":stats["frames"],"sizes":sorted(stats["sizes"]),"saved":stats["saved"]},ensure_ascii=False))
    app.quit()
QTimer.singleShot(2200, done)
app.exec()

