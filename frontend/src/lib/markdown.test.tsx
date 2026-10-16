import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./markdown";

describe("Markdown", () => {
  it("renders report markdown without ever injecting HTML", () => {
    const src = [
      "# Report",
      "",
      "Run **ok** with `code` and careers\\_page",
      "",
      "| Source | New |",
      "|---|---:|",
      "| lever | 3 |",
      "",
      "- **Intern** — Nebbia · [link](https://example.com/a)",
      "  - Why: fit",
      "- [bad](javascript:alert(1)) <img src=x onerror=alert(1)>",
    ].join("\n");
    const { container } = render(<Markdown source={src} />);
    expect(screen.getByRole("heading", { level: 2, name: "Report" })).toBeInTheDocument();
    expect(screen.getByText("ok").tagName).toBe("STRONG");
    expect(screen.getByText(/careers_page/)).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "3" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /link/ })).toHaveAttribute("href", "https://example.com/a");
    expect(screen.queryByRole("link", { name: /bad/ })).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(container.querySelectorAll("ul ul li")).toHaveLength(1);
  });
});
