import { readSSE } from "./sse";
import { ApiError, requestJson } from "./client";

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
  archived: boolean;
  updated_at: string;
}

export interface HealthSnapshot {
  status: "healthy" | "degraded";
  backend: { status: "ok" };
  database: { status: string };
  llm: { status: "ok" | "degraded" | "unavailable"; model_loaded: boolean };
}

export interface RuntimeInfo {
  runtime: { os: string; python: string };
  model: { name: string; sha256: string; context_size: number; llama_cpp_version: string };
  data_directory: string;
  frontend_serving_mode: string;
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
  return requestJson<ConversationRecord>("/v1/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: null }),
  });
}

export async function listConversations(includeArchived = false): Promise<ConversationRecord[]> {
  const query = new URLSearchParams({ limit: "50", offset: "0" });
  if (includeArchived) query.set("include_archived", "true");
  const body = await requestJson<{ items: ConversationRecord[] }>(`/v1/conversations?${query}`);
  return body.items;
}

export async function updateConversation(
  conversationId: string,
  update: Pick<ConversationRecord, "title"> | { archived: boolean },
): Promise<ConversationRecord> {
  return requestJson<ConversationRecord>(`/v1/conversations/${encodeURIComponent(conversationId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
}

export const loadHealth = () => requestJson<HealthSnapshot>("/v1/health");
export const loadRuntimeInfo = () => requestJson<RuntimeInfo>("/v1/runtime-info");

export async function loadConversationMessages(conversationId: string): Promise<ChatMessage[]> {
  const body = await requestJson<{ items: ChatMessage[] }>(
    `/v1/conversations/${encodeURIComponent(conversationId)}/messages?limit=500`,
  );
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

  if (!response.ok) {
    let payload: { error?: { code?: string; retryable?: boolean } } = {};
    try { payload = await response.json() as typeof payload; } catch { /* handled below */ }
    throw new ApiError(payload.error?.code ?? `HTTP_${response.status}`, Boolean(payload.error?.retryable), response.status);
  }

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
