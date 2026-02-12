const express = require('express');

const app = express();
const PORT = process.env.PORT || 3000;
const TRANSLATE_ENDPOINT = process.env.TRANSLATE_ENDPOINT || 'https://libretranslate.com/translate';

app.use(express.json());
app.use(express.static('public'));

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
