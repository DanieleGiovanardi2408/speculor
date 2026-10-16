import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { PostingActionsProvider } from "../components/PostingActions";
import { ToastProvider } from "../components/Toast";
import { handleMock, resetMockDb } from "../mocks/handler";
import type { PostingSummary } from "../api/types";

export function renderWithProviders(ui: ReactElement, { route = "/" } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={[route]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <PostingActionsProvider>{ui}</PostingActionsProvider>
        </MemoryRouter>
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

/** Routes `fetch` to the in-memory mock API; returns the spy to inspect calls. */
export function useMockApi() {
  resetMockDb();
  const spy = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, "http://localhost");
    const query = Object.fromEntries(url.searchParams.entries());
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const res = await handleMock(init?.method ?? "GET", url.pathname, query, body);
    return new Response(res.body === undefined ? null : JSON.stringify(res.body), { status: res.status });
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}
