@echo off
chcp 65001 >nul
pushd "%~dp0"
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo 没找到 ffmpeg，请把 ffmpeg\bin 加进 PATH 再运行。
  pause
  exit /b 1
)
python pic2video.py
if errorlevel 1 pause
popd
