import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { GLOBAL_SHORTCUTS, POSTINGS_SHORTCUTS, useShortcuts } from "../lib/shortcuts";
import { Dialog } from "./Dialog";

function Group({ title, items }: { title: string; items: { keys: string; label: string }[] }) {
  return (
    <section>
      <h3 className="eyebrow mb-2">{title}</h3>
      <dl className="divide-y divide-line rounded-lg border border-line">
        {items.map((s) => (
          <div key={s.keys} className="flex items-center justify-between gap-4 px-3 py-2 text-sm">
            <dt>{s.label}</dt>
            <dd className="flex shrink-0 gap-1">
              {s.keys.split(" ").map((k, i) =>
                k === "/" && s.keys.includes(" / ") ? (
                  <span key={i} className="text-ink-muted">
                    o
                  </span>
                ) : (
                  <kbd key={i} className="rounded border border-line-strong bg-subtle px-1.5 py-0.5 font-mono text-xs">
                    {k}
                  </kbd>
                ),
              )}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** Global shortcuts (g o, g a, g p) and the "?" help dialog. */
export function GlobalShortcuts() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  useShortcuts({
    "g o": () => navigate("/"),
    "g a": () => navigate("/annunci"),
    "g p": () => navigate("/approvazioni"),
    "?": () => setOpen(true),
  });
  return (
    <>
      <button type="button" className="btn-ghost btn-sm hidden w-full justify-start md:flex" onClick={() => setOpen(true)} aria-keyshortcuts="?">
        <kbd className="rounded border border-line-strong bg-subtle px-1 font-mono text-xs">?</kbd> Scorciatoie da tastiera
      </button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Scorciatoie da tastiera" description="Non funzionano mentre scrivi in un campo di testo.">
        <div className="space-y-5">
          <Group title="Ovunque" items={GLOBAL_SHORTCUTS} />
          <Group title="Annunci" items={POSTINGS_SHORTCUTS} />
          <Group title="Finestra “Salta”" items={[{ keys: "1–6", label: "Scegli il motivo (da 1 a 6), poi Invio" }]} />
        </div>
      </Dialog>
    </>
  );
}
