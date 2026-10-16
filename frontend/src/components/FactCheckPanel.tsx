import { useId, useState } from "react";
import type { Verification, VerificationClaim } from "../api/types";
import { DRAFT_KIND_LABEL } from "../lib/labels";
import { formatDateTime } from "../lib/format";
import { Badge, type Tone } from "./Badge";
import { Icon } from "./Icon";
import { Notice } from "./States";

const CHECK = { label: "Da verificare", tone: "warning" as Tone };
const VERDICT: Record<string, { label: string; tone: Tone }> = {
  supported: { label: "Supportata", tone: "success" },
  unsupported: { label: "Non supportata", tone: "danger" },
  check: CHECK,
};

const ERROR_LABEL: Record<string, string> = {
  disabled: "verificatore LLM non usato",
  rate_limit: "limite di richieste del provider raggiunto",
};

/** "cover_letter" -> "Lettera", "form_answers.2" -> "Risposta 2", draft kinds by label. */
export function textLabel(textId: string): string {
  if (textId === "cover_letter") return "Lettera";
  const m = /^form_answers\.(\d+)$/.exec(textId);
  if (m) return `Risposta ${m[1]}`;
  return (DRAFT_KIND_LABEL as Record<string, string>)[textId] ?? textId;
}

function ClaimRow({ c }: { c: VerificationClaim }) {
  const v = VERDICT[c.verdict] ?? CHECK;
  const bad = c.verdict === "unsupported";
  return (
    <li className={`rounded-lg border p-3 ${bad ? "border-danger/40 bg-danger-soft/40" : "border-line"}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge tone={v.tone} icon={bad ? "alert" : c.verdict === "supported" ? "check" : "info"}>
          {v.label}
        </Badge>
        <span className="text-ink-muted">{textLabel(c.text_id)}</span>
      </div>
      <p className="mt-1.5 text-sm">“{c.claim}”</p>
      <p className="mt-1 text-xs">
        {c.fact_id ? (
          <>
            <span className="text-ink-muted">{bad ? "Fatto più vicino o divieto: " : "Fatto: "}</span>
            <code className="break-all rounded bg-subtle px-1 font-mono text-ink">{c.fact_id}</code>
            {bad && <span className="ml-2 font-semibold text-danger-ink">non supportato</span>}
          </>
        ) : c.verdict === "check" ? (
          <span className="text-warning-ink">nessun fatto: da completare</span>
        ) : (
          <span className="font-semibold text-danger-ink">non supportato</span>
        )}
      </p>
      {c.reason && <p className="mt-1 text-xs text-ink-muted">{c.reason}</p>}
    </li>
  );
}

/** "Verifica fatti": every claim of the pack texts with its fact id, or "non supportato". */
export function FactCheckPanel({
  verification,
  stale,
  onVerify,
  pending,
}: {
  verification: Verification | null;
  stale: boolean;
  onVerify: () => void;
  pending: boolean;
}) {
  const headingId = useId();
  const onlyId = useId();
  const [onlyProblems, setOnlyProblems] = useState(false);
  const claims = verification?.claims ?? [];
  const shown = onlyProblems ? claims.filter((c) => c.verdict !== "supported") : claims;
  const counts = verification?.counts;

  return (
    <section aria-labelledby={headingId} className="mb-8">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 id={headingId} className="text-base font-semibold">
          Verifica fatti
        </h2>
        <button type="button" className="btn-secondary" onClick={onVerify} disabled={pending} aria-busy={pending}>
          <Icon name="undo" size={14} /> {pending ? "Verifica in corso…" : verification ? "Verifica di nuovo" : "Verifica ora"}
        </button>
      </div>

      {!verification || !counts ? (
        <p className="text-sm text-ink-muted">
          Nessuna verifica per questo pacchetto. Avviala per confrontare lettera, risposte e messaggi con il tuo fact bank.
        </p>
      ) : (
        <div className="card p-4">
          <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm" aria-label="Riepilogo della verifica">
            <li>
              <span className="font-semibold">{counts.supported}</span> supportate
            </li>
            <li className={counts.unsupported > 0 ? "font-semibold text-danger-ink" : ""}>
              <span className="font-semibold">{counts.unsupported}</span> non supportate
            </li>
            <li>
              <span className="font-semibold">{counts.check}</span> da verificare
            </li>
          </ul>
          <p className="mt-1 text-xs text-ink-muted">
            Verificata {formatDateTime(verification.verified_at)}
            {verification.model && ` con ${verification.model}`} · fatti da {verification.facts_source}
            {verification.facts_version && ` (${verification.facts_version})`}. I testi non vengono modificati: correggi tu le
            frasi non supportate o trasformale in [CHECK: ...].
          </p>
          {verification.status === "precheck_only" && (
            <p className="mt-2 text-xs text-warning-ink">
              Solo controllo automatico (numeri e divieti):{" "}
              {ERROR_LABEL[verification.error ?? ""] ?? "verificatore LLM non disponibile"}.
            </p>
          )}
          {stale && (
            <div className="mt-3">
              <Notice tone="warning" title="Testi modificati dopo la verifica">
                Salva le modifiche e premi «Verifica di nuovo» per aggiornare il controllo.
              </Notice>
            </div>
          )}

          {claims.length > 0 && (
            <details className="mt-3" open={counts.unsupported > 0}>
              <summary className="min-h-11 cursor-pointer py-2 text-sm font-medium sm:min-h-0">
                Mostra le affermazioni ({claims.length})
              </summary>
              <label htmlFor={onlyId} className="mt-2 inline-flex min-h-11 items-center gap-2 text-sm sm:min-h-0">
                <input id={onlyId} type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />
                Solo quelle da rivedere
              </label>
              <ul className="mt-2 space-y-2" aria-label="Affermazioni verificate">
                {shown.map((c, i) => (
                  <ClaimRow key={`${c.text_id}-${i}`} c={c} />
                ))}
              </ul>
              {shown.length === 0 && <p className="mt-2 text-sm text-ink-muted">Nessuna affermazione da rivedere.</p>}
            </details>
          )}
        </div>
      )}
    </section>
  );
}
