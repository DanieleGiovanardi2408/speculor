# Sources

Radar reads job postings only from public, documented endpoints, politely. This page describes
each connector: what it calls, what it reads, how it limits itself, and how it fails.

Code: `backend/radar/connectors/`. Board list: `config/companies.yaml` (gitignored; the format is
in `config/companies.example.yaml`).

## Collection rules (all sources)

Everything goes through `PoliteClient` (`connectors/http.py`):

| Rule | How |
|---|---|
| Honest identification | Every request sends `RADAR_USER_AGENT` (default `speculor-radar/0.1 (personal job-search tool; +<repo url>)`). No browser user agent, no cookies, no login, no proxies, no CAPTCHA solving. |
| Rate limit per host | At least 1 s between two requests to the same host (default). `linkedin.com`: 5 s. `*.myworkdayjobs.com`: 2 s. Override with `min_interval_per_host`. |
| Disk cache | Responses (200, plus 404/410 when a caller asks for them) are cached in `data/cache/http/` and keyed by URL + sorted params. The TTL is `RADAR_HTTP_CACHE_TTL_HOURS` (default 12 h). `use_cache=False` skips the cache. |
| Back-off | 429, 403, 999 and 5xx are retried up to 3 times. The client waits for `Retry-After` when present (seconds or HTTP date), otherwise 5 s, 10 s, 20 s. If `Retry-After` is above 120 s, or the retries run out, it raises `SourceError`. |
| robots.txt | `robots_allowed(url)` is cached per host. `get_text` (HTML pages) checks it by default and raises `RobotsDisallowed`, a subclass of `SourceError`. A missing robots.txt (4xx) allows everything. A 401/403/5xx robots.txt, or one that can't be reached, disallows everything. JSON API calls (`get_json`) don't check it automatically; connectors check it explicitly where it applies (Workday `/wday/cxs/`, LinkedIn `/jobs-guest/`). SmartRecruiters is disabled because of its robots.txt (see below). |
| Counts-only logs | Log lines never include company names or posting titles. `httpx` request logging (which shows URLs and slugs) is raised to WARNING. Error messages name only the host/source and the HTTP status. |

Connectors implement `fetch() -> list[RawPosting]` and raise `SourceError` on failure. The
pipeline records the error for that board and carries on with the others.

An optional `title_filter` (top-level in the companies config, overridable per company with a list
or `false`) is applied to the titles of every ATS connector. It does a case-insensitive word-prefix
match, so `intern` matches "Internship" but not "International".

## Greenhouse (`greenhouse.py`)

- **Endpoint**: `GET https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true`.
  Boards hosted on `job-boards.eu.greenhouse.io` use the **same** API host: there is no
  `boards-api.eu.greenhouse.io`, it doesn't resolve. `region: eu` in the config is informational
  only.
- **Reads**: id, title, `absolute_url`, location name, department, offices, `first_published`
  (posted_at), `application_deadline` (deadline, when set) and `content`. `content` is HTML-escaped
  HTML; it's unescaped, then converted to text.
- **Single posting**: `GET .../boards/<slug>/jobs/<id>`. A 404 means the posting is closed.
- **Limits**: country isn't given explicitly; the pipeline derives it from the location. Postings
  on custom domains (`?gh_jid=` on a company site) can't be mapped back to a slug for single-URL
  checks.
- **Failure**: an unknown slug returns 404, which raises `SourceError`.

## Lever (`lever.py`)

- **Endpoint**: `GET https://api.lever.co/v0/postings/<slug>?mode=json`, or
  `https://api.eu.lever.co/...` for boards on `jobs.eu.lever.co` (`region: eu`).
- **Reads**: id, `text` (title), `hostedUrl`, categories (location, department, team,
  commitment), `country` (ISO code), `createdAt` (ms epoch → posted_at), and description
  (`descriptionPlain` + `lists` + `additionalPlain`).
