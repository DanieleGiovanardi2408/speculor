import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { makePosting, renderWithProviders } from "../test/utils";
import { PostingRow } from "./PostingRow";

const sources = [
  { source: "greenhouse", url: "https://boards.example.com/acme/jobs/1", via: null, label: "Sito aziendale", listed: true },
  { source: "linkedin_jobs", url: "https://www.linkedin.com/jobs/view/1", via: null, label: "LinkedIn", listed: true },
  { source: "email", url: "https://www.linkedin.com/jobs/view/1", via: "linkedin_alert", label: "Email alert", listed: true },
];

describe("Posting sources", () => {
  it("shows source chips in the row and linked sources in the details", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ul>
        <PostingRow posting={makePosting({ sources })} />
      </ul>,
    );
    expect(screen.getByLabelText("Fonti: Sito aziendale, LinkedIn, Email alert")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Stage Data Analyst/ }));
    expect(screen.getByText("Fonti:")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Sito aziendale/ })).toHaveAttribute("href", sources[0]!.url);
    expect(screen.getByRole("link", { name: /^LinkedIn/ })).toHaveAttribute("href", sources[1]!.url);
    expect(screen.getByRole("link", { name: /Email alert/ })).toHaveAttribute("href", sources[2]!.url);
  });

  it("falls back to the posting's own source on older servers", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ul>
        <PostingRow posting={makePosting({ sources: undefined })} />
      </ul>,
    );
    await user.click(screen.getByRole("button", { name: /Stage Data Analyst/ }));
    expect(screen.getByText("Fonte:")).toBeInTheDocument();
  });
});
