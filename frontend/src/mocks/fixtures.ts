// Clearly fictional fixture data for VITE_MOCK=1 (development and screenshots only).
import type {
  Application,
  Company,
  Contact,
  Draft,
  FilterHit,
  Pack,
  PostingDetail,
  Priority,
  RunSummary,
  ScoreOut,
  SettingsOut,
  Stage,
} from "../api/types";

const DAY = 86_400_000;
export const isoDate = (offsetDays: number): string => new Date(Date.now() + offsetDays * DAY).toISOString().slice(0, 10);
export const isoTs = (offsetDays: number, hour = 7): string => {
  const d = new Date(Date.now() + offsetDays * DAY);
  d.setUTCHours(hour, 12, 0, 0);
  return d.toISOString();
};

function score(priority: Priority, why: string, not: string, warnings: string[] = [], langs: string[] = ["Inglese C1"]): ScoreOut {
  return {
    priority,
    why_fits: why,
    what_doesnt_fit: not,
    start_window: "febbraio-aprile 2027",
    language_requirements: langs,
    warnings,
    confidence: 0.8,
    provider: "mock",
    model: "mock-model",
    created_at: isoTs(0, 6),
  };
}

interface Seed {
  title: string;
  company: string;
  location: string;
  cc: string;
  zone: "Italia" | "Europa";
  type: string;
  area: string;
  source: string;
  deadline: number | null;
  firstSeen: number;
  priority: Priority | null;
  why?: string;
  not?: string;
  warnings?: string[];
  hits?: FilterHit[];
  filter_state?: PostingDetail["filter_state"];
  status?: PostingDetail["status"];
  user_status?: PostingDetail["user_status"];
  note?: string | null;
}

