import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatApp } from "./ChatApp";

const mocked = vi.hoisted(() => ({
  chat: {} as Record<string, unknown>,
  voice: {} as Record<string, unknown>,
}));

vi.mock("../chat/useChat", () => ({ useChat: () => mocked.chat }));
vi.mock("../voice/useVoiceRecorder", () => ({ useVoiceRecorder: () => mocked.voice }));

beforeAll(() => { Element.prototype.scrollIntoView = vi.fn(); });
afterEach(() => cleanup());

beforeEach(() => {
  mocked.chat = {
    conversations: [{ id: "conversation-1", title: "Voix", archived: false, updated_at: "now" }],
    archived: false,
    changeArchive: vi.fn(),
    selectedId: "conversation-1",
    selectConversation: vi.fn(),
    newConversation: vi.fn(),
    renameConversation: vi.fn(),
    archiveConversation: vi.fn(),
    messages: [],
    draft: "",
    setDraft: vi.fn(),
    state: "idle",
    runId: null,
    metrics: null,
    error: null,
    connectivity: "connected",
    engineAvailable: true,
    listLoading: false,
    messagesLoading: false,
    submit: vi.fn(),
    stop: vi.fn(),
    retry: vi.fn(),
    settingsOpen: false,
    showSettings: vi.fn(),
    runtimeInfo: null,
  };
  mocked.voice = {
    state: "idle",
    preview: "",
    setPreview: vi.fn(),
    error: null,
    levels: Array.from({ length: 20 }, () => 0.08),
    signalState: "waiting",
    microphoneLabel: null,
    elapsedSeconds: 0,
    start: vi.fn(),
    stop: vi.fn(),
    cancel: vi.fn(),
    send: vi.fn(),
  };
});

describe("voice capture feedback", () => {
  it("makes a pending microphone permission visible and cancellable", () => {
    mocked.voice = { ...mocked.voice, state: "requesting_permission" };
    render(<ChatApp />);

    expect(screen.getByText("Autorisation du microphone…")).toBeTruthy();
    expect(screen.getByText(/icône microphone près de l’adresse/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Annuler" }));
    expect(mocked.voice.cancel).toHaveBeenCalledOnce();
  });

  it("renders browser-compatible pixel bars and the real signal state", () => {
    mocked.voice = {
      ...mocked.voice,
      state: "recording",
      levels: [0, 1],
      signalState: "active",
      microphoneLabel: "Microphone Array",
      elapsedSeconds: 3,
    };
    const { container } = render(<ChatApp />);

    expect(screen.getByText("Voix détectée")).toBeTruthy();
    expect(screen.getByText(/Microphone Array/)).toBeTruthy();
    const bars = container.querySelectorAll(".voice-wave i");
    expect(bars).toHaveLength(2);
    expect((bars[0] as HTMLElement).style.height).toBe("8px");
    expect((bars[1] as HTMLElement).style.height).toBe("56px");
  });

  it("explains a permission request that never resolves", () => {
    mocked.voice = { ...mocked.voice, state: "error", error: "MIC_PERMISSION_TIMEOUT" };
    render(<ChatApp />);

    expect(screen.getByRole("alert").textContent).toContain("n’a pas répondu");
  });
});
