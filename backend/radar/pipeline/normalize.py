"""Normalization helpers: countries, URLs, hashes, de-duplication keys."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Country names (Italian, English, local) and common cities -> ISO code.
_COUNTRY_ALIASES: dict[str, str] = {
    "italia": "IT",
    "italy": "IT",
    "paesi bassi": "NL",
    "netherlands": "NL",
    "the netherlands": "NL",
    "nederland": "NL",
    "francia": "FR",
    "france": "FR",
    "germania": "DE",
    "germany": "DE",
    "deutschland": "DE",
    "spagna": "ES",
    "spain": "ES",
    "españa": "ES",
    "espana": "ES",
    "portogallo": "PT",
    "portugal": "PT",
    "polonia": "PL",
    "poland": "PL",
    "polska": "PL",
    "belgio": "BE",
    "belgium": "BE",
    "belgique": "BE",
    "belgië": "BE",
    "lussemburgo": "LU",
    "luxembourg": "LU",
    "irlanda": "IE",
    "ireland": "IE",
    "svizzera": "CH",
    "switzerland": "CH",
    "schweiz": "CH",
    "suisse": "CH",
    "austria": "AT",
    "österreich": "AT",
    "osterreich": "AT",
    "danimarca": "DK",
    "denmark": "DK",
    "svezia": "SE",
    "sweden": "SE",
    "norvegia": "NO",
    "norway": "NO",
    "finlandia": "FI",
    "finland": "FI",
    "grecia": "GR",
    "greece": "GR",
    "repubblica ceca": "CZ",
    "czech republic": "CZ",
    "czechia": "CZ",
    "romania": "RO",
    "ungheria": "HU",
    "hungary": "HU",
    "estonia": "EE",
    "lettonia": "LV",
    "latvia": "LV",
    "lituania": "LT",
    "lithuania": "LT",
    "croazia": "HR",
    "croatia": "HR",
    "slovenia": "SI",
    "slovacchia": "SK",
    "slovakia": "SK",
    "bulgaria": "BG",
    "malta": "MT",
    "cipro": "CY",
    "cyprus": "CY",
    "islanda": "IS",
    "iceland": "IS",
    "liechtenstein": "LI",
    "regno unito": "GB",
    "united kingdom": "GB",
    "uk": "GB",
    "england": "GB",
    "great britain": "GB",
    "scotland": "GB",
    "stati uniti": "US",
    "united states": "US",
    "usa": "US",
    "us": "US",
    "canada": "CA",
    "india": "IN",
    "singapore": "SG",
    "japan": "JP",
    "china": "CN",
    "australia": "AU",
    "brazil": "BR",
    "mexico": "MX",
    "israel": "IL",
    "united arab emirates": "AE",
    "uae": "AE",
    "turkey": "TR",
    "serbia": "RS",
    "remoto": "REMOTE",
    "remote": "REMOTE",
}
_CITY_COUNTRY: dict[str, str] = {
    "roma": "IT",
    "rome": "IT",
    "milano": "IT",
    "milan": "IT",
    "torino": "IT",
    "turin": "IT",
    "bologna": "IT",
    "napoli": "IT",
    "naples": "IT",
    "firenze": "IT",
    "florence": "IT",
    "genova": "IT",
    "padova": "IT",
    "verona": "IT",
    "bari": "IT",
    "trento": "IT",
    "amsterdam": "NL",
    "rotterdam": "NL",
    "utrecht": "NL",
    "eindhoven": "NL",
    "the hague": "NL",
    "den haag": "NL",
    "delft": "NL",
    "paris": "FR",
    "parigi": "FR",
    "lyon": "FR",
    "marseille": "FR",
    "toulouse": "FR",
    "berlin": "DE",
    "berlino": "DE",
    "munich": "DE",
    "münchen": "DE",
    "monaco di baviera": "DE",
    "hamburg": "DE",
    "frankfurt": "DE",
    "cologne": "DE",
    "köln": "DE",
    "stuttgart": "DE",
    "madrid": "ES",
    "barcelona": "ES",
    "barcellona": "ES",
    "valencia": "ES",
    "lisbon": "PT",
    "lisbona": "PT",
    "lisboa": "PT",
    "porto": "PT",
    "warsaw": "PL",
    "varsavia": "PL",
    "krakow": "PL",
    "kraków": "PL",
    "brussels": "BE",
    "bruxelles": "BE",
    "antwerp": "BE",
    "dublin": "IE",
    "dublino": "IE",
    "zurich": "CH",
    "zürich": "CH",
    "zurigo": "CH",
    "geneva": "CH",
    "ginevra": "CH",
    "lausanne": "CH",
    "basel": "CH",
    "vienna": "AT",
    "wien": "AT",
    "copenhagen": "DK",
    "stockholm": "SE",
    "oslo": "NO",
    "helsinki": "FI",
    "prague": "CZ",
    "praga": "CZ",
    "budapest": "HU",
    "athens": "GR",
    "bucharest": "RO",
    "tallinn": "EE",
    "luxembourg city": "LU",
    "london": "GB",
    "londra": "GB",
    "manchester": "GB",
    "edinburgh": "GB",
    "cambridge": "GB",
    "new york": "US",
    "san francisco": "US",
    "seattle": "US",
    "boston": "US",
}

EU_EEA_CH = {
    "AT",
    "BE",
    "BG",
    "HR",
    "CY",
    "CZ",
    "DK",
    "EE",
    "FI",
    "FR",
    "DE",
    "GR",
    "HU",
    "IE",
    "IT",
    "LV",
    "LT",
    "LU",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SK",
    "SI",
    "ES",
    "SE",
    "IS",
    "LI",
    "NO",
    "CH",
}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().lower()


def country_code(*texts: str | None) -> str | None:
    """Best-effort ISO code from country/location strings. Multi-country -> first match."""
    for text in texts:
        if not text:
            continue
        t = _fold(text)
        if t.upper() in EU_EEA_CH | {"GB", "US"} and len(t) == 2:
            return t.upper()
        if t in _COUNTRY_ALIASES:
            return _COUNTRY_ALIASES[t]
        parts = [p.strip() for p in re.split(r"[,/;|()\-–]| o | or ", t) if p.strip()]
        for part in parts:
            if part in _COUNTRY_ALIASES:
                return _COUNTRY_ALIASES[part]
        for part in parts:
            if part in _CITY_COUNTRY:
                return _CITY_COUNTRY[part]
        for alias, code in _COUNTRY_ALIASES.items():
            if len(alias) > 3 and re.search(rf"\b{re.escape(alias)}\b", t):
                return code
        for city, code in _CITY_COUNTRY.items():
            if re.search(rf"\b{re.escape(city)}\b", t):
                return code
    return None


def zone_for(code: str | None) -> str:
    if code == "IT":
        return "Italia"
    if code in EU_EEA_CH or code == "GB":
        return "Europa"
    return "Altro"


_TRACKING_PARAMS = re.compile(r"^(utm_|gh_src|lever-|source|ref|trk|refId|trackingId|src$)", re.I)


def canonical_url(url: str) -> str:
    """Lowercase host, drop fragments, tracking params and trailing slashes."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.endswith("linkedin.com"):
        host = "www.linkedin.com"  # it.linkedin.com, de.linkedin.com -> same posting
        m = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)", parts.path)
        if m:
            return f"https://www.linkedin.com/jobs/view/{m.group(1)}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k)])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(title: str, company: str, location: str | None, raw_text: str | None) -> str:
    return sha256("\x1f".join([title.strip(), company.strip(), location or "", raw_text or ""]))


_COMPANY_SUFFIX = re.compile(
    r"\b(s\.?p\.?a\.?|s\.?r\.?l\.?|gmbh|b\.?v\.?|n\.?v\.?|ltd|limited|inc|llc|sas|sa|ag|plc|group|"
    r"gruppo|italia|italy)\b\.?",
    re.I,
)


def normalize_company(name: str) -> str:
    name = _fold(name)
    name = _COMPANY_SUFFIX.sub(" ", name)
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def normalize_title(title: str) -> str:
    t = _fold(title)
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)  # drop "(m/f/d)", "(H1 2027)" etc.
    t = re.sub(r"\b(m/f/d|m/w/d|f/m/x|h/f|m/f|all genders)\b", " ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def dedupe_key(company: str, title: str, country: str | None) -> str:
    return f"{normalize_company(company)}|{normalize_title(title)}|{country or ''}"
