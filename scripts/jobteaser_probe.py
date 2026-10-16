#!/usr/bin/env python3
"""LOCAL ONLY: dump the STRUCTURE of the logged-in JobTeaser pages to calibrate the connector.

Usage (after ``make jobteaser-login``)::

    uv run python scripts/jobteaser_probe.py [--listing URL ...] [--offer-id ID] [--headed]
        [--out data/jobteaser_probe.json]

It opens, one at a time with a pause of a few seconds: the dashboard, page 1 of every listing
in ``config/jobteaser_searches.yaml`` (or ``--listing`` URLs; each checked against robots.txt
first, disallowed ones are NOT opened) and one offer page (``--offer-id`` or the newest offer
known from alert emails). For each page it prints only structure, never content:

* page kind (ok / login / block), HTTP status, URL shape (ids masked, query KEYS only);
* intercepted JSON responses: URL shape, key tree (keys, types, list lengths), how many
  offer-like objects ``offers_from_json`` finds, the reported total;
* embedded JSON (``__NEXT_DATA__``, ``application/json``, JSON-LD ``@type`` values);
* DOM: offer-link count, the tag/class/data-testid chain around offer links, ``data-testid``
  frequencies, form field names, filter links (query keys and short identifier values, plus the
  link label only when it is a filter label such as "mirate"/"partner"/"riservat");
* what the connector parsers extract (counts / parse mode / description length only).

The output contains no titles, companies, descriptions, names, cookies or tokens. Still,
review it before sharing.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from selectolax.lexbor import LexborHTMLParser, LexborNode  # noqa: E402

from radar.connectors import jobteaser as jt  # noqa: E402

_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{3,}", re.I)
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.,:-]{1,40}$")
_FILTER_LABEL = re.compile(
    r"mirat|partner|riservat|school|scuola|target|exclusive|esclusiv|luiss|stage|internship|"
    r"tirocin|contratt|contract",
    re.I,
)


def url_shape(url: str) -> dict[str, Any]:
    parts = urlsplit(url)
    path = _ID.sub("<id>", parts.path)
    path = re.sub(r"<id>-[^/]+", "<id>-<slug>", path)
    return {
        "host": parts.netloc,
        "path": path,
        "query_keys": sorted({k for k, _ in parse_qsl(parts.query, keep_blank_values=True)}),
    }


def key_tree(data: Any, depth: int = 0, max_depth: int = 4) -> Any:
    """Keys and types only: {'key': 'str'}, lists as ['list[n]', <tree of first item>]."""
    if isinstance(data, dict):
        if depth >= max_depth:
            return f"dict[{len(data)}]"
        # keys can be ids (normalized stores keyed by offer id): masked, first one kept
        out: dict[str, Any] = {}
        for k, v in list(data.items())[:60]:
            out.setdefault(_ID.sub("<id>", str(k)), key_tree(v, depth + 1, max_depth))
        return out
    if isinstance(data, list):
        head = key_tree(data[0], depth + 1, max_depth) if data else None
        return [f"list[{len(data)}]", head] if head is not None else f"list[{len(data)}]"
    if data is None:
        return "null"
    return type(data).__name__


def _chain(node: LexborNode | None, levels: int = 4) -> list[str]:
    out: list[str] = []
    while node is not None and levels > 0 and node.tag not in ("body", "html"):
        attrs = node.attributes
        classes = ".".join(sorted((attrs.get("class") or "").split())[:4])
        testid = attrs.get("data-testid")
        out.append(
            node.tag
            + (f".{classes}" if classes else "")
            + (f"[data-testid={testid}]" if testid else "")
        )
        node = node.parent
        levels -= 1
    return out


def dom_structure(html: str, base_url: str) -> dict[str, Any]:
    tree = LexborHTMLParser(html)
    offer_links = [
        a for a in tree.css("a[href]") if jt.offer_href_key(a.attributes.get("href"), base_url)
    ]
    chains = Counter(" < ".join(_chain(a, 4)) for a in offer_links)
    testids = Counter(n.attributes.get("data-testid") or "" for n in tree.css("[data-testid]"))
    fields = sorted(
        {
            f"{n.tag}:{n.attributes.get('type') or ''}:{n.attributes.get('name') or ''}"
            for n in tree.css("input, select, textarea")
            if n.attributes.get("type") not in ("password", "hidden")
        }
    )
    filters: Counter[str] = Counter()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        parts = urlsplit(href)
        if "job-offers" not in parts.path or _ID.search(parts.path) or not parts.query:
            continue
        pairs = [
            f"{k}={v if _SAFE_VALUE.match(v) else '<value>'}"
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        ]
        label = re.sub(r"\s+", " ", a.text(deep=True)).strip()
        label = label[:40] if _FILTER_LABEL.search(label) else ""
        filters[f"{'&'.join(sorted(pairs))}  {label}".strip()] += 1
    scripts = Counter(
        f"{n.attributes.get('type') or 'js'}#{n.attributes.get('id') or ''}"
        for n in tree.css("script")
        if n.attributes.get("type") or n.attributes.get("id")
    )
    return {
        "offer_links": len(offer_links),
        "offer_link_chains": chains.most_common(5),
        "data_testids": testids.most_common(40),
        "form_fields": fields[:40],
        "filter_links": filters.most_common(40),
        "scripts": scripts.most_common(20),
        "h1_count": len(tree.css("h1")),
    }


def page_structure(page: jt.Page, kind: str, base_url: str) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind, "status": page.status, "url": url_shape(page.url)}
    if jt.is_login_page(page, base_url):
        out["page"] = "login"
        return out
    if jt.is_block_page(page):
        out["page"] = "block"
        return out
    out["page"] = "ok"
    out["json_responses"] = [
        {
            "url": url_shape(url),
            "offers_found": len(jt.offers_from_json(body)),
            "total": jt.json_total(body),
            "keys": key_tree(body),
        }
        for url, body in page.payloads
    ]
    embedded = jt.embedded_json(page.html)
    out["embedded_json"] = [
        {
            "ld_types": sorted({str(o.get("@type")) for o in jt.walk_json(b) if "@type" in o}),
            "offers_found": len(jt.offers_from_json(b)),
            "keys": key_tree(b, max_depth=5),
        }
        for b in embedded
    ]
    out["dom"] = dom_structure(page.html, base_url)
    offers, how = jt.parse_listing(page, base_url)
    out["listing_parse"] = {"how": how, "offers": len(offers)}
    if kind == "detail":
        d = jt.parse_detail(page)
        out["detail_parse"] = {
            "how": d["how"],
            "closed": d["closed"],
            "description_chars": len(d["description"]),
            "has_title": bool(d["title"]),
            "has_company": bool(d["company"]),
            "has_location": bool(d["location"]),
            "closing_date_parsed": jt.parse_human_date(d["closing"]) is not None,
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listing", action="append", default=[], help="listing URL (page 1)")
    parser.add_argument("--offer-id", default=None)
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args()

    from radar.config import get_settings
    from radar.connectors.http import PoliteClient
    from radar.connectors.jobteaser_browser import PlaywrightBrowser

    settings = get_settings()
    if not jt.profile_ready(settings):
        print("Browser profile missing: run `make jobteaser-login` first.")
        return 1
    cfg = jt.load_config()
    with PoliteClient.from_settings() as client:
        conn = jt.JobTeaserConnector(
            lambda: PlaywrightBrowser(jt.profile_dir(settings)), client, cfg
        )
        robots = conn.robots()
        targets: list[tuple[str, str]] = [("dashboard", f"{conn.base_url}/{conn.locale}")]
        listings = args.listing or [jt._page_url(t, 1) for _k, t in conn.listings]
        targets += [("listing", u) for u in dict.fromkeys(listings)]
        offer_id = args.offer_id
        if offer_id is None:
            try:
                _known, alert_ids = jt.db_offer_ids()
                offer_id = alert_ids[0] if alert_ids else None
            except Exception:
                offer_id = None
        if offer_id:
            targets.append(("detail", conn.offer_url(offer_id)))

        report: list[dict[str, Any]] = []
        browser = PlaywrightBrowser(jt.profile_dir(settings), headless=not args.headed)
        try:
            for i, (kind, url) in enumerate(targets):
                if not robots.allowed(url):
                    report.append(
                        {"kind": kind, "url": url_shape(url), "page": "robots-disallowed"}
                    )
                    continue
                if i:
                    time.sleep(random.uniform(3.0, 6.0))
                page = browser.visit(url)
                entry = page_structure(page, kind, conn.base_url)
                report.append(entry)
                if entry["page"] in ("login", "block"):
                    break  # stop at once: log in again / never fight a block page
        finally:
            browser.close()
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
