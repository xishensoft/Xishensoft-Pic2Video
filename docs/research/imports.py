import importlib, sys, traceback
mods = ["tkinter","PySide6","PySide6.QtMultimedia","PySide6.QtMultimediaWidgets","PyQt6","PyQt6.QtMultimedia","PyQt6.QtMultimediaWidgets","PyQt5","wx","moviepy","ffmpeg","av","PIL","numpy","cv2","imageio","imageio_ffmpeg","flask","fastapi","uvicorn","gradio","streamlit","webview","pyglet","pygame"]
for m in mods:
    try:
        mod = importlib.import_module(m)
        v = getattr(mod, "__version__", getattr(mod, "VERSION", ""))
        print(f"OK   {m:38s} {v}")
    except Exception as e:
        print(f"FAIL {m:38s} {type(e).__name__}: {e}")
