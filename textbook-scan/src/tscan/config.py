"""設定ファイルの読み込み(仕様書 §14.5)。

優先順位(REQ-STORE-01): CLI引数 > 本ごとの設定 > ユーザー設定(USER_CONFIG_PATH)
> リポジトリ既定値(DEFAULT_CONFIG_PATH) > コード上の既定値。

DEFAULT_CONFIG_PATH はリポジトリに同梱された「工場出荷時の既定値」で、
バージョン管理対象であり、`tscan config-set-output-dir` 等では書き換えない。
ユーザーが変更した値は USER_CONFIG_PATH (~/.config/tscan/config.yaml) に保存する。
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "default.yaml"
USER_CONFIG_PATH = Path.home() / ".config" / "tscan" / "config.yaml"


class EngineConfig(BaseModel):
    horizontal: list[str] = ["apple_vision", "yomitoku"]
    vertical: list[str] = ["yomitoku", "apple_vision"]


class MathEngineConfig(BaseModel):
    primary: str = "mathpix"
    fallback: str = "texify"


class ThresholdsConfig(BaseModel):
    auto_accept: float = 0.95
    review_required: float = 0.80
    ensemble_full_match: float = 1.00
    ensemble_partial_match: float = 0.95


class PreprocessConfig(BaseModel):
    calibration_search_margin_px: int = 40
    aspect_ratio_tolerance: float = 0.05


class TscanConfig(BaseModel):
    """§14.5 REQ-STORE-01の優先順位: CLI引数 > 本ごとの設定 > このファイル > 既定値。"""

    output_root: str = "~/Documents/教科書スキャン"
    per_book_override: bool = True
    thresholds: ThresholdsConfig = ThresholdsConfig()
    preprocess: PreprocessConfig = PreprocessConfig()
    offline_mode: bool = False

    def resolved_output_root(self) -> Path:
        return Path(self.output_root).expanduser()


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_config() -> TscanConfig:
    """リポジトリ既定値 + ユーザー設定をマージして読み込む(後者が優先)。"""
    merged = {**_read_yaml(DEFAULT_CONFIG_PATH), **_read_yaml(USER_CONFIG_PATH)}
    # engines/performance等、TscanConfigに未定義の追加項目はここで無視する
    return TscanConfig(**{k: v for k, v in merged.items() if k in TscanConfig.model_fields})


def set_user_output_dir(path: str) -> None:
    """既定保存先をユーザー設定に保存する(§14.5)。リポジトリのdefault.yamlは変更しない。"""
    USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = _read_yaml(USER_CONFIG_PATH)
    raw["output_root"] = path
    USER_CONFIG_PATH.write_text(yaml.dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
