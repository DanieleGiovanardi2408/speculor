// "Salva in Radar": pure parser for a JobTeaser listing page (no side effects, no network).
// Reads only the offer cards already rendered in the given DOM: title, company, offer URL,
// location, date if shown. Loaded as a classic content script before content.js, and imported
// by the unit tests (frontend/src/extension/jobteaserParser.test.ts) with fictional HTML.
(function () {
  "use strict";

  var MAX_OFFERS = 500;
  var OFFER_PATH =
    /^\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?job-offers\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+)(?:-[^/]*)?\/?$/i;
  var LISTING_PATH = /^\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?job-offers\/?$/i;
  var DATE_RE =
    /(\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s+(gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic|jan|may|jun|jul|aug|sep|oct|dec)[a-z]*\.?\s*\d{0,4}\b|\b(oggi|ieri|today|yesterday)\b|\b\d+\s+(giorn[oi]|settiman[ae]|mes[ei]|or[ae]|days?|weeks?|months?|hours?)\s+(fa|ago)\b|\bpubblicat[ao]\b|\bpublished\b)/i;
  var CONTRACT_RE =
    /^(stage|tirocinio|tirocini|internship|stage curriculare|stage extracurriculare|apprendistato|full[- ]?time|part[- ]?time|cdi|cdd|lavoro|job|graduate programme?|graduate program|alternanza|alternance|vie|freelance|contratto a tempo (in)?determinato)$/i;
  var SEPARATORS = /\s+[·•|–]\s+/;

  function clean(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

  function isJobteaserHost(host) {
    host = String(host || "").toLowerCase();
    return host === "jobteaser.com" || host.slice(-14) === ".jobteaser.com";
  }

  /** True for a listing/search page URL (offer list, with or without query), not an offer. */
  function isListingUrl(href) {
    try {
      var u = new URL(href);
      return u.protocol === "https:" && isJobteaserHost(u.hostname) && LISTING_PATH.test(u.pathname);
    } catch (e) {
      return false;
    }
  }

  /** {id, url} for an offer link (canonical: no query, no fragment), else null. */
  function offerLink(href, baseUrl) {
    var u;
    try {
      u = new URL(href, baseUrl);
    } catch (e) {
      return null;
    }
    if (u.protocol !== "https:" || !isJobteaserHost(u.hostname)) return null;
    var m = OFFER_PATH.exec(u.pathname);
    if (!m) return null;
    return { id: m[1].toLowerCase(), url: u.origin + u.pathname.replace(/\/$/, "") };
  }

  function offerIdsIn(el, baseUrl) {
    var ids = {};
    var anchors = el.querySelectorAll("a[href]");
    for (var i = 0; i < anchors.length; i++) {
      var link = offerLink(anchors[i].getAttribute("href"), baseUrl);
      if (link) ids[link.id] = true;
    }
    return Object.keys(ids);
  }

  /** The largest ancestor of the link that contains offer links to this offer only. */
  function cardOf(anchor, id, root, baseUrl) {
    var card = anchor;
    var node = anchor.parentElement;
    for (var depth = 0; node && depth < 8; depth++) {
      if (node === root || node.tagName === "BODY" || node.tagName === "HTML") break;
      var ids = offerIdsIn(node, baseUrl);
      if (ids.length > 1 || (ids.length === 1 && ids[0] !== id)) break;
      card = node;
      node = node.parentElement;
    }
    return card;
  }

  function first(card, selectors) {
    for (var i = 0; i < selectors.length; i++) {
      var el = card.querySelector(selectors[i]);
      if (el && clean(el.textContent)) return el;
    }
    return null;
  }

  /** Visible-ish text segments of a card, in document order (scripts/styles skipped). */
  function lines(card) {
    var out = [];
    var doc = card.ownerDocument;
    var walker = doc.createTreeWalker(card, 4 /* NodeFilter.SHOW_TEXT */);
    var node;
    while ((node = walker.nextNode())) {
      var parent = node.parentElement;
      if (parent && /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|SVG|BUTTON)$/i.test(parent.tagName)) continue;
      if (parent && parent.closest("[aria-hidden='true'], [hidden]")) continue;
      var parts = clean(node.nodeValue).split(SEPARATORS);
      for (var i = 0; i < parts.length; i++) {
        var part = clean(parts[i]);
        if (part) out.push(part);
      }
    }
    return out;
  }

  function cap(text, n) {
    text = clean(text);
    return text.length > n ? text.slice(0, n) : text;
  }

  function parseCard(card, anchor, link) {
    var titleEl = first(card, [
      "h1",
      "h2",
      "h3",
      "h4",
      "[data-testid*='title' i]",
      "[class*='title' i]",
    ]);
    var title = clean(titleEl ? titleEl.textContent : "") || clean(anchor.textContent);
    title = title || clean(anchor.getAttribute("aria-label") || anchor.getAttribute("title"));
    if (!title) return null;

    var companyEl = first(card, [
      "[data-testid*='company' i]",
      "[data-testid*='organization' i]",
      "[class*='company' i]",
      "[class*='organization' i]",
      "[class*='employer' i]",
    ]);
    var locationEl = first(card, [
      "[data-testid*='location' i]",
      "[class*='location' i]",
      "[class*='city' i]",
      "address",
    ]);
    var timeEl = card.querySelector("time");
    var date = timeEl ? clean(timeEl.getAttribute("datetime") || timeEl.textContent) : "";

    var rest = lines(card).filter(function (line) {
      return line !== title && title.indexOf(line) === -1;
    });
    if (!date) {
      for (var i = 0; i < rest.length; i++) {
        if (DATE_RE.test(rest[i]) && rest[i].length <= 60) {
          date = rest[i];
          break;
        }
      }
    }
    var plain = rest.filter(function (line) {
      return line !== date && !DATE_RE.test(line) && !CONTRACT_RE.test(line) && line.length <= 120;
    });
    var company = clean(companyEl ? companyEl.textContent : "") || plain[0] || "";
    var location = clean(locationEl ? locationEl.textContent : "");
    if (!location) {
      var others = plain.filter(function (line) {
        return line !== company;
      });
      location = others[0] || "";
    }
    return {
      title: cap(title, 300),
      company: cap(company, 200),
      url: link.url,
      location: cap(location, 200) || null,
      date: cap(date, 60) || null,
    };
  }

  /**
   * Offers rendered in `root` (a Document or Element), one per offer id, in page order.
   * `baseUrl` resolves relative links (the page URL). At most 500.
   */
  function parseOffers(root, baseUrl) {
    var scope = root.body || root;
    var seen = {};
    var out = [];
    var anchors = scope.querySelectorAll("a[href]");
    for (var i = 0; i < anchors.length && out.length < MAX_OFFERS; i++) {
      var link = offerLink(anchors[i].getAttribute("href"), baseUrl);
      if (!link || seen[link.id]) continue;
      var card = cardOf(anchors[i], link.id, scope, baseUrl);
      var offer = parseCard(card, anchors[i], link);
      if (!offer) continue;
      seen[link.id] = true;
      out.push(offer);
    }
    return out;
  }

  globalThis.RadarJobteaserParser = {
    parseOffers: parseOffers,
    isListingUrl: isListingUrl,
    offerLink: offerLink,
    MAX_OFFERS: MAX_OFFERS,
  };
})();
