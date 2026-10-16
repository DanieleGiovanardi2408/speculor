// Types mirror docs/API.md exactly. Keep in sync with the backend contract.

export type Priority = "Alta" | "Media" | "Bassa";
export type FilterState = "pass" | "flagged" | "rejected" | "overridden";
export type PostingStatus = "open" | "closed" | "unknown";
export type UserStatus = "todo" | "sent" | "skipped";
export type DraftStatus = "draft" | "approved" | "sent" | "skipped" | "failed";
/** `internship` (default list) or `post_graduation` (graduate programmes, junior roles). */
export type PostingTrack = "internship" | "post_graduation";
export type DraftKind = "linkedin_connection_note" | "linkedin_message" | "followup" | "email";
export type Stage =
  | "Inviata"
  | "Screening HR"
  | "Test/case"
  | "Colloquio 1"
  | "Colloquio 2+"
  | "Offerta"
  | "Rifiutata"
  | "Nessuna risposta"
  | "Ritirata";

export interface FilterHit {
  rule_id: string;
  action: "reject" | "flag";
  reason: string;
  evidence: string | null;
  overridden: boolean;
}

export interface ScoreOut {
  priority: Priority;
  why_fits: string;
  what_doesnt_fit: string;
  start_window: string | null;
  language_requirements: string[];
  warnings: string[];
  confidence: number | null;
  provider: string;
  model: string;
  created_at: string;
}

export interface PostingSummary {
  id: number;
  source: string;
  url: string;
  title: string;
  company_name: string;
  location: string | null;
  country_code: string | null;
  zone: string | null;
  type: string | null;
  area: string | null;
  deadline: string | null;
  start_info: string | null;
  status: PostingStatus;
  user_status: UserStatus;
  filter_state: FilterState;
  first_seen: string;
  last_seen: string;
  note: string | null;
  /** From latest score, null if unscored. */
  priority: Priority | null;
  score: ScoreOut | null;
  /** Flags and rejections. */
  filter_hits: FilterHit[];
  has_pack: boolean;
  /** Added by the backend in parallel; older servers omit it. */
  track?: PostingTrack | null;
  /** Parsed from the "[Saltato: reason]" note prefix; older servers omit it. */
  skip_reason?: string | null;
}

export interface ChangeEventOut {
  kind: "new" | "changed" | "closed" | "reopened";
  changes: Record<string, [unknown, unknown]> | null;
  at: string;
  run_id: number | null;
}

export interface PostingDetail extends PostingSummary {
  raw_text: string | null;
  via: string | null;
  history: ChangeEventOut[];
  pack_id: number | null;
}

export interface Contact {
  id: number;
  posting_id: number | null;
  name: string;
  role: string | null;
  profile_url: string | null;
  why: string | null;
  /** "manual" (typed by the user) or "search" (found in public search results). */
  source: "manual" | "search";
  source_query: string | null;
  /** Found by the search and not yet checked by the user ("da verificare"). */
  needs_review: boolean;
}

export interface ContactBudget {
  month_cost_eur: number;
  monthly_cap_eur: number;
  calls_today: number;
  daily_call_cap: number;
}

export interface ContactSearchResult {
  stored: number;
  found: number;
  dropped: Record<string, number>;
  contacts: Contact[];
  budget: ContactBudget;
}

export interface Draft {
  id: number;
  posting_id: number | null;
  pack_id: number | null;
  contact_id: number | null;
  kind: DraftKind;
  channel: "linkedin" | "email" | "other";
  recipient: string | null;
  text: string;
  status: DraftStatus;
  style_issues: string[];
  send_result: string | null;
  created_at: string;
  updated_at: string;
  sent_at: string | null;
  posting_title: string | null;
  company_name: string | null;
  contact_name: string | null;
}

export interface FormAnswer {
  question: string;
  answer: string;
}

export interface Pack {
  id: number;
  posting_id: number;
  cover_letter: string;
  form_answers: FormAnswer[];
  cv_version: string;
  apply_link: string;
  language: string;
  style_issues: string[];
  provider: string;
  model: string;
  updated_at: string;
  posting: PostingSummary;
  contacts: Contact[];
  drafts: Draft[];
}

