import { CHANNEL_OPTIONS } from "./labels";

/** Best guess of the application channel from the posting source and its `via` field. */
export function guessChannel(source: string | null | undefined, via: string | null | undefined): string {
  const v = (via ?? "").trim();
  const exact = CHANNEL_OPTIONS.find((c) => c.toLowerCase() === v.toLowerCase());
  if (exact) return exact;
  const hay = `${v} ${source ?? ""}`.toLowerCase();
  if (hay.includes("linkedin")) return "LinkedIn";
  if (hay.includes("@") || /\b(e-?mail|mail)\b/.test(hay)) return "Email";
  if (hay.includes("referral") || hay.includes("referenza")) return "Referral";
  return "Sito aziendale";
}

export const DEFAULT_CV = "CV v1";
