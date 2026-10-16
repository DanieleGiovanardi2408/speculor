import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { isTypingTarget, useShortcuts } from "./shortcuts";

function Harness({ onJ, onGo }: { onJ: () => void; onGo: () => void }) {
  useShortcuts({ j: onJ, "g o": onGo });
  return (
    <div>
      <label htmlFor="q">Cerca</label>
      <input id="q" />
      <label htmlFor="t">Nota</label>
      <textarea id="t" />
      <label htmlFor="s">Stato</label>
      <select id="s">
        <option>a</option>
      </select>
      <button type="button">Pulsante</button>
    </div>
  );
}

describe("useShortcuts", () => {
  it("fires single keys and g-sequences outside fields", async () => {
    const user = userEvent.setup();
    const onJ = vi.fn();
    const onGo = vi.fn();
    render(<Harness onJ={onJ} onGo={onGo} />);
    await user.keyboard("j");
    await user.keyboard("go");
    expect(onJ).toHaveBeenCalledTimes(1);
    expect(onGo).toHaveBeenCalledTimes(1);
  });

  it("does not fire while typing in inputs, textareas or selects", async () => {
    const user = userEvent.setup();
    const onJ = vi.fn();
    const onGo = vi.fn();
    render(<Harness onJ={onJ} onGo={onGo} />);
    await user.type(screen.getByLabelText("Cerca"), "jgo");
    await user.type(screen.getByLabelText("Nota"), "j go");
    screen.getByLabelText("Stato").focus();
    await user.keyboard("j");
    expect(onJ).not.toHaveBeenCalled();
    expect(onGo).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Cerca")).toHaveValue("jgo");
  });

  it("ignores keys with modifiers and while a modal is open", async () => {
    const user = userEvent.setup();
    const onJ = vi.fn();
    render(<Harness onJ={onJ} onGo={vi.fn()} />);
    await user.keyboard("{Control>}j{/Control}");
    const modal = document.createElement("div");
    modal.setAttribute("aria-modal", "true");
    document.body.appendChild(modal);
    await user.keyboard("j");
    modal.remove();
    expect(onJ).not.toHaveBeenCalled();
  });

  it("classifies typing targets", () => {
    const cb = document.createElement("input");
    cb.type = "checkbox";
    expect(isTypingTarget(cb)).toBe(false);
    expect(isTypingTarget(document.createElement("input"))).toBe(true);
    expect(isTypingTarget(document.createElement("button"))).toBe(false);
  });
});
