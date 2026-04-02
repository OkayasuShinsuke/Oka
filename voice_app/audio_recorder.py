"""
音声録音モジュール
VAD (Voice Activity Detection) による自動停止対応
"""
import io
import queue
import threading
import time
import wave
from typing import Callable, Optional

import numpy as np

try:
    import sounddevice as sd
    import webrtcvad
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False


class AudioRecorder:
    """
    VAD付き音声録音クラス。
    - 発話開始を検知して録音開始
    - 無音が続いたら自動停止
    - コールバックで録音完了を通知
    """

    FRAME_DURATION_MS = 30   # VADフレーム長(ms) 10/20/30のみ有効
    BYTES_PER_SAMPLE = 2     # int16

    def __init__(self, config):
        self.config = config
        self.sample_rate = config.getint("audio", "sample_rate")
        self.channels = config.getint("audio", "channels")
        self.vad_aggressiveness = config.getint("audio", "vad_aggressiveness")
        self.silence_threshold_ms = config.getint("audio", "silence_threshold_ms")
        self.max_record_seconds = config.getint("audio", "max_record_seconds")
        self.device_index = config.getint("audio", "device_index")
        if self.device_index == -1:
            self.device_index = None

        self.frame_size = int(self.sample_rate * self.FRAME_DURATION_MS / 1000)
        self.bytes_per_frame = self.frame_size * self.BYTES_PER_SAMPLE * self.channels

        self._vad = webrtcvad.Vad(self.vad_aggressiveness) if AUDIO_AVAILABLE else None
        self._recording = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._on_complete: Optional[Callable] = None
        self._on_start: Optional[Callable] = None
        self._on_cancel: Optional[Callable] = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(
        self,
        on_complete: Callable[[bytes], None],
        on_start: Optional[Callable] = None,
        on_cancel: Optional[Callable] = None,
    ):
        """録音を開始する"""
        if self._recording:
            return
        self._recording = True
        self._stop_event.clear()
        self._on_complete = on_complete
        self._on_start = on_start
        self._on_cancel = on_cancel

        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """録音を手動停止する"""
        self._stop_event.set()
        self._recording = False

    def _record_loop(self):
        """録音ループ (別スレッドで動作)"""
        audio_frames = []
        silence_frames = 0
        speech_frames = 0
        silence_limit = int(self.silence_threshold_ms / self.FRAME_DURATION_MS)
        max_frames = int(self.max_record_seconds * 1000 / self.FRAME_DURATION_MS)
        started = False  # 発話開始したか
        pre_speech_buffer = []  # 発話前のバッファ (クリップ防止)
        pre_speech_frames = int(300 / self.FRAME_DURATION_MS)  # 300ms分

        audio_queue = queue.Queue()

        def audio_callback(indata, frames, time_info, status):
            audio_queue.put(bytes(indata))

        try:
            with sd.RawInputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                blocksize=self.frame_size,
                device=self.device_index,
                callback=audio_callback,
            ):
                while not self._stop_event.is_set():
                    try:
                        frame = audio_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    # フレームサイズ確認
                    if len(frame) != self.bytes_per_frame:
                        continue

                    is_speech = False
                    try:
                        is_speech = self._vad.is_speech(frame, self.sample_rate)
                    except Exception:
                        is_speech = True  # VADエラー時は音声とみなす

                    if not started:
                        # 発話開始待ち
                        pre_speech_buffer.append(frame)
                        if len(pre_speech_buffer) > pre_speech_frames:
                            pre_speech_buffer.pop(0)
                        if is_speech:
                            started = True
                            if self._on_start:
                                self._on_start()
                            # 発話前バッファを含める
                            audio_frames.extend(pre_speech_buffer)
                            audio_frames.append(frame)
                            speech_frames = 1
                    else:
                        audio_frames.append(frame)
                        if is_speech:
                            silence_frames = 0
                            speech_frames += 1
                        else:
                            silence_frames += 1

                        # 無音が続いたら停止
                        if silence_frames >= silence_limit and speech_frames > 5:
                            break

                        # 最大録音時間を超えたら停止
                        if len(audio_frames) >= max_frames:
                            break

        except Exception as e:
            print(f"[AudioRecorder] エラー: {e}")
            self._recording = False
            if self._on_cancel:
                self._on_cancel()
            return

        self._recording = False

        if not started or speech_frames <= 5:
            # 有効な音声なし
            if self._on_cancel:
                self._on_cancel()
            return

        # PCMデータをWAVに変換
        wav_data = self._frames_to_wav(audio_frames)
        if self._on_complete and not self._stop_event.is_set():
            self._on_complete(wav_data)

    def _frames_to_wav(self, frames: list) -> bytes:
        """PCMフレームリストをWAVバイト列に変換"""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.BYTES_PER_SAMPLE)
            wf.setframerate(self.sample_rate)
            wf.writeframes(b"".join(frames))
        return buf.getvalue()

    @staticmethod
    def list_devices():
        """利用可能なオーディオデバイス一覧を返す"""
        if not AUDIO_AVAILABLE:
            return []
        devices = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                devices.append((i, d["name"]))
        return devices
