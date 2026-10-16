import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { makePosting, renderWithProviders, useMockApi } from "../test/utils";
import { PostingRow } from "./PostingRow";

afterEach(() => vi.unstubAllGlobals());

const calls = (spy: ReturnType<typeof useMockApi>, method: string, path: string) =>
  spy.mock.calls.filter(([url, init]) => (init?.method ?? "GET") === method && String(url).startsWith(path));

describe("Salta con motivo", () => {
  it("picks a quick reason, adds details, confirms with Enter and offers undo", async () => {
    const spy = useMockApi();
    const user = userEvent.setup();
    renderWithProviders(
      <ul>
        <PostingRow posting={makePosting({ id: 101 })} defaultOpen />
      </ul>,
    );
    await user.click(screen.getByRole("button", { name: /Salta/ }));
    const dialog = screen.getByRole("dialog", { name: "Salta annuncio" });
    await user.click(within(dialog).getByLabelText(/Sede/));
    await user.type(within(dialog).getByLabelText(/Dettagli/), "solo Londra{Enter}");
    await waitFor(() => expect(calls(spy, "PATCH", "/api/postings/101")).toHaveLength(1));
    const [, init] = calls(spy, "PATCH", "/api/postings/101")[0]!;
    expect(JSON.parse(String(init!.body))).toEqual({ user_status: "skipped", skip_reason: "Sede: solo Londra" });
    expect(await screen.findByText(/Saltato: “Stage Data Analyst” \(Sede: solo Londra\)/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Annulla" }));
    await waitFor(() => expect(calls(spy, "PATCH", "/api/postings/101")).toHaveLength(2));
    expect(JSON.parse(String(calls(spy, "PATCH", "/api/postings/101")[1]![1]!.body))).toEqual({ user_status: "todo" });
  });

  it("number keys choose the reason and Altro needs a word", async () => {
    useMockApi();
    const user = userEvent.setup();
    renderWithProviders(
      <ul>
        <PostingRow posting={makePosting({ id: 101 })} defaultOpen />
      </ul>,
    );
    await user.click(screen.getByRole("button", { name: /Salta/ }));
    await user.keyboard("6");
    expect(screen.getByLabelText(/Altro/)).toBeChecked();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Scrivi in due parole il motivo.")).toHaveAttribute("role", "alert");
  });

  it("shows the stored reason on skipped postings", () => {
    renderWithProviders(
      <ul>
        <PostingRow posting={makePosting({ user_status: "skipped", note: "[Saltato: Lingua] serve tedesco" })} />
      </ul>,
    );
    expect(screen.getByText(/: Lingua/)).toBeInTheDocument();
  });
});

describe("Ripristina con annulla", () => {
  it("restores a rejected posting, highlights the evidence and undoes from the toast", async () => {
    const spy = useMockApi();
    const user = userEvent.setup();
    renderWithProviders(
      <ul>
        <PostingRow
          posting={makePosting({
            id: 109,
            priority: null,
            score: null,
            filter_state: "rejected",
            filter_hits: [{ rule_id: "seniority", action: "reject", reason: "Ruolo senior", evidence: "5+ years", overridden: false }],
          })}
        />
      </ul>,
    );
    expect(screen.getByText("seniority")).toBeInTheDocument();
    expect(screen.getByText("Ruolo senior")).toBeInTheDocument();
    expect(screen.getByText("5+ years").closest("mark")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: /Ripristina/ }));
    await waitFor(() => expect(calls(spy, "POST", "/api/postings/109/override")).toHaveLength(1));
    await user.click(await screen.findByRole("button", { name: "Annulla" }));
    await waitFor(() => expect(calls(spy, "DELETE", "/api/postings/109/override")).toHaveLength(1));
    expect(await screen.findByText(/di nuovo tra gli scartati/)).toBeInTheDocument();
  });
});
