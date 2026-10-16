import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { pauseMockConnector } from "../mocks/handler";
import { TodayPage } from "../pages/TodayPage";
import { renderWithProviders, useMockApi } from "../test/utils";
import { ConnectorPauseBanner } from "./ConnectorPauseBanner";

afterEach(() => vi.unstubAllGlobals());

const ACTION =
  "Apri linkedin.com nel browser, completa la verifica, poi esegui `linkedin auth login` e `radar connectors resume linkedin_jobs`.";

describe("ConnectorPauseBanner", () => {
  it("renders nothing when no source is paused", async () => {
    const spy = useMockApi();
    renderWithProviders(<ConnectorPauseBanner />);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Riprendi/ })).not.toBeInTheDocument();
  });

  it("lists every paused source with reason and Italian action, and resumes one", async () => {
    const spy = useMockApi();
    pauseMockConnector("linkedin_jobs", "checkpoint", ACTION);
    pauseMockConnector("jobteaser", "sessione scaduta", "Accedi di nuovo a JobTeaser nel browser.");
    const user = userEvent.setup();
    renderWithProviders(<ConnectorPauseBanner />);

    const banner = await screen.findByRole("region", { name: "2 fonti sono in pausa" });
    expect(banner).toHaveTextContent("LinkedIn (ricerche salvate)");
    expect(banner).toHaveTextContent("Motivo: checkpoint");
    expect(banner).toHaveTextContent(ACTION);
    expect(banner).toHaveTextContent("JobTeaser");
    expect(banner).toHaveTextContent("Accedi di nuovo a JobTeaser nel browser.");

    await user.click(screen.getByRole("button", { name: "Riprendi LinkedIn (ricerche salvate)" }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([url, init]) => init?.method === "POST" && String(url).endsWith("/api/connectors/linkedin_jobs/resume"),
        ),
      ).toBe(true),
    );
    expect(await screen.findByRole("region", { name: "Una fonte è in pausa" })).not.toHaveTextContent("LinkedIn");
    expect(await screen.findByText(/LinkedIn \(ricerche salvate\): ripresa/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Riprendi JobTeaser" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: /in pausa/ })).not.toBeInTheDocument());
  });

  it("falls back to the raw source name for unknown sources", async () => {
    useMockApi();
    pauseMockConnector("imap_inbox", "login fallito", "Controlla la password per le app.");
    renderWithProviders(<ConnectorPauseBanner />);
    expect(await screen.findByRole("region", { name: "Una fonte è in pausa" })).toHaveTextContent("imap_inbox");
  });

  it("shows on the Oggi screen", async () => {
    useMockApi();
    pauseMockConnector("email", "IMAP login", "Controlla le credenziali IMAP nel file .env.");
    renderWithProviders(<TodayPage />);
    expect(await screen.findByRole("region", { name: "Una fonte è in pausa" })).toHaveTextContent("Email (avvisi)");
    expect(screen.getByRole("button", { name: "Riprendi Email (avvisi)" })).toBeEnabled();
  });
});
