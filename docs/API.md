# Radar HTTP API (contract)

Base path `/api`. JSON everywhere. Dates are ISO strings (`YYYY-MM-DD`), timestamps ISO 8601 UTC.
Local mode has no auth. When `RADAR_AUTH_REQUIRED=true`, every route except `/api/health` needs
`Authorization: Bearer <Supabase access token>` whose email equals `RADAR_ALLOWED_EMAIL`.

## Types

```ts
type Priority = "Alta" | "Media" | "Bassa";
type FilterState = "pass" | "flagged" | "rejected" | "overridden";
type PostingStatus = "open" | "closed" | "unknown";
type UserStatus = "todo" | "sent" | "skipped";
type DraftStatus = "draft" | "approved" | "sent" | "skipped" | "failed";
type DraftKind = "linkedin_connection_note" | "linkedin_message" | "followup" | "email";
type Stage = "Inviata" | "Screening HR" | "Test/case" | "Colloquio 1" | "Colloquio 2+"
  | "Offerta" | "Rifiutata" | "Nessuna risposta" | "Ritirata";

interface FilterHit { rule_id: string; action: "reject" | "flag"; reason: string;
  evidence: string | null; overridden: boolean }

interface ScoreOut { priority: Priority; why_fits: string; what_doesnt_fit: string;
  start_window: string | null; language_requirements: string[]; warnings: string[];
  confidence: number | null; provider: string; model: string; created_at: string }

interface PostingSummary {
  id: number; source: string; url: string; title: string; company_name: string;
  location: string | null; country_code: string | null; zone: string | null;
  type: string | null; area: string | null; deadline: string | null; start_info: string | null;
  status: PostingStatus; user_status: UserStatus; filter_state: FilterState;
  first_seen: string; last_seen: string; note: string | null;
  priority: Priority | null;            // from latest score, null if unscored
  score: ScoreOut | null;
  filter_hits: FilterHit[];             // flags and rejections
  has_pack: boolean;
  track: Track;                         // computed from filter_hits, see "Tracks" below
  skip_reason: string | null;           // parsed from the "[Saltato: reason]" note prefix
}
type Track = "internship" | "post_graduation";
interface RunDetail extends RunSummary { report_md: string | null }  // Markdown, private
interface UsageRow { day: string; provider: string; model: string; purpose: string;
  requests: number; input_tokens: number; output_tokens: number }
interface UsageTotal { provider: string; model: string; requests: number;
  input_tokens: number; output_tokens: number }
interface Usage { since: string; until: string; rows: UsageRow[];  // by local day/model/purpose
  today: UsageTotal[]; period: UsageTotal[] }                     // totals by model
interface ChangeEventOut { kind: "new" | "changed" | "closed" | "reopened";
  changes: Record<string, [unknown, unknown]> | null; at: string; run_id: number | null }
interface PostingDetail extends PostingSummary { raw_text: string | null; via: string | null;
  history: ChangeEventOut[]; pack_id: number | null }

interface Contact { id: number; posting_id: number | null; name: string; role: string | null;
  profile_url: string | null; why: string | null }
interface Draft { id: number; posting_id: number | null; pack_id: number | null;
  contact_id: number | null; kind: DraftKind; channel: "linkedin" | "email" | "other";
  recipient: string | null; text: string; status: DraftStatus; style_issues: string[];
  verification: Verification | null; verification_stale: boolean;
  send_result: string | null; created_at: string; updated_at: string; sent_at: string | null;
  posting_title: string | null; company_name: string | null; contact_name: string | null }
interface FormAnswer { question: string; answer: string }
interface Pack { id: number; posting_id: number; cover_letter: string; form_answers: FormAnswer[];
  cv_version: string; apply_link: string; language: string; style_issues: string[];
  verification: Verification | null; verification_stale: boolean;  // stale: a text changed
  provider: string; model: string; updated_at: string;
  posting: PostingSummary; contacts: Contact[]; drafts: Draft[] }
// Fact check against config/facts.yaml (radar/llm/verify.py). Texts are never rewritten.
interface VerificationClaim { text_id: string;  // cover_letter | form_answers.<n> | draft kind
  claim: string; fact_id: string | null; verdict: "supported" | "unsupported" | "check";
  source: "llm" | "precheck" | "llm+precheck"; reason: string | null }
interface Verification { version: number; status: "ok" | "precheck_only"; verified_at: string;
  provider: string | null; model: string | null; error: string | null;  // disabled | rate_limit | ...
  facts_source: string; facts_version: string | null; texts_hash: string;
  counts: { supported: number; unsupported: number; check: number; total: number };
  claims: VerificationClaim[]; drafter_claims: { claim: string; fact_ids: string[] }[] }

interface Application { id: number; posting_id: number | null; company: string; role: string;
  city: string | null; date_sent: string; channel: string; cv_version: string; stage: Stage;
  next_step: string | null; next_step_date: string | null; notes: string | null;
  responded: boolean; updated_at: string; followup_due: boolean }
interface StatsRow { key: string; sent: number; responses: number; interviews: number;
  interview_rate: number }
interface AppStats { by_channel: StatsRow[]; by_cv: StatsRow[]; by_stage: Record<Stage, number> }

interface RunSummary { id: number; started_at: string; finished_at: string | null;
  status: "running" | "ok" | "partial" | "failed"; stats: Record<string, unknown> | null }
interface Today { weekly_goal: number; applications_this_week: number;
  new_since_yesterday: PostingSummary[];  // internship track only
  new_post_graduation: number;            // new postings on the post_graduation track (count)
  closed_since_yesterday: PostingSummary[];
  upcoming_deadlines: PostingSummary[];   // internship track only
  followups_due: Application[];
  pending_approvals: number; last_run: RunSummary | null }
interface Company { id: number; name: string; sector: string | null; city: string | null;
  careers_url: string | null; program: string | null; why: string | null; notes: string | null;
  priority: string | null; ats: string | null; ats_slug: string | null; active: boolean;
  open_postings: number }
interface SettingsOut { weekly_goal: number; sources_enabled: Record<string, boolean> }
```

