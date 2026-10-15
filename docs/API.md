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
}
interface ChangeEventOut { kind: "new" | "changed" | "closed" | "reopened";
  changes: Record<string, [unknown, unknown]> | null; at: string; run_id: number | null }
interface PostingDetail extends PostingSummary { raw_text: string | null; via: string | null;
  history: ChangeEventOut[]; pack_id: number | null }

interface Contact { id: number; posting_id: number | null; name: string; role: string | null;
  profile_url: string | null; why: string | null }
interface Draft { id: number; posting_id: number | null; pack_id: number | null;
  contact_id: number | null; kind: DraftKind; channel: "linkedin" | "email" | "other";
  recipient: string | null; text: string; status: DraftStatus; style_issues: string[];
  send_result: string | null; created_at: string; updated_at: string; sent_at: string | null;
  posting_title: string | null; company_name: string | null; contact_name: string | null }
interface FormAnswer { question: string; answer: string }
interface Pack { id: number; posting_id: number; cover_letter: string; form_answers: FormAnswer[];
  cv_version: string; apply_link: string; language: string; style_issues: string[];
  provider: string; model: string; updated_at: string;
  posting: PostingSummary; contacts: Contact[]; drafts: Draft[] }

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
  new_since_yesterday: PostingSummary[]; closed_since_yesterday: PostingSummary[];
  upcoming_deadlines: PostingSummary[]; followups_due: Application[];
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
| GET | /api/postings | query: `priority, zone, type, status, source, user_status, q, sort (priority\|deadline\|company\|first_seen), include_rejected (bool, default false), only_rejected (bool)` | PostingSummary[] |
| GET | /api/postings/{id} | | PostingDetail |
| POST | /api/postings | `{url}` manual add | PostingDetail |
| PATCH | /api/postings/{id} | `{user_status?, status?, note?}` | PostingDetail |
| POST | /api/postings/{id}/override | (marks all reject hits overridden, filter_state=overridden) | PostingDetail |
| POST | /api/postings/{id}/mark-sent | `{channel, cv_version, date_sent?}` | Application |
| GET | /api/postings/{id}/pack | | Pack (404 if none) |
| POST | /api/postings/{id}/pack | generate now (LLM) | Pack |
| PATCH | /api/packs/{id} | `{cover_letter?, form_answers?, cv_version?, apply_link?}` | Pack |
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

Rules: no route ever submits an application or sends a message. `followup_due` = stage
`Inviata`, not responded, `date_sent` 14+ days ago. "Since yesterday" = change events in the
latest run (fallback: last 24 hours).

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
