import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SourcesBanner } from "../components/SourcesBanner";
import { pauseMockConnector } from "../mocks/handler";
import { renderWithProviders, useMockApi } from "../test/utils";
import { SourcesPage, duration } from "./SourcesPage";
import { TodayPage } from "./TodayPage";

afterEach(() => vi.unstubAllGlobals());

describe("SourcesPage", () => {
  it("shows every source with its state, problem, action and last run", async () => {
    useMockApi();
    renderWithProviders(<SourcesPage />);
    const gh = await screen.findByRole("region", { name: "Greenhouse" });
    expect(within(gh).getByText("Errori")).toBeInTheDocument();
    expect(gh).toHaveTextContent("HTTP 404");
    expect(gh).toHaveTextContent("Cosa fare:");
    expect(within(gh).getAllByText("uv run python scripts/check_boards.py --ats greenhouse")[0]?.tagName).toBe("CODE");
    expect(gh).toHaveTextContent("412 letti, 6 nuovi, 3 chiusi");
    expect(gh).toHaveTextContent("12 bacheche · 11 funzionano · 2 senza annunci · 1 con errori");
    expect(within(gh).getByText("Annunci nuovi, ultimi 7 giorni")).toBeInTheDocument();

    const email = screen.getByRole("region", { name: "Email di avviso (IMAP)" });
    expect(within(email).getByText("Spenta")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Estensione «Salva in Radar»" })).toHaveTextContent("Ultimo «Salva in Radar»");
    expect(screen.getByRole("region", { name: "JobTeaser (pagine offerta)" })).toHaveTextContent("non annunci nuovi");
  });

  it("expands the failing boards with company names", async () => {
    useMockApi();
    const user = userEvent.setup();
    renderWithProviders(<SourcesPage />);
    const gh = await screen.findByRole("region", { name: "Greenhouse" });
    const summary = within(gh).getByText(/Bacheche con errori \(1\)/);
    expect(within(gh).getByText("Lumen Labs AI")).not.toBeVisible();
    await user.click(summary);
    expect(within(gh).getByText("Lumen Labs AI")).toBeVisible();
  });

  it("shows quotas as meters and the run duration with awake time", async () => {
    useMockApi();
    renderWithProviders(<SourcesPage />);
    const li = await screen.findByRole("meter", { name: "Chiamate di linkedin-cli" });
    expect(li).toHaveAttribute("aria-valuenow", "18");
    expect(li).toHaveAttribute("aria-valuemax", "25");
    expect(screen.getByRole("meter", { name: "openai/gpt-oss-20b" })).toHaveAttribute("aria-valuemax", "200000");
    expect(screen.getByRole("meter", { name: "Spesa stimata questo mese" })).toBeInTheDocument();
    expect(screen.getByText(/durata/)).toHaveTextContent("durata 31 min, lavoro effettivo 30 min");
    expect(screen.getByRole("link", { name: /Leggi il resoconto completo/ })).toHaveAttribute("href", "/esecuzioni/42");
  });

  it("resumes a paused source", async () => {
    const spy = useMockApi();
    pauseMockConnector("jobteaser", "sessione scaduta", "Esegui `make jobteaser-login`, poi Riprendi.");
    const user = userEvent.setup();
    renderWithProviders(<SourcesPage />);
    const jt = await screen.findByRole("region", { name: "JobTeaser (pagine offerta)" });
    expect(within(jt).getByText("In pausa")).toBeInTheDocument();
    expect(jt).toHaveTextContent("sessione scaduta");
    await user.click(within(jt).getByRole("button", { name: "Riprendi JobTeaser (pagine offerta)" }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([url, init]) => init?.method === "POST" && String(url).endsWith("/api/connectors/jobteaser/resume"))).toBe(true),
    );
    await waitFor(() => expect(within(screen.getByRole("region", { name: "JobTeaser (pagine offerta)" })).getByText("Funziona")).toBeInTheDocument());
  });

  it("formats durations in Italian", () => {
    expect(duration(45)).toBe("45 s");
    expect(duration(1500)).toBe("25 min");
    expect(duration(16213)).toBe("4 h 30 min");
    expect(duration(null)).toBe("—");
  });
});

describe("Oggi links to Fonti", () => {
  it("shows the failing sources with a link to Fonti", async () => {
    useMockApi();
    renderWithProviders(<SourcesBanner />);
    const banner = await screen.findByRole("region", { name: /Una fonte ha avuto errori/ });
    expect(banner).toHaveTextContent("Greenhouse");
    expect(within(banner).getByRole("link", { name: /Apri Fonti/ })).toHaveAttribute("href", "/fonti");
  });

  it("the pause banner on Oggi links to Fonti too", async () => {
    useMockApi();
    pauseMockConnector("linkedin_jobs", "checkpoint", "Apri linkedin.com e completa la verifica.");
    renderWithProviders(<TodayPage />);
    const banner = await screen.findByRole("region", { name: "Una fonte è in pausa" });
    expect(within(banner).getByRole("link", { name: /Stato di tutte le fonti/ })).toHaveAttribute("href", "/fonti");
    expect(await screen.findByRole("link", { name: /Apri Fonti/ })).toBeInTheDocument();
  });
});
