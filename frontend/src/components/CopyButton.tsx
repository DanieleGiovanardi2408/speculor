import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";

async function writeClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  ta.remove();
}

export function CopyButton({
  text,
  label = "Copia",
  srContext,
  size = "sm",
  onCopied,
}: {
  text: string;
  label?: string;
  /** Extra screen-reader-only context, e.g. "lettera di presentazione". */
  srContext?: string;
  size?: "sm" | "md";
  onCopied?: () => void;
}) {
  const [state, setState] = useState<"idle" | "copied" | "error">("idle");
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);

  async function copy() {
    try {
      await writeClipboard(text);
      setState("copied");
      onCopied?.();
    } catch {
      setState("error");
    }
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), 2000);
  }

  return (
    <>
      <button type="button" onClick={copy} className={`btn-secondary ${size === "sm" ? "btn-sm" : ""}`}>
        <Icon name={state === "copied" ? "check" : "copy"} size={14} />
        <span>{state === "copied" ? "Copiato" : label}</span>
        {srContext && <span className="sr-only"> {srContext}</span>}
      </button>
      <span className="sr-only" aria-live="polite">
        {state === "copied" ? "Copiato" : state === "error" ? "Copia non riuscita" : ""}
      </span>
    </>
  );
}
