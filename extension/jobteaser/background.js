// "Salva in Radar" service worker: POSTs the offers from the content script to the local Radar
// API. Requests from an extension service worker to an origin listed in host_permissions are
// not subject to CORS or to Private Network Access checks (those apply to web pages), so the
// JobTeaser page itself never talks to 127.0.0.1. Chrome sends Origin: chrome-extension://<id>,
// which Radar requires together with the local token.
"use strict";

var DEFAULT_API_BASE = "http://127.0.0.1:8000";
var MAX_OFFERS = 500;
var API_BASE_RE = /^http:\/\/(127\.0\.0\.1|localhost)(:\d{1,5})?$/;

function isJobteaserUrl(href) {
  try {
    var host = new URL(href).hostname.toLowerCase();
    return host === "jobteaser.com" || host.slice(-14) === ".jobteaser.com";
  } catch (e) {
    return false;
  }
}

function settings() {
  return chrome.storage.local.get({ apiBase: DEFAULT_API_BASE, token: "" });
}

async function send(offers) {
  var s = await settings();
  var base = String(s.apiBase || DEFAULT_API_BASE).replace(/\/+$/, "");
  if (!API_BASE_RE.test(base)) {
    return { ok: false, error: "Indirizzo di Radar non valido: controlla le opzioni dell'estensione." };
  }
  if (!s.token) {
    return { ok: false, error: "Token mancante: esegui `radar extension token` e incollalo nelle opzioni." };
  }
  var res;
  try {
    res = await fetch(base + "/api/ingest/jobteaser", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Radar-Token": s.token },
      body: JSON.stringify({ offers: offers.slice(0, MAX_OFFERS) }),
      credentials: "omit",
      cache: "no-store",
    });
  } catch (e) {
    return { ok: false, error: "Radar non risponde su " + base + ": è avviato (make dev-real)?" };
  }
  if (res.status === 401) return { ok: false, error: "Token non valido: rigeneralo con `radar extension token`." };
  if (res.status === 403) return { ok: false, error: "Radar ha rifiutato l'origine dell'estensione (RADAR_EXTENSION_ID?)." };
  if (!res.ok) return { ok: false, error: "Radar ha risposto con errore " + res.status + "." };
  var body = await res.json();
  return { ok: true, received: body.received, new: body.new, updated: body.updated, ignored: body.ignored };
}

chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (!message || message.type !== "radar:save-jobteaser") return false;
  // only from our own content script on a JobTeaser tab
  if (sender.id !== chrome.runtime.id || !sender.url || !isJobteaserUrl(sender.url)) return false;
  if (!Array.isArray(message.offers)) return false;
  send(message.offers).then(sendResponse, function () {
    sendResponse({ ok: false, error: "Invio non riuscito." });
  });
  return true; // async response
});
