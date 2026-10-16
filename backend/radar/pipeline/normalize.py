"""Normalization helpers: countries, URLs, hashes, de-duplication keys."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
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

# Non-European cities/regions seen on ATS boards (so they are rejected, not "unknown").
_CITY_COUNTRY.update(
    {
        "washington": "US",
        "mountain view": "US",
        "palo alto": "US",
        "menlo park": "US",
        "chicago": "US",
        "austin": "US",
        "los angeles": "US",
        "denver": "US",
        "atlanta": "US",
        "miami": "US",
        "dallas": "US",
        "houston": "US",
        "sunnyvale": "US",
        "san jose": "US",
        "new jersey": "US",
        "california": "US",
        "texas": "US",
        "illinois": "US",
        "massachusetts": "US",
        "connecticut": "US",
        "greenwich": "US",
        "stamford": "US",
        "toronto": "CA",
        "montreal": "CA",
        "montréal": "CA",
        "vancouver": "CA",
        "hong kong": "HK",
        "seoul": "KR",
        "south korea": "KR",
        "korea": "KR",
        "tokyo": "JP",
        "shanghai": "CN",
        "beijing": "CN",
        "shenzhen": "CN",
        "taipei": "TW",
        "taiwan": "TW",
        "sydney": "AU",
        "melbourne": "AU",
        "mumbai": "IN",
        "bangalore": "IN",
        "bengaluru": "IN",
        "hyderabad": "IN",
        "pune": "IN",
        "gurgaon": "IN",
        "gurugram": "IN",
        "new delhi": "IN",
        "chennai": "IN",
        "doha": "QA",
        "qatar": "QA",
        "dubai": "AE",
        "abu dhabi": "AE",
        "riyadh": "SA",
        "saudi arabia": "SA",
        "tel aviv": "IL",
        "são paulo": "BR",
        "sao paulo": "BR",
        "mexico city": "MX",
        "buenos aires": "AR",
        "bogotá": "CO",
        "bogota": "CO",
        "jakarta": "ID",
        "manila": "PH",
        "kuala lumpur": "MY",
        "bangkok": "TH",
        "ho chi minh": "VN",
        "cairo": "EG",
        "lagos": "NG",
        "nairobi": "KE",
        "johannesburg": "ZA",
        "cape town": "ZA",
        "auckland": "NZ",
        "istanbul": "TR",
        "kuwait": "KW",
        "santiago": "CL",
        "chile": "CL",
        "west palm beach": "US",
        "singapore": "SG",
        "nantes": "FR",
        "strasbourg": "FR",
        "niort": "FR",
        "lille": "FR",
        "bordeaux": "FR",
        "nice": "FR",
        "montpellier": "FR",
        "zug": "CH",
        "eindhoven": "NL",
    }
)

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
    """Best-effort ISO code from country/location strings.

    Multi-location strings ("London, New York, Singapore") resolve to the first European
    location if there is one (EU/EEA/CH first, then GB), else the first match.
    """
    for text in texts:
        if not text:
            continue
        t = _fold(text)
        if len(t) == 2 and t.upper() in EU_EEA_CH | {"GB", "US"}:
            return t.upper()
        if t in _COUNTRY_ALIASES:
            return _COUNTRY_ALIASES[t]
        found = _codes_in(t)
        if found:
            for code in found:
                if code in EU_EEA_CH:
                    return code
            return "GB" if "GB" in found else found[0]
    return None


def _codes_in(t: str) -> list[str]:
    """All country codes mentioned in a location string, in order of appearance."""
    parts = [p.strip() for p in re.split(r"[,/;|()\-–]| o | or | and | e ", t) if p.strip()]
    codes: list[str] = []
    for part in parts:
        code = _COUNTRY_ALIASES.get(part) or _CITY_COUNTRY.get(part)
        if code is None:
            for alias, c in (*_COUNTRY_ALIASES.items(), *_CITY_COUNTRY.items()):
                if len(alias) > 3 and re.search(rf"\b{re.escape(alias)}\b", part):
                    code = c
                    break
        if code is not None and code not in codes:
            codes.append(code)
    return codes


def zone_for(code: str | None) -> str:
    if code == "IT":
        return "Italia"
    if code in EU_EEA_CH or code == "GB":
        return "Europa"
    return "Altro"


_TRACKING_PARAMS = re.compile(
    r"^(utm_|gh_src$|lever-|source|ref|trk|tracking|src$|fbclid$|gclid$|dclid$|msclkid$|"
    r"mc_[ce]id$|_hs|mkt_tok$|lipi$|midtoken$|midsig$|ebp$|recommendedflavor$|"
    r"originalsubdomain$|trkemail$|iis$|iisn$|cmp$|jobteaser_source$|jt_source$|"
    r"language$|lang$|locale$|hl$)",
    re.I,
)
_LI_VIEW = re.compile(r"/jobs/view/(?:[^/]*?-)?(\d+)(?:[/?#]|$)")
_LI_GUEST = re.compile(r"/jobs-guest/jobs/api/jobposting/(\d+)", re.I)
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_JT_OFFER = re.compile(rf"/job-offers/({_UUID}|\d+)", re.I)
_GH_JOB = re.compile(r"/jobs/(\d+)")
_SLUG_UUID = re.compile(rf"^/([^/]+)/({_UUID})", re.I)
_LOCALE_SEG = re.compile(
    r"^(?:en|it|fr|de|es|nl|pt|pl|sv|da|no|nb|fi|cs|ro|hu|el|tr|ja|zh|ko|ru)(?:[-_][a-z]{2})?$",
    re.I,
)
# Hosts whose first path segment is a board slug, never a locale.
_SLUG_HOSTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "smartrecruiters.com",
    "personio.de",
    "personio.com",
)


def _clean_query(query: str) -> str:
    pairs = [(k, v) for k, v in parse_qsl(query) if not _TRACKING_PARAMS.match(k)]
    return urlencode(sorted(pairs))


def tidy_url(url: str) -> str:
    """A clean, still clickable URL: lowercase host, no fragment, tracking params or trailing
    slash; LinkedIn job links become ``https://www.linkedin.com/jobs/view/<id>``."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.endswith("linkedin.com"):
        job = _linkedin_job_id(parts.path, parts.query)
        if job:
            return f"https://www.linkedin.com/jobs/view/{job}"
        host = "www.linkedin.com"  # it.linkedin.com, de.linkedin.com -> same posting
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    return urlunsplit(("https", host, path, _clean_query(parts.query), ""))


