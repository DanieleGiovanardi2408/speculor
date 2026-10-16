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
| robots.txt | `robots_allowed(url)` is cached per host. `get_text` (HTML pages) checks it by default and raises `RobotsDisallowed`, a subclass of `SourceError`. A missing robots.txt (4xx) allows everything. A 401/403/5xx robots.txt, or one that can't be reached, disallows everything. JSON API calls (`get_json`) don't check it automatically; connectors check it explicitly where it applies (Workday `/wday/cxs/`, LinkedIn `/jobs-guest/`, and every Personio, Workable, Teamtailor, Recruitee and inRecruiting request). SmartRecruiters is disabled because of its robots.txt (see below). |
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

## Personio (`personio.py`)

- **Endpoint**: `GET https://<slug>.jobs.personio.de/xml` (or `.jobs.personio.com` with
  `region: com`), plus `?language=<lang>` when the entry sets `lang`. robots.txt is checked first.
- **Reads**: `<position>` id, name (title), office + additional offices (location), department,
  employmentType, seniority, schedule, createdAt (posted_at) and every `jobDescription`
  (heading + HTML body converted to text). The posting URL is `https://<host>/job/<id>`.
- **Exhaustive**: the feed is the complete list of published positions, so close-by-absence
  applies.
- **Single posting**: `<slug>.jobs.personio.(de|com)/job/<id>` is looked up in the cached feed.
- **Limits**: no deadline field. Many Personio career sites serve `robots.txt` with
  `Disallow: /`; those boards raise `RobotsDisallowed` and are skipped, never worked around.
  Feeds with a DTD / entity declarations are refused.

## Workable (`workable.py`)

- **Endpoint**: `GET https://apply.workable.com/api/v1/widget/accounts/<slug>?details=true`, the
  public endpoint behind Workable's embeddable jobs widget. robots.txt is checked first
  (`apply.workable.com` currently disallows nothing).
- **Reads**: shortcode (id), title, url, locations (city, country, ISO code), department,
  employment_type, experience, function, published_on (posted_at), and the HTML `description`.
- **Exhaustive**: the widget lists every published job of the account.
- **Single posting**: `apply.workable.com/<slug>/j/<shortcode>` is looked up in the cached board;
  short links without the account slug (`apply.workable.com/j/<code>`) give `None`.
- **Limits**: no deadline field. An unknown slug is a 404; an existing account with no open jobs
  answers 200 with an empty list, so the account `name` is worth checking when adding a slug.

## Teamtailor (`teamtailor.py`)

- **Endpoint**: `GET https://<slug>.teamtailor.com/jobs.rss`, or `https://<host>/jobs.rss` when the
  slug contains a dot (custom careers domain). robots.txt is checked first.
- **Reads**: item title, link, the numeric job id from the link (falls back to `guid`), pubDate
  (posted_at), the HTML description, `tt:locations` (city, country), `tt:department`, `tt:role`
  and `remoteStatus`.
- **Exhaustive**: the RSS feed lists every published job (checked against the career-site
  sitemap on two boards at setup).
- **Limits**: no deadline field.

## Recruitee (`recruitee.py`)

