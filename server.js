const express = require('express');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const app = express();
const PORT = process.env.PORT || 3000;
const TRANSLATE_ENDPOINT = process.env.TRANSLATE_ENDPOINT || 'https://libretranslate.com/translate';
const SAVES_DIR = path.join(__dirname, 'data', 'saves');
const SAVE_CODE_RE = /^\d{6}$/;
const MAX_SAVE_JSON_LENGTH = 50 * 1024; // 50KB

// VOICEVOX（ローカルで動く無料の音声合成エンジン）へのプロキシ設定。
// エンジンを起動していない環境（このサーバーだけ動かしている場合）では
// 単に接続エラーになるので、クライアント側は自動でWeb Speech APIにフォールバックする。
const VOICEVOX_URL = process.env.VOICEVOX_URL || 'http://localhost:50021';
// 3 = 「ずんだもん（ノーマル）」のつもりのデフォルト値。ただし話者IDはVOICEVOXの
// バージョンや更新で変わる可能性があるため、決め打ちにせず、エンジンが起動していれば
// `GET http://localhost:50021/speakers` を実際に確認してから設定するのが望ましい。
const VOICEVOX_SPEAKER = process.env.VOICEVOX_SPEAKER || '3';
const VOICEVOX_TIMEOUT_MS = 2000;
// speak() のmood（ask/praise/sorry）に応じて、VOICEVOXのaudio_queryが返す
// speedScale/pitchScaleを少し調整する（Web Speech版のSPEAK_MOODSと同じ考え方）。
const VOICEVOX_MOOD_PARAMS = {
  ask: { speedScale: 0.98, pitchScale: 0.02 },
  praise: { speedScale: 1.05, pitchScale: 0.07 },
  sorry: { speedScale: 0.92, pitchScale: -0.03 },
};

app.use(express.json({ limit: '64kb' }));
app.use(express.static('public'));

function saveFilePath(code) {
  return path.join(SAVES_DIR, `${code}.json`);
}

function generateSaveCode() {
  let code;
  let attempts = 0;
  do {
    code = String(crypto.randomInt(0, 1000000)).padStart(6, '0');
    attempts += 1;
  } while (fs.existsSync(saveFilePath(code)) && attempts < 20);
  return code;
}

app.post('/api/save', (req, res) => {
  const { code, data } = req.body || {};

  if (code !== undefined && !SAVE_CODE_RE.test(String(code))) {
    return res.status(400).json({ error: 'code は6けたの数字である必要があります。' });
  }
  if (!data || typeof data !== 'object') {
    return res.status(400).json({ error: 'data は必須です。' });
  }

  let json;
  try {
    json = JSON.stringify(data);
  } catch (error) {
    return res.status(400).json({ error: 'data を保存できませんでした。' });
  }
  if (json.length > MAX_SAVE_JSON_LENGTH) {
    return res.status(400).json({ error: `セーブデータが大きすぎます（上限 ${MAX_SAVE_JSON_LENGTH / 1024}KB）。` });
  }

  try {
    fs.mkdirSync(SAVES_DIR, { recursive: true });
    const finalCode = code || generateSaveCode();
    fs.writeFileSync(saveFilePath(finalCode), json);
    return res.json({ code: finalCode });
  } catch (error) {
    return res.status(500).json({ error: 'セーブに失敗しました。', detail: String(error) });
  }
});

app.get('/api/save/:code', (req, res) => {
  const { code } = req.params;

  if (!SAVE_CODE_RE.test(code)) {
    return res.status(400).json({ error: 'code は6けたの数字である必要があります。' });
  }

  try {
    const raw = fs.readFileSync(saveFilePath(code), 'utf8');
    return res.json({ data: JSON.parse(raw) });
  } catch (error) {
    return res.status(404).json({ error: 'その ひきつぎばんごうは みつかりませんでした。' });
  }
});

app.post('/api/translate', async (req, res) => {
  const { text, source, target } = req.body || {};

  if (!text || !source || !target) {
    return res.status(400).json({ error: 'text, source, target は必須です。' });
  }

  try {
    const response = await fetch(TRANSLATE_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, source, target, format: 'text' })
    });

    if (!response.ok) {
      const detail = await response.text();
      return res.status(502).json({ error: '翻訳APIエラー', detail });
    }

    const data = await response.json();
    return res.json({ translatedText: data.translatedText || data.translation || '' });
  } catch (error) {
    return res.status(500).json({ error: '翻訳処理に失敗しました。', detail: String(error) });
  }
});

app.get('/api/tts', async (req, res) => {
  const text = String(req.query.text || '').trim();
  const mood = String(req.query.mood || 'ask');

  if (!text) {
    return res.status(400).json({ error: 'text は必須です。' });
  }
  if (text.length > 200) {
    return res.status(400).json({ error: 'text が長すぎます。' });
  }

  const speaker = encodeURIComponent(VOICEVOX_SPEAKER);
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), VOICEVOX_TIMEOUT_MS);

  try {
    // ① audio_query: テキストから音声合成用のクエリ(JSON)を生成する
    const queryUrl = `${VOICEVOX_URL}/audio_query?text=${encodeURIComponent(text)}&speaker=${speaker}`;
    const queryRes = await fetch(queryUrl, { method: 'POST', signal: controller.signal });
    if (!queryRes.ok) {
      throw new Error(`audio_query failed: ${queryRes.status}`);
    }
    const query = await queryRes.json();

    const moodParams = VOICEVOX_MOOD_PARAMS[mood] || VOICEVOX_MOOD_PARAMS.ask;
    if (typeof query.speedScale === 'number') query.speedScale *= moodParams.speedScale;
    if (typeof query.pitchScale === 'number') query.pitchScale += moodParams.pitchScale;

    // ② synthesis: ①のクエリをbodyに渡して実際の音声(wav)を合成する
    const synthUrl = `${VOICEVOX_URL}/synthesis?speaker=${speaker}`;
    const synthRes = await fetch(synthUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(query),
      signal: controller.signal,
    });
    if (!synthRes.ok) {
      throw new Error(`synthesis failed: ${synthRes.status}`);
    }

    clearTimeout(timeoutId);
    res.setHeader('Content-Type', 'audio/wav');
    const buf = Buffer.from(await synthRes.arrayBuffer());
    return res.send(buf);
  } catch (error) {
    clearTimeout(timeoutId);
    // 接続拒否・タイムアウト・エンジン側エラーなど、原因を区別せず
    // 「エンジンが使えない」として503を返す（クライアントはこれを見てフォールバックする）
    return res.status(503).json({ error: 'VOICEVOXエンジンに接続できませんでした。', detail: String(error && error.message || error) });
  }
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Voice translator is running on http://0.0.0.0:${PORT}`);
});
