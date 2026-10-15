// Tiny in-memory implementation of docs/API.md used when VITE_MOCK=1.
import type {
  AppStats,
  Application,
  ApplicationCreate,
  Contact,
  ContactCreate,
  Draft,
  DraftCreate,
  DraftForContactBody,
  MarkSentBody,
  Pack,
  PackPatch,
  PostingDetail,
  PostingPatch,
  PostingSummary,
  Priority,
  SettingsOut,
  Stage,
  StatsRow,
  Today,
} from "../api/types";
import {
  applicationsSeed,
  companiesSeed,
  contactsSeed,
  draftsSeed,
  isoDate,
  packsSeed,
  postingsSeed,
  runsSeed,
  settingsSeed,
} from "./fixtures";

export interface MockResponse {
  status: number;
  body: unknown;
}

const clone = <T>(v: T): T => structuredClone(v);

let db = freshDb();
function freshDb() {
  return {
    postings: clone(postingsSeed),
    packs: clone(packsSeed),
    contacts: clone(contactsSeed),
    drafts: clone(draftsSeed),
    applications: clone(applicationsSeed),
    companies: clone(companiesSeed),
    runs: clone(runsSeed),
    settings: clone(settingsSeed),
    seq: 1000,
  };
}
/** Test helper. */
export function resetMockDb() {
  db = freshDb();
}

const ok = (body: unknown, status = 200): MockResponse => ({ status, body: clone(body) });
const notFound = (): MockResponse => ({ status: 404, body: { detail: "Non trovato" } });
const bad = (msg: string): MockResponse => ({ status: 422, body: { detail: msg } });
const now = () => new Date().toISOString();
const nextId = () => ++db.seq;

function summary(p: PostingDetail): PostingSummary {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { raw_text, via, history, pack_id, ...rest } = p;
  return rest;
}

function fullPack(p: (typeof db.packs)[number]): Pack {
  const posting = db.postings.find((x) => x.id === p.posting_id)!;
  return {
    ...p,
    posting: summary(posting),
    contacts: db.contacts.filter((c) => c.posting_id === p.posting_id),
    drafts: db.drafts.filter((d) => d.posting_id === p.posting_id),
  };
}

const PRIO_RANK: Record<Priority, number> = { Alta: 0, Media: 1, Bassa: 2 };

function listPostings(q: Record<string, unknown>): PostingSummary[] {
  const truthy = (v: unknown) => v === true || v === "true" || v === "1";
  let list = db.postings.slice();
  if (truthy(q.only_rejected)) list = list.filter((p) => p.filter_state === "rejected");
  else if (!truthy(q.include_rejected)) list = list.filter((p) => p.filter_state !== "rejected");
  const eq = (key: keyof PostingSummary) => {
    const v = q[key];
    if (v == null || v === "") return;
    const values = String(v).split(",");
    list = list.filter((p) => values.includes(String(p[key])));
  };
  (["priority", "zone", "type", "status", "source", "user_status"] as const).forEach(eq);
  if (q.q) {
    const needle = String(q.q).toLowerCase();
    list = list.filter((p) => `${p.title} ${p.company_name} ${p.location ?? ""}`.toLowerCase().includes(needle));
  }
  const sort = (q.sort as string) || "priority";
  list.sort((a, b) => {
    switch (sort) {
      case "deadline":
        return (a.deadline ?? "9999").localeCompare(b.deadline ?? "9999");
      case "company":
        return a.company_name.localeCompare(b.company_name, "it");
      case "first_seen":
        return b.first_seen.localeCompare(a.first_seen);
      default:
        return (a.priority ? PRIO_RANK[a.priority] : 9) - (b.priority ? PRIO_RANK[b.priority] : 9);
    }
  });
  return list.map(summary);
}

function withFollowup(a: Application): Application {
  const days = (Date.now() - new Date(a.date_sent).getTime()) / 86_400_000;
  return { ...a, followup_due: a.stage === "Inviata" && !a.responded && days >= 14 };
}

