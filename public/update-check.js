// update-check.js — ローカルで起動した server.js に対して「GitHub側に新しい
// コミットがあるか」を聞き、あればページ上部にバナーを出す共通スクリプト。
// まなびっこ(kids)・Advanced Lab(advanced)の両方から読み込まれる。
(function () {
  "use strict";

  var STYLE = document.createElement("style");
  STYLE.textContent =
    "#update-banner{position:fixed;top:0;left:0;right:0;z-index:99999;" +
    "background:#2A2250;color:#fff;font-family:system-ui,-apple-system,sans-serif;" +
    "padding:10px 14px;display:flex;flex-wrap:wrap;align-items:center;justify-content:center;" +
    "gap:10px;font-size:14px;box-shadow:0 2px 8px rgba(0,0,0,.35);}" +
    "#update-banner button{border:none;border-radius:999px;padding:6px 16px;" +
    "font-weight:700;cursor:pointer;font-size:14px;}" +
    "#update-banner .ub-apply{background:#FFD23F;color:#2A2250;}" +
    "#update-banner .ub-close{background:transparent;color:#fff;opacity:.75;text-decoration:underline;}" +
    "#update-banner[data-busy=\"1\"] .ub-apply{opacity:.6;pointer-events:none;}";
  document.head.appendChild(STYLE);

  function showBanner() {
    if (document.getElementById("update-banner")) return;
    var bar = document.createElement("div");
    bar.id = "update-banner";
    bar.innerHTML =
      '<span class="ub-text">🔔 新しいアップデートがあります</span>' +
      '<button type="button" class="ub-apply">今すぐ更新する</button>' +
      '<button type="button" class="ub-close">あとで</button>';
    document.body.appendChild(bar);
    bar.querySelector(".ub-apply").addEventListener("click", applyUpdate);
    bar.querySelector(".ub-close").addEventListener("click", function () {
      bar.remove();
    });
  }

  function applyUpdate() {
    var bar = document.getElementById("update-banner");
    if (!bar) return;
    bar.dataset.busy = "1";
    bar.querySelector(".ub-text").textContent = "⏳ 更新しています…（サーバーが少しだけ再起動します）";
    fetch("/api/update-apply", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || !data.ok) throw new Error((data && data.error) || "更新に失敗しました");
        bar.querySelector(".ub-text").textContent = "✅ 更新できました！読み込み直します…";
        waitForServerAndReload(0);
      })
      .catch(function (err) {
        bar.querySelector(".ub-text").textContent = "⚠️ 更新に失敗しました: " + err.message;
        bar.dataset.busy = "";
      });
  }

  function waitForServerAndReload(attempt) {
    setTimeout(function () {
      fetch("/api/update-check", { cache: "no-store" })
        .then(function () { location.reload(); })
        .catch(function () {
          if (attempt < 20) waitForServerAndReload(attempt + 1);
          else location.reload();
        });
    }, 700);
  }

  function checkForUpdate() {
    fetch("/api/update-check", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.updateAvailable) showBanner();
      })
      .catch(function () { /* サーバー未起動・ネットワーク無しなどは黙って無視 */ });
  }

  if (document.readyState === "complete" || document.readyState === "interactive") {
    checkForUpdate();
  } else {
    document.addEventListener("DOMContentLoaded", checkForUpdate);
  }
})();