export interface Application {
  id: number;
  posting_id: number | null;
  company: string;
  role: string;
  city: string | null;
  date_sent: string;
  channel: string;
  cv_version: string;
  stage: Stage;
  next_step: string | null;
  next_step_date: string | null;
  notes: string | null;
  responded: boolean;
  updated_at: string;
  followup_due: boolean;
}

export interface StatsRow {
  key: string;
  sent: number;
  responses: number;
  interviews: number;
  interview_rate: number;
}

export interface AppStats {
  by_channel: StatsRow[];
  by_cv: StatsRow[];
  by_stage: Record<Stage, number>;
}

export interface RunSummary {
  id: number;
  started_at: string;
  finished_at: string | null;
  status: "running" | "ok" | "partial" | "failed";
  stats: Record<string, unknown> | null;
}

export interface RunDetail extends RunSummary {
  report_md: string | null;
}

export interface UsageRow {
  day: string;
  provider: string;
  model: string;
  purpose: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
}

export interface UsageTotal {
  provider: string;
  model: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
}

/** GET /api/usage: LLM usage of the last `days` local days (default 7). */
export interface Usage {
  since: string;
  until: string;
  rows: UsageRow[];
  today: UsageTotal[];
  period: UsageTotal[];
}

export interface Today {
  weekly_goal: number;
  applications_this_week: number;
  new_since_yesterday: PostingSummary[];
  closed_since_yesterday: PostingSummary[];
  upcoming_deadlines: PostingSummary[];
  followups_due: Application[];
  pending_approvals: number;
  last_run: RunSummary | null;
  /** New post-graduation postings in the latest run; older servers omit it. */
  new_post_graduation?: number;
}

export interface Company {
  id: number;
  name: string;
  sector: string | null;
  city: string | null;
  careers_url: string | null;
  program: string | null;
  why: string | null;
  notes: string | null;
  priority: string | null;
  ats: string | null;
  ats_slug: string | null;
  active: boolean;
  open_postings: number;
}

export interface SettingsOut {
  weekly_goal: number;
  sources_enabled: Record<string, boolean>;
}

// ---- Request bodies / query params (derived from the Routes table) ----

export type PostingSort = "priority" | "deadline" | "company" | "first_seen";

export interface PostingsQuery {
  priority?: string;
  zone?: string;
  type?: string;
  status?: string;
  source?: string;
  user_status?: string;
  q?: string;
  sort?: PostingSort;
  include_rejected?: boolean;
  only_rejected?: boolean;
  track?: PostingTrack | "all";
}

export interface PostingPatch {
  user_status?: UserStatus;
  status?: PostingStatus;
  note?: string | null;
  /** Non-empty: posting becomes "skipped" and the reason is stored as a note prefix. */
  skip_reason?: string;
}

export interface MarkSentBody {
  channel: string;
  cv_version: string;
  date_sent?: string;
}

export interface PackPatch {
  cover_letter?: string;
  form_answers?: FormAnswer[];
  cv_version?: string;
  apply_link?: string;
}

export interface ContactCreate {
  posting_id: number;
  name: string;
  role?: string;
  profile_url?: string;
  why?: string;
}

export interface ContactPatch {
  needs_review?: boolean;
}

export interface DraftCreate {
  posting_id: number;
  contact_id?: number;
  kind: DraftKind;
  channel?: Draft["channel"];
  recipient?: string;
  text: string;
}

/** POST /api/drafts/for-contact. Not in docs/API.md; shape taken from backend schemas.DraftForContact. */
export interface DraftForContactBody {
  posting_id: number;
  contact_id: number;
  kind: DraftKind;
}

export interface DraftPatch {
  text?: string;
  recipient?: string | null;
  status?: "draft" | "approved" | "skipped";
}

export interface ApplicationCreate {
  company: string;
  role: string;
  city?: string;
  date_sent: string;
  channel: string;
  cv_version: string;
  stage?: Stage;
  notes?: string;
  posting_id?: number;
}

export type ApplicationPatch = Partial<
  Pick<
    Application,
    | "company"
    | "role"
    | "city"
    | "date_sent"
    | "channel"
    | "cv_version"
    | "stage"
    | "next_step"
    | "next_step_date"
    | "notes"
    | "responded"
  >
>;

export interface CompanyPatch {
  notes?: string | null;
  priority?: string | null;
  active?: boolean;
}

export type SettingsPatch = Partial<SettingsOut>;