- **Single posting**: `GET .../postings/<slug>/<id>?mode=json`. A 404 means closed.
- **Limits**: no deadline field. robots.txt for both API hosts asks for `Crawl-delay: 1`, which the
  1 s interval respects.
- **Failure**: an unknown slug returns 404 (`SourceError`); a non-list body is also a `SourceError`.

## Ashby (`ashby.py`)

- **Endpoint**: `GET https://api.ashbyhq.com/posting-api/job-board/<slug>?includeCompensation=false`.
- **Reads**: id, title, `jobUrl`, location, secondary locations, `address.postalAddress.addressCountry`
  (country), department/team, employmentType, `publishedAt`, and `descriptionPlain` (falling back to
  `descriptionHtml` converted to text). Postings with `isListed: false` are skipped.
- **Single posting**: there's no public per-posting endpoint, so the cached board is read and
  searched for the id. Absent from the board means closed.
- **Limits**: no deadline field. Slugs can contain dots (for example `acme.ai`).
- **Failure**: an unknown slug returns 404 (`SourceError`).

## SmartRecruiters (`smartrecruiters.py`)

- **Endpoints**: list `GET https://api.smartrecruiters.com/v1/companies/<id>/postings?offset=&limit=100`
  (paginated until `totalFound`, at most 30 pages); detail `GET .../postings/<postingId>`.
- **Reads**: list: id, name, location (`country` is ISO and gets uppercased), releasedDate,
  typeOfEmployment, experienceLevel, function. Detail: `postingUrl`, `applyUrl` and the
  `jobAd.sections` (company description, job description, qualifications, additional information)
  converted to text.
- **Prefilter**: the description needs one request per posting, so details are fetched only for
  titles that pass the title filter (the config list, or a built-in default when none is set).
  There are at most `max_details` (60) detail requests per board per run. Postings beyond the cap
  are returned without a description (`extra.detail_fetched = false`). After a failed detail
  request the connector stops requesting details for that run.
- **Single posting**: `GET .../postings/<id>`. A 404, or `active: false`, means closed.
- **Limits / caveats**:
  - An unknown company id returns **200 with `totalFound: 0`**, not 404. Slugs were therefore
    checked by requiring at least one posting.
  - `api.smartrecruiters.com/robots.txt` is `User-agent: LinkedInBot / Allow: /v1/companies/`
    followed by `User-agent: * / Disallow: /`, so generic agents like Radar are disallowed.
- **Current status: disabled.** The connector and its tests are kept, but the user's
  SmartRecruiters companies are set to `active: false`, so nothing is fetched. Enabling them would
  be a deliberate choice against that robots.txt.
- **Failure**: a non-JSON response or unexpected shape raises `SourceError`.

## Workday (`workday.py`)

Best effort: the CXS endpoint is public but undocumented, so a tenant can change or block it at any
time; that board is then reported and skipped.

- **Endpoints**: the public CXS JSON API behind every `https://<tenant>.<wdN>.myworkdayjobs.com/<site>`
  board (the same calls the board's own page makes, no login):
  - search `POST https://<tenant>.<wdN>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs` with
    `{"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "<term>"}`. `limit` is at most
    20. The response has `total`, `jobPostings` (`title`, `externalPath`, `locationsText`,
    `postedOn`, `bulletFields`) and `facets`. `total` is only reliable on the first page.
  - detail `GET .../wday/cxs/<tenant>/<site>/job/<externalPath>`: `jobPostingInfo` with the HTML
    `jobDescription`, `startDate`, `endDate`, `timeType`, `jobReqId`, `country`,
    `jobRequisitionLocation.country.alpha2Code`, `additionalLocations`, `externalUrl`.
    404 means the posting was removed.
- **Config**: `ats: workday`, `slug: "<tenant>|<wdN>|<site>"` (from the board URL; a full board URL
  also works), or separate `tenant`, `host` (`wd3` or the full host) and `site` fields. Optional
  per entry: `search_terms` (list), `countries` (list of country names, or `europe`),
  `applied_facets` (raw `{facetParameter: [ids]}`), `max_details` (default 40).
- **Search instead of a full download**: boards hold hundreds to thousands of jobs, so the connector
  runs one search per term in `search_terms` (default `intern`, `internship`, `graduate`, `stage`,
  `tirocinio`, `junior`), paginates each one (at most 10 pages of 20), dedupes by `externalPath`,
  then applies the local `title_filter`. Workday's search also matches descriptions ("intern"
  returns "Internal Audit ..."), which is why the title filter still runs. Title-only filter words
  that aren't search terms (for example `analyst`) are only found when a search term also hits.
