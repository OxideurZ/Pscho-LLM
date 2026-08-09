import { readSSE } from "./sse";

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  role: ChatRole;
  content: string;
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

export async function streamChat(
  messages: ChatMessage[],
  signal: AbortSignal,
  handlers: ChatHandlers,
): Promise<void> {
  const response = await fetch("/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, generation: { max_tokens: 800 } }),
    signal,
  });

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
