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
| Rate limit per host | At least 1 s between two requests to the same host (default). `linkedin.com`: 5 s. Override with `min_interval_per_host`. |
| Disk cache | Responses (200, plus 404/410 when a caller asks for them) are cached in `data/cache/http/` and keyed by URL + sorted params. The TTL is `RADAR_HTTP_CACHE_TTL_HOURS` (default 12 h). `use_cache=False` skips the cache. |
| Back-off | 429, 403, 999 and 5xx are retried up to 3 times. The client waits for `Retry-After` when present (seconds or HTTP date), otherwise 5 s, 10 s, 20 s. If `Retry-After` is above 120 s, or the retries run out, it raises `SourceError`. |
| robots.txt | `robots_allowed(url)` is cached per host. `get_text` (HTML pages) checks it by default and raises `RobotsDisallowed`, a subclass of `SourceError`. A missing robots.txt (4xx) allows everything. A 401/403/5xx robots.txt, or one that can't be reached, disallows everything. JSON API calls (`get_json`) don't check robots.txt (see the SmartRecruiters note). |
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
    followed by `User-agent: * / Disallow: /`. We use the documented public Posting API (JSON, meant
    for career-site integrations), not HTML pages, so the HTML-only robots rule doesn't block it.
    That's a judgement call. To stop using it, set the SmartRecruiters companies to
    `active: false`.
- **Failure**: a non-JSON response or unexpected shape raises `SourceError`.

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

## Closed check (`closed.py`)

`check_open(client, url) -> True | False | None` checks a single posting. It never raises.

| URL | Method | Result |
|---|---|---|
| `job-boards(.eu).greenhouse.io/<slug>/jobs/<id>` | Greenhouse single-job API | 200 → open, 404 → closed |
| `jobs(.eu).lever.co/<slug>/<id>` | Lever single-posting API | 200 → open, 404 → closed |
| `jobs.ashbyhq.com/<slug>/<id>` | board API (cached) | listed → open, absent → closed |
| `jobs.smartrecruiters.com/<Company>/<id>-...` | Posting API detail | active → open, 404/inactive → closed |
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
with `ats: none` and an `unresolved` reason. Typical reasons: Workday (connector postponed),
SAP SuccessFactors, Avature, Oracle Recruiting Cloud, intervieweb, Teamtailor, Factorial,
Workable, Personio, or a fully custom careers portal. The list itself is private because it
reveals the user's target companies.
