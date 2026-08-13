import { useCallback, useEffect, useRef, useState } from "react";
import { ConversationRecord, offloadRuntime } from "../api/chat";
import { useChat } from "../chat/useChat";
import { useVoiceRecorder } from "../voice/useVoiceRecorder";
import { MemoryPanel } from "./MemoryPanel";

const stateLabels = {
  idle: "Prêt",
  submitting: "Envoi…",
  preparing: "Réponse en préparation…",
  generating: "Réponse en cours",
  complete: "Réponse terminée",
  cancelled: "Réponse interrompue",
  error: "Erreur de réponse",
};

function titleFor(conversation: ConversationRecord) {
  return conversation.title?.trim() || "Nouvelle discussion";
}

export function ChatApp() {
  const chat = useChat();
  const end = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLElement>(null);
  const busy = ["submitting", "preparing", "generating"].includes(chat.state);
  const [autoSendVoice, setAutoSendVoice] = useState(true);
  const [offloading, setOffloading] = useState(false);
  const [view, setView] = useState<"chat" | "memory">(() => window.location.pathname === "/memory" ? "memory" : "chat");
  const submitVoice = useCallback(
    async (text: string, binding: { clientTurnId: string; conversationId: string }) =>
      chat.submitVoice(text, binding.clientTurnId, binding.conversationId),
    [chat],
  );
  const voice = useVoiceRecorder(submitVoice, autoSendVoice);
  const canFollow = () => {
    const node = scroller.current;
    return !node || node.scrollHeight - node.scrollTop - node.clientHeight < 96;
  };
  const offload = useCallback(async () => {
    const confirmed = window.confirm(
      "Arrêter Psych-local et libérer toute la mémoire GPU ? Vos conversations restent enregistrées.",
    );
    if (!confirmed) return;
    setOffloading(true);
    try {
      await offloadRuntime();
    } catch {
      setOffloading(false);
      window.alert("L’arrêt n’a pas pu être confirmé. Utilisez stop.ps1 pour libérer les modèles.");
    }
  }, []);

  useEffect(() => {
    if (canFollow()) end.current?.scrollIntoView({ behavior: chat.state === "generating" ? "auto" : "smooth" });
  }, [chat.messages, chat.state]);

  return (
    <main className="app-shell">
      <aside className="sidebar" aria-label="Conversations">
        <div className="brand"><span className="mark" aria-hidden="true">P</span><div><strong>Psych-local</strong><small>Local uniquement</small></div></div>
        <button className="new-conversation" type="button" onClick={() => { setView("chat"); void chat.newConversation(); }}>＋ Nouvelle conversation</button>
        <button className={`memory-link ${view === "memory" ? "active" : ""}`} type="button" onClick={() => { setView("memory"); window.history.pushState({}, "", "/memory"); }}>◇ Mémoire</button>
        <div className="conversation-heading"><span>{chat.archived ? "Archives" : "Conversations"}</span><button type="button" onClick={() => void chat.changeArchive(!chat.archived)}>{chat.archived ? "Actives" : "Archives"}</button></div>
        <nav className="conversation-list" aria-busy={chat.listLoading}>
          {chat.listLoading && <p className="muted">Chargement…</p>}
          {!chat.listLoading && chat.conversations.length === 0 && <p className="muted">{chat.archived ? "Aucune archive" : "Aucune conversation"}</p>}
          {chat.conversations.map((conversation) => (
            <ConversationItem
              key={conversation.id}
              conversation={conversation}
              active={conversation.id === chat.selectedId}
              archived={chat.archived}
              onSelect={() => { setView("chat"); void chat.selectConversation(conversation.id); }}
              onRename={(title) => void chat.renameConversation(conversation.id, title)}
              onArchive={() => void chat.archiveConversation(conversation.id)}
            />
          ))}
        </nav>
        <div className="sidebar-footer">
          <button className="offload-link" type="button" disabled={offloading} onClick={() => void offload()}>⏻ {offloading ? "Libération en cours…" : "Arrêter et libérer la VRAM"}</button>
          <button className="settings-link" type="button" onClick={() => { setView("chat"); chat.showSettings(); }}>⚙ Réglages</button>
        </div>
      </aside>

      <section className="chat-pane">
        {chat.connectivity !== "connected" && (
          <div className="connectivity" role="status">
            {chat.connectivity === "reconnecting" ? "Reconnexion au backend local…" : "Psych-local n’arrive plus à joindre son backend local."}
            {chat.connectivity === "unavailable" && <button type="button" onClick={() => void chat.retry()}>Réessayer</button>}
          </div>
        )}
        {chat.connectivity === "connected" && !chat.engineAvailable && <div className="engine-warning" role="status">Le moteur local n’est pas disponible. L’historique reste accessible.</div>}
        {offloading && <div className="offload-notice" role="status"><strong>Psych-local s’arrête.</strong> Les modèles sont déchargés de la mémoire. Relancez <code>start.ps1</code> pour reprendre.</div>}
        {view === "memory" ? <MemoryPanel onOpenConversation={(id) => { setView("chat"); void chat.selectConversation(id); }} /> : chat.settingsOpen ? <SettingsPanel runtimeInfo={chat.runtimeInfo} securityReadiness={chat.securityReadiness} engineAvailable={chat.engineAvailable} connectivity={chat.connectivity} /> : <>
        <header className="chat-header">
          <div><p className="eyebrow">Discussion</p><h1>{chat.selectedId ? titleFor(chat.conversations.find((item) => item.id === chat.selectedId) ?? { title: null } as ConversationRecord) : "Bienvenue"}</h1></div>
          {chat.selectedId && <span className={`status status-${chat.state}`}><i />{stateLabels[chat.state]}</span>}
        </header>

        <section ref={scroller} className="conversation" aria-live="polite" aria-busy={chat.messagesLoading}>
          {!chat.selectedId && <EmptyWelcome onCreate={() => void chat.newConversation()} />}
          {chat.selectedId && chat.messagesLoading && <p className="muted">Chargement des messages…</p>}
          {chat.selectedId && !chat.messagesLoading && chat.messages.length === 0 && <div className="empty"><p className="eyebrow">Nouvelle discussion</p><h2>Que souhaitez-vous explorer ?</h2><p>Les conversations sont enregistrées localement. N’utilisez que des données non sensibles.</p></div>}
          {chat.messages.map((message, index) => (
            <article className={`message ${message.role}`} key={message.id ?? index}>
              <span>{message.role === "user" ? "Vous" : "Psych-local"}</span>
              <p>{message.content || <em>◌ Réponse en préparation…</em>}</p>
              {message.status === "interrupted" && <small className="message-status status-interrupted">Interrompu</small>}
              {message.status === "failed" && <small className="message-status status-failed">La réponse a été interrompue par une erreur.</small>}
            </article>
          ))}
          {chat.error && <div className="error" role="alert">{chat.error}</div>}
          {chat.metrics && <aside className="metrics" aria-label="Métriques de génération"><span>Premier token <b>{chat.metrics.ttft_ms ?? "—"} ms</b></span><span>Débit <b>{chat.metrics.tokens_per_second ?? "—"} tok/s</b></span></aside>}
          <div ref={end} />
        </section>

        {busy && chat.runId && <button className="stop" type="button" onClick={() => void chat.stop()}>■ Arrêter</button>}
        <form className="composer" onSubmit={chat.submit}>
          <textarea aria-label="Message" placeholder={chat.selectedId ? "Écrire un message…" : "Créez une conversation pour commencer"} value={chat.draft} onChange={(event) => chat.setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void chat.submit(); } }} disabled={!chat.selectedId || busy} rows={2} />
          <button type="submit" disabled={!chat.selectedId || busy || !chat.draft.trim() || !chat.engineAvailable || chat.connectivity !== "connected"} aria-label="Envoyer">↑</button>
        </form>
        <label className="voice-auto-send"><input type="checkbox" checked={autoSendVoice} onChange={(event) => setAutoSendVoice(event.target.checked)} /> Envoyer automatiquement la dictée</label>
        <VoiceControls
          enabled={Boolean(chat.selectedId && chat.engineAvailable && chat.connectivity === "connected" && !busy)}
          selectedId={chat.selectedId}
          voice={voice}
        />
        <footer>Persisté localement · Données non sensibles uniquement avant la milestone sécurité</footer>
        </>}
      </section>
    </main>
  );
}

