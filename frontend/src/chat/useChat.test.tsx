import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useChat } from "./useChat";

const api = vi.hoisted(() => ({
  bootstrapSession: vi.fn(),
  cancelRun: vi.fn(),
  createConversation: vi.fn(),
  listConversations: vi.fn(),
  loadConversationMessages: vi.fn(),
  loadHealth: vi.fn(),
  loadRuntimeInfo: vi.fn(),
  loadSecurityReadiness: vi.fn(),
  loadSession: vi.fn(),
  streamConversationTurn: vi.fn(),
  updateConversation: vi.fn(),
}));

vi.mock("../api/chat", () => api);

describe("chat connection lifecycle", () => {
  let heartbeat: TimerHandler | undefined;

  beforeEach(() => {
    heartbeat = undefined;
    window.history.replaceState({}, "", "/#bootstrap=launch-token");
    vi.spyOn(window, "setInterval").mockImplementation((handler, timeout) => {
      if (timeout === 15_000) heartbeat = handler;
      return 1;
    });
    vi.spyOn(window, "clearInterval").mockImplementation(() => undefined);
    api.bootstrapSession.mockResolvedValue({ status: "authenticated" });
    api.loadSession.mockResolvedValue({ status: "authenticated" });
    api.loadHealth.mockResolvedValue({
      status: "healthy",
      backend: { status: "ok" },
      database: { status: "ready" },
      llm: { status: "ok", model_loaded: true },
    });
    api.listConversations.mockResolvedValue([]);
    api.loadRuntimeInfo.mockResolvedValue(null);
    api.loadSecurityReadiness.mockResolvedValue(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("bootstraps once and keeps a healthy background check invisible", async () => {
    const { result } = renderHook(() => useChat());

    await waitFor(() => expect(api.bootstrapSession).toHaveBeenCalled());
    await waitFor(() => expect(api.loadHealth).toHaveBeenCalled());
    await waitFor(() => expect(api.loadSession).toHaveBeenCalled());
    await waitFor(() => expect(api.listConversations).toHaveBeenCalled());
    await waitFor(() => expect(result.current.connectivity).toBe("connected"));
    await waitFor(() => expect(result.current.listLoading).toBe(false));
    expect(api.bootstrapSession).toHaveBeenCalledTimes(1);
    expect(api.bootstrapSession).toHaveBeenCalledWith("launch-token");
    expect(window.location.hash).toBe("");

    expect(heartbeat).toBeTypeOf("function");
    act(() => { (heartbeat as () => void)(); });
    expect(result.current.connectivity).toBe("connected");
    await waitFor(() => expect(api.loadSession).toHaveBeenCalledTimes(2));
    expect(api.bootstrapSession).toHaveBeenCalledTimes(1);
    expect(result.current.connectivity).toBe("connected");
  });
});