- **Country scoping**: results aren't relevance-sorted, so on global tenants a page cap alone would
  drop matches at random. `countries` makes the connector send one unfiltered request first, find
  the tenant's own country facet (its name differs per tenant: `locationCountry`,
  `Location_Country`, a custom `CF_..._Country_...`) and apply it to every search. Tenants with only
  city-level facets take explicit `applied_facets` (`locations` ids). If no country facet is found,
  the search stays global.
- **Reads**: list: title, externalPath (→ URL `https://<host>/<site><externalPath>` and
  `external_id = "<tenant>:<last path segment>"`), locationsText, `postedOn` ("Posted 5 Days Ago" →
  approximate posted_at; "30+ Days Ago" → none). Detail (for title-filtered postings only, at most
  `max_details` per board per run): description converted to text, ISO country code, all locations,
  `startDate` (posted_at), `endDate` (deadline), time type, req id. After a failed detail request
  the connector stops requesting details for that run.
- **Politeness**: at least **2 s** between requests to the same `*.myworkdayjobs.com` host (the
  connector raises the interval if the client's is lower), responses cached like every other
  source (POST cache key = URL + sorted JSON body), the same back-off on 429/403/5xx. Before the
  first request it checks `https://<host>/robots.txt` for the `/wday/cxs/...` path and raises
  `RobotsDisallowed` without sending anything if it's disallowed. Detail GETs also check robots.txt.
  Some tenants disallow their HTML board path (for example `Disallow: /<site>/`) but not
  `/wday/cxs/`; the connector only calls `/wday/cxs/`.
- **No session tricks**: no cookies, no CSRF token, no browser emulation. 401/422 (the endpoint
  wants a session or CSRF token) raise `SourceError` immediately, and 403 is retried with back-off,
  then reported. That board is skipped and the others go on.
- **Single posting / closed check**: a `*.myworkdayjobs.com/[<locale>/]<site>/job/...` URL becomes
  the detail call above. 200 with `posted` not false → open; 404/410 → closed; robots disallow →
  `None`.
- **Limits**: only `*.myworkdayjobs.com` boards (not `myworkdaysite.com` or custom domains that
  embed Workday). The POST helper lives in `workday.py` and reuses `PoliteClient`'s throttle, cache
  and counters, because `PoliteClient` only issues GETs.
- **Status (user's config)**: 8 boards configured (7 target companies; one company has two sites).
  All 8 answered the anonymous CXS endpoint at setup, and none needed a session or CSRF token or was
  disallowed by robots.txt.

## LinkedIn guest search (`linkedin_guest.py`)

- **Endpoints**: search `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=&location=&f_TPR=r604800&start=0`
  (HTML job cards); detail `https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/<id>`.
- **Would read**: job id (`data-entity-urn`), title, company, location, date (`<time datetime>`),
  URL, description (`.show-more-less-html__markup`), the criteria list, and closed markers
  ("No longer accepting applications").
- **Volume**: one page per configured query, `max_details` (default 5) detail requests per run,
  and 5 s between requests.
- **Current status: disabled by robots.txt.** `https://www.linkedin.com/robots.txt` allows a list
  of named crawlers (LinkedInBot, Googlebot, Bingbot, ...). Even those are disallowed from
  `/jobs-guest/`. The file ends with `User-agent: * / Disallow: /` and a note to apply to
  whitelist-crawl@linkedin.com. Our honest user agent matches `*`, so `fetch()` checks robots.txt
  first and raises
  `SourceError("linkedin: robots.txt disallows /jobs-guest/ for our user agent; not fetching")`
  without sending any search or detail request. We don't evade this, so there's no browser user
  agent and no alternative endpoint. The parser is kept and tested on synthetic fixtures in case
  access is ever granted.
- LinkedIn postings can still be added by hand (see Manual add); they are stored from the URL only.

## Email alerts (IMAP) (`email_alerts.py`, `email_parsers/`)

Optional source `email`. It reads job-alert emails from **one Gmail label** (`radar`),
so LinkedIn and JobTeaser postings arrive without anything being fetched from those sites.

- **Enable**: in the local `.env` only, set `RADAR_IMAP_HOST` (`imap.gmail.com`),
  `RADAR_IMAP_USER` and `RADAR_IMAP_PASSWORD` (a Gmail app password, never the main one), and
  optionally `RADAR_IMAP_FOLDER` (default `radar`, the Gmail label a filter files the alerts
  under).
  Optional: `RADAR_IMAP_PORT` (993) and `RADAR_IMAP_SINCE_DAYS` (14). The registry adds the
  connector only when host, user and password are all set. It can be turned off with
  `email: {enabled: false}` in the companies config. There are deliberately no extra senders.
- **Access**: IMAP4 over SSL (`imaplib.IMAP4_SSL`, default TLS context, 30 s timeout). Only the
  configured label is opened, read-only (`select(folder, readonly=True)`, i.e. `EXAMINE`), and
  messages are fetched with `BODY.PEEK[]`, so nothing is marked as read. Nothing is ever stored on
  the server, flagged, moved or deleted.
- **Search**: one `SEARCH SINCE <today − N days> FROM "<sender>"` per sender:
  `jobalerts-noreply@linkedin.com`, `jobs-listings@linkedin.com`, `jobs-noreply@linkedin.com`,
  `jobteaser.com`. Results are newest first, capped at 300 messages. Each message is then
  classified by its From header. Anything that isn't a LinkedIn alert or a JobTeaser digest
  (invitations, newsletters, personal mail) is ignored: it is not parsed, stored or sent to the
  LLM.
- **Parsing** (HTML with selectolax/lexbor, falling back to the text/plain part; hidden preheaders
  removed). Anchors are grouped by posting, and each group's card container gives the visible lines:
  - *LinkedIn alerts*: links `linkedin.com/comm/jobs/view/<id>/?trackingId=...` →
    `url = https://www.linkedin.com/jobs/view/<id>` (the canonical URL, so it de-duplicates against
    manual and LinkedIn postings). `external_id = linkedin:<id>`. Title comes from the link text;
    company and location come from the `Company · Location` line. `extra.via = "linkedin_alert"`.
  - *JobTeaser digests* (e.g. `<school>.jobteaser.com`): links `.../job-offers/<uuid or number>-<slug>`
    → the URL without its query, `external_id = jobteaser:<id>`. Title, company, contract
    (`employment_type`, e.g. Stage/Tirocinio/Internship), location and the `Inizio: ...` line
    (`start_info`). `extra.via = "jobteaser_alert"`.
  - Redirect wrappers whose target sits in a query parameter (`url`, `q`, `u`, `target`,
    `redirect`, ..., e.g. Outlook safelinks and Google redirects) are unwrapped. Tracking params
    (`utm_*`, `trackingId`, `refId`, `trk*`, `midToken`, `otpToken`, `lipi`, `eid`, ...) are
    dropped. Opaque click trackers (an encrypted target, e.g. SendGrid `upn=`) can't be unwrapped,
    so those links are skipped.
  - `raw_text` is the card snippet only (title, company, location, badges). `extra.alert_date` is
    the email's Date header.
- **De-duplication**: inside one run, the same posting in several emails (by canonical URL) is
  returned once, and the newest email wins. Across sources, alert postings dedupe against the
  other sources by canonical URL (and dedupe key) as usual.
- **Not exhaustive**: an alert lists only some postings, so the connector sets
  `exhaustive = False` and `pipeline/closed.py` never closes `email` postings by absence.
- **Privacy**: credentials come only from the environment (the password is a `SecretStr` and isn't
  shown in the connector's repr). Logs show counts only (messages, messages with postings,
  unparsable, postings), never subjects, senders, bodies or posting content. Every failure (connect, login,
  select, search, fetch) raises a generic `SourceError` (e.g. `email: IMAP login failed`) with the
  server text and the exception chain suppressed.
- **Real samples**: the fixtures in `tests/fixtures/email/` are synthetic. Once IMAP is set up,
  `uv run python scripts/fetch_alert_samples.py` (local only) downloads the latest 3 LinkedIn and 3
  JobTeaser alerts read-only, anonymizes them and writes `tests/fixtures/email/real_*.eml`.
  Review those files by hand and run `scripts/check_private.py` on them before committing.

## Closed check (`closed.py`)

`check_open(client, url) -> True | False | None` checks a single posting. It never raises.

| URL | Method | Result |
|---|---|---|
| `job-boards(.eu).greenhouse.io/<slug>/jobs/<id>` | Greenhouse single-job API | 200 → open, 404 → closed |
| `jobs(.eu).lever.co/<slug>/<id>` | Lever single-posting API | 200 → open, 404 → closed |
| `jobs.ashbyhq.com/<slug>/<id>` | board API (cached) | listed → open, absent → closed |
| `jobs.smartrecruiters.com/<Company>/<id>-...` | Posting API detail | active → open, 404/inactive → closed |
| `<tenant>.<wdN>.myworkdayjobs.com/[<locale>/]<site>/job/...` | CXS job detail, **only if robots.txt allows** | 200 → open, 404/410 or `posted: false` → closed |
| `*.linkedin.com/jobs/view/...<id>` | guest jobPosting, **only if robots.txt allows** | currently always `None` |
| `jobteaser.com` and other login-walled sites | not fetched | `None` |
| anything else, or any error | — | `None` |

## Manual add (`pipeline/manual.py`)

`add_posting_from_url(session, url)` works in this order:

1. If a posting with the same canonical URL already exists, it is returned.
2. Supported ATS URLs are read through the APIs above.
3. LinkedIn and login-walled URLs are not fetched. A stub is built from the URL slug (title and
   company when the URL has the `...-at-<company>-<id>` form), with `status="unknown"`.
4. Any other URL: one GET, only if robots.txt allows it. Fields come from a schema.org
   `JobPosting` JSON-LD block if present (title, hiringOrganization, jobLocation/addressCountry,
   description, datePosted, validThrough → deadline). Otherwise from `og:title`/`<title>`,
   `og:site_name` and the visible text of `<main>`/`<article>`/`<body>`.
5. If the fetch fails or robots.txt disallows it, the result is a URL-only stub with
   `status="unknown"`.

The posting is stored with `source="manual"` and `uid="manual:<sha256(canonical_url)[:16]>"`;
`country_code`, `zone`, `content_hash` and `dedupe_key` are computed with
`radar.pipeline.normalize`.

## Unresolved companies

Companies without a supported public board stay in the user's (gitignored) `companies.yaml`
with `ats: none` and an `unresolved` reason. Typical reasons:
SAP SuccessFactors, Avature, Oracle Recruiting Cloud, intervieweb, Teamtailor, Factorial,
Workable, Personio, or a fully custom careers portal. The list itself is private because it
reveals the user's target companies.
