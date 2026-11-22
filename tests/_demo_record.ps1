param([string]$OutDir = (Join-Path $env:TEMP 'p2v_rec2'), [string]$AppDir = 'E:\deepseek\Xishensoft-Pic2Video\dist\Pic2Video')
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class Win32Dpi {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
}
'@
[Win32Dpi]::SetProcessDPIAware() | Out-Null
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$rec = Join-Path $OutDir 'rec.mkv'
$rectFile = Join-Path $OutDir 'rects.txt'
Remove-Item $rec,$rectFile -ErrorAction SilentlyContinue
$ff = Start-Process -FilePath 'ffmpeg' -ArgumentList @('-hide_banner','-loglevel','error','-f','gdigrab','-framerate','15','-i','desktop','-c:v','libx264','-preset','ultrafast','-crf','20','-pix_fmt','yuv420p','-t','18','-y',$rec) -PassThru -WindowStyle Hidden
Start-Sleep -Milliseconds 700
$env:PIC2VIDEO_SHOT = (Join-Path $OutDir 'shot')
$env:PIC2VIDEO_SHOT_UI = 'pro'
$env:PIC2VIDEO_SHOT_THEME = 'dark'
$env:PIC2VIDEO_SHOT_LANG = 'zh-CN'
$env:PIC2VIDEO_SHOT_IMAGES = (@('C:\Users\XISHENSOFT\AppData\Local\Temp\p2v_demo_imgs\demo_1.png','C:\Users\XISHENSOFT\AppData\Local\Temp\p2v_demo_imgs\demo_2.png','C:\Users\XISHENSOFT\AppData\Local\Temp\p2v_demo_imgs\demo_3.png','C:\Users\XISHENSOFT\AppData\Local\Temp\p2v_demo_imgs\demo_4.png') -join ';')
$env:PIC2VIDEO_SHOT_AUDIO = 'E:\deepseek\Xishensoft-Pic2Video\tests\fixtures\tone6.m4a'
$env:PIC2VIDEO_EXPORT_PATH = (Join-Path $OutDir 'out.mp4')
$app = Start-Process -FilePath (Join-Path $AppDir 'Pic2Video.exe') -PassThru
$sw = [Diagnostics.Stopwatch]::StartNew()
while (-not $app.HasExited -and $sw.Elapsed.TotalSeconds -lt 60) {
  $app.Refresh()
  if ($app.MainWindowHandle -ne [IntPtr]::Zero) {
    $r = New-Object Win32Dpi+RECT
    if ([Win32Dpi]::GetWindowRect($app.MainWindowHandle, [ref]$r)) {
      $line = '{0:N2}`t{1}`t{2}`t{3}`t{4}' -f $sw.Elapsed.TotalSeconds, $r.Left, $r.Top, $r.Right, $r.Bottom
      Add-Content -Path $rectFile -Value $line
    }
  }
  Start-Sleep -Milliseconds 200
}
Start-Sleep -Milliseconds 400
$ff.WaitForExit(20000) | Out-Null
'APP_EXIT=' + $app.ExitCode
'ELAPSED=' + [math]::Round($sw.Elapsed.TotalSeconds,1)
Start-Sleep -Milliseconds 600
$mp4 = Join-Path $OutDir 'rec_all.mp4'
& ffmpeg -hide_banner -loglevel error -i $rec -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -y $mp4
'REC=' + (Test-Path $mp4)
if (Test-Path $mp4) { 'REC_DUR=' + [math]::Round((& ffprobe -v error -show_entries format=duration -of csv=p=0 $mp4),1) }
Get-Content $rectFile -Tail 3