const seeds: Seed[] = [
  {
    title: "Stage Data Analyst, Pricing",
    company: "Nebbia Labs",
    location: "Milano",
    cc: "IT",
    zone: "Italia",
    type: "Stage curriculare",
    area: "Data",
    source: "greenhouse",
    deadline: 6,
    firstSeen: 0,
    priority: "Alta",
    why: "Richiede SQL e Python a livello intermedio, team piccolo con mentoring esplicito; inizio a febbraio compatibile con la tesi.",
    not: "Chiede preferibilmente esperienza con dbt, che non compare nel CV.",
  },
  {
    title: "Junior Product Analyst (Internship)",
    company: "Lanterna Analytics",
    location: "Berlino",
    cc: "DE",
    zone: "Europa",
    type: "Internship",
    area: "Product",
    source: "lever",
    deadline: 12,
    firstSeen: 0,
    priority: "Alta",
    why: "Ruolo analitico su esperimenti A/B, in inglese; il progetto di tesi su test statistici è molto pertinente.",
    not: "Sede in presenza 4 giorni su 5: valutare i costi di trasferimento.",
    warnings: ["Il tedesco è indicato come 'gradito'."],
    hits: [{ rule_id: "lang_de", action: "flag", reason: "Lingua tedesca citata", evidence: "German is a plus", overridden: false }],
    filter_state: "flagged",
  },
  {
    title: "Tirocinio Operations & Supply Chain",
    company: "Borgo Robotics",
    location: "Torino",
    cc: "IT",
    zone: "Italia",
    type: "Tirocinio extracurriculare",
    area: "Operations",
    source: "careers_page",
    deadline: 3,
    firstSeen: -2,
    priority: "Media",
    why: "Ruolo operativo con analisi dati di magazzino; azienda in crescita.",
    not: "Rimborso spese non indicato; poche informazioni sul tutor.",
    note: "Chiedere a Giulia (ex compagna di corso) com'è il team.",
  },
  {
    title: "Graduate Trainee, Sustainability Reporting",
    company: "Acquaviva Energia",
    location: "Bologna",
    cc: "IT",
    zone: "Italia",
    type: "Graduate program",
    area: "Sustainability",
    source: "workday",
    deadline: 9,
    firstSeen: -1,
    priority: "Media",
    why: "Programma strutturato di 12 mesi con rotazioni; tema ESG in linea con gli esami a scelta.",
    not: "Richiede laurea conseguita entro marzo: verificare le date.",
    warnings: ["Data di inizio vincolata alla laurea."],
    hits: [{ rule_id: "degree_required", action: "flag", reason: "Laurea già conseguita richiesta", evidence: "Laurea magistrale conseguita entro il 31/03", overridden: false }],
    filter_state: "flagged",
  },
  {
    title: "UX Research Intern",
    company: "Tramonto Studio",
    location: "Amsterdam",
    cc: "NL",
    zone: "Europa",
    type: "Internship",
    area: "Design",
    source: "lever",
    deadline: null,
    firstSeen: 0,
    priority: "Bassa",
    why: "Interessante per le competenze di ricerca qualitativa.",
    not: "Focus forte su design visivo e portfolio, poco allineato al profilo.",
  },
  {
    title: "Stage Controllo di Gestione",
    company: "Pietra Bianca Consulting",
    location: "Roma",
    cc: "IT",
    zone: "Italia",
    type: "Stage curriculare",
    area: "Finance",
    source: "careers_page",
    deadline: 20,
    firstSeen: -4,
    priority: "Media",
    why: "Excel avanzato e reporting mensile: esperienza diretta dal tirocinio precedente.",
    not: "Settore consulenza: orari potenzialmente lunghi.",
    user_status: "sent",
  },
  {
    title: "Bioinformatics Internship",
    company: "Gelso Bio",
    location: "Lione",
    cc: "FR",
    zone: "Europa",
    type: "Internship",
    area: "Data",
    source: "greenhouse",
    deadline: 15,
    firstSeen: -6,
    priority: null,
  },
  {
    title: "Senior Data Engineer",
    company: "Nebbia Labs",
    location: "Milano",
    cc: "IT",
    zone: "Italia",
    type: "Full-time",
    area: "Data",
    source: "greenhouse",
    deadline: null,
    firstSeen: 0,
    priority: null,
    hits: [{ rule_id: "seniority", action: "reject", reason: "Ruolo senior (5+ anni di esperienza)", evidence: "At least 5 years of experience in data engineering", overridden: false }],
    filter_state: "rejected",
  },
  {
    title: "Stagista Marketing (solo madrelingua francese)",
    company: "Ferrovia Quieta",
    location: "Parigi",
    cc: "FR",
    zone: "Europa",
    type: "Internship",
    area: "Marketing",
    source: "workday",
    deadline: 10,
    firstSeen: -1,
    priority: null,
    hits: [{ rule_id: "lang_native", action: "reject", reason: "Lingua madre richiesta non posseduta", evidence: "Français langue maternelle exigé", overridden: false }],
    filter_state: "rejected",
  },
  {
    title: "Internship Business Intelligence",
    company: "Ferrovia Quieta",
    location: "Milano",
    cc: "IT",
    zone: "Italia",
    type: "Internship",
    area: "Data",
    source: "workday",
    deadline: -1,
    firstSeen: -12,
    priority: "Alta",
    why: "Power BI e SQL, team internazionale a Milano.",
    not: "Annuncio scaduto.",
    status: "closed",
  },
  {
    title: "Tirocinio HR Analytics",
    company: "Borgo Robotics",
    location: "Torino",
    cc: "IT",
    zone: "Italia",
    type: "Tirocinio extracurriculare",
    area: "HR",
    source: "careers_page",
    deadline: 25,
    firstSeen: -8,
    priority: "Bassa",
    why: "Componente analitica presente.",
    not: "Prevalentemente attività amministrative.",
    user_status: "skipped",
    note: "[Saltato: Sede] Solo in presenza, troppo lontano.",
  },
  {
    title: "Strategy Intern, Energy Transition",
    company: "Acquaviva Energia",
    location: "Bruxelles",
    cc: "BE",
    zone: "Europa",
    type: "Internship",
    area: "Strategy",
    source: "manual",
    deadline: 4,
    firstSeen: -3,
    priority: "Alta",
    why: "Analisi di scenario e policy UE: combacia con la tesi sul mercato elettrico.",
    not: "Durata 6 mesi, possibile sovrapposizione con l'ultima sessione d'esami.",
  },
];