- **Endpoint**: `GET https://<slug>.recruitee.com/api/offers/` (or a custom host when the slug
  contains a dot), the public Careers Site API. robots.txt is checked first (the Recruitee host
  redirects it to the company's careers domain; redirects are followed).
- **Reads**: id, title, careers_url, location / country code, department, employment type,
  experience, published_at (posted_at), `close_at` (deadline), description + requirements (HTML).
  Offers whose `status` is not `published` are skipped.
- **Exhaustive**: yes, the endpoint lists every published offer.

## inRecruiting (`inrecruiting.py`)

- **Endpoint**: company career pages built on Zucchetti inRecruiting (intervieweb.it) load
  `GET https://<host>.intervieweb.it/annunci.php?lang=<l>&d=<domain>&k=<key>&format=json_en`, a
  JSON list of every published announcement for that site. `k` is the public site key printed in
  the careers page source (not a credential). robots.txt is checked first.
- **Config**: `ats: inrecruiting`, `slug: "<host>|<d>|<k>"`, optional `lang` (`it` by default).
  Find the three values in the careers page source (search for `annunci.php`).
- **Reads**: id, title, url, location/city, nation, function or team (department), contract
  type, seniority, `published` (Italian local time, stored in UTC), `expires` (deadline, local
  date) and the HTML description (falls back to the company/position/requirements sections).
- **Exhaustive**: yes, the list is what the company's own careers page shows.
- **Limits**: the shared `inrecruiting.intervieweb.it` host disallows `LAC` URLs in robots.txt;
  this feed doesn't use them. Note that Python's `urllib.robotparser` does plain prefix matching
  and ignores `*` wildcards in robots.txt rules.

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

## LinkedIn saved searches (`linkedin_jobs.py`)

Source `linkedin_jobs`. The user's own job searches, run with the user's own LinkedIn session
through linkedin-cli (mayai-linkedin-cli >= 0.3, `RADAR_LINKEDIN_BIN`) as a subprocess. Nothing
here talks to LinkedIn directly: the CLI's jitter, daily quotas and block detection apply as-is.

- **Enabled only when** the private `config/linkedin_searches.yaml` (gitignored; format in
  `config/linkedin_searches.example.yaml`) and the binary both exist, and never in CI.
- **Commands used**: `linkedin --json quota` (offline, read before and after every call),
  `linkedin --json jobs search --geo-id <id> --experience internship --experience entry
  --date-posted <window> --sort recent --limit 25 -- <keywords>`, `linkedin --json jobs get <id>`.
- **Budget**: at most `max_calls` (default and ceiling 25) calls a day that reach LinkedIn,
  measured on the CLI's own counter, never more than the CLI's `jobs` (30/day) and `api_total`
  quotas still allow. One search with a `geo_id` = one call (25 results). A location given only
  by `name` costs one extra lookup the first time (cached by the CLI). `max_details` calls are
  kept for `jobs get` on the newest postings whose description is not in the DB yet; a posting
  that turns out closed is dropped.
- **Once a day**: the `linkedin_jobs:state` setting stores `last_run` (local date), the rotation
  `cursor` and the call count. A second run on the same day returns nothing and calls nothing.
  `last_run` is written before the first call, so a crash can't cause a second run.
- **Rotation**: each (search, location) pair is a slot, ordered round-robin (every search's
  first location, then every second location, ...). Each day runs slots from the cursor until
  the next one would exceed the budget, and the next day continues from there. The cycle takes
  `ceil(slot cost / (max_calls - max_details))` days; `date_posted: auto` uses `24h` for a 1-day
  cycle, `week` up to 7 days and `month` beyond, so a full cycle never leaves a gap. With the
  default budget (22 search calls a day) up to 154 slots fit in a week.
- **Mapping**: `external_id` = LinkedIn job id, `url` = `https://www.linkedin.com/jobs/view/<id>/`,
  which canonicalizes to the same URL as LinkedIn email alerts, so the two de-duplicate.
  The `(Ibrido)` / `(Da remoto)` suffix of the location goes to `extra.workplace`; salary text,
  Easy Apply and the search id go to `extra`.
- **Not exhaustive** (`exhaustive = False`): a posting missing from a search is never closed.
- **Stops, never retries**: CLI exit 3 (checkpoint, captcha, HTTP 429 or 999) and exit 2 (not
  logged in) raise `SourcePaused("linkedin_jobs", reason, action)`; the Oggi screen shows the
  action ("Apri linkedin.com nel browser e completa la verifica..., poi `linkedin auth login` e
  `radar connectors resume linkedin_jobs`"). Exit 4 (CLI quota) ends the day quietly; two failed
  searches in a row end the day; a timeout is a `SourceError`.
- **geoIds**: the number after `geoId=` in the address bar when searching jobs for a place on
  linkedin.com (no API call). Italy is `103350119` (verified 2026-10-16).

## Email alerts (IMAP) (`email_alerts.py`, `email_parsers/`)

Optional source `email`. It reads job-alert emails from **one folder/label** (`radar`: a Gmail
label or an Outlook folder filled by a filter/rule), so LinkedIn and JobTeaser postings arrive
without anything being fetched from those sites. Setup steps: [RUNNING.md](RUNNING.md#email-alerts-imap).

- **Enable**: in the local `.env` only, set `RADAR_IMAP_USER` and the credentials of the auth mode:
  - `password` (Gmail; default for non-Microsoft addresses): a Google app password, from
    `RADAR_IMAP_PASSWORD` or the macOS keychain (`radar imap set-password`). `LOGIN` over TLS.
  - `oauth2` (default for outlook/hotmail/live/msn addresses; Outlook.com no longer accepts
    passwords since 16 Sep 2024): `RADAR_IMAP_OAUTH_CLIENT_ID` of the user's own Entra app
    (public client, tenant `consumers`), then `radar imap login` (MSAL device-code flow, scope
    `https://outlook.office.com/IMAP.AccessAsUser.All`). The MSAL token cache lives only in the
    keychain; each run refreshes the access token silently and sends `AUTHENTICATE XOAUTH2`.
  Optional: `RADAR_IMAP_AUTH` (`auto`), `RADAR_IMAP_HOST` (default `outlook.office365.com` /
  `imap.gmail.com` from the address), `RADAR_IMAP_FOLDER` (`radar`), `RADAR_IMAP_PORT` (993),
  `RADAR_IMAP_SINCE_DAYS` (14). It can be turned off with `email: {enabled: false}` in the
  companies config. There are deliberately no extra senders.
- **Access**: IMAP4 over SSL (default TLS context, 30 s timeout). Read-only is enforced twice:
  the connector only gets a `ReadOnlyClient` (no store/copy/move/expunge/append/delete/uid
  methods; `select` is always `EXAMINE`; `fetch` accepts only `BODY.PEEK[...]`), and the real
  connection is a `ReadOnlyIMAP4_SSL` whose command layer refuses anything outside
  `CAPABILITY NOOP LOGIN AUTHENTICATE EXAMINE SEARCH FETCH LOGOUT` before it is sent. Nothing is
  ever marked as read, flagged, moved or deleted.
- **Search**: `SEARCH SINCE <today − N days>` in the folder (newest first, at most 2000
  candidates), then only `BODY.PEEK[HEADER.FIELDS (FROM DATE)]` of the candidates, in batches.
  Messages whose From is not an allowlisted alert sender are never downloaded. Allowlist:
  `jobalerts-noreply@linkedin.com`, `jobs-listings@linkedin.com`, `jobs-noreply@linkedin.com`, other
  `@linkedin.com` senders whose display name says "Job Alerts" / "offerte di lavoro", and any
  address at `jobteaser.com` or a subdomain (JobTeaser does not document its alert sender). The
  sender check is local because server-side `FROM` search differs between Gmail and Exchange.
  At most 300 alert messages per run. Anything else (invitations, newsletters, personal mail) is
  not parsed, stored or sent to the LLM.
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
    `redirect`, ..., e.g. Outlook safelinks and Google redirects) or, for LinkedIn/JobTeaser
    links, in a percent- or base64-encoded path segment are unwrapped offline (a tracking link
    is never requested). LinkedIn `.../jobs/search/?currentJobId=<id>` links map to the same
    `/jobs/view/<id>` URL. Tracking params
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
- **Privacy**: credentials come only from the environment or the macOS keychain (the password is
  a `SecretStr`; authenticators and the connector never show secrets in their repr). Logs show
  counts only (alert messages, messages with postings, unparsable, postings), never subjects,
  senders, bodies or posting content. Connect/select/search/fetch failures raise a generic
  `SourceError` (e.g. `email: IMAP search failed`) with the server text and the exception chain
  suppressed. Auth failures pause the source (`SourcePaused`): *login IMAP rifiutato* (password
  mode, `radar imap set-password`) or *token scaduto* (OAuth2, `radar imap login`); both
  commands resume the source when they succeed.
- **Real samples**: the fixtures in `tests/fixtures/email/` are synthetic. `make email-samples`
  (local only, either auth mode) downloads the latest 3 LinkedIn and 3 JobTeaser alerts
  read-only, anonymizes them and writes them to the gitignored `private/email_samples/`. After a
  manual review, `make promote-email-sample FILE=...` re-runs the personal-data checks and copies
  one file to `tests/fixtures/email/` (it never commits).

## JobTeaser, Luiss career service (`jobteaser.py`, `jobteaser_browser.py`)

Optional source `jobteaser`. It runs **only on the user's Mac, never in CI or the cloud**, through
the user's own JobTeaser session kept in a dedicated Chromium profile.

- **Setup**: `make jobteaser-login` (installs the optional `browser` extra and Chromium, then opens
  a headed window on `https://luiss.jobteaser.com/it/users/sign_in`). The user logs in by hand
  and closes the window. The session stays in the profile directory `RADAR_JOBTEASER_PROFILE`
  (default `~/Library/Application Support/speculor/jobteaser-profile`, mode 700, outside the
  repo). Cookies are never read, exported or logged. The registry adds the connector, and
  `GET /api/settings` lists the source, only when that directory exists.
  `jobteaser: {enabled: false}` in the companies config turns it off.
  `make jobteaser-test` checks the session and parses one listing page and one offer page,
  printing counts only.
- **Config**: `config/jobteaser_searches.yaml` (gitignored; the committed
  `jobteaser_searches.example.yaml` is the fallback) lists the base URL, the reserved-offer
  listings ("Offerte mirate", "Offerte dei partner"), the search URL template and keywords,
  `max_pages` (3) and `max_details` (40 offer pages a day).
- **Politeness**: headless, one tab, one navigation at a time, a 3 to 6 s pause between pages.
  Every URL is checked against the host's robots.txt **with standard wildcard semantics**
  (`*`, `$`, longest match; the stdlib parser ignores wildcards) before it is opened.
  `luiss.jobteaser.com/robots.txt` allows offer pages (`/*/job-offers/<id>`) and the dashboard,
  but `Disallow: /*` covers the listing/search pages, so **listing URLs are currently skipped**
  (counted as `listing_robots_disallowed`) and the connector only opens offer pages: those
  known from JobTeaser alert emails or the seed, to add their description and closing date.
- **Session/block**: a redirect to `/users/sign_in` or to an SSO host raises
  `SourcePaused("jobteaser", "sessione scaduta", ...)`. A Cloudflare challenge, captcha, 403 or
  429 raises `SourcePaused("jobteaser", "captcha o blocco", ...)`. Nothing is solved or retried.
  The source stays paused until `radar connectors resume jobteaser`.
- **Parsing**: listing offers come from the JSON the web app loads (intercepted XHR/fetch
  responses from jobteaser hosts, `__NEXT_DATA__`), with the offer links in the DOM as fallback.
  Offer pages: JSON-LD `JobPosting` first, then JSON, then DOM selectors and a
  `Scadenza: <date>` line. 404/410 or an "offerta scaduta" marker means closed: the offer isn't
  returned and its id goes into `data/jobteaser_state.json` (ids only), so it isn't opened again.
- **Mapping**: `external_id` is the JobTeaser offer id. `url` is built exactly like the email
  parser builds it (`https://luiss.jobteaser.com/it/job-offers/<id>[-<slug>]`).
  `normalize.canonical_url` maps every JobTeaser offer URL (any host, locale or slug) to
  `https://www.jobteaser.com/en/job-offers/<id>`, so connector, alert and seed rows de-duplicate.
  Rows stored before that rule are matched by offer id too. `via` is "Career Service Luiss". As a
  source it ranks like `manual`, above its own alert. Only offers whose page was read in this run
  are returned, so a stored description is never overwritten with listing text.
- **Not exhaustive** (`exhaustive = False`): never closes postings by absence.
- **Calibration**: `uv run python scripts/jobteaser_probe.py` (after login) prints the structure
  of the logged-in pages: URL shapes, JSON key trees, DOM chains, `data-testid`s, filter-link
  query keys. It prints no content.

## Closed check (`closed.py`)

`check_open(client, url) -> True | False | None` checks a single posting. It never raises.

| URL | Method | Result |
|---|---|---|
| `job-boards(.eu).greenhouse.io/<slug>/jobs/<id>` | Greenhouse single-job API | 200 → open, 404 → closed |
| `jobs(.eu).lever.co/<slug>/<id>` | Lever single-posting API | 200 → open, 404 → closed |
| `jobs.ashbyhq.com/<slug>/<id>` | board API (cached) | listed → open, absent → closed |
| `jobs.smartrecruiters.com/<Company>/<id>-...` | Posting API detail | active → open, 404/inactive → closed |
| `<tenant>.<wdN>.myworkdayjobs.com/[<locale>/]<site>/job/...` | CXS job detail, **only if robots.txt allows** | 200 → open, 404/410 or `posted: false` → closed |
| `<slug>.jobs.personio.(de\|com)/job/<id>` | XML feed (cached), **only if robots.txt allows** | listed → open, absent → closed |
| `apply.workable.com/<slug>/j/<code>` | widget board (cached) | listed → open, absent → closed |
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

## Board coverage and `scripts/check_boards.py`

`uv run python scripts/check_boards.py [companies.yaml] [--ats X] [--sector Y] [--exclude OLD.yaml]`
probes every active board one at a time through `PoliteClient` and prints, per board, the ATS,
slug, ok/error/robots, the number of jobs on the board (title filter off; for Workday, the
search-term and country-scoped result) and how many pass the title filter, then totals per
sector and per ATS and the failures with their reason. It writes nothing to the database. Its
output names slugs, so it stays on the user's machine.

Snapshot of the user's (private) config after adding sector-tagged boards (October 2026): 194
active boards — Greenhouse 74, Ashby 47, Workable 27, Workday 18, Lever 15, Personio 7,
Teamtailor 4, Recruitee 1, inRecruiting 1. Every board answered at setup; about a third of the
new ones had no open job that day. Companies carry a free-text `sector`
(`fintech`, `vc`, `consulting`, `real_estate`, `ai`, `banking_insurance`, `big_tech`, ...),
stored on the company row by `sync_companies_config` (run by the pipeline and by
`radar sync-companies`).

While adding boards, every candidate slug was checked with one request per ATS. Discarded
candidates fell into: 404 (no such board), a 200 board that belonged to a different company
with the same slug (checked by reading titles and locations), Workday 422 (wrong site name),
and Personio sites whose robots.txt is `Disallow: /` (skipped, as for any disallowed board).

## Unresolved companies

Companies without a supported public board stay in the user's (gitignored) `companies.yaml`
with `ats: none` and an `unresolved` reason. Typical reasons:
SAP SuccessFactors, Avature, Oracle Recruiting Cloud, Factorial, a Personio site whose robots.txt
disallows everything, an inRecruiting page without a public feed, or a fully custom careers
portal. The list itself is private because it
reveals the user's target companies.
