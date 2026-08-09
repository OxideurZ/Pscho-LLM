import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelRun,
  bootstrapSession,
  ChatMessage,
  ConversationRecord,
  createConversation,
  listConversations,
  loadConversationMessages,
  loadHealth,
  loadRuntimeInfo,
  RunMetrics,
  RuntimeInfo,
  streamConversationTurn,
  updateConversation,
} from "../api/chat";
import { ApiError, userMessageForError } from "../api/client";

export type ChatState = "idle" | "submitting" | "preparing" | "generating" | "complete" | "cancelled" | "error";
export type ConnectivityState = "connected" | "reconnecting" | "unavailable";

type ConversationRuntime = {
  state: ChatState;
  runId: string | null;
  metrics: RunMetrics | null;
  error: string | null;
};

const idleRuntime = (): ConversationRuntime => ({ state: "idle", runId: null, metrics: null, error: null });
const busyStates: ChatState[] = ["submitting", "preparing", "generating"];
const routePrefix = "/conversations/";

function routeConversationId(): string | null {
  const match = window.location.pathname.match(/^\/conversations\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

function stateFromMessages(messages: ChatMessage[]): ChatState {
  const last = messages.at(-1);
  if (last?.role !== "assistant") return "idle";
  if (last.status === "streaming") return last.content ? "generating" : "preparing";
  if (last.status === "interrupted") return "cancelled";
  if (last.status === "failed") return "error";
  return "idle";
}

export function useChat() {
  const [conversations, setConversations] = useState<ConversationRecord[]>([]);
  const [archived, setArchived] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(
    () => routeConversationId(),
  );
  const [messageCache, setMessageCache] = useState<Record<string, ChatMessage[]>>({});
  const [runtimes, setRuntimes] = useState<Record<string, ConversationRuntime>>({});
  const [draft, setDraft] = useState("");
  const [connectivity, setConnectivity] = useState<ConnectivityState>("reconnecting");
  const [engineAvailable, setEngineAvailable] = useState(false);
  const [runtimeInfo, setRuntimeInfo] = useState<RuntimeInfo | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(() => window.location.pathname === "/settings");
  const [listLoading, setListLoading] = useState(true);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const controllers = useRef<Record<string, AbortController>>({});
  const selectedIdRef = useRef<string | null>(selectedId);

  useEffect(() => { selectedIdRef.current = selectedId; }, [selectedId]);

  const currentRuntime = selectedId ? runtimes[selectedId] ?? idleRuntime() : idleRuntime();
  const messages = selectedId ? messageCache[selectedId] ?? [] : [];

  const refreshConversations = useCallback(async (includeArchived = archived) => {
    const items = await listConversations(includeArchived);
    setConversations(includeArchived ? items.filter((item) => item.archived) : items.filter((item) => !item.archived));
    return items;
  }, [archived]);

  const refreshMessages = useCallback(async (id: string) => {
    setMessagesLoading(true);
    try {
      const persisted = await loadConversationMessages(id);
      setMessageCache((current) => ({ ...current, [id]: persisted }));
      setRuntimes((current) => ({
        ...current,
        [id]: { ...(current[id] ?? idleRuntime()), state: stateFromMessages(persisted), runId: null, error: persisted.at(-1)?.status === "failed" ? userMessageForError("LLM_GENERATION_FAILED") : null },
      }));
      return persisted;
    } finally {
      setMessagesLoading(false);
    }
  }, []);

  const reconcile = useCallback(async () => {
    setConnectivity("reconnecting");
    const retryDelays = [0, 350, 900];
    let lastError: unknown;
    for (const delay of retryDelays) {
      if (delay) await new Promise((resolve) => window.setTimeout(resolve, delay));
      try {
        await bootstrapSession();
        const health = await loadHealth();
        setEngineAvailable(health.llm.status === "ok" && health.llm.model_loaded);
        void loadRuntimeInfo().then(setRuntimeInfo).catch(() => undefined);
        setConnectivity("connected");
        const availableConversations = await refreshConversations();
        const id = selectedIdRef.current;
        if (id && availableConversations.some((conversation) => conversation.id === id)) {
          await refreshMessages(id);
        } else if (id) {
          setSelectedId(null);
          if (window.location.pathname.startsWith(routePrefix)) window.history.replaceState({}, "", "/");
        }
        return;
      } catch (error) {
        lastError = error;
      }
    }
    setEngineAvailable(false);
    setConnectivity("unavailable");
    if (lastError instanceof ApiError && lastError.code !== "BACKEND_UNAVAILABLE") {
      // The app stays usable after the next explicit retry; no stale run is retained.
    }
  }, [refreshConversations, refreshMessages]);

  useEffect(() => {
    let mounted = true;
    const initialLoad = async () => {
      setListLoading(true);
      await reconcile();
      if (mounted) setListLoading(false);
    };
    void initialLoad();
    const healthTimer = window.setInterval(() => { void reconcile(); }, 15_000);
    const popState = () => {
      setSettingsOpen(window.location.pathname === "/settings");
      void selectConversation(routeConversationId(), false);
    };
    window.addEventListener("popstate", popState);
    return () => {
      mounted = false;
      window.clearInterval(healthTimer);
      window.removeEventListener("popstate", popState);
    };
  // selectConversation is deliberately declared below and only invoked after mount.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reconcile]);

  const selectConversation = useCallback(async (id: string | null, push = true) => {
    setSettingsOpen(false);
    setSelectedId(id);
    if (id) {
      if (push && window.location.pathname !== `${routePrefix}${encodeURIComponent(id)}`) {
        window.history.pushState({}, "", `${routePrefix}${encodeURIComponent(id)}`);
      }
      try { await refreshMessages(id); } catch { setConnectivity("unavailable"); }
    } else if (push) {
      window.history.pushState({}, "", "/");
    }
  }, [refreshMessages]);

  const showSettings = useCallback(() => {
    setSettingsOpen(true);
    setSelectedId(null);
    window.history.pushState({}, "", "/settings");
  }, []);

  const newConversation = useCallback(async () => {
    try {
      const conversation = await createConversation();
      if (!archived) setConversations((current) => [conversation, ...current]);
      await selectConversation(conversation.id);
      requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>("textarea[aria-label='Message']")?.focus());
    } catch (error) {
      setConnectivity("unavailable");
      throw error;
    }
  }, [archived, selectConversation]);

  const renameConversation = useCallback(async (id: string, title: string) => {
    const updated = await updateConversation(id, { title: title.trim() || null });
    setConversations((current) => current.map((item) => item.id === id ? updated : item));
  }, []);

  const archiveConversation = useCallback(async (id: string) => {
    await updateConversation(id, { archived: true });
    setConversations((current) => current.filter((item) => item.id !== id));
    if (selectedIdRef.current === id) await selectConversation(null);
  }, [selectConversation]);

  const changeArchive = useCallback(async (next: boolean) => {
    setArchived(next);
    setListLoading(true);
    try {
      const items = await listConversations(next);
      setConversations(next ? items.filter((item) => item.archived) : items.filter((item) => !item.archived));
    } catch { setConnectivity("unavailable"); }
    finally { setListLoading(false); }
  }, []);

  const setRuntime = useCallback((id: string, update: Partial<ConversationRuntime>) => {
    setRuntimes((current) => ({ ...current, [id]: { ...(current[id] ?? idleRuntime()), ...update } }));
  }, []);

  const appendDelta = useCallback((id: string, text: string) => {
    setMessageCache((current) => {
      const messages = [...(current[id] ?? [])];
      const last = messages.at(-1);
      if (last?.role === "assistant") messages[messages.length - 1] = { ...last, content: last.content + text, status: "streaming" };
      return { ...current, [id]: messages };
    });
  }, []);

  const submit = useCallback(async (
    event?: FormEvent,
    suppliedContent?: string,
    inputType: "text" | "voice" = "text",
    suppliedClientTurnId?: string,
    suppliedConversationId?: string,
  ) => {
    event?.preventDefault();
    const content = (suppliedContent ?? draft).trim();
    const id = suppliedConversationId ?? selectedIdRef.current;
    const targetRuntime = id ? runtimes[id] ?? idleRuntime() : idleRuntime();
    if (!content || !id || busyStates.includes(targetRuntime.state) || !engineAvailable) return;
    const clientTurnId = suppliedClientTurnId ?? crypto.randomUUID();
    const abortController = new AbortController();
    controllers.current[id] = abortController;
    if (suppliedContent === undefined) setDraft("");
    setRuntime(id, { state: "submitting", error: null, metrics: null, runId: null });
    setMessageCache((current) => ({ ...current, [id]: [
      ...(current[id] ?? []),
      { id: `pending-user-${clientTurnId}`, role: "user", content, status: "complete" },
      { id: `pending-assistant-${clientTurnId}`, role: "assistant", content: "", status: "streaming" },
    ] }));
    try {
      await streamConversationTurn(id, clientTurnId, content, abortController.signal, {
        onStarted: (runId) => setRuntime(id, { state: "preparing", runId }),
        onDelta: (text) => { appendDelta(id, text); setRuntime(id, { state: "generating" }); },
        onMetrics: (metrics) => setRuntime(id, { metrics }),
        onDone: () => { setRuntime(id, { state: "complete", runId: null }); void refreshMessages(id); void refreshConversations(); },
        onCancelled: () => { setRuntime(id, { state: "cancelled", runId: null }); void refreshMessages(id); },
        onError: (code) => { setRuntime(id, { state: "error", runId: null, error: userMessageForError(code) }); void refreshMessages(id); },
      }, inputType);
    } catch (error) {
      if ((error as Error).name === "AbortError") return;
      const code = error instanceof ApiError ? error.code : "BACKEND_UNAVAILABLE";
      setRuntime(id, { state: "error", runId: null, error: userMessageForError(code) });
      if (code === "BACKEND_UNAVAILABLE") setConnectivity("unavailable");
      void refreshMessages(id).catch(() => undefined);
    } finally {
      delete controllers.current[id];
    }
  }, [appendDelta, draft, engineAvailable, refreshConversations, refreshMessages, runtimes, setRuntime]);

  const submitVoice = useCallback(
    async (content: string, clientTurnId: string, conversationId: string) => {
      await submit(undefined, content, "voice", clientTurnId, conversationId);
    },
    [submit],
  );

  const stop = useCallback(async () => {
    const id = selectedIdRef.current;
    if (!id || !currentRuntime.runId) return;
    try { await cancelRun(currentRuntime.runId); }
    catch { setRuntime(id, { state: "error", error: "L’arrêt n’a pas pu être confirmé." }); }
  }, [currentRuntime.runId, setRuntime]);

  return useMemo(() => ({
    conversations, archived, changeArchive, selectedId, selectConversation, newConversation, renameConversation, archiveConversation,
    messages, draft, setDraft, state: currentRuntime.state, runId: currentRuntime.runId, metrics: currentRuntime.metrics,
    error: currentRuntime.error, connectivity, engineAvailable, listLoading, messagesLoading, submit, submitVoice, stop, retry: reconcile,
    settingsOpen, showSettings, runtimeInfo,
  }), [archiveConversation, archived, changeArchive, connectivity, conversations, currentRuntime, draft, engineAvailable, listLoading, messages, messagesLoading, newConversation, reconcile, renameConversation, selectConversation, selectedId, settingsOpen, showSettings, stop, submit, submitVoice, runtimeInfo]);
}
