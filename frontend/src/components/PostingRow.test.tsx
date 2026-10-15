import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { makePosting, renderWithProviders } from "../test/utils";
import { PostingRow } from "./PostingRow";

describe("PostingRow", () => {
  it("expands with the keyboard and exposes aria-expanded", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ul>
        <PostingRow posting={makePosting()} />
      </ul>,
    );
    const toggle = screen.getByRole("button", { name: /Stage Data Analyst/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    toggle.focus();
    await user.keyboard("{Enter}");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("SQL e Python richiesti.")).toBeVisible();
    const link = screen.getByRole("link", { name: /Apri annuncio/ });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.getByLabelText("Nota personale")).toBeInTheDocument();
  });

  it("shows rule, reason, evidence and Ripristina for rejected postings", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ul>
        <PostingRow
          posting={makePosting({
            priority: null,
            score: null,
            filter_state: "rejected",
            filter_hits: [{ rule_id: "seniority", action: "reject", reason: "Ruolo senior", evidence: "5+ years", overridden: false }],
          })}
        />
      </ul>,
    );
    await user.click(screen.getByRole("button", { name: /Stage Data Analyst/ }));
    expect(screen.getByText("seniority")).toBeInTheDocument();
    expect(screen.getByText("Ruolo senior")).toBeInTheDocument();
    expect(screen.getByText(/5\+ years/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ripristina/ })).toBeInTheDocument();
  });
});