export const companiesSeed: Company[] = [
  ["Nebbia Labs", "Software / Data", "Milano", "Alta", "greenhouse", "Programma stage annuale"],
  ["Lanterna Analytics", "Analytics", "Berlino", "Alta", "lever", null],
  ["Borgo Robotics", "Robotica", "Torino", "Media", null, null],
  ["Acquaviva Energia", "Energia", "Bologna", "Media", "workday", "Graduate program"],
  ["Tramonto Studio", "Design", "Amsterdam", "Bassa", "lever", null],
  ["Pietra Bianca Consulting", "Consulenza", "Roma", "Media", null, null],
  ["Gelso Bio", "Biotech", "Lione", "Bassa", "greenhouse", null],
  ["Ferrovia Quieta", "Trasporti", "Milano", "Media", "workday", null],
].map(([name, sector, city, priority, ats, program], i) => ({
  id: i + 1,
  name: name as string,
  sector: sector as string,
  city: city as string,
  careers_url: `https://${(name as string).toLowerCase().replace(/\s+/g, "-")}.example.com/careers`,
  program: (program as string | null) ?? null,
  why: null,
  notes: i === 0 ? "Hanno risposto in fretta l'anno scorso." : null,
  priority: priority as string,
  ats: (ats as string | null) ?? null,
  ats_slug: ats ? (name as string).toLowerCase().replace(/\s+/g, "") : null,
  active: i !== 6,
  open_postings: 0,
}));

export const postingsSeed: PostingDetail[] = seeds.map((s, i) => {
  const id = 101 + i;
  const slug = s.company.toLowerCase().replace(/\s+/g, "-");
  const filterHits = s.hits ?? [];
  return {
    id,
    source: s.source,
    url: `https://jobs.example.com/${slug}/${id}`,
    title: s.title,
    company_name: s.company,
    location: s.location,
    country_code: s.cc,
    zone: s.zone,
    type: s.type,
    area: s.area,
    deadline: s.deadline == null ? null : isoDate(s.deadline),
    start_info: "Inizio previsto: febbraio",
    status: s.status ?? "open",
    user_status: s.user_status ?? "todo",
    filter_state: s.filter_state ?? "pass",
    first_seen: isoTs(s.firstSeen),
    last_seen: isoTs(0),
    note: s.note ?? null,
    priority: s.priority,
    score: s.priority && s.why ? score(s.priority, s.why, s.not ?? "", s.warnings) : null,
    filter_hits: filterHits,
    has_pack: id === 101 || id === 112,
    similarity:
      (s.filter_state ?? "pass") === "rejected"
        ? null
        : Math.round(((s.priority === "Alta" ? 0.68 : s.priority === "Media" ? 0.52 : 0.34) + (i % 5) * 0.02) * 100) / 100,
    track: /graduate|full.?time/i.test(s.type) ? "post_graduation" : "internship",
    skip_reason: (s.user_status ?? "todo") === "skipped" ? (/^\[Saltato: ([^\]]+)\]/.exec(s.note ?? "")?.[1] ?? null) : null,
    raw_text: `${s.company} cerca: ${s.title}, area ${s.area}, sede ${s.location}. ${filterHits.map((h) => h.evidence).filter(Boolean).join(". ")} Requisiti: Python, SQL, curiosità. (Annuncio fittizio di esempio.)`,
    via: s.source === "manual" ? "manual" : null,
    history: [{ kind: "new", changes: null, at: isoTs(s.firstSeen), run_id: 40 }],
    pack_id: id === 101 ? 1 : id === 112 ? 2 : null,
  };
});

