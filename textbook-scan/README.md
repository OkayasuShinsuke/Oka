# textbook-scan (tscan)

教科書スキャン・日本語/数式OCRシステム。仕様書は [`docs/textbook-scan-spec.md`](../docs/textbook-scan-spec.md)(このリポジトリの `claude/textbook-scan-spec-akt5cc` ブランチ由来)を参照してください。各モジュールのdocstring・コメントに対応する仕様書の章番号(§)を記載しています。

## セットアップ

```bash
cd textbook-scan
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

macOS実機で使う場合、追加で以下をインストールしてください(Linux開発環境では不要/インストール不可)。

```bash
pip install -e ".[apple-vision]"   # macOS実機のみ。Apple Vision連携(§9.2)
pip install -e ".[yomitoku]"       # 任意。torch等の重い依存を含む(§9.2)
```

数式OCR(Mathpix)を使う場合は環境変数を設定してください(§16.2 REQ-SEC-02)。

```bash
export MATHPIX_APP_ID=xxxx
export MATHPIX_APP_KEY=xxxx
```

## 動作確認

```bash
pytest tests/ -v          # 45件のテストが通ることを確認済み(Linux上)
tscan doctor               # 依存関係・保存先の空き容量を診断
```

## 現時点で動く範囲 / 動かない範囲

このリポジトリはmacOSの実機・Mathpixの有料APIキー・実際にスキャンした教科書画像がない環境(このセッションのLinuxサンドボックス)で実装したため、**プラットフォーム非依存の部分は実装・テスト済み**ですが、**macOS/API必須の部分はインターフェースのみ実装**しています。正直な現状は以下の通りです。

| 機能 | 状態 | 対応する仕様書の章 |
|---|---|---|
| 取り込み・EXIF順リネーム | ✅ 実装・テスト済み | §7.6 |
| ページ抜け検出ロジック | ✅ 実装・テスト済み(ノンブルOCR自体は未接続) | §7.5 |
| ページ管理(挿入/削除/並べ替え、フラクショナル・インデックス) | ✅ 実装・テスト済み | §7.7 |
| 前処理(P1形式変換, P3-P4台形補正, P5デスキュー, P6見開き分割, P7照明補正, P8ノイズ除去, P10保存) | ✅ 実装・テスト済み(合成画像で検証) | §8 |
| セッション校正・補正結果の自動検査 | ✅ 実装・テスト済み | §8.3.1, §8.3.2 |
| レイアウト解析(縦書き判定・読み順・数式スコアリング) | ✅ 実装・テスト済み | §10 |
| 品質保証ロジック(ブレース対応検査・信頼度統合・多数決・意味検証・辞書候補) | ✅ 実装・テスト済み | §11 |
| データモデル・JSON入出力 | ✅ 実装・テスト済み | §12 |
| Markdown/品質レポート出力 | ✅ 実装・テスト済み | §13.2, §13.4 |
| CLI(ingest/calibrate/run/pages/doctor/check/export/config) | ✅ 実装・スモークテスト済み | §14.4 |
| レビューUI | 🟡 ホーム画面のみ最小実装。ページ一覧・編集画面は未実装 | §11.7 |
| Apple Vision連携 | 🔴 macOS実機必須。骨格のみ(呼び出すと `NotImplementedError`) | §9.2 |
| yomitoku連携 | 🔴 実モデルでの検証未実施。骨格のみ | §9.2 |
| Mathpix連携 | 🟡 リクエスト構築・レスポンス処理は実装済みだが、実APIキーでの疎通は未検証 | §9.2 |
| PDF出力(テキストレイヤー付き) | 🔴 画像のみのPDF化は実装。透明テキストレイヤーの重ね合わせは未実装 | §13.1 |

## 既知の制約

- `tscan run` は前処理(P1〜P10相当)までを実行します。OCR(§9)・検証(§11)は各エンジンの`recognize()`が未実装のため、`tscan run`からは呼び出していません。実装時は`src/tscan/ocr/`配下の各エンジンを実装し、パイプラインに接続してください。
- ノンブルOCRが未接続のため、`tscan check`は`Page.printed_number`が設定されていることを前提とします。