function VoiceControls({ enabled, selectedId, voice }: { enabled: boolean; selectedId: string | null; voice: ReturnType<typeof useVoiceRecorder> }) {
  if (voice.state === "transcript_ready") return <section className="voice-preview" aria-label="Aperçu de la dictée"><label htmlFor="voice-preview">Transcription prête — modifiez-la ou envoyez-la</label><textarea id="voice-preview" value={voice.preview} onChange={(event) => voice.setPreview(event.target.value)} rows={3} /><span><button className="voice-primary" type="button" onClick={() => void voice.send()}>Envoyer la dictée</button><button className="voice-secondary" type="button" onClick={() => void voice.cancel()}>Annuler</button></span></section>;
  if (voice.state === "requesting_permission") return <section className="voice-permission" aria-live="polite"><span className="voice-spinner" aria-hidden="true" /><div><strong>Autorisation du microphone…</strong><small>Acceptez la demande du navigateur. Si elle n’apparaît pas, vérifiez l’icône microphone près de l’adresse.</small></div><button className="voice-secondary" type="button" onClick={() => void voice.cancel()}>Annuler</button></section>;
  if (voice.state === "recording") return <RecordingPanel voice={voice} />;
  if (voice.state === "transcribing" || voice.state === "cancel_requested") return <section className="voice-progress" aria-live="polite"><span className="voice-spinner" aria-hidden="true" /><div><strong>{voice.state === "cancel_requested" ? "Annulation en cours" : "Transcription locale en cours"}</strong><small>{voice.state === "cancel_requested" ? "Le fichier audio est en cours de suppression." : "Votre dictée reste sur cette machine."}</small></div><button className="voice-secondary" type="button" onClick={() => void voice.cancel()}>Annuler</button></section>;
  return <section className="voice-idle"><div><strong>Dicter un message</strong><small>Microphone local · transcription hors ligne</small></div><button className="voice-record-button" type="button" disabled={!enabled || !selectedId} onClick={() => selectedId && void voice.start(selectedId)}><span aria-hidden="true">●</span> Commencer à dicter</button>{voice.error && <p className="voice-error" role="alert">{voiceErrorMessage(voice.error)}</p>}</section>;
}

