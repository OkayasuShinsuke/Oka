"""
テキスト後処理モジュール
- 句読点・改行の自動付与
- LLM (Ollama / OpenAI) による高品質整形 (オプション)
"""
import re
import threading
from typing import Optional


# 文末記号のパターン (句点補完判定用)
SENTENCE_ENDERS = re.compile(r"[。！？!?]$")
# 読点を付けるべき接続詞パターン
CONJUNCTION_PATTERN = re.compile(
    r"(そして|しかし|また|ただし|なお|さらに|つまり|すなわち|"
    r"ところで|それから|ゆえに|したがって|一方|あるいは)"
)


class TextProcessor:
    """
    音声認識結果のテキストを整形するクラス。
    句読点補完とLLM後処理をサポート。
    """

    def __init__(self, config):
        self.config = config
        self._llm_lock = threading.Lock()

    def process(self, text: str) -> str:
        """テキストを処理する (同期)"""
        if not text.strip():
            return text

        # 基本クリーニング
        text = self._clean(text)

        # 句読点自動付与
        if self.config.getbool("punctuation", "enabled"):
            text = self._add_punctuation(text)

        return text

    def process_with_llm(self, text: str, callback) -> None:
        """LLMで非同期処理する"""
        def _run():
            try:
                result = self._llm_process(text)
                callback(result, None)
            except Exception as e:
                callback(text, str(e))

        threading.Thread(target=_run, daemon=True).start()

    def _clean(self, text: str) -> str:
        """基本的なクリーニング"""
        # 先頭・末尾の空白除去
        text = text.strip()
        # 重複スペース除去
        text = re.sub(r"\s+", " ", text)
        # whisperが出す英語混じりの「。」を日本語句点に統一
        text = text.replace("。。", "。")
        return text

    def _add_punctuation(self, text: str) -> str:
        """句読点と改行を自動追加"""
        add_linebreak = self.config.getbool("punctuation", "add_linebreak")

        # 文末に句点がなければ追加
        if text and not SENTENCE_ENDERS.search(text):
            text = text + "。"

        if add_linebreak:
            # 接続詞の前に改行
            text = CONJUNCTION_PATTERN.sub(r"\n\1", text)
            # 複数文が連続している場合、句点後に改行
            text = re.sub(r"([。！？!?])(?=[^\n])", r"\1\n", text)
            # 末尾の不要な改行を削除
            text = text.rstrip("\n")

        return text

    def _llm_process(self, text: str) -> str:
        """LLMでテキストを整形する (同期)"""
        provider = self.config.get("llm", "provider")

        if provider == "ollama":
            return self._ollama_process(text)
        elif provider == "openai":
            return self._openai_process(text)
        else:
            return text

    def _ollama_process(self, text: str) -> str:
        """Ollama APIでテキスト整形"""
        import requests

        url = self.config.get("llm", "ollama_url")
        model = self.config.get("llm", "ollama_model")
        prompt_template = self.config.get("llm", "prompt")
        prompt = prompt_template.format(text=text)

        response = requests.post(
            f"{url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 512,
                },
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        result = data.get("response", text).strip()
        return result if result else text

    def _openai_process(self, text: str) -> str:
        """OpenAI APIでテキスト整形"""
        import requests

        api_key = self.config.get("llm", "openai_api_key")
        model = self.config.get("llm", "openai_model")
        prompt_template = self.config.get("llm", "prompt")
        prompt = prompt_template.format(text=text)

        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 512,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        result = data["choices"][0]["message"]["content"].strip()
        return result if result else text

    @property
    def llm_enabled(self) -> bool:
        return self.config.getbool("llm", "enabled")
