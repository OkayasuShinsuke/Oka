"""
音声認識アプリ - エントリポイント

使い方:
    python main.py

必要なパッケージ:
    pip install -r requirements.txt

初回起動時にWhisperモデルが自動ダウンロードされます。
モデルは ~/.voice_app/models/ に保存されます。
"""
import sys
import os
import threading
import tkinter as tk

# Windowsで管理者権限チェック (keyboard ライブラリに必要)
def _check_windows_admin():
    if sys.platform == "win32":
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin()
        except Exception:
            return False
    return True  # 非Windowsは問題なし


def main():
    print("=" * 50)
    print("  日本語音声認識アプリ (Whisper)")
    print("=" * 50)

    # Windowsで管理者権限が推奨 (グローバルホットキー)
    if sys.platform == "win32" and not _check_windows_admin():
        print("[警告] 管理者権限なしで実行中。")
        print("       グローバルホットキーが動作しない場合は管理者として実行してください。")

    # --- インポートチェック ---
    missing = []
    try:
        import faster_whisper
    except ImportError:
        missing.append("faster-whisper")
    try:
        import sounddevice
    except ImportError:
        missing.append("sounddevice")
    try:
        import webrtcvad
    except ImportError:
        missing.append("webrtcvad")
    try:
        import keyboard
    except ImportError:
        missing.append("keyboard")
    try:
        import pyperclip
    except ImportError:
        missing.append("pyperclip")
    try:
        import pyautogui
    except ImportError:
        missing.append("pyautogui")

    if missing:
        print(f"\n[エラー] 以下のパッケージが見つかりません:\n  {', '.join(missing)}")
        print("\n以下のコマンドでインストールしてください:")
        print("  pip install -r requirements.txt")
        if sys.platform == "win32":
            print("\nまたは install.bat を実行してください")
        input("\nEnterキーで終了...")
        sys.exit(1)

    # --- モジュール初期化 ---
    from config import Config
    from audio_recorder import AudioRecorder
    from transcriber import Transcriber
    from text_processor import TextProcessor
    from text_injector import TextInjector
    from hotkey_manager import HotkeyManager
    from notification import StatusOverlay
    from app import VoiceApp
    from tray_icon import TrayIcon

    config = Config()

    # tkinter ルートウィンドウ (オーバーレイ用・非表示)
    root = tk.Tk()
    root.withdraw()

    overlay = StatusOverlay()
    recorder = AudioRecorder(config)
    transcriber = Transcriber(config)
    processor = TextProcessor(config)
    injector = TextInjector(config)
    hotkey_mgr = HotkeyManager(config)

    app = VoiceApp(
        config=config,
        recorder=recorder,
        transcriber=transcriber,
        processor=processor,
        injector=injector,
        hotkey_mgr=hotkey_mgr,
        overlay=overlay,
    )

    # オーバーレイ更新をメインスレッドで処理するためのキュー
    import queue
    ui_queue = queue.Queue()

    def on_state_change(state):
        from app import AppState
        ui_queue.put(("state", state))

    app.add_status_callback(on_state_change)

    # 設定ウィンドウを開く関数
    def open_settings():
        ui_queue.put(("settings", None))

    # システムトレイを別スレッドで起動
    tray = TrayIcon(app, config, settings_callback=open_settings)
    tray_thread = threading.Thread(target=tray.run, daemon=True)
    tray_thread.start()

    # アプリ起動
    app.start()

    hotkey = config.get("general", "hotkey")
    print(f"\n準備完了！")
    print(f"  ホットキー: {hotkey.upper()} で音声入力を開始/停止")
    print(f"  システムトレイアイコンを右クリックでメニュー")
    print(f"  Ctrl+C で終了\n")

    # メインループ (tkinterイベントループ + UIキュー処理)
    settings_win = None

    try:
        while True:
            # UIキューを処理
            try:
                while True:
                    item = ui_queue.get_nowait()
                    kind, data = item

                    if kind == "settings":
                        from settings_window import SettingsWindow

                        def on_settings_save():
                            # ホットキーを再登録
                            new_hotkey = config.get("general", "hotkey")
                            hotkey_mgr.update_hotkey(new_hotkey, app._on_hotkey)

                        settings_win = SettingsWindow(config, on_save=on_settings_save)
                        settings_win.show(parent=root)

                    elif kind == "state":
                        from app import AppState
                        state = data
                        if state == AppState.RECORDING:
                            overlay._do_update("● 録音中...", "#FF4444", "#222222")
                        elif state == AppState.PROCESSING:
                            overlay._do_update("⏳ 認識中...", "#FF8C00", "#222222")
                        elif state == AppState.LLM:
                            overlay._do_update("🤖 LLM整形中...", "#8844FF", "#222222")

            except queue.Empty:
                pass

            # tkinter イベント処理
            try:
                root.update()
            except tk.TclError:
                break

            # 少し待つ (CPU使用率を抑える)
            import time
            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n\n終了中...")
    finally:
        app.stop()
        try:
            root.destroy()
        except Exception:
            pass
        print("終了しました。")


if __name__ == "__main__":
    main()