const INTERVIEW_STAGES: Stage[] = ["Colloquio 1", "Colloquio 2+", "Offerta"];
function statsBy(key: "channel" | "cv_version"): StatsRow[] {
  const groups = new Map<string, Application[]>();
  db.applications.forEach((a) => groups.set(a[key], [...(groups.get(a[key]) ?? []), a]));
  return [...groups.entries()].map(([k, apps]) => {
    const interviews = apps.filter((a) => INTERVIEW_STAGES.includes(a.stage)).length;
    return {
      key: k,
      sent: apps.length,
      responses: apps.filter((a) => a.responded).length,
      interviews,
      interview_rate: apps.length ? interviews / apps.length : 0,
    };
  });
}

function today(): Today {
  const weekStart = new Date();
  weekStart.setDate(weekStart.getDate() - ((weekStart.getDay() + 6) % 7));
  const ws = weekStart.toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 86_400_000).toISOString();
  const visible = db.postings.filter((p) => p.filter_state !== "rejected");
  return {
    weekly_goal: db.settings.weekly_goal,
    applications_this_week: db.applications.filter((a) => a.date_sent >= ws).length,
    new_since_yesterday: visible.filter((p) => p.first_seen >= yesterday).map(summary),
    closed_since_yesterday: visible.filter((p) => p.status === "closed").map(summary),
    upcoming_deadlines: visible
      .filter((p) => p.status === "open" && p.user_status === "todo" && p.deadline && p.deadline >= isoDate(0) && p.deadline <= isoDate(14))
      .sort((a, b) => a.deadline!.localeCompare(b.deadline!))
      .map(summary),
    followups_due: db.applications.map(withFollowup).filter((a) => a.followup_due),
    pending_approvals: db.drafts.filter((d) => d.status === "draft").length,
    last_run: db.runs[0] ?? null,
  };
}

