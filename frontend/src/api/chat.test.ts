import { afterEach, describe, expect, it, vi } from "vitest";
import { loadConversationMessages, streamConversationTurn } from "./chat";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("persistent conversation API", () => {
  it("reloads ordered persisted message states", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [
            { id: "m1", sequence_no: 1, role: "user", content: "Hello", status: "complete" },
            {
              id: "m2",
              sequence_no: 2,
              role: "assistant",
              content: "Partial",
              status: "interrupted",
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const messages = await loadConversationMessages("conv/a");

    expect(fetchMock).toHaveBeenCalledWith("/v1/conversations/conv%2Fa/messages?limit=500");
    expect(messages.map(({ sequence_no, status }) => ({ sequence_no, status }))).toEqual([
      { sequence_no: 1, status: "complete" },
      { sequence_no: 2, status: "interrupted" },
    ]);
  });

  it("sends a stable client turn id and handles the terminal stream", async () => {
    const stream = [
      'event: run_started\ndata: {"run_id":"run-1"}\n\n',
      'event: delta\ndata: {"text":"Answer"}\n\n',
      "event: done\ndata: {}\n\n",
    ].join("");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const handlers = {
      onStarted: vi.fn(),
      onDelta: vi.fn(),
      onMetrics: vi.fn(),
      onDone: vi.fn(),
      onCancelled: vi.fn(),
      onError: vi.fn(),
    };

    await streamConversationTurn(
      "conv-1",
      "019c0000-0000-7000-8000-000000000001",
      "Question",
      new AbortController().signal,
      handlers,
    );

    const [, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(request.body as string)).toMatchObject({
      client_turn_id: "019c0000-0000-7000-8000-000000000001",
      content: "Question",
      input_type: "text",
    });
    expect(handlers.onStarted).toHaveBeenCalledWith("run-1");
    expect(handlers.onDelta).toHaveBeenCalledWith("Answer");
    expect(handlers.onDone).toHaveBeenCalledOnce();
    expect(handlers.onError).not.toHaveBeenCalled();
  });
});