def _linkedin_job_id(path: str, query: str) -> str | None:
    m = _LI_VIEW.search(path) or _LI_GUEST.search(path)
    if m:
        return m.group(1)
    for k, v in parse_qsl(query):
        if k.lower() == "currentjobid" and v.isdigit():
            return v
    return None


def canonical_url(url: str) -> str:
    """Matching key for "same posting" across sources (stored in ``Posting.canonical_url``).

    Beyond :func:`tidy_url`: LinkedIn alert/search links (``/comm/jobs/view/<id>``,
    ``?currentJobId=<id>``) and JobTeaser offers on any career-center host or locale collapse
    to one key per job id; Greenhouse jobs (board, EU board, ``?gh_jid=`` career pages) key on the
    global job id; Lever/Ashby drop ``/apply``; locale path prefixes (``/en/``, ``/it-it/``,
    Workday ``/en-US/``) and ``www.`` are dropped.
    """
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().rsplit("@", 1)[-1].removesuffix(":443").removesuffix(":80")
    path = re.sub(r"/{2,}", "/", parts.path)
    params = {k.lower(): v for k, v in parse_qsl(parts.query)}
    if host.endswith("linkedin.com"):
        job = _linkedin_job_id(path, parts.query)
        if job:
            return f"https://www.linkedin.com/jobs/view/{job}"
        host = "www.linkedin.com"
    elif host.endswith("jobteaser.com"):
        m = _JT_OFFER.search(path)
        if m:
            return f"https://www.jobteaser.com/job-offers/{m.group(1).lower()}"
    gh_id = params.get("gh_jid") or ""
    if host.endswith("greenhouse.io"):
        m = _GH_JOB.search(path)
        gh_id = m.group(1) if m else params.get("token", "") if "embed" in path else gh_id
    if gh_id.isdigit():
        return f"https://boards.greenhouse.io/jobs/{gh_id}"
    if host.endswith("lever.co") or host == "jobs.ashbyhq.com":
        m = _SLUG_UUID.match(path)
        if m:
            base = "jobs.lever.co" if host.endswith("lever.co") else host
            return f"https://{base}/{m.group(1).lower()}/{m.group(2).lower()}"
    host = host.removeprefix("www.")
    segs = [s for s in path.split("/") if s]
    if len(segs) > 1 and _LOCALE_SEG.match(segs[0]) and not host.endswith(_SLUG_HOSTS):
        segs = segs[1:]
    return urlunsplit(("https", host, "/" + "/".join(segs), _clean_query(parts.query), ""))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(title: str, company: str, location: str | None, raw_text: str | None) -> str:
    return sha256("\x1f".join([title.strip(), company.strip(), location or "", raw_text or ""]))


