import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setMockJobteaserPass } from "../mocks/handler";
import { TodayPage } from "../pages/TodayPage";
import { renderWithProviders, useMockApi } from "../test/utils";
import { JobteaserPassCard } from "./JobteaserPassCard";

afterEach(() => vi.unstubAllGlobals());

const LINKS = [
  { label: "Offerte mirate", url: "https://luiss.jobteaser.com/it/job-offers?f=mirate" },
  { label: "Offerte dei partner", url: "https://luiss.jobteaser.com/it/job-offers?f=partner" },
  { label: "Data scientist", url: "https://luiss.jobteaser.com/it/job-offers?q=data" },
];
const LAST = { at: "2026-10-12T08:30:00Z", received: 24, new: 5, updated: 18, ignored: 1 };

describe("JobteaserPassCard", () => {
  it("on Monday shows the direct links and the last pass with its counts", async () => {
    useMockApi();
    setMockJobteaserPass({ last_pass: LAST, remind: "monday", links: LINKS, extension_configured: true });
    renderWithProviders(<JobteaserPassCard />);
    const card = await screen.findByRole("region", { name: "Passaggio JobTeaser" });
    expect(card).toHaveTextContent("È lunedì");
    expect(card).toHaveTextContent("24 offerte inviate, 5 nuove, 1 ignorata");
    expect(card).toHaveTextContent("Ultimo passaggio: 12 ott");
    const list = screen.getByRole("list", { name: "Pagine JobTeaser da passare" });
    const links = Array.from(list.querySelectorAll("a"));
    expect(links.map((a) => a.getAttribute("href"))).toEqual(LINKS.map((l) => l.url));
    expect(screen.getByRole("link", { name: /Offerte mirate/ })).toHaveAttribute("target", "_blank");
    expect(card).not.toHaveTextContent("Estensione non configurata");
  });

  it("when stale or never done, says so; degrades without links or token", async () => {
    useMockApi();
    setMockJobteaserPass({ last_pass: null, remind: "never", links: [], extension_configured: false });
    renderWithProviders(<JobteaserPassCard />);
    const card = await screen.findByRole("region", { name: "Passaggio JobTeaser" });
    expect(card).toHaveTextContent("Non hai ancora salvato");
    expect(card).toHaveTextContent("Nessun passaggio finora.");
    expect(card).toHaveTextContent("Nessun link configurato");
    expect(card).toHaveTextContent("radar extension token");
    expect(screen.queryByRole("list", { name: "Pagine JobTeaser da passare" })).not.toBeInTheDocument();
  });

  it("renders nothing when no reminder is due", async () => {
    const spy = useMockApi();
    setMockJobteaserPass({ last_pass: LAST, remind: null, links: LINKS, extension_configured: true });
    renderWithProviders(<JobteaserPassCard />);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.queryByRole("region", { name: "Passaggio JobTeaser" })).not.toBeInTheDocument();
  });

  it("shows on the Oggi screen when stale", async () => {
    useMockApi();
    setMockJobteaserPass({ last_pass: LAST, remind: "stale", links: LINKS, extension_configured: true });
    renderWithProviders(<TodayPage />);
    expect(await screen.findByRole("region", { name: "Passaggio JobTeaser" })).toHaveTextContent("più di 7 giorni");
  });
});
