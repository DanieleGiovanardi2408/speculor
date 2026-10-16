import { sourceLabel } from "./labels";

/** Plain-Italian explanation of a pipeline error string such as "SourceError (HTTP 429)". */
export function plainRunError(raw: string): string {
  const r = raw.toLowerCase();
  if (/\b429\b|rate.?limit|too many/.test(r)) return "troppe richieste, il sito ci ha chiesto di rallentare. Riprova da solo al prossimo giro.";
  if (/timeout|timed out/.test(r)) return "il sito non ha risposto in tempo.";
  if (/\b40[13]\b|forbidden|unauthori[sz]ed/.test(r)) return "il sito ha negato l'accesso.";
  if (/\b404\b|not found/.test(r)) return "pagina non trovata: la bacheca potrebbe essere cambiata o chiusa.";
  if (/\b5\d\d\b/.test(r)) return "il sito ha avuto un problema temporaneo.";
  if (/connect|network|dns|resolve/.test(r)) return "impossibile collegarsi: controlla la connessione.";
  if (/operationalerror|database|sqlite|locked/.test(r)) return "problema con il database locale (forse era occupato).";
  if (/missing_key|api key|api_key/.test(r)) return "manca la chiave API del modello linguistico.";
  if (/parse|json|decode/.test(r)) return "risposta del sito in un formato inatteso.";
  return "errore imprevisto.";
}

export function plainRunErrorLine(source: string | null, raw: string): string {
  const who = source && source !== "pipeline" ? sourceLabel(source) : "Esecuzione";
  return `${who}: ${plainRunError(raw)}`;
}

export function plainLlmSkipped(reason: string): string {
  if (/missing_key/.test(reason)) return "Valutazione con il modello linguistico saltata: manca la chiave API.";
  if (/budget|limit/.test(reason)) return "Valutazione con il modello linguistico saltata: limite di utilizzo raggiunto.";
  if (/disabled|off/.test(reason)) return "Valutazione con il modello linguistico disattivata.";
  return `Valutazione con il modello linguistico saltata (${reason}).`;
}

const RULE_LABEL: Record<string, string> = {
  experience_years: "Anni di esperienza",
  seniority: "Livello senior",
  language: "Lingua",
  languages: "Lingua",
  start_date: "Data di inizio",
  duration: "Durata",
  location: "Sede",
  zone: "Zona",
  degree: "Titolo di studio",
  graduation: "Laurea già conseguita",
  post_graduation: "Dopo la laurea",
  unpaid: "Non retribuito",
  keyword: "Parole escluse",
  keywords: "Parole escluse",
  title: "Titolo",
  deadline: "Scadenza",
  closed: "Chiuso",
};
export const ruleLabel = (id: string) => RULE_LABEL[id] ?? id.replace(/[_-]+/g, " ").replace(/^./, (c) => c.toUpperCase());

const PURPOSE_LABEL: Record<string, string> = { scoring: "valutazione", pack: "pacchetti", packs: "pacchetti", draft: "bozze", drafts: "bozze", verify: "verifica fatti" };
export const purposeLabel = (p: string) => PURPOSE_LABEL[p] ?? p;