def ascii_fold(text: str) -> str:
    """Lowercase, accents removed ("Société Générale" -> "societe generale")."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip().lower()


# Legal forms and filler words dropped from company names (after dots are removed).
_COMPANY_DROP = frozenset(
    [
        "spa",
        "srl",
        "sas",
        "sapa",
        "snc",
        "sarl",
        "sa",
        "se",
        "ag",
        "kg",
        "kgaa",
        "gmbh",
        "mbh",
        "co",
        "bv",
        "nv",
        "ltd",
        "limited",
        "inc",
        "incorporated",
        "llc",
        "llp",
        "plc",
        "corp",
        "corporation",
        "company",
        "oy",
        "oyj",
        "ab",
        "asa",
        "as",
        "aps",
        "sl",
        "slu",
        "pte",
        "pty",
        "sprl",
        "bvba",
        "holding",
        "holdings",
        "group",
        "gruppo",
        "italia",
        "italy",
        "the",
        "and",
    ]
)
# Known alternative names -> one key (keys are compact normalized names, see company_key).
COMPANY_ALIASES: dict[str, str] = {
    "bostonconsulting": "bcg",
    "pricewaterhousecoopers": "pwc",
    "ernstyoung": "ey",
}


def normalize_company(name: str) -> str:
    """ "Nebbia Labs S.p.A." -> "nebbia labs": accents, case, legal suffixes, punctuation."""
    t = ascii_fold(name).replace("&", " and ").replace(".", "")
    tokens = re.sub(r"[^a-z0-9]+", " ", t).split()
    kept = [w for w in tokens if w not in _COMPANY_DROP]
    return " ".join(kept or tokens)


@lru_cache(maxsize=8192)
def company_key(name: str) -> str:
    """Compact company key: spaces removed, aliases resolved ("Nebbia-Labs" == "NebbiaLabs")."""
    compact = normalize_company(name).replace(" ", "")
    return COMPANY_ALIASES.get(compact, compact)


_GENDER = re.compile(
    r"(?<![a-z])(?:[mfwdhx]\s*/\s*){1,3}[mfwdhx](?![a-z])|\ball genders?\b|\bgn\b|\*in\b"
)
_DURATION = re.compile(
    r"\b\d{1,2}(?:\s*(?:-|to|a|–)\s*\d{1,2})?\s*-?\s*"
    r"(?:months?|mesi|mese|mois|monate?|maanden|meses|weeks?|settimane)\b"
)
_SEASON = (
    r"summer|spring|autumn|fall|winter|estate|primavera|autunno|inverno|ete|printemps|automne|"
    r"hiver|sommer|herbst|fruhling|fruehjahr|h[12]|q[1-4]|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|gennaio|febbraio|marzo|aprile|"
    r"maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"
)
_SEASON_YEAR = re.compile(rf"\b(?:{_SEASON})\s*(?:of\s*)?'?(?:20)?\d{{2}}\b")
_YEAR = re.compile(r"\b20\d\d(?:\s*/\s*(?:20)?\d\d)?\b")
_WORK_MODE = re.compile(r"\b(?:remote|hybrid|on-?site|in office|da remoto|ibrido)\b")
_TITLE_SYNONYMS = {
    "internship": "intern",
    "internships": "intern",
    "interns": "intern",
    "stage": "intern",
    "stagiaire": "intern",
    "stagista": "intern",
    "tirocinio": "intern",
    "tirocinante": "intern",
    "praktikum": "intern",
    "praktikant": "intern",
    "praktikantin": "intern",
    "pasantia": "intern",
    "practicas": "intern",
    "jr": "junior",
    "sr": "senior",
}
_TITLE_STOP = frozenset(
    [
        "and",
        "of",
        "the",
        "for",
        "in",
        "a",
        "an",
        "at",
        "to",
        "with",
        "di",
        "de",
        "del",
        "della",
        "la",
        "le",
        "en",
        "et",
        "und",
        "im",
        "per",
        "e",
        "y",
    ]
)
_PLACE_RE: re.Pattern[str] | None = None


def _place_re() -> re.Pattern[str]:
    """City and country names (folded), dropped from titles ("Data Intern - Milan")."""
    global _PLACE_RE
    if _PLACE_RE is None:
        names = {ascii_fold(n) for n in (*_CITY_COUNTRY, *_COUNTRY_ALIASES) if len(n) > 3}
        alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        _PLACE_RE = re.compile(rf"\b(?:{alt})\b")
    return _PLACE_RE


def title_tokens(title: str) -> list[str]:
    """Normalized title words: gender markers, durations, seasons/years, places, work mode,
    bracketed tags and punctuation removed; internship synonyms folded to "intern"."""
    return list(_title_tokens(title))


@lru_cache(maxsize=8192)
def _title_tokens(title: str) -> tuple[str, ...]:
    t = ascii_fold(title)
    t = _GENDER.sub(" ", t)
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)  # drop "(m/f/d)", "(H1 2027)" etc.
    t = _DURATION.sub(" ", t)
    t = _SEASON_YEAR.sub(" ", t)
    t = _YEAR.sub(" ", t)
    t = _WORK_MODE.sub(" ", t)
    t = _place_re().sub(" ", t)
    out: list[str] = []
    for w in re.sub(r"[^a-z0-9]+", " ", t).split():
        w = _TITLE_SYNONYMS.get(w, w)
        if w in _TITLE_STOP:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return tuple(out)


def normalize_title(title: str) -> str:
    return " ".join(title_tokens(title))


# Words that make two otherwise similar titles different jobs.
_TITLE_GUARD = frozenset(
    [
        "intern",
        "junior",
        "senior",
        "lead",
        "principal",
        "staff",
        "head",
        "manager",
        "director",
        "graduate",
        "trainee",
        "phd",
        "thesis",
        "werkstudent",
        "apprentice",
        "associate",
        "vp",
        "chief",
        "part",
        "i",
        "ii",
        "iii",
        "iv",
        "1",
        "2",
        "3",
    ]
)


def title_similarity(a: str, b: str) -> float:
    """Token-set similarity of two titles in [0, 1] (0 when seniority/level words differ)."""
    ta, tb = set(title_tokens(a)), set(title_tokens(b))
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    if (ta & _TITLE_GUARD) != (tb & _TITLE_GUARD):
        return 0.0
    return SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()


_CITY_ALIASES = {
    "milano": "milan",
    "roma": "rome",
    "torino": "turin",
    "napoli": "naples",
    "firenze": "florence",
    "genova": "genoa",
    "munchen": "munich",
    "monaco di baviera": "munich",
    "koln": "cologne",
    "wien": "vienna",
    "zurigo": "zurich",
    "ginevra": "geneva",
    "lisboa": "lisbon",
    "lisbona": "lisbon",
    "varsavia": "warsaw",
    "bruxelles": "brussels",
    "dublino": "dublin",
    "praga": "prague",
    "londra": "london",
    "parigi": "paris",
    "berlino": "berlin",
    "barcellona": "barcelona",
    "den haag": "the hague",
}
_CITY_RE: re.Pattern[str] | None = None


def cities(location: str | None) -> set[str]:
    """Known city names in a location string, canonicalized ("Milano" == "Milan")."""
    global _CITY_RE
    if not location:
        return set()
    if _CITY_RE is None:
        names = {ascii_fold(n) for n in _CITY_COUNTRY} | set(_CITY_ALIASES)
        alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        _CITY_RE = re.compile(rf"\b(?:{alt})\b")
    found = {_CITY_ALIASES.get(m, m) for m in _CITY_RE.findall(ascii_fold(location))}
    return found


def dedupe_key(company: str, title: str, country: str | None) -> str:
    return f"{company_key(company)}|{normalize_title(title)}|{country or ''}"
