import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePack, useVerifyPack } from "../api/hooks";
import type { Verification } from "../api/types";
import { renderWithProviders, useMockApi } from "../test/utils";
import { FactCheckPanel, textLabel } from "./FactCheckPanel";

afterEach(() => vi.unstubAllGlobals());

const verification: Verification = {
  version: 1,
  status: "ok",
  verified_at: "2026-10-15T07:00:00Z",
  provider: "groq",
  model: "openai/gpt-oss-120b",
  error: null,
  facts_source: "facts.example.yaml",
  facts_version: "2026-10-01",
  texts_hash: "x",
  counts: { supported: 1, unsupported: 2, check: 1, total: 4 },
  claims: [
    { text_id: "cover_letter", claim: "I won 17 awards.", fact_id: null, verdict: "unsupported", source: "precheck", reason: "numero non presente nei fatti: 17" },
    { text_id: "form_answers.2", claim: "I am a native English speaker.", fact_id: "identity.languages.english.level", verdict: "unsupported", source: "precheck", reason: null },
    { text_id: "linkedin_message", claim: "[CHECK: team]", fact_id: null, verdict: "check", source: "llm", reason: null },
    { text_id: "cover_letter", claim: "I built tinybench.", fact_id: "projects.tinybench.name", verdict: "supported", source: "llm", reason: null },
  ],
  drafter_claims: [],
};

describe("FactCheckPanel", () => {
  it("lists claims with fact ids, marks unsupported ones and shows counts", async () => {
    const user = userEvent.setup();
    render(<FactCheckPanel verification={verification} stale={false} pending={false} onVerify={() => {}} />);
    expect(screen.getByRole("heading", { name: "Verifica fatti" })).toBeInTheDocument();
    const summary = screen.getByRole("list", { name: "Riepilogo della verifica" });
    expect(summary).toHaveTextContent("1 supportate");
    expect(summary).toHaveTextContent("2 non supportate");
    expect(summary).toHaveTextContent("1 da verificare");

    const list = screen.getByRole("list", { name: "Affermazioni verificate" }); // open: there are problems
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(within(rows[0]!).getByText("Non supportata")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("non supportato")).toHaveClass("text-danger-ink");
    expect(within(rows[0]!).getByText("Lettera")).toBeInTheDocument();
    expect(within(rows[0]!).getByText(/numero non presente/)).toBeInTheDocument();
    expect(within(rows[1]!).getByText("identity.languages.english.level")).toBeInTheDocument();
    expect(within(rows[3]!).getByText("projects.tinybench.name")).toBeInTheDocument();
    expect(within(rows[3]!).queryByText("non supportato")).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Solo quelle da rivedere"));
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
  });

  it("warns when texts changed and offers a first check when there is none", async () => {
    const user = userEvent.setup();
    const onVerify = vi.fn();
    const { rerender } = render(<FactCheckPanel verification={verification} stale pending={false} onVerify={onVerify} />);
    expect(screen.getByText("Testi modificati dopo la verifica")).toBeInTheDocument();
    rerender(<FactCheckPanel verification={null} stale={false} pending={false} onVerify={onVerify} />);
    expect(screen.getByText(/Nessuna verifica per questo pacchetto/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Verifica ora/ }));
    expect(onVerify).toHaveBeenCalledOnce();
    rerender(<FactCheckPanel verification={null} stale={false} pending onVerify={onVerify} />);
    expect(screen.getByRole("button", { name: /Verifica in corso/ })).toBeDisabled();
  });

  it("explains a pre-check-only result", () => {
    render(<FactCheckPanel verification={{ ...verification, status: "precheck_only", error: "rate_limit" }} stale={false} pending={false} onVerify={() => {}} />);
    expect(screen.getByText(/Solo controllo automatico/)).toHaveTextContent("limite di richieste");
  });

  it("labels texts in Italian", () => {
    expect(textLabel("cover_letter")).toBe("Lettera");
    expect(textLabel("form_answers.3")).toBe("Risposta 3");
    expect(textLabel("linkedin_connection_note")).toBe("Nota di collegamento LinkedIn");
  });
});

function Harness() {
  const pack = usePack(101);
  const verify = useVerifyPack(101);
  if (!pack.data) return <p>Caricamento</p>;
  return (
    <FactCheckPanel
      verification={pack.data.verification}
      stale={pack.data.verification_stale}
      pending={verify.isPending}
      onVerify={() => verify.mutate(pack.data!.id)}
    />
  );
}

describe("Verifica di nuovo", () => {
  it("posts to the verify endpoint and shows the new result", async () => {
    const spy = useMockApi();
    const user = userEvent.setup();
    renderWithProviders(<Harness />);
    expect(await screen.findByRole("heading", { name: "Verifica fatti" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Riepilogo della verifica" })).toHaveTextContent("1 non supportate");
    await user.click(screen.getByRole("button", { name: /Verifica di nuovo/ }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([url, init]) => init?.method === "POST" && String(url).endsWith("/api/packs/1/verify"))).toBe(true),
    );
    await waitFor(() => expect(screen.getByRole("list", { name: "Riepilogo della verifica" })).toHaveTextContent("0 non supportate"));
  });
});
