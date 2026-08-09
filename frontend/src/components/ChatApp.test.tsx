import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { ChatApp } from "./ChatApp";

vi.mock("../chat/useChat", () => ({
  useChat: () => ({
    messages: [
      { role: "user", content: "Bonjour" },
      { role: "assistant", content: "" },
    ],
    draft: "",
    setDraft: vi.fn(),
    state: "preparing",
    runId: "run-1",
    metrics: null,
    error: null,
    submit: vi.fn(),
    stop: vi.fn(),
  }),
}));

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

describe("ChatApp preparation state", () => {
  it("shows the exact preparation indicator and keeps Stop accessible", () => {
    render(<ChatApp />);

    expect(screen.getByText("◌ Réponse en préparation…")).toBeTruthy();
    expect(screen.getByRole("button", { name: "■ Arrêter" })).toBeTruthy();
  });
});