export const contactsSeed: Contact[] = [
  { id: 1, posting_id: 101, name: "Marta Esempio", role: "Analytics Lead", profile_url: "https://www.linkedin.com/in/marta-esempio-fittizia", why: "Responsabile del team pricing citato nell'annuncio." },
  { id: 2, posting_id: 101, name: "Luca Prova", role: "Talent Acquisition", profile_url: null, why: "Recruiter indicato come referente." },
  { id: 3, posting_id: 112, name: "Sofia Campione", role: "Strategy Manager", profile_url: "https://www.linkedin.com/in/sofia-campione-fittizia", why: "Ha scritto il post sul programma di internship." },
];

const draftBase = {
  pack_id: null,
  send_result: null,
  sent_at: null,
  style_issues: [] as string[],
};

export const draftsSeed: Draft[] = [
  {
    ...draftBase,
    id: 1,
    posting_id: 101,
    pack_id: 1,
    contact_id: 1,
    kind: "linkedin_connection_note",
    channel: "linkedin",
    recipient: "marta-esempio-fittizia",
    text: "Ciao Marta, ho visto l'apertura per lo stage Data Analyst nel team Pricing di Nebbia Labs. Sto chiudendo una tesi su modelli di elasticità della domanda e mi piacerebbe restare in contatto. Grazie!",
    status: "draft",
    created_at: isoTs(0, 6),
    updated_at: isoTs(0, 6),
    posting_title: "Stage Data Analyst, Pricing",
    company_name: "Nebbia Labs",
    contact_name: "Marta Esempio",
  },
  {
    ...draftBase,
    id: 2,
    posting_id: 101,
    pack_id: 1,
    contact_id: 2,
    kind: "email",
    channel: "email",
    recipient: null,
    text: "Gentile Luca,\n\nle scrivo in merito allo stage Data Analyst, Pricing. Ho inviato la candidatura tramite il portale e volevo solo confermare la mia disponibilità full-time da febbraio-aprile 2027.\n\nCordiali saluti",
    status: "draft",
    style_issues: ["Frase d'apertura generica: personalizza con un dettaglio del team."],
    created_at: isoTs(0, 6),
    updated_at: isoTs(0, 6),
    posting_title: "Stage Data Analyst, Pricing",
    company_name: "Nebbia Labs",
    contact_name: "Luca Prova",
  },
  {
    ...draftBase,
    id: 3,
    posting_id: 112,
    pack_id: 2,
    contact_id: 3,
    kind: "linkedin_connection_note",
    channel: "linkedin",
    recipient: null,
    text: "Ciao Sofia, ho letto il tuo post sul programma di internship in Strategy di Acquaviva Energia. Mi sto candidando e lavoro sulla transizione energetica in tesi: mi farebbe piacere collegarci.",
    status: "draft",
    created_at: isoTs(-1, 6),
    updated_at: isoTs(-1, 6),
    posting_title: "Strategy Intern, Energy Transition",
    company_name: "Acquaviva Energia",
    contact_name: "Sofia Campione",
  },
  {
    ...draftBase,
    id: 4,
    posting_id: 106,
    contact_id: null,
    kind: "followup",
    channel: "linkedin",
    recipient: "recruiter-pietrabianca-fittizio",
    text: "Buongiorno, la ricontatto per lo stage in Controllo di Gestione: resto molto interessato e disponibile per un colloquio.",
    status: "approved",
    created_at: isoTs(-2, 6),
    updated_at: isoTs(-1, 9),
    posting_title: "Stage Controllo di Gestione",
    company_name: "Pietra Bianca Consulting",
    contact_name: null,
  },
  {
    ...draftBase,
    id: 5,
    posting_id: 110,
    contact_id: null,
    kind: "linkedin_message",
    channel: "linkedin",
    recipient: "bi-lead-ferroviaquieta-fittizio",
    text: "Grazie per la chiacchierata di ieri sul team BI!",
    status: "sent",
    send_result: "ok",
    sent_at: isoTs(-5, 10),
    created_at: isoTs(-6, 6),
    updated_at: isoTs(-5, 10),
    posting_title: "Internship Business Intelligence",
    company_name: "Ferrovia Quieta",
    contact_name: null,
  },
];