## Routes

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | /api/health | | `{status:"ok"}` |
| GET | /api/today | | Today |
| GET | /api/postings | query: `priority, zone, type, status, source, user_status, q, sort (priority\|deadline\|company\|first_seen), include_rejected (bool, default false), only_rejected (bool), track (internship\|post_graduation\|all, default internship)` | PostingSummary[] |
| GET | /api/postings/{id} | | PostingDetail |
| POST | /api/postings | `{url}` manual add | PostingDetail |
| PATCH | /api/postings/{id} | `{user_status?, status?, note?, skip_reason?}` (see "Skip reasons") | PostingDetail |
| POST | /api/postings/{id}/override | (marks all reject hits and the `post_graduation` flag overridden, filter_state=overridden; the posting moves to the internship track) | PostingDetail |
| DELETE | /api/postings/{id}/override | undo a restore: overridden hits count again, filter_state back to `rejected` (or `flagged`/`pass` without reject hits) | PostingDetail |
| POST | /api/postings/{id}/mark-sent | `{channel, cv_version, date_sent?}` | Application |
| GET | /api/postings/{id}/pack | | Pack (404 if none) |
| POST | /api/postings/{id}/pack | generate now (LLM) | Pack |
| PATCH | /api/packs/{id} | `{cover_letter?, form_answers?, cv_version?, apply_link?}` | Pack |
| POST | /api/packs/{id}/verify | `?llm=false` for the deterministic pre-check only | Pack (re-checked) |
| POST | /api/contacts | `{posting_id, name, role?, profile_url?, why?}` | Contact |
| DELETE | /api/contacts/{id} | | 204 |
| GET | /api/drafts | query: `status` (default `draft`), `channel` | Draft[] |
| POST | /api/drafts | `{posting_id, contact_id?, kind, channel?, recipient?, text}` | Draft |
| PATCH | /api/drafts/{id} | `{text?, recipient?, status?}` (status only `draft\|approved\|skipped`; `sent` is set only by `radar send` or `/mark-sent`) | Draft |
| POST | /api/drafts/for-contact | `{posting_id, contact_id, kind}` LLM writes a draft for one contact (status `draft`, nothing is sent) | Draft |
| POST | /api/drafts/{id}/mark-sent | manual send recorded (copy button flow) | Draft |
| GET | /api/applications | | Application[] |
| POST | /api/applications | `{company, role, city?, date_sent, channel, cv_version, stage?, notes?, posting_id?}` | Application |
| PATCH | /api/applications/{id} | any editable field | Application |
| GET | /api/applications/stats | | AppStats |
| GET | /api/companies | | Company[] |
| PATCH | /api/companies/{id} | `{notes?, priority?, active?}` | Company |
| GET | /api/settings | | SettingsOut |
| PUT | /api/settings | SettingsOut (partial ok) | SettingsOut |
| GET | /api/runs | query `limit` (default 10) | RunSummary[] |
| GET | /api/runs/{id} | | RunDetail (404 unknown id) |
| GET | /api/usage | query `days` (1-90, default 7; today included, Europe/Amsterdam days) | Usage (sums of `llm_usage` rows; counts only) |

Rules: no route ever submits an application or sends a message. `followup_due` = stage
`Inviata`, not responded, `date_sent` 14+ days ago. "Since yesterday" = change events in the
latest run (fallback: last 24 hours).

Tracks: the filter rule `post_graduation` (action `flag`) marks graduate programmes and
full-time roles that start after the July 2027 graduation (or are for people who already
graduated). It is not an exclusion: `filter_state` stays `flagged` (or `rejected` if another
reject rule fires). `PostingSummary.track` is `post_graduation` when the posting has a
`post_graduation` hit with `overridden: false`, else `internship`. `GET /api/postings` returns
the internship track by default; `track=post_graduation` gives the separate view and
`track=all` both (the `include_rejected` / `only_rejected` switches apply on top). In Today,
`new_since_yesterday` and `upcoming_deadlines` are internship-only and `new_post_graduation`
counts the new post-graduation postings.

Skip reasons: there is no column; the reason is stored as a prefix of `Posting.note`:
`"[Saltato: Sede: solo Londra] my own note"`. `PATCH {skip_reason}` (max 200 chars) sets
`user_status=skipped` (unless `user_status` is also given) and writes the prefix; patching
`note` alone keeps an existing prefix; any `user_status` other than `skipped` removes it.
`note` in responses is the raw stored text, `skip_reason` the parsed reason (null if none).

Notes: list filters (`priority, zone, type, status, source, user_status` on postings; `status` on
drafts) also accept comma-separated values; `priority=none` matches unscored postings and
`GET /api/drafts?status=all` returns every draft. Errors are `{detail: string}` (FastAPI): 404
unknown id, 422 validation, 409 editing a sent draft, 501 when an optional component (LLM
packs, manual add) is not installed, 502 when the LLM / fetch fails. Auth errors: 401 missing,
invalid or expired token, 403 email not allowed.

### Notes for clients

- `RunSummary.stats` shape: `{sources: {<source>: {boards, fetched, new, errors}}, new, changed,
  closed, reopened, rejected, flagged, errors: [{source, error}], llm: {scoring: {...}, packs:
  {...}}}` (counts only, never names).
- `StatsRow.interview_rate` is a ratio in `[0, 1]`.
- `zone` values: `Italia`, `Europa`, `Altro`.
