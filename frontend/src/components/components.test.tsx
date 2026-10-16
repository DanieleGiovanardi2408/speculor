import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { PriorityBadge, SimilarityBadge } from "./Badge";
import { CopyButton } from "./CopyButton";
import { Dialog } from "./Dialog";
import { Tabs } from "./Tabs";

describe("PriorityBadge", () => {
  it("conveys priority with text, not color alone", () => {
    render(<PriorityBadge priority="Alta" />);
    expect(screen.getByText("Alta")).toBeInTheDocument();
    expect(screen.getByText(/Priorità/)).toHaveClass("sr-only");
  });
  it("labels unscored postings", () => {
    render(<PriorityBadge priority={null} />);
    expect(screen.getByText("Non valutato")).toBeInTheDocument();
  });
});

describe("SimilarityBadge", () => {
  it("shows the value with an accessible label", () => {
    render(<SimilarityBadge value={0.714} />);
    expect(screen.getByText("Affinità 0,71")).toBeInTheDocument();
    expect(screen.getByText("Affinità con il profilo: 71 su 100")).toHaveClass("sr-only");
  });
  it("renders nothing when not ranked", () => {
    const { container } = render(<SimilarityBadge value={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("CopyButton", () => {
  it("copies text and announces Copiato", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<CopyButton text="Ciao Marta" />);
    await user.click(screen.getByRole("button", { name: /Copia/ }));
    expect(writeText).toHaveBeenCalledWith("Ciao Marta");
    expect(screen.getByRole("button", { name: /Copiato/ })).toBeInTheDocument();
    const live = document.querySelector('[aria-live="polite"]');
    expect(live).toHaveTextContent("Copiato");
  });
});

function DialogHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Apri</button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Segna come inviata">
        <label htmlFor="x">Canale</label>
        <input id="x" />
        <button>Conferma</button>
      </Dialog>
    </>
  );
}

describe("Dialog", () => {
  it("is modal, traps focus, closes on Escape and restores focus", async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);
    const opener = screen.getByRole("button", { name: "Apri" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Segna come inviata" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});

describe("Tabs", () => {
  it("supports arrow-key navigation", async () => {
    const user = userEvent.setup();
    function H() {
      const [v, setV] = useState<"a" | "b">("a");
      return (
        <Tabs label="Stato" value={v} onChange={setV} tabs={[{ key: "a", label: "Da approvare" }, { key: "b", label: "Approvate" }]}>
          <p>Pannello {v}</p>
        </Tabs>
      );
    }
    render(<H />);
    await user.tab();
    expect(screen.getByRole("tab", { name: "Da approvare" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    const second = screen.getByRole("tab", { name: "Approvate" });
    expect(second).toHaveFocus();
    expect(second).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Pannello b");
  });
});
