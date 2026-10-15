import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { Icon } from "./Icon";

type ToastKind = "success" | "error" | "info";
interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}
interface ToastApi {
  show: (message: string, kind?: ToastKind) => void;
  success: (message: string) => void;
  error: (message: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return ctx;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback((id: number) => setItems((xs) => xs.filter((t) => t.id !== id)), []);
  const show = useCallback(
    (message: string, kind: ToastKind = "success") => {
      const id = ++seq.current;
      setItems((xs) => [...xs.slice(-3), { id, kind, message }]);
      window.setTimeout(() => dismiss(id), kind === "error" ? 8000 : 4000);
    },
    [dismiss],
  );
  const api = useMemo<ToastApi>(
    () => ({ show, success: (m) => show(m, "success"), error: (m) => show(m, "error") }),
    [show],
  );

  const polite = items.filter((t) => t.kind !== "error");
  const assertive = items.filter((t) => t.kind === "error");

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-20 z-50 flex flex-col items-center gap-2 px-4 md:bottom-6 md:items-end md:pr-6">
        {/* Live regions are always mounted so screen readers pick up insertions. */}
        <div role="status" aria-live="polite" className="flex flex-col items-center gap-2 md:items-end">
          {polite.map((t) => (
            <ToastView key={t.id} item={t} onClose={() => dismiss(t.id)} />
          ))}
        </div>
        <div role="alert" aria-live="assertive" className="flex flex-col items-center gap-2 md:items-end">
          {assertive.map((t) => (
            <ToastView key={t.id} item={t} onClose={() => dismiss(t.id)} />
          ))}
        </div>
      </div>
    </ToastContext.Provider>
  );
}

function ToastView({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  const style =
    item.kind === "error"
      ? "border-danger/30 bg-surface text-danger-ink"
      : "border-line bg-ink text-white";
  return (
    <div
      className={`pointer-events-auto flex w-full max-w-sm items-start gap-2 rounded-xl border px-4 py-3 text-sm shadow-lg shadow-black/10 ${style}`}
    >
      <Icon name={item.kind === "error" ? "alert" : item.kind === "info" ? "info" : "check"} className="mt-0.5 shrink-0" />
      <p className="flex-1">{item.message}</p>
      <button
        type="button"
        onClick={onClose}
        className="-m-1 rounded p-1 opacity-80 hover:opacity-100"
        aria-label="Chiudi notifica"
      >
        <Icon name="x" size={14} />
      </button>
    </div>
  );
}
