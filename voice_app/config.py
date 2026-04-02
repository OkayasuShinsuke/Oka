"""
設定管理モジュール
"""
import configparser
import os
from pathlib import Path

CONFIG_FILE = Path.home() / ".voice_app" / "config.ini"

DEFAULTS = {
    "general": {
        "hotkey": "ctrl+shift+space",
        "language": "ja",
        "auto_paste": "true",
    },
    "whisper": {
        "model": "large-v3",          # tiny/base/small/medium/large-v3
        "device": "auto",             # auto/cpu/cuda
        "compute_type": "auto",       # auto/int8/float16/float32
        "beam_size": "5",
        "vad_filter": "true",
        "vad_min_silence_duration_ms": "1000",
    },
    "punctuation": {
        "enabled": "true",
        "add_linebreak": "true",
        "linebreak_threshold": "2",   # 何秒の間があれば改行するか
    },
    "llm": {
        "enabled": "false",
        "provider": "ollama",         # ollama / openai
        "ollama_url": "http://localhost:11434",
        "ollama_model": "llama3.2:3b",
        "openai_api_key": "",
        "openai_model": "gpt-4o-mini",
        "prompt": (
            "以下の日本語テキストに句読点（。、）と適切な改行を追加してください。"
            "元の意味を変えずに、読みやすく整形してください。"
            "整形後のテキストのみ出力してください:\n\n{text}"
        ),
    },
    "audio": {
        "sample_rate": "16000",
        "channels": "1",
        "vad_aggressiveness": "2",    # 0-3 (高いほど感度高)
        "silence_threshold_ms": "1500",  # 無音が続いたら録音停止
        "max_record_seconds": "60",
        "device_index": "-1",         # -1 = デフォルトデバイス
    },
    "ui": {
        "show_popup": "true",
        "popup_duration_ms": "3000",
        "show_tray_icon": "true",
    },
}


class Config:
    def __init__(self):
        self._parser = configparser.ConfigParser()
        self._set_defaults()
        self._load()

    def _set_defaults(self):
        for section, values in DEFAULTS.items():
            self._parser[section] = values

    def _load(self):
        if CONFIG_FILE.exists():
            self._parser.read(CONFIG_FILE, encoding="utf-8")

    def save(self):
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            self._parser.write(f)

    def get(self, section: str, key: str) -> str:
        return self._parser.get(section, key)

    def getbool(self, section: str, key: str) -> bool:
        return self._parser.getboolean(section, key)

    def getint(self, section: str, key: str) -> int:
        return self._parser.getint(section, key)

    def getfloat(self, section: str, key: str) -> float:
        return self._parser.getfloat(section, key)

    def set(self, section: str, key: str, value: str):
        if not self._parser.has_section(section):
            self._parser.add_section(section)
        self._parser.set(section, key, str(value))

    def sections(self):
        return self._parser.sections()

    def options(self, section: str):
        return self._parser.options(section)
