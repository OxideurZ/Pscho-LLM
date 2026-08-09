import { ApiError, requestJson } from "./client";

export type MemoryKind = "personal_fact" | "event" | "goal" | "preference" | "belief";
export type EpistemicStatus = "stated" | "interpretation" | "uncertain";

export type MemoryListItem = {
  id: string;
  content: string;
  kind: MemoryKind;
  epistemic_status: EpistemicStatus;
  status: "active" | "disabled" | "superseded";
  last_supported_at: string;
  supporting_source_count: number;
  user_locked: number;
};

export type MemorySource = {
  message_id: string;
  conversation_id: string;
  conversation_title: string | null;
  message_created_at: string;
  start_char: number;
  end_char: number;
  excerpt: string;
  input_type: "text" | "voice";
  source_role: string;
  hash_valid: boolean;
};

export type MemoryDetail = MemoryListItem & {
  observed_at: string;
  event_start_at: string | null;
  event_end_at: string | null;
  time_text: string | null;
  time_precision: string | null;
  sources: MemorySource[];
  entities: Array<{ id: string; display_name: string; entity_type: string; role: string }>;
  revisions: Array<{ id: string; revision_no: number; actor: string; created_at: string }>;
};

export type EntityItem = {
  id: string;
  display_name: string;
  entity_type: string;
  resolution_status: string;
  linked_memory_count: number;
};

export type MemoryStatus = {
  memory_enabled: boolean;
  background_idle_seconds: number;
  active_job_id: string | null;
  jobs: { pending: number; retry: number; failed: number };
  memories: { active: number; disabled: number };
};

export type BackfillInput = {
  conversation_ids?: string[];
  created_after?: string;
  created_before?: string;
  all_eligible?: boolean;
};

export type BackfillPreview = {
  scope_kind: "conversations" | "date_range" | "all_eligible";
  eligible_messages: number;
  conversations: number;
  created_after: string | null;
  created_before: string;
};

export type BackfillProgress = {
  id: string;
  status: "pending" | "complete" | "failed" | "cancelled";
  eligible: number;
  processed: number;
  pending: number;
  failed: number;
  memories_created: number;
};

export const listMemories = (status?: string, kind?: string) => {
  const query = new URLSearchParams();
  if (status) query.set("status", status);
  if (kind) query.set("kind", kind);
  return requestJson<{ items: MemoryListItem[] }>(`/v1/memory?${query.toString()}`);
};

export const getMemory = (id: string) => requestJson<MemoryDetail>(`/v1/memory/${id}`);

export const updateMemory = (
  id: string,
  update: Partial<Pick<MemoryDetail, "content" | "kind" | "epistemic_status">>,
) => requestJson<MemoryDetail>(`/v1/memory/${id}`, {
  method: "PATCH",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(update),
});

export const setMemoryEnabled = (id: string, enabled: boolean) =>
  requestJson<MemoryDetail>(`/v1/memory/${id}/${enabled ? "enable" : "disable"}`, {
    method: "POST",
  });

export async function deleteMemory(id: string): Promise<void> {
  const response = await fetch(`/v1/memory/${id}`, { method: "DELETE" });
  if (!response.ok) throw new ApiError("MEMORY_DELETE_FAILED", false, response.status);
}

export const listEntities = () => requestJson<{ items: EntityItem[] }>("/v1/entities");

export const renameEntity = (id: string, displayName: string) =>
  requestJson<EntityItem>(`/v1/entities/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name: displayName }),
  });

export const getMemoryStatus = () => requestJson<MemoryStatus>("/v1/memory/status");

export const previewBackfill = (input: BackfillInput) =>
  requestJson<{ preview: BackfillPreview; confirmation_required: true }>("/v1/memory/backfill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });

export const startBackfill = (input: BackfillInput) =>
  requestJson<{ backfill: BackfillProgress; confirmation_required: false }>("/v1/memory/backfill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...input, confirm: true }),
  });
