import type { Application, PostingSummary, Priority, Today } from "../api/types";
import { daysUntil } from "./format";

export type ActionKind = "deadline" | "new_alta" | "pack" | "followup" | "drafts" | "post_graduation";

export interface ActionItem {
  key: string;
  kind: ActionKind;
  title: string;
  subtitle: string;
  /** Primary destination: one click to the right place. */
  to: string;
  cta: string;
  /** Optional second destination (e.g. the posting row when the primary is the pack). */
  secondary?: { to: string; label: string };
  priority?: Priority | null;
  days?: number;
  posting?: PostingSummary;
  application?: Application;
}

export const DEADLINE_WINDOW = 7;
const RANK: Record<string, number> = { Alta: 0, Media: 1, Bassa: 2 };
const rank = (p: PostingSummary) => RANK[p.priority ?? ""] ?? 3;

export const postingHref = (p: Pick<PostingSummary, "id"> & { track?: string | null }) =>
  `/annunci?open=${p.id}${p.track === "post_graduation" ? "&vista=laurea" : ""}`;
export const packHref = (id: number) => `/annunci/${id}/pacchetto`;
export const applicationHref = (id: number) => `/candidature?apri=${id}`;

const actionable = (p: PostingSummary) => p.user_status === "todo" && p.status !== "closed" && p.filter_state !== "rejected";

function sub(p: PostingSummary) {
  return [p.company_name, p.location].filter(Boolean).join(" · ");
}

function deadlineText(days: number) {
  if (days === 0) return "scade oggi";
  if (days === 1) return "scade domani";
  return `scade tra ${days} giorni`;
}

/**
 * "What to do now", in order: deadlines in the next 7 days (Alta first), new Alta postings,
 * packs ready to review, follow-ups, drafts to approve, then new post-graduation postings.
 * A posting appears once, in its most urgent group.
 */
export function buildTodayActions(today: Today, readyPacks: PostingSummary[] = []): ActionItem[] {
  const out: ActionItem[] = [];
  const seen = new Set<number>();

  const deadlines = today.upcoming_deadlines
    .filter((p) => actionable(p) && p.deadline)
    .map((p) => ({ p, days: daysUntil(p.deadline!) }))
    .filter(({ days }) => days >= 0 && days <= DEADLINE_WINDOW)
    .sort((a, b) => Number(b.p.priority === "Alta") - Number(a.p.priority === "Alta") || a.days - b.days || rank(a.p) - rank(b.p) || a.p.id - b.p.id);
  for (const { p, days } of deadlines) {
    seen.add(p.id);
    out.push({
      key: `deadline-${p.id}`,
      kind: "deadline",
      title: p.title,
      subtitle: `${sub(p)} · ${deadlineText(days)}`,
      to: p.has_pack ? packHref(p.id) : postingHref(p),
      cta: p.has_pack ? "Apri pacchetto" : "Apri annuncio",
      secondary: p.has_pack ? { to: postingHref(p), label: "Dettagli annuncio" } : undefined,
      priority: p.priority,
      days,
      posting: p,
    });
  }

  for (const p of today.new_since_yesterday.filter((x) => x.priority === "Alta" && actionable(x) && !seen.has(x.id))) {
    seen.add(p.id);
    out.push({
      key: `new-${p.id}`,
      kind: "new_alta",
      title: p.title,
      subtitle: `${sub(p)} · nuovo dall'ultima esecuzione`,
      to: postingHref(p),
      cta: "Valuta",
      secondary: p.has_pack ? { to: packHref(p.id), label: "Apri pacchetto" } : undefined,
      priority: p.priority,
      posting: p,
    });
  }

  const packs = readyPacks
    .filter((p) => p.has_pack && actionable(p) && !seen.has(p.id))
    .sort((a, b) => rank(a) - rank(b) || (a.deadline ?? "9999").localeCompare(b.deadline ?? "9999") || a.id - b.id);
  for (const p of packs) {
    seen.add(p.id);
    out.push({
      key: `pack-${p.id}`,
      kind: "pack",
      title: p.title,
      subtitle: `${sub(p)} · pacchetto pronto da rivedere`,
      to: packHref(p.id),
      cta: "Rivedi pacchetto",
      priority: p.priority,
      posting: p,
    });
  }

  for (const a of [...today.followups_due].sort((x, y) => x.date_sent.localeCompare(y.date_sent) || x.id - y.id)) {
    const ago = -daysUntil(a.date_sent);
    out.push({
      key: `followup-${a.id}`,
      kind: "followup",
      title: a.role,
      subtitle: `${a.company} · inviata ${ago} giorni fa, nessuna risposta`,
      to: applicationHref(a.id),
      cta: "Fai follow-up",
      days: ago,
      application: a,
    });
  }

  if (today.pending_approvals > 0) {
    const n = today.pending_approvals;
    out.push({
      key: "drafts",
      kind: "drafts",
      title: n === 1 ? "1 bozza di messaggio da approvare" : `${n} bozze di messaggi da approvare`,
      subtitle: "Rileggi e approva: niente parte senza il tuo via libera.",
      to: "/approvazioni",
      cta: "Rivedi bozze",
    });
  }

  const pg = today.new_post_graduation ?? 0;
  if (pg > 0) {
    out.push({
      key: "post-graduation",
      kind: "post_graduation",
      title: pg === 1 ? "1 nuovo annuncio dopo la laurea" : `${pg} nuovi annunci dopo la laurea`,
      subtitle: "Graduate program e ruoli junior: da tenere d'occhio, non urgenti.",
      to: "/annunci?vista=laurea&ordina=first_seen",
      cta: "Guarda",
    });
  }
  return out;
}

export const ACTION_GROUP_LABEL: Record<ActionKind, string> = {
  deadline: "Scadenza vicina",
  new_alta: "Nuovo, priorità Alta",
  pack: "Pacchetto pronto",
  followup: "Follow-up",
  drafts: "Approvazioni",
  post_graduation: "Dopo la laurea",
};