export const packsSeed: Omit<Pack, "posting" | "contacts" | "drafts">[] = [
  {
    id: 1,
    posting_id: 101,
    cover_letter:
      "Disponibile full-time da febbraio-aprile 2027.\n\nGentile team di Nebbia Labs,\n\nmi candido allo stage Data Analyst nel team Pricing a Milano. Sto concludendo la laurea magistrale e nella tesi stimo l'elasticità della domanda per un e-commerce di medie dimensioni, con Python e SQL su dati di transazione reali.\n\nNel tirocinio precedente ho automatizzato un report settimanale: la preparazione è passata da un giorno a un'ora. Ho documentato ogni passaggio così che il team potesse riusarlo senza di me.\n\nPorterei Python, SQL e l'abitudine di spiegare i risultati in parole semplici. Parlo italiano e inglese in modo fluente.\n\nGrazie per l'attenzione. Sarei felice di raccontarvi come potrei aiutare il team.\n\nCordiali saluti",
    form_answers: [
      { question: "Perché vuoi lavorare in Nebbia Labs?", answer: "Perché il team pricing lavora su problemi che sto già studiando in tesi, con dati reali e ciclo di feedback rapido." },
      { question: "Disponibilità di inizio", answer: "Disponibile full-time da febbraio-aprile 2027." },
    ],
    cv_version: "cv-data-it-v3",
    apply_link: "https://jobs.example.com/nebbia-labs/101/apply",
    language: "it",
    style_issues: ["Il secondo paragrafo è lungo: valuta di dividerlo."],
    provider: "mock",
    model: "mock-model",
    updated_at: isoTs(0, 6),
  },
  {
    id: 2,
    posting_id: 112,
    cover_letter: "Available full-time from February-April 2027.\n\nDear Acquaviva Energia team,\n\nI am applying for the Strategy Internship on the energy transition. My thesis models the Italian electricity market under different renewable scenarios.\n\nKind regards",
    form_answers: [],
    cv_version: "cv-strategy-en-v1",
    apply_link: "https://jobs.example.com/acquaviva-energia/112/apply",
    language: "en",
    style_issues: [],
    provider: "mock",
    model: "mock-model",
    updated_at: isoTs(-1, 6),
  },
];

const app = (id: number, company: string, role: string, city: string, daysAgo: number, channel: string, cv: string, stage: Stage, extra: Partial<Application> = {}): Application => ({
  id,
  posting_id: null,
  company,
  role,
  city,
  date_sent: isoDate(-daysAgo),
  channel,
  cv_version: cv,
  stage,
  next_step: null,
  next_step_date: null,
  notes: null,
  responded: stage !== "Inviata" && stage !== "Nessuna risposta",
  updated_at: isoTs(-1),
  followup_due: stage === "Inviata" && daysAgo >= 14 && !extra.responded,
  ...extra,
});

