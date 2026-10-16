// run.js — server.js が「更新を取り込んだので再起動して」と伝えてきたとき
// （終了コード87）、自動でもう一度起動し直すだけの薄いラッパー。
// `npm start` はこのファイルを起動する（server.js を直接は起動しない）。
const { spawn } = require('child_process');

function start() {
  const child = spawn(process.execPath, ['server.js'], {
    cwd: __dirname,
    stdio: 'inherit',
    env: process.env,
  });
  child.on('exit', (code) => {
    if (code === 87) {
      console.log('\n🔄 更新を取り込んだので、サーバーを再起動します...\n');
      start();
    } else {
      process.exit(code === null ? 0 : code);
    }
  });
}

start();
