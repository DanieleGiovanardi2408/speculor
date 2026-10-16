import { useEffect, useRef } from "react";

/** True when keys should go to the element (typing), not to app shortcuts. */
export function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = (el as HTMLInputElement).type;
    return !["checkbox", "radio", "button", "submit", "reset", "range", "color"].includes(type);
  }
  return false;
}

/** A modal dialog is open: page shortcuts must not act behind it. */
const modalOpen = () => !!document.querySelector('[aria-modal="true"]');

/** Handlers return `false` when they did not handle the key (the browser default then applies). */
export type ShortcutMap = Record<string, (e: KeyboardEvent) => void | boolean>;

/**
 * Single-key shortcuts ("j", "?", "/", "Enter") and two-key sequences ("g o").
 * Ignored while typing in a field, with Ctrl/Meta/Alt held, or while a modal dialog is open.
 */
export function useShortcuts(map: ShortcutMap, enabled = true) {
  const mapRef = useRef(map);
  mapRef.current = map;
  useEffect(() => {
    if (!enabled) return;
    let pending: string | null = null;
    let timer: number | undefined;
    function onKey(e: KeyboardEvent) {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
      if (isTypingTarget(e.target) || modalOpen()) return;
      const key = e.key;
      // Enter on a link or button keeps its native meaning.
      if (key === "Enter" && e.target instanceof HTMLElement && e.target.closest("a, button, summary, [role='button'], [role='tab']")) return;
      const handlers = mapRef.current;
      if (pending) {
        const combo = `${pending} ${key}`;
        pending = null;
        window.clearTimeout(timer);
        if (handlers[combo] && handlers[combo](e) !== false) e.preventDefault();
        return;
      }
      if (Object.keys(handlers).some((k) => k.startsWith(`${key} `))) {
        pending = key;
        timer = window.setTimeout(() => (pending = null), 1200);
        return;
      }
      const h = handlers[key];
      if (h && h(e) !== false) e.preventDefault();
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.clearTimeout(timer);
    };
  }, [enabled]);
}

export const GLOBAL_SHORTCUTS: { keys: string; label: string }[] = [
  { keys: "g o", label: "Vai a Oggi" },
  { keys: "g a", label: "Vai ad Annunci" },
  { keys: "g p", label: "Vai ad Approvazioni" },
  { keys: "?", label: "Mostra questa guida" },
];

export const POSTINGS_SHORTCUTS: { keys: string; label: string }[] = [
  { keys: "j / ↓", label: "Annuncio successivo" },
  { keys: "k / ↑", label: "Annuncio precedente" },
  { keys: "Invio / o", label: "Apri o chiudi i dettagli" },
  { keys: "p", label: "Apri il pacchetto candidatura" },
  { keys: "s", label: "Segna come inviata" },
  { keys: "x", label: "Salta (con motivo)" },
  { keys: "r", label: "Ripristina (tra gli scartati)" },
  { keys: "/", label: "Vai alla ricerca" },
];
