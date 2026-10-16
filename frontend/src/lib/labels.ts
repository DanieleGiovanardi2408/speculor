import type { DraftKind, DraftStatus, PostingStatus, Stage, UserStatus } from "../api/types";

export const STAGES: Stage[] = [
  "Inviata",
  "Screening HR",
  "Test/case",
  "Colloquio 1",
  "Colloquio 2+",
  "Offerta",
  "Rifiutata",
  "Nessuna risposta",
  "Ritirata",
];

export const DRAFT_KIND_LABEL: Record<DraftKind, string> = {
  linkedin_connection_note: "Nota di collegamento LinkedIn",
  linkedin_message: "Messaggio LinkedIn",
  followup: "Follow-up",
  email: "Email",
};

/** Character limits per draft kind. */
export const DRAFT_LIMIT: Record<DraftKind, number | null> = {
  linkedin_connection_note: 280,
  linkedin_message: 600,
  followup: 600,
  email: null,
};

export const DRAFT_STATUS_LABEL: Record<DraftStatus, string> = {
  draft: "Bozza",
  approved: "Approvata",
  sent: "Inviata",
  skipped: "Saltata",
  failed: "Invio fallito",
};

export const USER_STATUS_LABEL: Record<UserStatus, string> = {
  todo: "Da valutare",
  sent: "Candidatura inviata",
  skipped: "Saltato",
};

export const POSTING_STATUS_LABEL: Record<PostingStatus, string> = {
  open: "Aperto",
  closed: "Chiuso",
  unknown: "Stato incerto",
};

export const SOURCE_LABEL: Record<string, string> = {
  greenhouse: "Greenhouse",
  lever: "Lever",
  workday: "Workday",
  careers_page: "Pagina carriere",
  linkedin_rss: "LinkedIn (RSS)",
  linkedin: "LinkedIn",
  linkedin_jobs: "LinkedIn (ricerche salvate)",
  email: "Email (avvisi)",
  jobteaser: "JobTeaser",
  ashby: "Ashby",
  smartrecruiters: "SmartRecruiters",
  manual: "Aggiunto a mano",
};
export const sourceLabel = (s: string) => SOURCE_LABEL[s] ?? s;

/** Same labels the backend demo/CLI uses, so stats group correctly. */
export const CHANNEL_OPTIONS = ["Sito aziendale", "LinkedIn", "Email", "Referral", "Altro"];
