import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  cancelRun,
  ChatMessage,
  createConversation,
  loadConversationMessages,
  RunMetrics,
  streamConversationTurn,
} from "../api/chat";

export type ChatState =
  | "idle"
  | "loading"
  | "submitting"
  | "starting"
  | "preparing"
  | "generating"
  | "complete"
  | "cancelled"
  | "error";

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [state, setState] = useState<ChatState>("idle");
  const [runId, setRunId] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<RunMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const activeRun = useRef<string | null>(null);
  const conversationIdRef = useRef<string | null>(null);

  const refreshMessages = useCallback(async (id: string) => {
    setMessages(await loadConversationMessages(id));
  }, []);

  const ensureConversation = useCallback(async (): Promise<string> => {
    if (conversationIdRef.current) return conversationIdRef.current;
    const conversation = await createConversation();
    localStorage.setItem("psych-local-conversation-id", conversation.id);
    conversationIdRef.current = conversation.id;
    setConversationId(conversation.id);
    return conversation.id;
  }, []);

  const appendDelta = useCallback((text: string) => {
    setMessages((current) => {
      const next = [...current];
      const last = next.at(-1);
      if (last?.role === "assistant") next[next.length - 1] = { ...last, content: last.content + text };
      return next;
    });
  }, []);

  const finish = useCallback((nextState: ChatState) => {
    setState(nextState);
    setRunId(null);
    activeRun.current = null;
    controller.current = null;
  }, []);

  const submit = useCallback(async (event?: FormEvent) => {
    event?.preventDefault();
    const content = draft.trim();
    if (!content || ["submitting", "starting", "preparing", "generating"].includes(state)) return;

    const clientTurnId = crypto.randomUUID();
    setError(null);
    setMetrics(null);
    setState("submitting");

    try {
      const id = await ensureConversation();
      setMessages((current) => [
        ...current,
        { id: `pending-user-${clientTurnId}`, role: "user", content, status: "complete" },
        {
          id: `pending-assistant-${clientTurnId}`,
          role: "assistant",
          content: "",
          status: "streaming",
        },
      ]);
      setDraft("");
      const abortController = new AbortController();
      controller.current = abortController;
      setState("starting");
      await streamConversationTurn(id, clientTurnId, content, abortController.signal, {
        onStarted: (id) => {
          activeRun.current = id;
          setRunId(id);
          setState("preparing");
        },
        onDelta: (text) => {
          setState("generating");
          appendDelta(text);
        },
        onMetrics: setMetrics,
        onDone: () => {
          finish("complete");
          void refreshMessages(id);
        },
        onCancelled: () => {
          finish("cancelled");
          void refreshMessages(id);
        },
        onError: (code, retryable) => {
          setError(`${code}${retryable ? " — vous pouvez réessayer." : ""}`);
          finish("error");
          void refreshMessages(id);
        },
      });
    } catch (caught) {
      if ((caught as Error).name === "AbortError") return;
      setError("Connexion interrompue — vérifiez que le service local fonctionne.");
      finish("error");
    }
  }, [appendDelta, draft, ensureConversation, finish, refreshMessages, state]);

  const stop = useCallback(async () => {
    if (!activeRun.current) return;
    try {
      await cancelRun(activeRun.current);
    } catch {
      setError("L’arrêt n’a pas pu être confirmé.");
      controller.current?.abort();
      finish("error");
    }
  }, [finish]);

  useEffect(() => {
    let active = true;
    const resume = async () => {
      setState("loading");
      try {
        const stored = localStorage.getItem("psych-local-conversation-id");
        if (stored) {
          try {
            const persisted = await loadConversationMessages(stored);
            if (!active) return;
            conversationIdRef.current = stored;
            setConversationId(stored);
            setMessages(persisted);
            setState("idle");
            return;
          } catch {
            localStorage.removeItem("psych-local-conversation-id");
          }
        }
        const id = await ensureConversation();
        if (active) {
          await refreshMessages(id);
          setState("idle");
        }
      } catch {
        if (active) {
          setError("Impossible de charger la conversation locale.");
          setState("error");
        }
      }
    };
    void resume();
    return () => {
      active = false;
      const id = activeRun.current;
      if (id) {
        void fetch(`/v1/runs/${encodeURIComponent(id)}/cancel`, {
          method: "POST",
          keepalive: true,
        });
      }
      controller.current?.abort();
    };
  }, [ensureConversation, refreshMessages]);

  return {
    messages,
    draft,
    setDraft,
    state,
    runId,
    conversationId,
    metrics,
    error,
    submit,
    stop,
  };
}
