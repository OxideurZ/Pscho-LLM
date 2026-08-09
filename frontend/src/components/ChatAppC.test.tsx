import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatApp } from "./ChatApp";

const mocked = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock("../chat/useChat", () => ({ useChat: () => mocked.value }));

beforeAll(() => { Element.prototype.scrollIntoView = vi.fn(); });

beforeEach(() => {
  mocked.value = {
    conversations: [
      { id: "a", title: "Discussion A", archived: false, updated_at: "now" },
      { id: "b", title: "Discussion B", archived: false, updated_at: "now" },
    ], archived: false, changeArchive: vi.fn(), selectedId: "a", selectConversation: vi.fn(), newConversation: vi.fn(), renameConversation: vi.fn(), archiveConversation: vi.fn(),
    messages: [{ id: "m1", role: "assistant", content: "Réponse partielle", status: "interrupted" }], draft: "", setDraft: vi.fn(), state: "idle", runId: null, metrics: null, error: null,
    connectivity: "connected", engineAvailable: true, listLoading: false, messagesLoading: false, submit: vi.fn(), stop: vi.fn(), retry: vi.fn(), settingsOpen: false, showSettings: vi.fn(), runtimeInfo: null,
  };
});

describe("Milestone C conversation shell", () => {
  it("lists, selects and creates conversations without mixing their state", () => {
    render(<ChatApp />);
    fireEvent.click(screen.getByRole("button", { name: "Discussion B" }));
    fireEvent.click(screen.getAllByRole("button", { name: /Nouvelle conversation/ })[0]);
    expect(mocked.value.selectConversation).toHaveBeenCalledWith("b");
    expect(mocked.value.newConversation).toHaveBeenCalledOnce();
    expect(screen.getByText("Interrompu")).toBeTruthy();
  });

  it("makes backend and engine failures distinct and offers bounded retry", () => {
    mocked.value = { ...mocked.value, connectivity: "unavailable", engineAvailable: false };
    render(<ChatApp />);
    expect(screen.getByText("Psych-local n’arrive plus à joindre son backend local.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Réessayer" }));
    expect(mocked.value.retry).toHaveBeenCalledOnce();

    mocked.value = { ...mocked.value, connectivity: "connected" };
    render(<ChatApp />);
    expect(screen.getByText("Le moteur local n’est pas disponible. L’historique reste accessible.")).toBeTruthy();
  });

  it("maps a failed persisted answer to a safe user-facing message", () => {
    mocked.value = { ...mocked.value, messages: [{ id: "m2", role: "assistant", content: "", status: "failed" }] };
    render(<ChatApp />);
    expect(screen.getByText("La réponse a été interrompue par une erreur.")).toBeTruthy();
  });
});
