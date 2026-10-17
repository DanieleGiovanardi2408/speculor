"use strict";

var DEFAULT_API_BASE = "http://127.0.0.1:8000";
var API_BASE_RE = /^http:\/\/(127\.0\.0\.1|localhost)(:\d{1,5})?$/;
var form = document.getElementById("form");
var apiBase = document.getElementById("apiBase");
var token = document.getElementById("token");
var status = document.getElementById("status");

chrome.storage.local.get({ apiBase: DEFAULT_API_BASE, token: "" }).then(function (s) {
  apiBase.value = s.apiBase;
  token.value = s.token;
});

form.addEventListener("submit", function (event) {
  event.preventDefault();
  var base = apiBase.value.trim().replace(/\/+$/, "") || DEFAULT_API_BASE;
  if (!API_BASE_RE.test(base)) {
    status.textContent = "Usa http://127.0.0.1:<porta> o http://localhost:<porta>.";
    return;
  }
  chrome.storage.local.set({ apiBase: base, token: token.value.trim() }).then(function () {
    status.textContent = "Salvato.";
  });
});
