import type { PostingSummary } from "../api/types";

/** Quick reasons offered when skipping a posting. */
export const SKIP_CHIPS = ["Fuori target", "Lingua", "Sede", "Scadenza", "Già candidato", "Altro"] as const;

const PREFIX = /^\[Saltato: ([^\]\n]{1,200})\]\s?/;

/** "[Saltato: Sede] nota" -> { reason: "Sede", body: "nota" }. Mirrors backend routes/skip.py. */
export function splitSkip(note: string | null | undefined): { reason: string | null; body: string } {
  if (!note) return { reason: null, body: "" };
  const m = PREFIX.exec(note);
  if (!m) return { reason: null, body: note };
  return { reason: m[1]!.trim() || null, body: note.slice(m[0].length) };
}

/** Skip reason of a posting: the API field if present, else parsed from the note. */
export function skipReasonOf(p: Pick<PostingSummary, "note" | "user_status"> & { skip_reason?: string | null }): string | null {
  if (p.user_status !== "skipped") return null;
  if (p.skip_reason !== undefined) return p.skip_reason;
  return splitSkip(p.note).reason;
}

/** Note text without the skip prefix (what the user wrote). */
export const noteBody = (note: string | null | undefined) => splitSkip(note).body;

export function composeReason(chip: string, detail: string): string {
  const d = detail.trim();
  if (!chip) return d;
  return d ? `${chip}: ${d}` : chip;
}
