import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtMultimedia import QMediaDevices
app = QApplication(sys.argv)
d0 = QMediaDevices.defaultAudioOutput()
print("default:", d0.description(), "|", bytes(d0.id()).decode("utf-8", "replace"))
for d in QMediaDevices.audioOutputs():
    print("dev:", d.description(), "|", bytes(d.id()).decode("utf-8", "replace"))
print("count:", len(QMediaDevices.audioOutputs()))
