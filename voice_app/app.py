"""
音声認識アプリ - コアロジック
"""
import threading
import time
from enum import Enum, auto
from typing import Optional


class AppState(Enum):
    IDLE = auto()       # 待機中
    RECORDING = auto()  # 録音中
    PROCESSING = auto() # 認識処理中
    LLM = auto()        # LLM後処理中


class VoiceApp:
    """
    音声認識アプリのメインクラス。
    各モジュールを統合して制御する。
    """

    def __init__(self, config, recorder, transcriber, processor, injector, hotkey_mgr, overlay):
        self.config = config
        self.recorder = recorder
        self.transcriber = transcriber
        self.processor = processor
        self.injector = injector
        self.hotkey_mgr = hotkey_mgr
        self.overlay = overlay

        self._state = AppState.IDLE
        self._state_lock = threading.Lock()
        self._status_callbacks = []

    @property
    def state(self) -> AppState:
        return self._state

    def _set_state(self, state: AppState):
        with self._state_lock:
            self._state = state
        for cb in self._status_callbacks:
            try:
                cb(state)
            except Exception:
                pass

    def add_status_callback(self, callback):
        self._status_callbacks.append(callback)

    def start(self):
        """アプリを起動する"""
        # モデルを非同期ロード
        print("[App] Whisperモデルをロード中...")
        self.overlay.show_info("モデルロード中...")
        self.transcriber.load_model_async(self._on_model_loaded)

        # ホットキーを登録
        hotkey = self.config.get("general", "hotkey")
        self.hotkey_mgr.register(hotkey, self._on_hotkey)
        print(f"[App] ホットキー: {hotkey}")

    def stop(self):
        """アプリを停止する"""
        self.recorder.stop()
        self.hotkey_mgr.unregister_all()
        self._set_state(AppState.IDLE)

    def _on_model_loaded(self, success: bool, error: Optional[str]):
        """モデルロード完了コールバック"""
        if success:
            print("[App] モデルロード完了 - 音声認識が使用可能です")
            self.overlay.show_info("準備完了！", 2000)
        else:
            print(f"[App] モデルロードエラー: {error}")
            self.overlay.show_error(f"モデルエラー: {error}", 5000)

    def _on_hotkey(self):
        """ホットキー押下時の処理"""
        if self._state == AppState.IDLE:
            if not self.transcriber.is_ready:
                if self.transcriber.is_loading:
                    self.overlay.show_info("モデルロード中...", 2000)
                else:
                    self.overlay.show_error("モデル未ロード", 2000)
                return
            self._start_recording()
        elif self._state == AppState.RECORDING:
            self._stop_recording_early()
        else:
            # 処理中は無視
            pass

    def _start_recording(self):
        """録音を開始する"""
        self._set_state(AppState.RECORDING)
        self.overlay.show_recording()
        print("[App] 録音開始")

        self.recorder.start(
            on_complete=self._on_audio_complete,
            on_start=None,
            on_cancel=self._on_recording_cancelled,
        )

    def _stop_recording_early(self):
        """ホットキーで録音を手動停止"""
        print("[App] 録音手動停止")
        self.recorder.stop()
        # on_audio_complete が呼ばれる (録音データが十分あれば)

    def _on_recording_cancelled(self):
        """録音がキャンセルされた (音声なし)"""
        print("[App] 録音キャンセル (音声なし)")
        self._set_state(AppState.IDLE)
        self.overlay.show_info("音声なし", 1500)

    def _on_audio_complete(self, wav_data: bytes):
        """録音完了 - 認識処理を開始"""
        print(f"[App] 録音完了: {len(wav_data)} bytes")
        self._set_state(AppState.PROCESSING)
        self.overlay.show_processing()

        # 別スレッドで認識処理
        threading.Thread(
            target=self._do_transcribe,
            args=(wav_data,),
            daemon=True
        ).start()

    def _do_transcribe(self, wav_data: bytes):
        """音声認識処理 (別スレッド)"""
        try:
            text, confidence = self.transcriber.transcribe(wav_data)
            print(f"[App] 認識結果: '{text}' (信頼度: {confidence:.2f})")

            if not text.strip():
                self._set_state(AppState.IDLE)
                self.overlay.show_info("認識できませんでした", 2000)
                return

            # テキスト後処理
            processed_text = self.processor.process(text)

            if self.processor.llm_enabled:
                # LLM後処理
                self._set_state(AppState.LLM)
                self.processor.process_with_llm(
                    processed_text,
                    lambda result, err: self._on_llm_complete(result, err)
                )
            else:
                self._output_text(processed_text)

        except Exception as e:
            print(f"[App] 認識エラー: {e}")
            self._set_state(AppState.IDLE)
            self.overlay.show_error(f"認識エラー: {str(e)[:30]}", 4000)

    def _on_llm_complete(self, text: str, error: Optional[str]):
        """LLM処理完了コールバック"""
        if error:
            print(f"[App] LLMエラー (元テキストを使用): {error}")
        self._output_text(text)

    def _output_text(self, text: str):
        """テキストをアクティブウィンドウに出力"""
        print(f"[App] 出力: '{text}'")

        auto_paste = self.config.getbool("general", "auto_paste")

        if auto_paste:
            # 少し待ってからペースト (ホットキーの修飾キーが離れるのを待つ)
            time.sleep(0.15)
            self.injector.inject(text)
        else:
            self.injector.copy_to_clipboard(text)

        self._set_state(AppState.IDLE)
        self.overlay.show_result(text)

    def toggle_punctuation(self):
        """句読点自動付与のトグル"""
        current = self.config.getbool("punctuation", "enabled")
        new_val = not current
        self.config.set("punctuation", "enabled", str(new_val).lower())
        self.config.save()
        status = "ON" if new_val else "OFF"
        self.overlay.show_info(f"句読点自動付与: {status}", 2000)
        return new_val

    def toggle_linebreak(self):
        """改行自動付与のトグル"""
        current = self.config.getbool("punctuation", "add_linebreak")
        new_val = not current
        self.config.set("punctuation", "add_linebreak", str(new_val).lower())
        self.config.save()
        status = "ON" if new_val else "OFF"
        self.overlay.show_info(f"自動改行: {status}", 2000)
        return new_val

    def toggle_llm(self):
        """LLM後処理のトグル"""
        current = self.config.getbool("llm", "enabled")
        new_val = not current
        self.config.set("llm", "enabled", str(new_val).lower())
        self.config.save()
        status = "ON" if new_val else "OFF"
        self.overlay.show_info(f"LLM整形: {status}", 2000)
        return new_val
