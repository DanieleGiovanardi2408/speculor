import { useId, useRef, type ReactNode } from "react";

export interface TabDef<K extends string> {
  key: K;
  label: ReactNode;
}

/** WAI-ARIA tabs: roving tabindex, Arrow/Home/End keys, automatic activation. */
export function Tabs<K extends string>({
  tabs,
  value,
  onChange,
  label,
  children,
}: {
  tabs: TabDef<K>[];
  value: K;
  onChange: (k: K) => void;
  label: string;
  children: ReactNode;
}) {
  const base = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  function onKeyDown(e: React.KeyboardEvent, index: number) {
    let next: number | null = null;
    if (e.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    if (next == null) return;
    e.preventDefault();
    onChange(tabs[next]!.key);
    refs.current[next]?.focus();
  }

  return (
    <div>
      <div role="tablist" aria-label={label} className="mb-5 flex gap-1 relative overflow-x-auto border-b border-line">
        {tabs.map((t, i) => {
          const selected = t.key === value;
          return (
            <button
              key={t.key}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="tab"
              id={`${base}-tab-${t.key}`}
              aria-selected={selected}
              aria-controls={`${base}-panel`}
              tabIndex={selected ? 0 : -1}
              onClick={() => onChange(t.key)}
              onKeyDown={(e) => onKeyDown(e, i)}
              className={`-mb-px flex items-center gap-1.5 whitespace-nowrap border-b-2 px-2.5 py-2.5 text-sm sm:px-3 font-medium ${
                selected ? "border-accent text-accent-ink" : "border-transparent text-ink-muted hover:text-ink"
              }`}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <div role="tabpanel" id={`${base}-panel`} aria-labelledby={`${base}-tab-${value}`} tabIndex={0} className="focus-visible:outline-offset-4">
        {children}
      </div>
    </div>
  );
}