function RecordingPanel({ voice }: { voice: ReturnType<typeof useVoiceRecorder> }) {
  const signal = {
    waiting: "Initialisation du signal…",
    active: "Voix détectée",
    silent: "Micro ouvert, mais aucun son détecté — parlez près du micro",
    muted: "Le micro ne fournit aucun signal — vérifiez Windows ou le bouton muet",
  }[voice.signalState];
  return <section className="voice-recording" aria-live="polite"><div className="voice-recording-head"><span className="recording-dot" aria-hidden="true" /><div><strong>Enregistrement en cours</strong><small>{formatDuration(voice.elapsedSeconds)} · {voice.microphoneLabel ?? "microphone local"}</small></div><span className="recording-limit">15 min max.</span></div><div className="voice-wave" aria-label="Niveau du microphone">{voice.levels.map((level, index) => <i key={index} style={{ height: `${Math.round(8 + level * 48)}px` }} />)}</div><p className={`voice-signal voice-signal-${voice.signalState}`}><span aria-hidden="true" />{signal}</p><div className="voice-actions"><button className="voice-primary" type="button" onClick={() => voice.stop()}>Terminer et transcrire</button><button className="voice-secondary" type="button" onClick={() => void voice.cancel()}>Annuler</button></div></section>;
}

function formatDuration(totalSeconds: number) { return `${String(Math.floor(totalSeconds / 60)).padStart(2, "0")}:${String(totalSeconds % 60).padStart(2, "0")}`; }

function voiceErrorMessage(code: string) {
  const messages: Record<string, string> = {
    MIC_PERMISSION_DENIED: "L’accès au microphone a été refusé. Autorisez-le dans votre navigateur puis réessayez.",
    MIC_PERMISSION_TIMEOUT: "Le navigateur n’a pas répondu à la demande du micro. Vérifiez l’autorisation près de la barre d’adresse, puis réessayez.",
    MIC_UNAVAILABLE: "Aucun microphone utilisable n’a été trouvé sur cette machine.",
    MIC_TRACK_UNAVAILABLE: "Le microphone a été ouvert, mais sa piste audio est indisponible.",
    MIC_DEVICE_BUSY: "Le microphone est déjà utilisé ou bloqué par Windows. Fermez l’autre application puis réessayez.",
    MIC_AUDIO_CONTEXT_BLOCKED: "Le navigateur a bloqué l’analyse audio. Cliquez à nouveau sur Commencer à dicter.",
    MIC_BROWSER_UNSUPPORTED: "Ce navigateur ne permet pas la capture du microphone. Utilisez Chrome, Edge ou Firefox à jour.",
    MIC_INSECURE_CONTEXT: "Le microphone exige une page locale sécurisée. Ouvrez Psych-local via start.ps1.",
    AUDIO_EMPTY: "Le navigateur n’a produit aucun son. Vérifiez le microphone sélectionné puis réessayez.",
    AUDIO_DECODE_FAILED: "Le navigateur n’a pas pu préparer cet enregistrement. Réessayez avec Chrome ou Edge à jour.",
    STT_NO_SPEECH: "Aucune parole exploitable n’a été détectée. Rapprochez-vous du micro et réessayez.",
    STT_UNAVAILABLE: "Le moteur local de transcription n’est pas disponible.",
  };
  return messages[code] ?? "La dictée n’a pas pu être traitée. Vous pouvez recommencer.";
}

