// "Salva in Radar" content script: runs only on JobTeaser listing pages (see manifest.json).
// On click it parses the offers ALREADY rendered on this page (parser.js) and hands them to the
// service worker. It never scrolls, never clicks "next", never fetches other pages, never reads
// cookies or storage of the site.
(function () {
  "use strict";

  var parser = globalThis.RadarJobteaserParser;
  if (!parser || document.getElementById("radar-save-root")) return;

  var root = document.createElement("div");
  root.id = "radar-save-root";
  var shadow = root.attachShadow({ mode: "closed" });
  shadow.innerHTML =
    "<style>" +
    ":host{all:initial}" +
    ".wrap{position:fixed;right:16px;bottom:16px;z-index:2147483647;display:flex;flex-direction:column;" +
    "align-items:flex-end;gap:8px;font:14px/1.4 system-ui,-apple-system,sans-serif}" +
    "button{background:#1f4fd1;color:#fff;border:0;border-radius:999px;padding:10px 16px;font:inherit;" +
    "font-weight:600;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.25)}" +
    "button:disabled{opacity:.6;cursor:progress}" +
    "button:focus-visible{outline:3px solid #ffbf47;outline-offset:2px}" +
    ".msg{max-width:280px;background:#111;color:#fff;border-radius:8px;padding:8px 12px;" +
    "box-shadow:0 2px 8px rgba(0,0,0,.25)}" +
    ".msg.err{background:#8a1c1c}" +
    "</style>" +
    '<div class="wrap"><div class="msg" role="status" aria-live="polite" hidden></div>' +
    '<button type="button">Salva in Radar</button></div>';
  var button = shadow.querySelector("button");
  var msg = shadow.querySelector(".msg");
  var hideTimer = null;

  function show(text, isError) {
    msg.textContent = text;
    msg.className = isError ? "msg err" : "msg";
    msg.hidden = false;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(function () {
      msg.hidden = true;
    }, 8000);
  }

  function plural(n, one, many) {
    return n + " " + (n === 1 ? one : many);
  }

  button.addEventListener("click", function () {
    var offers = parser.parseOffers(document, location.href);
    if (offers.length === 0) {
      show("Nessuna offerta visibile in questa pagina.", true);
      return;
    }
    button.disabled = true;
    chrome.runtime.sendMessage({ type: "radar:save-jobteaser", offers: offers }, function (res) {
      button.disabled = false;
      if (chrome.runtime.lastError || !res) {
        show("Estensione non raggiungibile: ricarica la pagina.", true);
      } else if (res.ok) {
        show(
          plural(res.received, "offerta inviata", "offerte inviate") +
            ", " +
            plural(res.new, "nuova", "nuove") +
            (res.ignored ? " (" + res.ignored + " ignorate)" : ""),
          false
        );
      } else {
        show(res.error || "Invio non riuscito.", true);
      }
    });
  });

  // Single-page app: keep the button only while this tab shows a listing page.
  function sync() {
    root.style.display = parser.isListingUrl(location.href) ? "" : "none";
  }
  document.documentElement.appendChild(root);
  sync();
  setInterval(sync, 1000);
})();
