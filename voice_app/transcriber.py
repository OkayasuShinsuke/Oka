"""
音声認識モジュール (faster-whisper)
高精度日本語音声認識
"""
import io
import os
import threading
from pathlib import Path
from typing import Optional, Tuple

MODEL_CACHE_DIR = Path.home() / ".voice_app" / "models"

# モデルサイズと性能の目安
MODEL_INFO = {
    "tiny":    {"size": "75MB",   "vram": "~1GB",  "desc": "最速・低精度"},
    "base":    {"size": "145MB",  "vram": "~1GB",  "desc": "高速・基本精度"},
    "small":   {"size": "466MB",  "vram": "~2GB",  "desc": "バランス型"},
    "medium":  {"size": "1.5GB",  "vram": "~5GB",  "desc": "高精度"},
    "large-v3":{"size": "3.1GB",  "vram": "~10GB", "desc": "最高精度 (推奨)"},
    "large-v3-turbo": {"size": "1.6GB", "vram": "~6GB", "desc": "高精度・高速"},
}


class Transcriber:
    """
    faster-whisperを使った音声認識クラス。
    モデルは初回ロード時にダウンロードされます。
    """

    def __init__(self, config):
        self.config = config
        self._model = None
        self._model_name = config.get("whisper", "model")
        self._device = config.get("whisper", "device")
        self._compute_type = config.get("whisper", "compute_type")
        self._beam_size = config.getint("whisper", "beam_size")
        self._language = config.get("general", "language")
        self._vad_filter = config.getbool("whisper", "vad_filter")
        self._vad_min_silence = config.getint("whisper", "vad_min_silence_duration_ms")
        self._lock = threading.Lock()
        self._loading = False
        self._load_error: Optional[str] = None

    def load_model_async(self, callback=None):
        """モデルを非同期でロードする"""
        def _load():
            try:
                self._loading = True
                self._load_model()
                self._loading = False
                if callback:
                    callback(True, None)
            except Exception as e:
                self._loading = False
                self._load_error = str(e)
                if callback:
                    callback(False, str(e))

        t = threading.Thread(target=_load, daemon=True)
        t.start()

    def _load_model(self):
        """モデルをロードする (同期)"""
        from faster_whisper import WhisperModel

        device = self._device
        compute_type = self._compute_type

        # デバイス自動検出
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        # compute_type自動設定
        if compute_type == "auto":
            if device == "cuda":
                compute_type = "float16"
            else:
                compute_type = "int8"

        print(f"[Transcriber] モデルロード中: {self._model_name} ({device}/{compute_type})")

        self._model = WhisperModel(
            self._model_name,
            device=device,
            compute_type=compute_type,
            download_root=str(MODEL_CACHE_DIR),
        )
        print(f"[Transcriber] モデルロード完了: {self._model_name}")

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    @property
    def is_loading(self) -> bool:
        return self._loading

    def transcribe(self, wav_data: bytes) -> Tuple[str, float]:
        """
        音声データを文字起こしする。
        Returns: (テキスト, 平均信頼度)
        """
        if not self.is_ready:
            raise RuntimeError("モデルがロードされていません")

        import io as _io
        audio_file = _io.BytesIO(wav_data)

        with self._lock:
            segments, info = self._model.transcribe(
                audio_file,
                language=self._language,
                beam_size=self._beam_size,
                vad_filter=self._vad_filter,
                vad_parameters={
                    "min_silence_duration_ms": self._vad_min_silence,
                    "speech_pad_ms": 200,
                },
                word_timestamps=False,
                condition_on_previous_text=True,
                # 日本語品質向上: initial_promptで日本語出力を強制
                without_timestamps=False,
                initial_prompt="日本語の音声認識です。句読点を正確に付けてください。",
            )

            texts = []
            confidences = []
            for seg in segments:
                texts.append(seg.text.strip())
                if seg.avg_logprob:
                    # logprobを信頼度(0-1)に変換
                    import math
                    conf = math.exp(max(seg.avg_logprob, -5))
                    confidences.append(conf)

        text = "".join(texts)
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return text, avg_conf

    def update_model(self, model_name: str):
        """モデルを変更する"""
        self._model_name = model_name
        self._model = None
        self.config.set("whisper", "model", model_name)
