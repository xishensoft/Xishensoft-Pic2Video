@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo 没找到 python，先装 Python 3.10+ 并把它加进 PATH。
    pause
    exit /b 1
)

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo 没装 PyInstaller，先装一下：
    python -m pip install pyinstaller
    if errorlevel 1 ( pause & exit /b 1 )
)

python assets\make_icon.py || ( pause & exit /b 1 )

python -m PyInstaller --noconfirm --clean --windowed --onedir --name Pic2Video ^
    --icon assets\icon.ico ^
    --add-data "assets\icon.ico;assets" ^
    --add-data "assets\icon.png;assets" ^
    pic2video.py
if errorlevel 1 ( pause & exit /b 1 )

echo.
echo 打包好了：dist\Pic2Video\Pic2Video.exe
echo 整个 dist\Pic2Video 文件夹压成 zip 就是免安装版。
pause
