"""
通知・オーバーレイウィンドウモジュール
録音状態をユーザーに視覚的に通知する
"""
import threading
import time
import tkinter as tk
from typing import Optional


class StatusOverlay:
    """
    録音状態を示すコンパクトなオーバーレイウィンドウ。
    画面右下に表示。
    """

    def __init__(self):
        self._window: Optional[tk.Tk] = None
        self._label: Optional[tk.Label] = None
        self._lock = threading.Lock()
        self._hide_timer: Optional[threading.Timer] = None

    def show_recording(self):
        """録音中表示"""
        self._update("● 録音中...", "#FF4444", "#222222")

    def show_processing(self):
        """処理中表示"""
        self._update("⏳ 認識中...", "#FF8C00", "#222222")

    def show_result(self, text: str, duration_ms: int = 3000):
        """認識結果を一時表示"""
        preview = text[:30] + "..." if len(text) > 30 else text
        self._update(f"✓ {preview}", "#00C851", "#222222")
        self._schedule_hide(duration_ms)

    def show_error(self, msg: str, duration_ms: int = 4000):
        """エラーメッセージを一時表示"""
        self._update(f"✗ {msg}", "#FF4444", "#222222")
        self._schedule_hide(duration_ms)

    def show_info(self, msg: str, duration_ms: int = 2000):
        """情報メッセージを一時表示"""
        self._update(f"ℹ {msg}", "#4488FF", "#222222")
        self._schedule_hide(duration_ms)

    def hide(self):
        """ウィンドウを隠す"""
        if self._window:
            try:
                self._window.withdraw()
            except Exception:
                pass

    def destroy(self):
        """ウィンドウを破棄する"""
        if self._window:
            try:
                self._window.destroy()
                self._window = None
            except Exception:
                pass

    def _update(self, text: str, fg: str, bg: str):
        """UIを更新する (スレッドセーフ)"""
        if threading.current_thread() is threading.main_thread():
            self._do_update(text, fg, bg)
        else:
            # tkinterはメインスレッドからのみ操作可能
            # after を使ってメインスレッドにディスパッチ
            try:
                if self._window:
                    self._window.after(0, lambda: self._do_update(text, fg, bg))
                else:
                    threading.Thread(
                        target=self._create_and_update,
                        args=(text, fg, bg),
                        daemon=True
                    ).start()
            except Exception:
                pass

    def _create_and_update(self, text: str, fg: str, bg: str):
        """新しいtkinterウィンドウを作成してメインスレッドで表示"""
        # このメソッドは別スレッドで呼ばれるが、
        # tkinterはメインスレッド専用のため、
        # 実際の処理はメインアプリ側のafter()で行う
        pass

    def _do_update(self, text: str, fg: str, bg: str):
        """実際のUI更新 (メインスレッドから呼ぶこと)"""
        try:
            if not self._window or not self._window.winfo_exists():
                self._create_window(bg)

            if self._window:
                self._window.configure(bg=bg)
                self._label.configure(text=text, fg=fg, bg=bg)
                self._window.deiconify()
                self._window.lift()
                self._window.attributes("-topmost", True)
                self._reposition()
        except Exception as e:
            print(f"[Overlay] UI更新エラー: {e}")

    def _create_window(self, bg: str):
        """オーバーレイウィンドウを作成"""
        self._window = tk.Toplevel()
        self._window.overrideredirect(True)  # タイトルバーなし
        self._window.attributes("-topmost", True)
        self._window.attributes("-alpha", 0.92)
        self._window.configure(bg=bg)

        self._label = tk.Label(
            self._window,
            text="",
            font=("Yu Gothic UI", 12, "bold"),
            fg="#FFFFFF",
            bg=bg,
            padx=16,
            pady=8,
        )
        self._label.pack()
        self._reposition()

    def _reposition(self):
        """画面右下に配置"""
        if not self._window:
            return
        try:
            self._window.update_idletasks()
            screen_w = self._window.winfo_screenwidth()
            screen_h = self._window.winfo_screenheight()
            win_w = self._window.winfo_width()
            win_h = self._window.winfo_height()
            x = screen_w - win_w - 20
            y = screen_h - win_h - 60
            self._window.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _schedule_hide(self, ms: int):
        """指定ミリ秒後にウィンドウを隠す"""
        if self._hide_timer:
            self._hide_timer.cancel()
        self._hide_timer = threading.Timer(ms / 1000, self.hide)
        self._hide_timer.daemon = True
        self._hide_timer.start()
