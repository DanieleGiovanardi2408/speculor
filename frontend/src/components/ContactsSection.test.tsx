import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePack } from "../api/hooks";
import { renderWithProviders, useMockApi } from "../test/utils";
import { ContactsSection } from "./ContactsSection";

afterEach(() => vi.unstubAllGlobals());

const calls = (spy: ReturnType<typeof useMockApi>, method: string, path: string) =>
  spy.mock.calls.filter(([url, init]) => (init?.method ?? "GET") === method && String(url).startsWith(path));

function Harness({ postingId }: { postingId: number }) {
  const { data } = usePack(postingId);
  if (!data) return <p>Caricamento</p>;
  return <ContactsSection postingId={postingId} contacts={data.contacts} drafts={data.drafts} />;
}

describe("ContactsSection: Cerca contatti", () => {
  it("finds public profiles, marks them da verificare with link, reason and source query", async () => {
    const spy = useMockApi();
    const user = userEvent.setup();
    renderWithProviders(<Harness postingId={101} />);
    const button = await screen.findByRole("button", { name: "Cerca contatti" });
    expect(screen.queryByText("da verificare")).not.toBeInTheDocument(); // manual contacts are trusted
    await user.click(button);
    await waitFor(() => expect(calls(spy, "POST", "/api/postings/101/contacts/find")).toHaveLength(1));

    const item = (await screen.findByText("Giulia Esempio")).closest("li")!;
    expect(within(item).getByText("da verificare")).toBeInTheDocument();
    expect(within(item).getByText(/Alumni Luiss: profilo fittizio/)).toBeInTheDocument();
    expect(within(item).getByRole("link", { name: /Profilo pubblico/ })).toHaveAttribute(
      "href",
      expect.stringMatching(/^https:\/\/example\.com\//),
    );
    expect(within(item).getByText(/"Luiss"/)).toBeInTheDocument();
    expect(await screen.findByText(/2 contatti trovati, da verificare/)).toBeInTheDocument();
    expect(screen.getByText(/Spesa stimata del mese: 0.03 € su 5 €/)).toBeInTheDocument();
  });

  it("marks a found contact as verified and deletes another", async () => {
    const spy = useMockApi();
    const user = userEvent.setup();
    renderWithProviders(<Harness postingId={101} />);
    await user.click(await screen.findByRole("button", { name: "Cerca contatti" }));
    const giulia = (await screen.findByText("Giulia Esempio")).closest("li")!;
    await user.click(within(giulia).getByRole("button", { name: "Segna come verificato Giulia Esempio" }));
    await waitFor(() => expect(within(giulia).queryByText("da verificare")).not.toBeInTheDocument());
    const [, init] = calls(spy, "PATCH", "/api/contacts/")[0]!;
    expect(JSON.parse(String(init!.body))).toEqual({ needs_review: false });

    await user.click(screen.getByRole("button", { name: "Elimina contatto Marco Fittizio" }));
    await waitFor(() => expect(screen.queryByText("Marco Fittizio")).not.toBeInTheDocument());
    expect(calls(spy, "DELETE", "/api/contacts/")).toHaveLength(1);
  });

  it("shows the error when the budget cap refuses the search", async () => {
    const spy = useMockApi();
    const user = userEvent.setup();
    renderWithProviders(<Harness postingId={101} />);
    await screen.findByRole("button", { name: "Cerca contatti" });
    spy.mockImplementationOnce(
      async () =>
        new Response(JSON.stringify({ detail: "Limite di spesa mensile per la ricerca contatti raggiunto." }), { status: 429 }),
    );
    await user.click(screen.getByRole("button", { name: "Cerca contatti" }));
    expect(await screen.findByText(/Ricerca non riuscita: .*Limite di spesa mensile/)).toBeInTheDocument();
  });
});
