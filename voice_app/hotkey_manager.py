"""
ホットキー管理モジュール
グローバルショートカットキーの登録・解除
"""
import threading
from typing import Callable, Dict, Optional


class HotkeyManager:
    """
    グローバルホットキーを管理するクラス。
    keyboard ライブラリを使用。
    """

    def __init__(self, config):
        self.config = config
        self._hotkeys: Dict[str, object] = {}
        self._lock = threading.Lock()

    def register(self, hotkey: str, callback: Callable):
        """ホットキーを登録する"""
        try:
            import keyboard

            with self._lock:
                # 既存の登録を解除
                if hotkey in self._hotkeys:
                    self.unregister(hotkey)

                handle = keyboard.add_hotkey(hotkey, callback, suppress=True)
                self._hotkeys[hotkey] = handle
                print(f"[HotkeyManager] 登録完了: {hotkey}")

        except ImportError:
            print("[HotkeyManager] keyboard パッケージが見つかりません")
        except Exception as e:
            print(f"[HotkeyManager] 登録エラー: {e}")

    def unregister(self, hotkey: str):
        """ホットキーの登録を解除する"""
        try:
            import keyboard

            with self._lock:
                if hotkey in self._hotkeys:
                    keyboard.remove_hotkey(self._hotkeys[hotkey])
                    del self._hotkeys[hotkey]
                    print(f"[HotkeyManager] 解除完了: {hotkey}")

        except Exception as e:
            print(f"[HotkeyManager] 解除エラー: {e}")

    def unregister_all(self):
        """全ホットキーを解除する"""
        hotkeys = list(self._hotkeys.keys())
        for hk in hotkeys:
            self.unregister(hk)

    def update_hotkey(self, new_hotkey: str, callback: Callable):
        """ホットキーを更新する (既存の全ホットキーを解除して再登録)"""
        self.unregister_all()
        self.register(new_hotkey, callback)
        self.config.set("general", "hotkey", new_hotkey)
        self.config.save()

    @staticmethod
    def parse_hotkey(hotkey_str: str) -> str:
        """ホットキー文字列を正規化する"""
        return hotkey_str.lower().strip().replace(" ", "")

    @staticmethod
    def is_valid_hotkey(hotkey_str: str) -> bool:
        """ホットキー文字列が有効かチェック"""
        try:
            import keyboard
            keyboard.parse_hotkey(hotkey_str)
            return True
        except Exception:
            return False
