const statusEl = document.getElementById('status');
const detectedLanguageEl = document.getElementById('detectedLanguage');
const sourceTextEl = document.getElementById('sourceText');
const translatedTextEl = document.getElementById('translatedText');
const voiceModeToggle = document.getElementById('voiceModeToggle');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const manualText = document.getElementById('manualText');
const translateBtn = document.getElementById('translateBtn');

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const hasSpeechRecognition = Boolean(SpeechRecognition);
let recognition;
let isListening = false;

const setStatus = (text) => {
  statusEl.textContent = text;
};

const detectLang = (text) => {
  const japanesePattern = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]/;
  return japanesePattern.test(text) ? 'ja' : 'vi';
};

const speak = (text, lang) => {
  if (!('speechSynthesis' in window)) {
    setStatus('音声読み上げはこのブラウザで未対応です。');
    return;
  }

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = lang === 'ja' ? 'ja-JP' : 'vi-VN';
  utterance.rate = 1;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
};

const runTranslation = async (inputText) => {
  const text = inputText.trim();
  if (!text) {
    return;
  }

  sourceTextEl.textContent = text;
  setStatus('翻訳中...');

  const sourceLang = detectLang(text);
  const targetLang = sourceLang === 'ja' ? 'vi' : 'ja';
  detectedLanguageEl.textContent = sourceLang === 'ja' ? '日本語' : 'ベトナム語';

  try {
    const response = await fetch('/api/translate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, source: sourceLang, target: targetLang })
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || '翻訳失敗');
    }

    translatedTextEl.textContent = data.translatedText;
    setStatus('翻訳完了（読み上げ中）');
    speak(data.translatedText, targetLang);
  } catch (error) {
    setStatus(`翻訳エラー: ${error.message}`);
  }
};

const setupRecognition = () => {
  if (!hasSpeechRecognition) {
    setStatus('このブラウザでは音声認識が未対応です。手動入力を利用してください。');
    startBtn.disabled = true;
    stopBtn.disabled = true;
    voiceModeToggle.checked = false;
    voiceModeToggle.disabled = true;
    return;
  }

  recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = false;
  recognition.lang = 'ja-JP';

  recognition.onstart = () => {
    isListening = true;
    setStatus('音声入力中...（日本語/ベトナム語を話してください）');
  };

  recognition.onend = () => {
    isListening = false;
    if (voiceModeToggle.checked) {
      recognition.start();
    } else {
      setStatus('待機中');
    }
  };

  recognition.onerror = (event) => {
    setStatus(`音声認識エラー: ${event.error}`);
  };

  recognition.onresult = (event) => {
    const transcript = event.results[event.results.length - 1][0].transcript;
    runTranslation(transcript);
  };
};

startBtn.addEventListener('click', () => {
  if (recognition && !isListening) {
    recognition.start();
  }
});

stopBtn.addEventListener('click', () => {
  if (recognition && isListening) {
    voiceModeToggle.checked = false;
    recognition.stop();
  }
});

voiceModeToggle.addEventListener('change', () => {
  if (!recognition) {
    return;
  }

  if (voiceModeToggle.checked && !isListening) {
    recognition.start();
  }

  if (!voiceModeToggle.checked && isListening) {
    recognition.stop();
  }
});

translateBtn.addEventListener('click', () => {
  runTranslation(manualText.value);
});

window.addEventListener('load', () => {
  setupRecognition();
  if (voiceModeToggle.checked && recognition) {
    recognition.start();
  }
});
