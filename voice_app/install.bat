@echo off
chcp 65001 > nul
echo ============================================
echo   音声認識アプリ - インストール
echo ============================================
echo.

REM Python確認
python --version > nul 2>&1
if errorlevel 1 (
    echo [エラー] Python が見つかりません。
    echo Python 3.9 以上をインストールしてください。
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/3] pip を最新化中...
python -m pip install --upgrade pip

echo.
echo [2/3] 依存パッケージをインストール中...
pip install -r requirements.txt

echo.
echo [3/3] CUDA対応のfaster-whisperをインストール (GPUがある場合)...
REM CUDA版は別途インストール
REM pip install faster-whisper[gpu]

echo.
echo ============================================
echo   インストール完了！
echo ============================================
echo.
echo 起動方法: python main.py
echo または    start.bat をダブルクリック
echo.
pause
