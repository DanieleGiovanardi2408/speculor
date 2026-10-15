const dateFmt = new Intl.DateTimeFormat("it-IT", { day: "numeric", month: "short", year: "numeric" });
const shortDateFmt = new Intl.DateTimeFormat("it-IT", { day: "numeric", month: "short" });
const dateTimeFmt = new Intl.DateTimeFormat("it-IT", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});
const numFmt = new Intl.NumberFormat("it-IT");
const pctFmt = new Intl.NumberFormat("it-IT", { style: "percent", maximumFractionDigits: 0 });

/** Parses `YYYY-MM-DD` as a local date (avoids UTC off-by-one). */
export function parseDate(value: string): Date {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y, m, d] = value.split("-").map(Number) as [number, number, number];
    return new Date(y, m - 1, d);
  }
  return new Date(value);
}

export const formatDate = (v: string | null | undefined, fallback = "—") => (v ? dateFmt.format(parseDate(v)) : fallback);
export const formatShortDate = (v: string | null | undefined, fallback = "—") =>
  v ? shortDateFmt.format(parseDate(v)) : fallback;
export const formatDateTime = (v: string | null | undefined, fallback = "—") =>
  v ? dateTimeFmt.format(new Date(v)) : fallback;
export const formatNumber = (n: number) => numFmt.format(n);
export const formatPercent = (ratio: number) => pctFmt.format(ratio);

/** Whole days from today (local) to the given date; negative = past. */
export function daysUntil(v: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const d = parseDate(v);
  d.setHours(0, 0, 0, 0);
  return Math.round((d.getTime() - today.getTime()) / 86_400_000);
}

export function relativeDays(v: string): string {
  const n = daysUntil(v);
  if (n === 0) return "oggi";
  if (n === 1) return "domani";
  if (n === -1) return "ieri";
  return n > 0 ? `tra ${n} giorni` : `${-n} giorni fa`;
}

export const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

export const wordCount = (text: string) => (text.trim() ? text.trim().split(/\s+/).length : 0);

export function plural(n: number, one: string, many: string) {
  return `${formatNumber(n)} ${n === 1 ? one : many}`;
}
