@echo off
chcp 65001 > nul
echo 音声認識アプリを起動中...

REM 管理者権限で実行 (グローバルホットキーに必要)
net session > nul 2>&1
if errorlevel 1 (
    echo 管理者権限で再起動しています...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
python main.py
pause
