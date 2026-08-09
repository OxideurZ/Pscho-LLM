import { useCallback, useEffect, useState } from "react";
import {
  BackfillInput,
  BackfillPreview,
  deleteMemory,
  EntityItem,
  getMemory,
  getMemoryStatus,
  listEntities,
  listMemories,
  MemoryDetail,
  MemoryListItem,
  MemoryStatus,
  previewBackfill,
  renameEntity,
  setMemoryEnabled,
  startBackfill,
  updateMemory,
} from "../api/memory";
import { ConversationRecord, listConversations } from "../api/chat";

type Section = "memories" | "entities" | "processing";
const kindLabels: Record<string, string> = {
  personal_fact: "Fait personnel", event: "Événement", goal: "Objectif",
  preference: "Préférence", belief: "Croyance",
};
const statusLabels: Record<string, string> = {
  stated: "Déclaré", interpretation: "Interprétation", uncertain: "Incertain",
};

export function MemoryPanel({ onOpenConversation }: { onOpenConversation: (id: string) => void }) {
  const [section, setSection] = useState<Section>("memories");
  const [items, setItems] = useState<MemoryListItem[]>([]);
  const [entities, setEntities] = useState<EntityItem[]>([]);
  const [status, setStatus] = useState<MemoryStatus | null>(null);
  const [selected, setSelected] = useState<MemoryDetail | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationRecord[]>([]);
  const [backfillScope, setBackfillScope] = useState<"all" | "conversations" | "date">("all");
  const [backfillIds, setBackfillIds] = useState<string[]>([]);
  const [backfillAfter, setBackfillAfter] = useState("");
  const [backfillBefore, setBackfillBefore] = useState("");
  const [backfillPreview, setBackfillPreview] = useState<BackfillPreview | null>(null);
  const [backfillMessage, setBackfillMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [memories, knownEntities, processing, knownConversations] = await Promise.all([
        listMemories(statusFilter || undefined, kindFilter || undefined),
        listEntities(), getMemoryStatus(), listConversations(true),
      ]);
      setItems(memories.items); setEntities(knownEntities.items); setStatus(processing);
      setConversations(knownConversations);
    } catch { setError("La mémoire locale n’a pas pu être chargée."); }
  }, [kindFilter, statusFilter]);
  useEffect(() => { void refresh(); }, [refresh]);

  const edit = async () => {
    if (!selected) return;
    const content = window.prompt("Corriger cette mémoire", selected.content);
    if (content?.trim()) { setSelected(await updateMemory(selected.id, { content: content.trim() })); await refresh(); }
  };
  const toggle = async () => {
    if (!selected) return;
    setSelected(await setMemoryEnabled(selected.id, selected.status === "disabled")); await refresh();
  };
  const remove = async () => {
    if (!selected || !window.confirm("Supprimer cette mémoire structurée ? Le message USER source restera dans la conversation.")) return;
    await deleteMemory(selected.id); setSelected(null); await refresh();
  };
  const backfillInput = (): BackfillInput => {
    if (backfillScope === "conversations") return { conversation_ids: backfillIds };
    if (backfillScope === "date") return {
      created_after: backfillAfter ? new Date(backfillAfter).toISOString() : undefined,
      created_before: backfillBefore ? new Date(backfillBefore).toISOString() : undefined,
    };
    return { all_eligible: true };
  };
  const prepareBackfill = async () => {
    setBackfillMessage(null); setBackfillPreview(null);
    try { setBackfillPreview((await previewBackfill(backfillInput())).preview); }
    catch { setBackfillMessage("Choisissez un périmètre valide avant de continuer."); }
  };
  const confirmBackfill = async () => {
    if (!backfillPreview) return;
    try {
      const result = await startBackfill(backfillInput());
      setBackfillMessage(`${result.backfill.eligible} messages sont planifiés en arrière-plan.`);
      setBackfillPreview(null); await refresh();
    } catch { setBackfillMessage("Le lancement du backfill a échoué."); }
  };

  return <section className="memory-panel">
    <header className="chat-header"><div><p className="eyebrow">Contrôle local</p><h1>Mémoire</h1></div><span className="memory-summary">{status?.memories.active ?? 0} actives</span></header>
    <nav className="memory-tabs" aria-label="Sections mémoire">
      {(["memories", "entities", "processing"] as Section[]).map((value) => <button type="button" key={value} className={section === value ? "active" : ""} onClick={() => setSection(value)}>{{ memories: "Mémoires", entities: "Entités", processing: "Traitement" }[value]}</button>)}
    </nav>
    {error && <p className="error" role="alert">{error}</p>}
    {section === "memories" && <div className="memory-layout">
      <div className="memory-list-pane">
        <div className="memory-filters">
          <select aria-label="Statut mémoire" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">Actives et désactivées</option><option value="active">Actives</option><option value="disabled">Désactivées</option></select>
          <select aria-label="Type de mémoire" value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}><option value="">Tous les types</option>{Object.entries(kindLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>
        </div>
        {items.length === 0 && <p className="memory-empty">Aucune mémoire pour ce filtre.</p>}
        {items.map((item) => <button type="button" key={item.id} className={`memory-card ${selected?.id === item.id ? "active" : ""}`} onClick={async () => setSelected(await getMemory(item.id))}><span>{kindLabels[item.kind]} · {statusLabels[item.epistemic_status]}</span><strong>{item.content}</strong><small>{item.supporting_source_count} source{item.supporting_source_count > 1 ? "s" : ""} · {item.status}</small></button>)}
      </div>
      <div className="memory-detail-pane">
        {!selected && <p className="memory-empty">Sélectionnez une mémoire pour voir pourquoi elle existe.</p>}
        {selected && <><div className="memory-detail-head"><span>{kindLabels[selected.kind]} · {statusLabels[selected.epistemic_status]}</span><h2>{selected.content}</h2>{Boolean(selected.user_locked) && <small>Corrigée et verrouillée par vous</small>}</div>
          <h3>Pourquoi ceci est-il mémorisé ?</h3><div className="memory-sources">{selected.sources.map((source) => <article key={`${source.message_id}-${source.start_char}`}><blockquote>{source.excerpt}</blockquote><small>{source.input_type === "voice" ? "Voix transcrite" : "Texte"} · {new Date(source.message_created_at).toLocaleString()} · {source.hash_valid ? "source vérifiée" : "source modifiée"}</small><button type="button" onClick={() => onOpenConversation(source.conversation_id)}>Ouvrir la conversation</button></article>)}</div>
          <div className="memory-controls"><button type="button" onClick={() => void edit()}>Modifier</button><button type="button" onClick={() => void toggle()}>{selected.status === "disabled" ? "Réactiver" : "Désactiver"}</button><button className="danger" type="button" onClick={() => void remove()}>Supprimer</button></div><p className="memory-delete-note">La suppression n’efface pas les messages USER originaux.</p></>}
      </div>
    </div>}
    {section === "entities" && <div className="entity-grid">{entities.map((entity) => <article key={entity.id}><span>{entity.entity_type} · {entity.resolution_status}</span><h2>{entity.display_name}</h2><p>{entity.linked_memory_count} mémoire(s) liée(s)</p><button type="button" onClick={async () => { const name = window.prompt("Nom affiché", entity.display_name); if (name?.trim()) { await renameEntity(entity.id, name.trim()); await refresh(); } }}>Renommer</button></article>)}</div>}
    {section === "processing" && <div className="processing-grid"><Info label="Mémoire automatique" value={status?.memory_enabled ? "Activée" : "Désactivée"} /><Info label="Inactivité requise" value={`${status?.background_idle_seconds ?? "—"} s`} /><Info label="Jobs en attente" value={String(status?.jobs.pending ?? 0)} /><Info label="Retries" value={String(status?.jobs.retry ?? 0)} /><Info label="Échecs" value={String(status?.jobs.failed ?? 0)} /><Info label="Job actif" value={status?.active_job_id ?? "Aucun"} />
      <section className="backfill-card"><h2>Construire la mémoire depuis l’historique</h2><p>Cette opération est optionnelle. Seuls les messages USER complets et non déjà traités seront proposés.</p>
        <label>Portée<select value={backfillScope} onChange={(event) => { setBackfillScope(event.target.value as "all" | "conversations" | "date"); setBackfillPreview(null); }}><option value="all">Tout l’historique éligible</option><option value="conversations">Conversations sélectionnées</option><option value="date">Plage de dates</option></select></label>
        {backfillScope === "conversations" && <div className="backfill-conversations">{conversations.map((conversation) => <label key={conversation.id}><input type="checkbox" checked={backfillIds.includes(conversation.id)} onChange={(event) => setBackfillIds((ids) => event.target.checked ? [...ids, conversation.id] : ids.filter((id) => id !== conversation.id))} />{conversation.title || "Conversation sans titre"}</label>)}</div>}
        {backfillScope === "date" && <div className="backfill-dates"><label>Depuis<input type="datetime-local" value={backfillAfter} onChange={(event) => setBackfillAfter(event.target.value)} /></label><label>Jusqu’à<input type="datetime-local" value={backfillBefore} onChange={(event) => setBackfillBefore(event.target.value)} /></label></div>}
        {!backfillPreview && <button type="button" onClick={() => void prepareBackfill()}>Voir l’aperçu</button>}
        {backfillPreview && <div className="backfill-preview"><strong>{backfillPreview.conversations} conversation(s), {backfillPreview.eligible_messages} message(s) éligible(s)</strong><p>Aucun traitement n’a encore été lancé.</p><button type="button" onClick={() => void confirmBackfill()}>Confirmer et lancer</button><button type="button" onClick={() => setBackfillPreview(null)}>Annuler</button></div>}
        {backfillMessage && <p className="memory-processing-note">{backfillMessage}</p>}
      </section><p className="memory-processing-note">Le chat et la dictée ont toujours priorité.</p></div>}
  </section>;
}

function Info({ label, value }: { label: string; value: string }) {
  return <div className="info-row"><span>{label}</span><strong>{value}</strong></div>;
}
