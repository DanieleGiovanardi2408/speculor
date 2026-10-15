import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { ToastProvider } from "../components/Toast";
import type { PostingSummary } from "../api/types";

export function renderWithProviders(ui: ReactElement, { route = "/" } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={[route]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>{ui}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

export function makePosting(over: Partial<PostingSummary> = {}): PostingSummary {
  return {
    id: 1,
    source: "greenhouse",
    url: "https://jobs.example.com/nebbia-labs/1",
    title: "Stage Data Analyst",
    company_name: "Nebbia Labs",
    location: "Milano",
    country_code: "IT",
    zone: "Italia",
    type: "Stage",
    area: "Data",
    deadline: null,
    start_info: null,
    status: "open",
    user_status: "todo",
    filter_state: "pass",
    first_seen: "2026-10-15T07:00:00Z",
    last_seen: "2026-10-15T07:00:00Z",
    note: null,
    priority: "Alta",
    score: {
      priority: "Alta",
      why_fits: "SQL e Python richiesti.",
      what_doesnt_fit: "Chiede dbt.",
      start_window: null,
      language_requirements: [],
      warnings: [],
      confidence: null,
      provider: "mock",
      model: "mock",
      created_at: "2026-10-15T07:00:00Z",
    },
    filter_hits: [],
    has_pack: false,
    ...over,
  };
}
