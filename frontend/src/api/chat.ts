import { readSSE } from "./sse";

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  id?: string;
  role: ChatRole;
  content: string;
  sequence_no?: number;
  status?: "complete" | "streaming" | "interrupted" | "failed" | "deleted";
}

export interface ConversationRecord {
  id: string;
  title: string | null;
}

export interface RunMetrics {
  ttft_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  tokens_per_second: number | null;
  total_ms: number | null;
  finish_reason?: string | null;
  hit_max_tokens?: boolean;
  evaluated_prompt_tokens?: number | null;
  reused_prompt_tokens?: number | null;
  cache_reuse_observable?: boolean;
}

export interface ChatHandlers {
  onStarted: (runId: string) => void;
  onDelta: (text: string) => void;
  onMetrics: (metrics: RunMetrics) => void;
  onDone: () => void;
  onCancelled: () => void;
  onError: (code: string, retryable: boolean) => void;
}

export async function createConversation(): Promise<ConversationRecord> {
  const response = await fetch("/v1/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: null }),
  });
  if (!response.ok) throw new Error(`HTTP_${response.status}`);
  return response.json() as Promise<ConversationRecord>;
}

export async function loadConversationMessages(conversationId: string): Promise<ChatMessage[]> {
  const response = await fetch(
    `/v1/conversations/${encodeURIComponent(conversationId)}/messages?limit=500`,
  );
  if (!response.ok) throw new Error(`HTTP_${response.status}`);
  const body = await response.json() as { items: ChatMessage[] };
  return body.items;
}

export async function streamConversationTurn(
  conversationId: string,
  clientTurnId: string,
  content: string,
  signal: AbortSignal,
  handlers: ChatHandlers,
): Promise<void> {
  const response = await fetch(
    `/v1/conversations/${encodeURIComponent(conversationId)}/turns`,
    {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client_turn_id: clientTurnId,
      content,
      input_type: "text",
      generation: { max_tokens: 800 },
    }),
    signal,
    },
  );

  for await (const frame of readSSE(response)) {
    const data = frame.data as Record<string, unknown>;
    switch (frame.event) {
      case "run_started":
        handlers.onStarted(data.run_id as string);
        break;
      case "delta":
        handlers.onDelta(data.text as string);
        break;
      case "metrics":
        handlers.onMetrics(data as unknown as RunMetrics);
        break;
      case "done":
        handlers.onDone();
        return;
      case "cancelled":
        handlers.onCancelled();
        return;
      case "error":
        handlers.onError(data.code as string, Boolean(data.retryable));
        return;
    }
  }
  throw new Error("STREAM_ENDED_WITHOUT_TERMINAL_EVENT");
}

export async function cancelRun(runId: string): Promise<void> {
  const response = await fetch(`/v1/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
  });
  if (!response.ok && response.status !== 409) throw new Error("CANCEL_FAILED");
}
