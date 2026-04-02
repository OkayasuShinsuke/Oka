"""
システムトレイアイコン管理
"""
import io
import threading
from typing import Callable, Optional


def _create_icon_image(color: str = "#0078D7", size: int = 64):
    """シンプルなアイコン画像を動的生成"""
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 背景円
        margin = 2
        draw.ellipse([margin, margin, size - margin, size - margin],
                     fill=color, outline=None)

        # マイクアイコン (簡易描画)
        cx, cy = size // 2, size // 2
        # マイクボディ
        mic_w, mic_h = size // 6, size // 3
        draw.rounded_rectangle(
            [cx - mic_w, cy - mic_h, cx + mic_w, cy + mic_h // 4],
            radius=mic_w,
            fill="white"
        )
        # マイクスタンド
        stand_y = cy + mic_h // 4
        draw.arc([cx - mic_w * 2, stand_y - mic_h // 3,
                  cx + mic_w * 2, stand_y + mic_h // 3],
                 0, 180, fill="white", width=2)
        draw.line([cx, stand_y + mic_h // 3, cx, stand_y + mic_h // 2],
                  fill="white", width=2)
        draw.line([cx - mic_w, stand_y + mic_h // 2,
                   cx + mic_w, stand_y + mic_h // 2],
                  fill="white", width=2)

        return img
    except ImportError:
        # PILがない場合は単色の正方形
        from PIL import Image
        return Image.new("RGBA", (size, size), (0, 120, 215, 255))


class TrayIcon:
    """
    システムトレイアイコンクラス。
    pystray を使用。
    """

    def __init__(self, app, config, settings_callback: Optional[Callable] = None):
        self._app = app
        self._config = config
        self._settings_callback = settings_callback
        self._tray = None
        self._icon_idle = None
        self._icon_recording = None

    def run(self):
        """システムトレイを開始する (ブロッキング)"""
        try:
            import pystray
            from pystray import MenuItem, Menu

            self._icon_idle = _create_icon_image("#0078D7")
            self._icon_recording = _create_icon_image("#FF4444")

            menu = Menu(
                MenuItem("音声入力 ON/OFF", self._toggle_recording, default=True),
                Menu.SEPARATOR,
                MenuItem(
                    "句読点自動付与",
                    self._toggle_punctuation,
                    checked=lambda item: self._config.getbool("punctuation", "enabled")
                ),
                MenuItem(
                    "自動改行",
                    self._toggle_linebreak,
                    checked=lambda item: self._config.getbool("punctuation", "add_linebreak")
                ),
                MenuItem(
                    "LLM整形",
                    self._toggle_llm,
                    checked=lambda item: self._config.getbool("llm", "enabled")
                ),
                Menu.SEPARATOR,
                MenuItem("設定", self._open_settings),
                Menu.SEPARATOR,
                MenuItem("終了", self._quit),
            )

            self._tray = pystray.Icon(
                "voice_app",
                self._icon_idle,
                "音声認識アプリ",
                menu=menu,
            )

            # アプリの状態変化でアイコンを更新
            self._app.add_status_callback(self._on_state_change)

            self._tray.run()

        except ImportError:
            print("[TrayIcon] pystray が見つかりません。トレイアイコンなしで動作します。")
            # トレイなしでもメインループを維持
            import time
            while True:
                time.sleep(1)

    def _on_state_change(self, state):
        """アプリの状態変化でアイコン更新"""
        from app import AppState
        if self._tray:
            if state == AppState.RECORDING:
                self._tray.icon = self._icon_recording
                self._tray.title = "音声認識アプリ - 録音中..."
            else:
                self._tray.icon = self._icon_idle
                self._tray.title = "音声認識アプリ"

    def _toggle_recording(self, icon, item):
        self._app._on_hotkey()

    def _toggle_punctuation(self, icon, item):
        self._app.toggle_punctuation()
        if self._tray:
            self._tray.update_menu()

    def _toggle_linebreak(self, icon, item):
        self._app.toggle_linebreak()
        if self._tray:
            self._tray.update_menu()

    def _toggle_llm(self, icon, item):
        self._app.toggle_llm()
        if self._tray:
            self._tray.update_menu()

    def _open_settings(self, icon, item):
        if self._settings_callback:
            self._settings_callback()

    def _quit(self, icon, item):
        self._app.stop()
        if self._tray:
            self._tray.stop()
