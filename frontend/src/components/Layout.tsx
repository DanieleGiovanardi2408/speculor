import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useDrafts } from "../api/hooks";
import { MOCK_MODE } from "../api/client";
import { Icon, type IconName } from "./Icon";
import { PostingActionsProvider } from "./PostingActions";
import { GlobalShortcuts } from "./ShortcutsHelp";

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  end?: boolean;
  mobilePrimary?: boolean;
}

const NAV: NavItem[] = [
  { to: "/", label: "Oggi", icon: "home", end: true, mobilePrimary: true },
  { to: "/annunci", label: "Annunci", icon: "list", mobilePrimary: true },
  { to: "/approvazioni", label: "Approvazioni", icon: "inbox", mobilePrimary: true },
  { to: "/candidature", label: "Candidature", icon: "briefcase", mobilePrimary: true },
  { to: "/aziende", label: "Aziende", icon: "building" },
  { to: "/impostazioni", label: "Impostazioni", icon: "settings" },
];

function Logo() {
  return (
    <span className="flex items-center gap-2 font-semibold tracking-tight text-ink">
      <svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true">
        <rect width="32" height="32" rx="8" fill="var(--color-accent)" />
        <circle cx="16" cy="16" r="9" fill="none" stroke="#fff" strokeWidth="2" />
        <circle cx="16" cy="16" r="4" fill="none" stroke="#fff" strokeWidth="2" />
        <path d="M16 16 L23 9" stroke="#fff" strokeWidth="2" strokeLinecap="round" />
      </svg>
      Radar
    </span>
  );
}

function PendingCount() {
  const { data } = useDrafts("draft");
  const n = data?.length ?? 0;
  if (!n) return null;
  return (
    <span className="ml-auto rounded-full bg-accent px-1.5 text-[11px] font-semibold leading-5 text-white">
      {n}
      <span className="sr-only"> in attesa</span>
    </span>
  );
}

function MockBanner() {
  if (!MOCK_MODE) return null;
  return (
    <p className="rounded-md border border-warning-ink/20 bg-warning-soft px-2 py-1 text-xs font-medium text-warning-ink">
      Modalità demo: dati fittizi
    </p>
  );
}

function MoreMenu() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const location = useLocation();
  const extra = NAV.filter((n) => !n.mobilePrimary);
  const active = extra.some((n) => location.pathname.startsWith(n.to));

  useEffect(() => setOpen(false), [location.pathname]);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        btn.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative flex-1">
      <button
        ref={btn}
        type="button"
        aria-expanded={open}
        aria-controls="more-menu"
        onClick={() => setOpen((o) => !o)}
        className={`flex h-14 w-full flex-col items-center justify-center gap-0.5 text-[11px] font-medium ${active ? "relative font-semibold text-accent-ink before:absolute before:inset-x-1/4 before:top-0 before:h-0.5 before:rounded-full before:bg-accent" : "text-ink-muted"}`}
      >
        <Icon name="more" size={20} />
        Altro
      </button>
      {open && (
        <ul id="more-menu" className="absolute bottom-full right-2 mb-2 w-48 rounded-xl border border-line bg-surface p-1 shadow-lg">
          {extra.map((n) => (
            <li key={n.to}>
              <NavLink
                to={n.to}
                className={({ isActive }) =>
                  `flex min-h-11 items-center gap-2 rounded-lg px-3 py-2.5 text-sm ${isActive ? "bg-accent-soft font-medium text-accent-ink" : "text-ink hover:bg-subtle"}`
                }
              >
                <Icon name={n.icon} />
                {n.label}
              </NavLink>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Layout() {
  const location = useLocation();
  const prevPath = useRef(location.pathname);

  // Focus management: move focus to the page heading on route change.
  useEffect(() => {
    // Ref comparison (not a "first render" flag) so StrictMode's double effect doesn't steal initial focus.
    if (prevPath.current === location.pathname) return;
    prevPath.current = location.pathname;
    window.scrollTo(0, 0);
    const id = window.requestAnimationFrame(() => {
      const target = document.getElementById("page-title") ?? document.getElementById("main");
      target?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(id);
  }, [location.pathname]);

  return (
    <div className="min-h-dvh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[60] focus:rounded-lg focus:bg-ink focus:px-4 focus:py-2 focus:text-white"
      >
        Vai al contenuto principale
      </a>

      {/* Mobile top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-surface/95 px-4 py-3 backdrop-blur md:hidden">
        <Logo />
        <MockBanner />
      </header>

      {/* Desktop sidebar */}
      <div className="md:grid md:grid-cols-[15rem_minmax(0,1fr)]">
      <aside className="hidden border-r border-line bg-surface md:block">
        <div className="sticky top-0 flex h-dvh flex-col px-4 py-6">
        <div className="mb-8 px-2">
          <Logo />
          <p className="mt-1 text-xs text-ink-muted">Caccia agli stage, con te al comando</p>
        </div>
        <nav aria-label="Navigazione principale">
          <ul className="space-y-0.5">
            {NAV.map((n) => (
              <li key={n.to}>
                <NavLink
                  to={n.to}
                  end={n.end}
                  className={({ isActive }) =>
                    `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm ${
                      isActive ? "bg-accent-soft font-semibold text-accent-ink" : "text-ink-muted hover:bg-subtle hover:text-ink"
                    }`
                  }
                >
                  <Icon name={n.icon} />
                  {n.label}
                  {n.to === "/approvazioni" && <PendingCount />}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="mt-auto space-y-3 px-2">
          <GlobalShortcuts />
          <MockBanner />
          <p className="text-xs leading-relaxed text-ink-muted">
            Radar non invia mai candidature né messaggi: approvi tu, invii tu.
          </p>
        </div>
        </div>
      </aside>

      <main id="main" tabIndex={-1} className="min-w-0 focus:outline-none">
        <div className="mx-auto max-w-6xl px-4 pb-28 pt-6 sm:px-6 md:pb-12 md:pt-10 lg:px-10">
          <PostingActionsProvider>
            <Outlet />
          </PostingActionsProvider>
        </div>
      </main>
      </div>

      {/* Mobile bottom bar */}
      <nav aria-label="Navigazione principale" className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-surface md:hidden">
        <ul className="flex pb-[env(safe-area-inset-bottom)]">
          {NAV.filter((n) => n.mobilePrimary).map((n) => (
            <li key={n.to} className="flex-1">
              <NavLink
                to={n.to}
                end={n.end}
                className={({ isActive }) =>
                  `flex h-14 flex-col items-center justify-center gap-0.5 text-[11px] font-medium ${isActive ? "relative font-semibold text-accent-ink before:absolute before:inset-x-1/4 before:top-0 before:h-0.5 before:rounded-full before:bg-accent" : "text-ink-muted"}`
                }
              >
                <Icon name={n.icon} size={20} />
                {n.label}
              </NavLink>
            </li>
          ))}
          <li className="flex flex-1">
            <MoreMenu />
          </li>
        </ul>
      </nav>
    </div>
  );
}
