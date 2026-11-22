
import os, sys, json
OUT = r"E:\deepseek\FM114.5\.scratch\pic2video\probe"; TMP=os.path.join(OUT,"tmp")
SAMPLE=os.path.join(TMP,"sample_filter.mp4")
res={}
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtCore import QUrl, QTimer
app=QApplication(sys.argv)
win=QMainWindow(); win.setWindowTitle("Qt probe"); win.resize(960,560)
vw=QVideoWidget(); win.setCentralWidget(vw)
player=QMediaPlayer(); out=QAudioOutput(); player.setAudioOutput(out); player.setVideoOutput(vw)
statuses=[]
player.mediaStatusChanged.connect(lambda s: statuses.append(str(s)))
errs=[]
player.errorOccurred.connect(lambda e,msg: errs.append(str(e)+": "+msg))
player.setSource(QUrl.fromLocalFile(SAMPLE)); player.play(); win.show()
def finish():
    info={"duration":player.duration(),"position":player.position(),"state":str(player.playbackState()),
          "mediaStatus":str(player.mediaStatus()),"hasVideo":player.hasVideo(),"hasAudio":player.hasAudio(),
          "error":str(player.error()),"errs":errs,"statuses":statuses}
    try:
        from PIL import ImageGrab
        g=win.frameGeometry()
        ImageGrab.grab(bbox=(g.x(),g.y(),g.x()+g.width(),g.y()+g.height())).save(os.path.join(OUT,"qt_window.png"))
        info["screenshot"]=True
    except Exception as e: info["screenshot"]=repr(e)
    print(json.dumps(info,ensure_ascii=False,indent=1))
    app.quit()
QTimer.singleShot(2600, finish)
app.exec()

