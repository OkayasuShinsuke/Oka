"""
テキスト入力モジュール
アクティブウィンドウへテキストを貼り付ける
"""
import time
import threading


class TextInjector:
    """
    テキストをアクティブなウィンドウに挿入するクラス。
    クリップボード経由でCtrl+Vを使う方法が最も確実。
    """

    def __init__(self, config):
        self.config = config
        self._prev_clipboard = ""

    def inject(self, text: str):
        """テキストをアクティブウィンドウに入力する"""
        if not text.strip():
            return

        try:
            import pyperclip
            import pyautogui

            # 元のクリップボード内容を保存
            try:
                prev = pyperclip.paste()
            except Exception:
                prev = ""

            # テキストをクリップボードにコピー
            pyperclip.copy(text)
            time.sleep(0.05)

            # Ctrl+V で貼り付け
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.1)

            # クリップボードを元に戻す (少し後に)
            def restore():
                time.sleep(0.5)
                try:
                    pyperclip.copy(prev)
                except Exception:
                    pass

            threading.Thread(target=restore, daemon=True).start()

        except ImportError as e:
            print(f"[TextInjector] パッケージ不足: {e}")
        except Exception as e:
            print(f"[TextInjector] エラー: {e}")

    def copy_to_clipboard(self, text: str):
        """クリップボードにコピーするのみ (自動貼り付けしない)"""
        try:
            import pyperclip
            pyperclip.copy(text)
        except Exception as e:
            print(f"[TextInjector] クリップボードエラー: {e}")
