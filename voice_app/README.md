# 日本語音声認識アプリ

Windows標準より高精度な日本語音声認識アプリです。
OpenAI Whisper (faster-whisper) を使用してローカルで動作します。

## 特徴

- **高精度日本語認識**: Whisper large-v3 モデルによる最高品質の認識
- **高速処理**: faster-whisper (CTranslate2) による最適化
- **ローカル動作**: インターネット不要（初回モデルダウンロードのみ必要）
- **ホットキー**: `Ctrl+Shift+Space` で音声入力のオン/オフ
- **自動句読点**: 。、の自動付与とオン/オフ切り替え
- **自動改行**: 接続詞や句点後の改行をオン/オフ
- **LLM整形**: Ollama または OpenAI でさらに高品質な整形（オプション）
- **システムトレイ**: タスクバーからメニューで設定変更
- **自動貼り付け**: 認識結果をアクティブウィンドウに自動入力

## インストール

### 必要環境

- Windows 10/11
- Python 3.9 以上
- マイク

### インストール手順

```bat
# 1. インストール
install.bat をダブルクリック

# または手動で:
pip install -r requirements.txt
```

### GPU加速 (オプション・推奨)

NVIDIA GPUがある場合、CUDA版をインストールで処理が大幅に高速化されます:

```bat
# CUDA 11.x
pip install faster-whisper

# CUDA Toolkitも必要:
# https://developer.nvidia.com/cuda-downloads
```

## 起動

```bat
# 管理者権限で起動 (グローバルホットキーに必要)
start.bat

# または
python main.py
```

## 使い方

1. `start.bat` を実行してアプリを起動
2. タスクバーにマイクアイコンが表示される
3. **`Ctrl+Shift+Space`** を押して話す
4. 話し終わると自動的に認識されてテキストが貼り付けられる
5. もう一度押すと強制停止

### システムトレイメニュー

トレイアイコンを右クリック:
- **音声入力 ON/OFF**: 録音開始/停止
- **句読点自動付与**: 。、の自動付与を切り替え
- **自動改行**: 改行の自動追加を切り替え
- **LLM整形**: LLMによる整形を切り替え
- **設定**: 設定ウィンドウを開く
- **終了**: アプリを終了

## 設定

### Whisperモデル

| モデル | サイズ | VRAM | 精度 | 速度 |
|--------|--------|------|------|------|
| tiny | 75MB | ~1GB | 低 | 最速 |
| base | 145MB | ~1GB | 中 | 高速 |
| small | 466MB | ~2GB | 中高 | 普通 |
| medium | 1.5GB | ~5GB | 高 | 普通 |
| **large-v3** | 3.1GB | ~10GB | **最高** | 遅め |
| large-v3-turbo | 1.6GB | ~6GB | 高 | 速め |

GPUなしの場合は `small` または `medium` を推奨。

### LLM後処理 (Ollama)

ローカルLLMでさらに高品質な句読点・整形を行えます:

```bash
# Ollamaをインストール
winget install Ollama.Ollama

# モデルをダウンロード
ollama pull llama3.2:3b     # 軽量
ollama pull qwen2.5:7b      # 高品質
```

設定ウィンドウで「LLM整形」を有効にしてOllamaモデルを指定してください。

### ホットキーのカスタマイズ

設定ウィンドウで変更可能:
- `ctrl+shift+space` (デフォルト)
- `ctrl+alt+v`
- `f9`
- など

## 設定ファイル

設定は `~/.voice_app/config.ini` に保存されます。

## トラブルシューティング

### ホットキーが動作しない
→ `start.bat` を使って管理者権限で起動してください

### 認識精度が低い
→ モデルを `large-v3` に変更してください（設定画面）
→ マイクの入力レベルを確認してください

### 処理が遅い
→ GPU (CUDA) が利用可能であれば設定を `cuda` に変更
→ `large-v3-turbo` モデルを使用
→ CPUのみの場合は `small` モデルを推奨

### 初回起動時にモデルがダウンロードされる
→ large-v3 は約3GB。初回のみインターネット接続が必要です。
→ `~/.voice_app/models/` に保存されます。