export async function handleMock(
  method: string,
  fullPath: string,
  query: Record<string, unknown>,
  body: unknown,
): Promise<MockResponse> {
  if (import.meta.env.MODE !== "test") await new Promise((r) => setTimeout(r, 180));
  const path = fullPath.replace(/^\/api/, "");
  const seg = path.split("/").filter(Boolean);
  const id = seg[1] ? Number(seg[1]) : NaN;
  const b = (body ?? {}) as Record<string, unknown>;
  const second = seg[1] ? (/^\d+$/.test(seg[1]) ? "/:id" : `/${seg[1]}`) : "";
  const route = `${method} /${seg[0] ?? ""}${second}${seg[2] ? `/${seg[2]}` : ""}`;

  switch (route) {
    case "GET /health":
      return ok({ status: "ok" });
    case "GET /today":
      return ok(today());

    case "GET /postings":
      return ok(listPostings(query));
    case "POST /postings": {
      const url = String(b.url ?? "");
      if (!/^https?:\/\//.test(url)) return bad("URL non valido");
      const newId = nextId();
      const p: PostingDetail = {
        id: newId,
        source: "manual",
        url,
        title: "Annuncio aggiunto manualmente",
        company_name: new URL(url).hostname.replace(/^www\./, ""),
        location: null,
        country_code: null,
        zone: null,
        type: null,
        area: null,
        deadline: null,
        start_info: null,
        status: "open",
        user_status: "todo",
        filter_state: "pass",
        first_seen: now(),
        last_seen: now(),
        note: null,
        priority: null,
        score: null,
        filter_hits: [],
        has_pack: false,
        raw_text: null,
        via: "manual",
        history: [{ kind: "new", changes: null, at: now(), run_id: null }],
        pack_id: null,
      };
      db.postings.unshift(p);
      return ok(p, 201);
    }
    case "GET /postings/:id": {
      const p = db.postings.find((x) => x.id === id);
      return p ? ok(p) : notFound();
    }
    case "PATCH /postings/:id": {
      const p = db.postings.find((x) => x.id === id);
      if (!p) return notFound();
      Object.assign(p, b as PostingPatch);
      return ok(p);
    }
    case "POST /postings/:id/override": {
      const p = db.postings.find((x) => x.id === id);
      if (!p) return notFound();
      p.filter_hits = p.filter_hits.map((h) => (h.action === "reject" ? { ...h, overridden: true } : h));
      p.filter_state = "overridden";
      return ok(p);
    }
    case "POST /postings/:id/mark-sent": {
      const p = db.postings.find((x) => x.id === id);
      if (!p) return notFound();
      const mb = b as unknown as MarkSentBody;
      if (!mb.channel || !mb.cv_version) return bad("Canale e versione CV sono obbligatori");
      p.user_status = "sent";
      const a: Application = withFollowup({
        id: nextId(),
        posting_id: p.id,
        company: p.company_name,
        role: p.title,
        city: p.location,
        date_sent: mb.date_sent ?? isoDate(0),
        channel: mb.channel,
        cv_version: mb.cv_version,
        stage: "Inviata",
        next_step: null,
        next_step_date: null,
        notes: null,
        responded: false,
        updated_at: now(),
        followup_due: false,
      });
      db.applications.unshift(a);
      return ok(a, 201);
    }
    case "GET /postings/:id/pack": {
      const pk = db.packs.find((x) => x.posting_id === id);
      return pk ? ok(fullPack(pk)) : notFound();
    }
    case "POST /postings/:id/pack": {
      const p = db.postings.find((x) => x.id === id);
      if (!p) return notFound();
      let pk = db.packs.find((x) => x.posting_id === id);
      if (!pk) {
        pk = {
          id: nextId(),
          posting_id: id,
          cover_letter: `Gentile team di ${p.company_name},\n\nvi scrivo per candidarmi alla posizione "${p.title}". (Bozza generata in modalità demo.)\n\nCordiali saluti`,
          form_answers: [{ question: "Perché questa posizione?", answer: "Bozza di risposta da completare." }],
          cv_version: "cv-data-it-v3",
          apply_link: p.url,
          language: "it",
          style_issues: [],
          provider: "mock",
          model: "mock-model",
          updated_at: now(),
        };
        db.packs.push(pk);
      }
      p.has_pack = true;
      p.pack_id = pk.id;
      return ok(fullPack(pk), 201);
    }
    case "PATCH /packs/:id": {
      const pk = db.packs.find((x) => x.id === id);
      if (!pk) return notFound();
      Object.assign(pk, b as PackPatch, { updated_at: now() });
      return ok(fullPack(pk));
    }

    case "POST /contacts": {
      const cb = b as unknown as ContactCreate;
      if (!cb.name) return bad("Il nome è obbligatorio");
      const c: Contact = {
        id: nextId(),
        posting_id: cb.posting_id,
        name: cb.name,
        role: cb.role ?? null,
        profile_url: cb.profile_url ?? null,
        why: cb.why ?? null,
      };
      db.contacts.push(c);
      return ok(c, 201);
    }
    case "DELETE /contacts/:id":
      db.contacts = db.contacts.filter((c) => c.id !== id);
      return { status: 204, body: undefined };

    case "GET /drafts": {
      const status = (query.status as string) || "draft";
      return ok(
        db.drafts.filter((d) => d.status === status && (!query.channel || d.channel === query.channel)),
      );
    }
    case "POST /drafts": {
      const db_ = b as unknown as DraftCreate;
      const p = db.postings.find((x) => x.id === db_.posting_id);
      const d: Draft = {
        id: nextId(),
        posting_id: db_.posting_id,
        pack_id: p?.pack_id ?? null,
        contact_id: db_.contact_id ?? null,
        kind: db_.kind,
        channel: db_.channel ?? (db_.kind === "email" ? "email" : "linkedin"),
        recipient: db_.recipient ?? null,
        text: db_.text,
        status: "draft",
        style_issues: [],
        send_result: null,
        created_at: now(),
        updated_at: now(),
        sent_at: null,
        posting_title: p?.title ?? null,
        company_name: p?.company_name ?? null,
        contact_name: db.contacts.find((c) => c.id === db_.contact_id)?.name ?? null,
      };
      db.drafts.push(d);
      return ok(d, 201);
    }
    case "POST /drafts/for-contact": {
      const fb = b as unknown as DraftForContactBody;
      const c = db.contacts.find((x) => x.id === fb.contact_id);
      if (!c) return notFound();
      const p = db.postings.find((x) => x.id === c.posting_id);
      const publicId = c.profile_url?.match(/linkedin\.com\/in\/([^/?#]+)/)?.[1] ?? null;
      const d: Draft = {
        id: nextId(),
        posting_id: c.posting_id,
        pack_id: p?.pack_id ?? null,
        contact_id: c.id,
        kind: fb.kind,
        channel: fb.kind === "email" ? "email" : "linkedin",
        recipient: publicId,
        text: `Ciao ${c.name.split(" ")[0]}, mi sto candidando per "${p?.title ?? "la posizione"}" in ${p?.company_name ?? "azienda"}. Mi farebbe piacere collegarci. (Bozza demo.)`,
        status: "draft",
        style_issues: [],
        send_result: null,
        created_at: now(),
        updated_at: now(),
        sent_at: null,
        posting_title: p?.title ?? null,
        company_name: p?.company_name ?? null,
        contact_name: c.name,
      };
      db.drafts.push(d);
      return ok(d, 201);
    }
    case "PATCH /drafts/:id": {
      const d = db.drafts.find((x) => x.id === id);
      if (!d) return notFound();
      if (b.status && !["draft", "approved", "skipped"].includes(String(b.status))) return bad("Stato non consentito");
      if (b.status === "approved" && d.channel === "linkedin" && !(b.recipient ?? d.recipient))
        return bad("Destinatario obbligatorio per approvare un messaggio LinkedIn");
      Object.assign(d, b, { updated_at: now() });
      return ok(d);
    }
    case "POST /drafts/:id/mark-sent": {
      const d = db.drafts.find((x) => x.id === id);
      if (!d) return notFound();
      Object.assign(d, { status: "sent", sent_at: now(), send_result: "manual", updated_at: now() });
      return ok(d);
    }

    case "GET /applications":
      return ok(db.applications.map(withFollowup));
    case "GET /applications/stats": {
        const by_stage = Object.fromEntries(
          (
            ["Inviata", "Screening HR", "Test/case", "Colloquio 1", "Colloquio 2+", "Offerta", "Rifiutata", "Nessuna risposta", "Ritirata"] as Stage[]
          ).map((s) => [s, db.applications.filter((a) => a.stage === s).length]),
        ) as Record<Stage, number>;
        const stats: AppStats = { by_channel: statsBy("channel"), by_cv: statsBy("cv_version"), by_stage };
        return ok(stats);
    }
    case "POST /applications": {
      const ab = b as unknown as ApplicationCreate;
      if (!ab.company || !ab.role || !ab.date_sent || !ab.channel || !ab.cv_version) return bad("Campi obbligatori mancanti");
      const a = withFollowup({
        id: nextId(),
        posting_id: ab.posting_id ?? null,
        company: ab.company,
        role: ab.role,
        city: ab.city ?? null,
        date_sent: ab.date_sent,
        channel: ab.channel,
        cv_version: ab.cv_version,
        stage: ab.stage ?? "Inviata",
        next_step: null,
        next_step_date: null,
        notes: ab.notes ?? null,
        responded: false,
        updated_at: now(),
        followup_due: false,
      });
      db.applications.unshift(a);
      return ok(a, 201);
    }
    case "PATCH /applications/:id": {
      const a = db.applications.find((x) => x.id === id);
      if (!a) return notFound();
      Object.assign(a, b, { updated_at: now() });
      return ok(withFollowup(a));
    }

    case "GET /companies":
      return ok(
        db.companies.map((c) => ({
          ...c,
          open_postings: db.postings.filter((p) => p.company_name === c.name && p.status === "open" && p.filter_state !== "rejected").length,
        })),
      );
    case "PATCH /companies/:id": {
      const c = db.companies.find((x) => x.id === id);
      if (!c) return notFound();
      Object.assign(c, b);
      return ok(c);
    }

    case "GET /settings":
      return ok(db.settings);
    case "PUT /settings": {
      const sb = b as Partial<SettingsOut>;
      if (sb.weekly_goal != null) {
        if (!Number.isInteger(sb.weekly_goal) || sb.weekly_goal < 1) return bad("Obiettivo non valido");
        db.settings.weekly_goal = sb.weekly_goal;
      }
      if (sb.sources_enabled) db.settings.sources_enabled = { ...db.settings.sources_enabled, ...sb.sources_enabled };
      return ok(db.settings);
    }
    case "GET /runs":
      return ok(db.runs.slice(0, Number(query.limit ?? 10)));
  }
  return notFound();
}
