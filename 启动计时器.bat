@echo off
chcp 65001 >nul
setlocal
rem 桌面透明计时器 —— 双击启动（无控制台窗口）

set "PYW=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0desktop_timer.py"
    exit /b
)

for %%P in (pythonw.exe) do if not "%%~$PATH:P"=="" (
    start "" "%%~$PATH:P" "%~dp0desktop_timer.py"
    exit /b
)

echo 未找到 pythonw.exe，请安装 Python 3 后重试。
pause
