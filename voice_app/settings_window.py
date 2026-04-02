"""
設定ウィンドウ (tkinter)
"""
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional

from config import Config
from transcriber import MODEL_INFO
from hotkey_manager import HotkeyManager
from audio_recorder import AudioRecorder


class SettingsWindow:
    """設定ウィンドウ"""

    def __init__(self, config: Config, on_save: Optional[Callable] = None):
        self.config = config
        self.on_save = on_save
        self._window: Optional[tk.Toplevel] = None

    def show(self, parent=None):
        """設定ウィンドウを開く"""
        if self._window and self._window.winfo_exists():
            self._window.lift()
            return

        if parent:
            self._window = tk.Toplevel(parent)
        else:
            self._window = tk.Tk()

        self._window.title("音声認識アプリ - 設定")
        self._window.geometry("560x680")
        self._window.resizable(False, False)
        self._window.configure(bg="#F5F5F5")

        self._build_ui()
        self._window.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        """UI構築"""
        w = self._window

        # スクロール可能なフレーム
        canvas = tk.Canvas(w, bg="#F5F5F5", highlightthickness=0)
        scrollbar = ttk.Scrollbar(w, orient="vertical", command=canvas.yview)
        self._scroll_frame = tk.Frame(canvas, bg="#F5F5F5")

        self._scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self._scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # マウスホイール対応
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        frame = self._scroll_frame
        pad = {"padx": 16, "pady": 4}

        # --- 一般設定 ---
        self._section_label(frame, "一般設定")

        # ホットキー
        self._row_label(frame, "ホットキー (音声入力のオン/オフ)")
        self._hotkey_var = tk.StringVar(value=self.config.get("general", "hotkey"))
        hotkey_frame = tk.Frame(frame, bg="#F5F5F5")
        hotkey_frame.pack(fill="x", **pad)
        tk.Entry(hotkey_frame, textvariable=self._hotkey_var, width=24, font=("Yu Gothic UI", 10)).pack(side="left")
        tk.Label(hotkey_frame, text="例: ctrl+shift+space, ctrl+alt+v", bg="#F5F5F5",
                 fg="#888", font=("Yu Gothic UI", 9)).pack(side="left", padx=8)

        # 自動貼り付け
        self._auto_paste_var = tk.BooleanVar(value=self.config.getbool("general", "auto_paste"))
        self._checkbox(frame, "認識後に自動貼り付け (Ctrl+V)", self._auto_paste_var)

        # --- Whisperモデル設定 ---
        self._section_label(frame, "音声認識モデル (Whisper)")

        # モデル選択
        self._row_label(frame, "モデル")
        model_frame = tk.Frame(frame, bg="#F5F5F5")
        model_frame.pack(fill="x", **pad)
        model_names = list(MODEL_INFO.keys())
        self._model_var = tk.StringVar(value=self.config.get("whisper", "model"))
        model_combo = ttk.Combobox(model_frame, textvariable=self._model_var,
                                    values=model_names, state="readonly", width=20)
        model_combo.pack(side="left")
        self._model_info_label = tk.Label(model_frame, text="", bg="#F5F5F5",
                                           fg="#555", font=("Yu Gothic UI", 9))
        self._model_info_label.pack(side="left", padx=8)
        model_combo.bind("<<ComboboxSelected>>", self._on_model_change)
        self._on_model_change(None)

        # デバイス
        self._row_label(frame, "処理デバイス")
        self._device_var = tk.StringVar(value=self.config.get("whisper", "device"))
        device_frame = tk.Frame(frame, bg="#F5F5F5")
        device_frame.pack(fill="x", **pad)
        for val, label in [("auto", "自動"), ("cpu", "CPU"), ("cuda", "GPU (CUDA)")]:
            tk.Radiobutton(device_frame, text=label, variable=self._device_var, value=val,
                           bg="#F5F5F5", font=("Yu Gothic UI", 10)).pack(side="left", padx=4)

        # VADフィルター
        self._vad_filter_var = tk.BooleanVar(value=self.config.getbool("whisper", "vad_filter"))
        self._checkbox(frame, "VADフィルター (無音部分を自動除去)", self._vad_filter_var)

        # --- 句読点・改行設定 ---
        self._section_label(frame, "句読点・改行")

        self._punct_enabled_var = tk.BooleanVar(value=self.config.getbool("punctuation", "enabled"))
        self._checkbox(frame, "句読点を自動補完 (。、など)", self._punct_enabled_var)

        self._linebreak_var = tk.BooleanVar(value=self.config.getbool("punctuation", "add_linebreak"))
        self._checkbox(frame, "改行を自動追加 (接続詞・句点後)", self._linebreak_var)

        # --- LLM設定 ---
        self._section_label(frame, "LLM後処理 (オプション・高精度整形)")

        self._llm_enabled_var = tk.BooleanVar(value=self.config.getbool("llm", "enabled"))
        self._checkbox(frame, "LLMで句読点・整形を改善", self._llm_enabled_var)

        self._row_label(frame, "プロバイダー")
        self._llm_provider_var = tk.StringVar(value=self.config.get("llm", "provider"))
        provider_frame = tk.Frame(frame, bg="#F5F5F5")
        provider_frame.pack(fill="x", **pad)
        for val, label in [("ollama", "Ollama (ローカル)"), ("openai", "OpenAI API")]:
            tk.Radiobutton(provider_frame, text=label, variable=self._llm_provider_var, value=val,
                           bg="#F5F5F5", font=("Yu Gothic UI", 10)).pack(side="left", padx=4)

        self._row_label(frame, "Ollamaモデル")
        self._ollama_model_var = tk.StringVar(value=self.config.get("llm", "ollama_model"))
        ollama_frame = tk.Frame(frame, bg="#F5F5F5")
        ollama_frame.pack(fill="x", **pad)
        tk.Entry(ollama_frame, textvariable=self._ollama_model_var, width=24,
                 font=("Yu Gothic UI", 10)).pack(side="left")
        tk.Label(ollama_frame, text="例: llama3.2:3b, qwen2.5:7b", bg="#F5F5F5",
                 fg="#888", font=("Yu Gothic UI", 9)).pack(side="left", padx=8)

        self._row_label(frame, "OpenAI APIキー")
        self._openai_key_var = tk.StringVar(value=self.config.get("llm", "openai_api_key"))
        tk.Entry(frame, textvariable=self._openai_key_var, width=40,
                 show="*", font=("Yu Gothic UI", 10)).pack(fill="x", **pad)

        # --- オーディオデバイス ---
        self._section_label(frame, "オーディオ")

        self._row_label(frame, "入力デバイス")
        device_list = AudioRecorder.list_devices()
        device_names = ["デフォルト"] + [f"{i}: {name}" for i, name in device_list]
        current_idx = self.config.getint("audio", "device_index")
        current_device = "デフォルト" if current_idx == -1 else next(
            (f"{i}: {name}" for i, name in device_list if i == current_idx), "デフォルト"
        )
        self._audio_device_var = tk.StringVar(value=current_device)
        ttk.Combobox(frame, textvariable=self._audio_device_var,
                     values=device_names, state="readonly", width=40).pack(fill="x", **pad)

        # 無音判定時間
        self._row_label(frame, "無音停止時間 (ミリ秒)")
        silence_frame = tk.Frame(frame, bg="#F5F5F5")
        silence_frame.pack(fill="x", **pad)
        self._silence_var = tk.IntVar(value=self.config.getint("audio", "silence_threshold_ms"))
        tk.Scale(silence_frame, from_=500, to=3000, orient="horizontal",
                 variable=self._silence_var, length=200, resolution=100,
                 bg="#F5F5F5", font=("Yu Gothic UI", 9)).pack(side="left")
        tk.Label(silence_frame, textvariable=self._silence_var, bg="#F5F5F5",
                 font=("Yu Gothic UI", 10), width=5).pack(side="left")
        tk.Label(silence_frame, text="ms", bg="#F5F5F5", font=("Yu Gothic UI", 10)).pack(side="left")

        # --- ボタン ---
        btn_frame = tk.Frame(w, bg="#F5F5F5")
        btn_frame.pack(fill="x", padx=16, pady=12)

        tk.Button(btn_frame, text="保存", command=self._save,
                  bg="#0078D7", fg="white", font=("Yu Gothic UI", 11, "bold"),
                  padx=24, pady=6, relief="flat", cursor="hand2").pack(side="right", padx=4)
        tk.Button(btn_frame, text="キャンセル", command=self._on_close,
                  bg="#E0E0E0", fg="#333", font=("Yu Gothic UI", 11),
                  padx=16, pady=6, relief="flat", cursor="hand2").pack(side="right", padx=4)

    def _section_label(self, parent, text: str):
        """セクションヘッダー"""
        frame = tk.Frame(parent, bg="#0078D7", height=2)
        frame.pack(fill="x", padx=12, pady=(12, 2))
        tk.Label(parent, text=text, bg="#F5F5F5", fg="#0078D7",
                 font=("Yu Gothic UI", 11, "bold")).pack(anchor="w", padx=16, pady=(4, 0))

    def _row_label(self, parent, text: str):
        """行ラベル"""
        tk.Label(parent, text=text, bg="#F5F5F5", fg="#333",
                 font=("Yu Gothic UI", 10)).pack(anchor="w", padx=16, pady=(6, 0))

    def _checkbox(self, parent, text: str, var: tk.BooleanVar):
        """チェックボックス"""
        tk.Checkbutton(parent, text=text, variable=var, bg="#F5F5F5",
                       font=("Yu Gothic UI", 10), anchor="w").pack(anchor="w", padx=16, pady=2)

    def _on_model_change(self, event):
        """モデル変更時に情報を更新"""
        model = self._model_var.get()
        info = MODEL_INFO.get(model, {})
        if info:
            self._model_info_label.config(
                text=f"サイズ: {info['size']}  VRAM: {info['vram']}  {info['desc']}"
            )

    def _save(self):
        """設定を保存する"""
        # バリデーション
        hotkey = self._hotkey_var.get().strip()
        if not hotkey:
            messagebox.showerror("エラー", "ホットキーを入力してください", parent=self._window)
            return

        if not HotkeyManager.is_valid_hotkey(hotkey):
            messagebox.showerror("エラー", f"無効なホットキー: {hotkey}", parent=self._window)
            return

        # 保存
        self.config.set("general", "hotkey", hotkey)
        self.config.set("general", "auto_paste", str(self._auto_paste_var.get()).lower())
        self.config.set("whisper", "model", self._model_var.get())
        self.config.set("whisper", "device", self._device_var.get())
        self.config.set("whisper", "vad_filter", str(self._vad_filter_var.get()).lower())
        self.config.set("punctuation", "enabled", str(self._punct_enabled_var.get()).lower())
        self.config.set("punctuation", "add_linebreak", str(self._linebreak_var.get()).lower())
        self.config.set("llm", "enabled", str(self._llm_enabled_var.get()).lower())
        self.config.set("llm", "provider", self._llm_provider_var.get())
        self.config.set("llm", "ollama_model", self._ollama_model_var.get())
        self.config.set("llm", "openai_api_key", self._openai_key_var.get())

        # オーディオデバイス
        device_str = self._audio_device_var.get()
        if device_str == "デフォルト":
            self.config.set("audio", "device_index", "-1")
        else:
            idx = device_str.split(":")[0]
            self.config.set("audio", "device_index", idx)

        self.config.set("audio", "silence_threshold_ms", str(self._silence_var.get()))

        self.config.save()
        messagebox.showinfo("保存完了", "設定を保存しました。\n変更はホットキー再起動後に有効になります。",
                            parent=self._window)

        if self.on_save:
            self.on_save()

        self._on_close()

    def _on_close(self):
        if self._window:
            self._window.destroy()
            self._window = None
