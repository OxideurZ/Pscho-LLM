import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { cancelRun, ChatMessage, RunMetrics, streamChat } from "../api/chat";

export type ChatState =
  | "idle"
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
  const controller = useRef<AbortController | null>(null);
  const activeRun = useRef<string | null>(null);

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

    const history: ChatMessage[] = [...messages, { role: "user", content }];
    setMessages([...history, { role: "assistant", content: "" }]);
    setDraft("");
    setError(null);
    setMetrics(null);
    setState("submitting");
    const abortController = new AbortController();
    controller.current = abortController;

    try {
      setState("starting");
      await streamChat(history, abortController.signal, {
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
        onDone: () => finish("complete"),
        onCancelled: () => finish("cancelled"),
        onError: (code, retryable) => {
          setError(`${code}${retryable ? " — vous pouvez réessayer." : ""}`);
          finish("error");
        },
      });
    } catch (caught) {
      if ((caught as Error).name === "AbortError") return;
      setError("Connexion interrompue — vérifiez que le service local fonctionne.");
      finish("error");
    }
  }, [appendDelta, draft, finish, messages, state]);

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

  useEffect(() => () => {
    const id = activeRun.current;
    if (id) void fetch(`/v1/runs/${encodeURIComponent(id)}/cancel`, { method: "POST", keepalive: true });
    controller.current?.abort();
  }, []);

  return { messages, draft, setDraft, state, runId, metrics, error, submit, stop };
}
