import { useJobteaserPass } from "../api/hooks";
import type { JobteaserPass } from "../api/types";
import { formatDateTime, plural } from "../lib/format";
import { ExternalLink } from "./ExternalLink";
import { Icon } from "./Icon";

const REASON: Record<NonNullable<JobteaserPass["remind"]>, string> = {
  monday: "È lunedì: passa le offerte JobTeaser della settimana.",
  stale: "L'ultimo passaggio su JobTeaser ha più di 7 giorni.",
  never: "Non hai ancora salvato offerte JobTeaser con l'estensione.",
};

/**
 * Weekly JobTeaser pass (GET /api/jobteaser/pass): every Monday (Europe/Rome) or when the last
 * pass is older than 7 days. Radar never opens JobTeaser listing pages itself (robots.txt), so
 * the user opens each link and clicks "Salva in Radar" in the browser extension.
 * Renders nothing when no reminder is due or the endpoint can't be loaded.
 */
export function JobteaserPassCard() {
  const { data } = useJobteaserPass();
  if (!data || !data.remind) return null;
  const last = data.last_pass;
  return (
    <section aria-labelledby="jt-pass-title" className="card border-info-ink/30 p-5">
      <h2 id="jt-pass-title" className="mb-1 flex items-center gap-2 text-sm font-semibold">
        <Icon name="briefcase" className="text-ink-muted" /> Passaggio JobTeaser
      </h2>
      <p className="mb-3 text-sm text-ink-muted">
        {REASON[data.remind]} Apri ogni pagina, lasciala caricare, poi premi «Salva in Radar».
      </p>
      {data.links.length > 0 ? (
        <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1.5 text-sm" aria-label="Pagine JobTeaser da passare">
          {data.links.map((link) => (
            <li key={`${link.label}|${link.url}`}>
              <ExternalLink href={link.url}>{link.label}</ExternalLink>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mb-3 text-sm">
          Nessun link configurato: aggiungi <code>links</code> in <code>config/jobteaser_searches.yaml</code>.
        </p>
      )}
      <p className="text-xs text-ink-muted">
        {last ? (
          <>
            Ultimo passaggio: {formatDateTime(last.at)} · {plural(last.received, "offerta inviata", "offerte inviate")},{" "}
            {plural(last.new, "nuova", "nuove")}
            {last.ignored > 0 && <>, {plural(last.ignored, "ignorata", "ignorate")}</>}
          </>
        ) : (
          "Nessun passaggio finora."
        )}
        {!data.extension_configured && (
          <>
            {" "}
            · Estensione non configurata: esegui <code>radar extension token</code> e incolla il token nelle opzioni.
          </>
        )}
      </p>
    </section>
  );
}
