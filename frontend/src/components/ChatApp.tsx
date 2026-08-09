import { useEffect, useRef } from "react";
import { useChat } from "../chat/useChat";

const stateLabels = {
  idle: "Prêt",
  loading: "Chargement…",
  submitting: "Envoi…",
  starting: "Démarrage du modèle…",
  preparing: "Réponse en préparation…",
  generating: "Réponse en cours",
  complete: "Réponse terminée",
  cancelled: "Réponse interrompue",
  error: "Service indisponible",
};

export function ChatApp() {
  const chat = useChat();
  const end = useRef<HTMLDivElement>(null);
  const busy = ["loading", "submitting", "starting", "preparing", "generating"].includes(chat.state);
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [chat.messages]);

  return (
    <main className="shell">
      <header>
        <span className="mark" aria-hidden="true">P</span>
        <div><h1>Psych-local</h1><p>Conversation locale et privée</p></div>
        <span className={`status status-${chat.state}`}><i />{stateLabels[chat.state]}</span>
      </header>

      <section className="conversation" aria-live="polite">
        {chat.messages.length === 0 && (
          <div className="empty">
            <p className="eyebrow">Espace local</p>
            <h2>Qu’est-ce qui vous occupe l’esprit&nbsp;?</h2>
            <p>Cette conversation est enregistrée localement. Utilisez uniquement des données de test non sensibles pendant Milestone B.</p>
          </div>
        )}
        {chat.messages.map((message, index) => (
          <article className={`message ${message.role}`} key={message.id ?? index}>
            <span>{message.role === "user" ? "Vous" : "Psych-local"}</span>
            <p>{message.content || <em>◌ Réponse en préparation…</em>}</p>
            {message.status && message.status !== "complete" && (
              <small className={`message-status status-${message.status}`}>{message.status}</small>
            )}
          </article>
        ))}
        {chat.error && <div className="error" role="alert">{chat.error}</div>}
        {chat.metrics && (
          <aside className="metrics" aria-label="Métriques de génération">
            <span>Premier token <b>{chat.metrics.ttft_ms ?? "—"} ms</b></span>
            <span>Débit <b>{chat.metrics.tokens_per_second ?? "—"} tok/s</b></span>
            <span>Durée <b>{chat.metrics.total_ms ?? "—"} ms</b></span>
          </aside>
        )}
        <div ref={end} />
      </section>

      {["preparing", "generating"].includes(chat.state) && chat.runId && (
        <button className="stop" type="button" onClick={chat.stop}>■ Arrêter</button>
      )}
      <form className="composer" onSubmit={chat.submit}>
        <textarea
          aria-label="Message"
          placeholder="Écrire un message…"
          value={chat.draft}
          onChange={(event) => chat.setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void chat.submit();
            }
          }}
          disabled={busy}
          rows={2}
        />
        <button type="submit" disabled={busy || !chat.draft.trim()} aria-label="Envoyer">↑</button>
      </form>
      <footer>Conversation persistée localement · Aucune mémoire entre conversations</footer>
    </main>
  );
}