function SettingsPanel({ runtimeInfo, securityReadiness, engineAvailable, connectivity }: { runtimeInfo: ReturnType<typeof useChat>["runtimeInfo"]; securityReadiness: ReturnType<typeof useChat>["securityReadiness"]; engineAvailable: boolean; connectivity: string }) {
  return <section className="settings-panel"><header className="chat-header"><div><p className="eyebrow">Réglages</p><h1>État local</h1></div></header><div className="settings-grid">
    <Info label="Backend" value={connectivity === "connected" ? "Connecté" : connectivity === "reconnecting" ? "Reconnexion" : "Indisponible"} />
    <Info label="Moteur LLM" value={engineAvailable ? "Disponible" : "Dégradé"} />
    <Info label="Modèle" value={runtimeInfo?.model.name ?? "Chargement…"} />
    <Info label="SHA modèle" value={runtimeInfo ? `${runtimeInfo.model.sha256.slice(0, 12)}…` : "—"} />
    <Info label="llama.cpp" value={runtimeInfo?.model.llama_cpp_version ?? "—"} />
    <Info label="Contexte" value={runtimeInfo ? `${runtimeInfo.model.context_size} tokens` : "—"} />
    <Info label="Données locales" value={runtimeInfo?.data_directory ?? "—"} />
    <Info label="Interface" value={runtimeInfo?.frontend_serving_mode ?? "—"} />
    <Info label="Security readiness" value={securityReadiness?.state ?? "Chargement…"} />
    <Info label="Base locale chiffrée" value={securityReadiness?.checks.encrypted_database?.status ?? "UNKNOWN"} />
    <Info label="Clé utilisateur protégée" value={securityReadiness?.checks.key_store?.status ?? "UNKNOWN"} />
    <Info label="Accès local protégé" value={securityReadiness?.checks.local_api_auth?.status ?? "UNKNOWN"} />
    <Info label="Backups chiffrés" value={securityReadiness?.checks.encrypted_backup?.status ?? "UNKNOWN"} />
  </div><p className="settings-note">Ce panneau n’affiche aucun contenu de conversation. Les réglages avancés restent hors scope de cette milestone.</p></section>;
}

function Info({ label, value }: { label: string; value: string }) { return <div className="info-row"><span>{label}</span><strong title={value}>{value}</strong></div>; }

function EmptyWelcome({ onCreate }: { onCreate: () => void }) {
  return <div className="empty"><p className="eyebrow">Psych-local</p><h2>Vos conversations, sur cette machine.</h2><p>Créez une discussion pour commencer. Pendant cette phase, utilisez uniquement des données artificielles ou non sensibles.</p><button type="button" className="new-conversation welcome-action" onClick={onCreate}>Nouvelle conversation</button></div>;
}

function ConversationItem({ conversation, active, archived, onSelect, onRename, onArchive }: { conversation: ConversationRecord; active: boolean; archived: boolean; onSelect: () => void; onRename: (title: string) => void; onArchive: () => void }) {
  const rename = () => {
    const value = window.prompt("Nom de la conversation", conversation.title ?? "");
    if (value !== null) onRename(value);
  };
  return <div className={`conversation-item ${active ? "active" : ""}`}><button type="button" className="conversation-open" aria-current={active ? "page" : undefined} onClick={onSelect}>{titleFor(conversation)}</button>{!archived && <span className="conversation-actions"><button type="button" aria-label={`Renommer ${titleFor(conversation)}`} onClick={rename}>✎</button><button type="button" aria-label={`Archiver ${titleFor(conversation)}`} onClick={onArchive}>⌫</button></span>}</div>;
}
