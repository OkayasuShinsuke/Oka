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

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Voice translator is running on http://0.0.0.0:${PORT}`);
});