export const applicationsSeed: Application[] = [
  app(1, "Pietra Bianca Consulting", "Stage Controllo di Gestione", "Roma", 4, "Sito aziendale", "cv-finance-it-v2", "Inviata", { posting_id: 106 }),
  app(2, "Ferrovia Quieta", "Internship Business Intelligence", "Milano", 20, "LinkedIn", "cv-data-it-v3", "Colloquio 1", { next_step: "Colloquio tecnico con il BI lead", next_step_date: isoDate(3), posting_id: 110 }),
  app(3, "Tramonto Studio", "Research Assistant", "Amsterdam", 18, "Email", "cv-data-en-v2", "Inviata"),
  app(4, "Gelso Bio", "Data Intern", "Lione", 30, "Sito aziendale", "cv-data-en-v2", "Rifiutata", { notes: "Rifiuto standard, nessun feedback." }),
  app(5, "Lanterna Analytics", "Analytics Intern (2025)", "Berlino", 25, "Referral", "cv-data-en-v2", "Test/case", { next_step: "Consegna case study", next_step_date: isoDate(2) }),
  app(6, "Borgo Robotics", "Tirocinio Operations", "Torino", 2, "Sito aziendale", "cv-data-it-v3", "Inviata"),
  app(7, "Acquaviva Energia", "Summer Internship", "Bologna", 40, "Email", "cv-finance-it-v2", "Nessuna risposta"),
  app(8, "Nebbia Labs", "Stage Analytics (call precedente)", "Milano", 1, "Sito aziendale", "cv-data-it-v3", "Screening HR", { next_step: "Telefonata HR", next_step_date: isoDate(1) }),
];

export const reportsSeed: Record<number, string> = {
  41: "# Radar daily report (demo)\n\nRun #41 · status **partial**\n\nNew **6** (passed 4, flagged 2, rejected 2) · changed 2 · closed 1\n\n## Sources\n\n| Source | Boards | Fetched | New | Errors |\n|---|---:|---:|---:|---:|\n| greenhouse | 3 | 42 | 3 | 0 |\n| careers\\_page | 1 | 0 | 0 | 1 |\n\n## New postings that passed the filters\n\n### Alta\n\n- **Stage Data Analyst** — Nebbia Labs (Milano) · [link](https://jobs.example.com/nebbia-labs/101)\n  - Why: SQL e Python richiesti.\n\n## Errors\n\n- careers\\_page: `ReadTimeout`\n",
  40: "# Radar daily report (demo)\n\nRun #40 · status **ok**\n\n- 3 new, 1 closed\n",
};

export const runsSeed: RunSummary[] = [
  {
    id: 41,
    started_at: isoTs(0, 5),
    finished_at: isoTs(0, 5).replace(":12:00", ":19:40"),
    status: "partial",
    stats: {
      sources: { greenhouse: { boards: 3, fetched: 42, new: 3, errors: 0 }, lever: { boards: 2, fetched: 18, new: 2, errors: 0 }, workday: { boards: 2, fetched: 25, new: 1, errors: 0 }, careers_page: { boards: 1, fetched: 0, new: 0, errors: 1 } },
      errors: [{ source: "careers_page", error: "ReadTimeout (borgo-robotics.example.com)" }],
      llm: { scoring: { candidates: 6, scored: 6, input_tokens: 41210, output_tokens: 6120 }, packs: { built: 2, input_tokens: 7000, output_tokens: 3000 } },
      new: 6,
      changed: 2,
      closed: 1,
      rejected: 2,
      flagged: 2,
    },
  },
  {
    id: 40,
    started_at: isoTs(-1, 5),
    finished_at: isoTs(-1, 5).replace(":12:00", ":18:02"),
    status: "ok",
    stats: { sources: { greenhouse: { boards: 3, fetched: 40, new: 2, errors: 0 }, lever: { boards: 2, fetched: 17, new: 1, errors: 0 } }, errors: [], llm: { scoring: { scored: 3, input_tokens: 30110, output_tokens: 6050 } }, new: 3, closed: 1 },
  },
  {
    id: 39,
    started_at: isoTs(-2, 5),
    finished_at: isoTs(-2, 5).replace(":12:00", ":13:10"),
    status: "failed",
    stats: { errors: [{ source: "pipeline", error: "OperationalError" }], llm: { skipped: "missing_key" }, new: 0, closed: 0 },
  },
];

export const settingsSeed: SettingsOut = {
  weekly_goal: 10,
  sources_enabled: { greenhouse: true, lever: true, workday: true, careers_page: true, linkedin_rss: false },
};
