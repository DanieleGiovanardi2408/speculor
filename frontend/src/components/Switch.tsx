import { useId } from "react";

/** Accessible on/off switch with visible text state (not color alone). */
export function Switch({ checked, onChange, label, description }: { checked: boolean; onChange: (v: boolean) => void; label: string; description?: string }) {
  const id = useId();
  return (
    <div className="flex items-center justify-between gap-4 py-3">
      <div>
        <span id={`${id}-label`} className="text-sm font-medium">
          {label}
        </span>
        {description && (
          <span id={`${id}-desc`} className="block text-xs text-ink-muted">
            {description}
          </span>
        )}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={`${id}-label`}
        aria-describedby={description ? `${id}-desc` : undefined}
        onClick={() => onChange(!checked)}
        className="flex shrink-0 items-center gap-2 rounded-full"
      >
        <span className={`w-8 text-right text-xs font-medium ${checked ? "text-accent-ink" : "text-ink-muted"}`} aria-hidden="true">
          {checked ? "Sì" : "No"}
        </span>
        <span className={`relative inline-flex h-6 w-11 items-center rounded-full border transition-colors ${checked ? "border-accent bg-accent" : "border-line-strong bg-subtle"}`}>
          <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-6" : "translate-x-1"}`} />
        </span>
      </button>
    </div>
  );
}
