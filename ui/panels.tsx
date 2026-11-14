import React, { useEffect, useRef, useState } from "react";
import { UpdateSettings } from "./update";
import { api, AppState, Backup, fmtBytes, fmtDate, Memory, Provider, Settings } from "./api";
import { Icon, ModelSelect, Orb, Phase, PHASE_LABEL, Toggle } from "./common";
import { frenchVoices, voiceSupport } from "./voice";

interface P { state: AppState; active: boolean; setState: (s: AppState) => void; toast: (t: string, k?: string) => void; refreshState: () => void }

const KIND_LABEL: Record<string, string> = { durable: "Durable", temporaire: "Temporaire" };

// ---------------------------------------------------------------- Mémoire
export function MemoryView({ state, active, toast, refreshState, startAdd, editId, toSphere }: P & { startAdd?: boolean; editId?: number; toSphere?: () => void }) {
  const [items, setItems] = useState<Memory[]>([]);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [editing, setEditing] = useState<Partial<Memory> & { days?: number } | null>(
    startAdd ? { content: "", category: "divers", importance: 3, kind: "durable" } : null);
  const opened = useRef(false);
  const [loading, setLoading] = useState(false);
  const load = async (query = q, category = cat) => {
    setLoading(true);
    try {
      const p = new URLSearchParams();
      if (query.trim()) p.set("q", query.trim());
      if (category) p.set("category", category);
      const list = await api<Memory[]>("GET", `/api/memories?${p}`);
      setItems(list);
      if (editId && !opened.current) { opened.current = true; const m = list.find((x) => x.id === editId); if (m) setEditing({ ...m }); }
    } catch (e: any) { toast(e.message, "error"); }
    setLoading(false);
  };
  useEffect(() => { if (active) load(); }, [active]);
  useEffect(() => { const t = setTimeout(() => active && load(q, cat), 250); return () => clearTimeout(t); }, [q, cat]);

  const save = async () => {
    if (!editing?.content?.trim()) { toast("Le souvenir est vide.", "error"); return; }
    try {
      const body = { content: editing.content, category: editing.category, importance: editing.importance, kind: editing.kind, days: editing.days };
      if (editing.id) await api("PATCH", `/api/memories/${editing.id}`, body);
      else await api("POST", "/api/memories", body);
      setEditing(null);
      load();
      refreshState();
    } catch (e: any) { toast(e.message, "error"); }
  };

  const remove = async (m: Memory) => {
    if (!window.confirm("Supprimer définitivement ce souvenir ?")) return;
    await api("DELETE", `/api/memories/${m.id}`).catch((e) => toast(e.message, "error"));
    load();
    refreshState();
  };

  const reindex = async () => {
    try {
      const r = await api("POST", "/api/memories/reindex", {});
      toast(`${r.indexed} souvenir(s) indexé(s) avec ${r.model}${r.failed ? `, ${r.failed} échec(s)` : ""}.`, "memory");
      load();
      refreshState();
    } catch (e: any) { toast(e.message, "error"); }
  };

  return (
    <div className="panel">
      <header className="panel-head">
        <div><h2>Mémoire</h2>
          <p className="muted">Mémoire personnelle : {state.stats.memories} souvenir{state.stats.memories > 1 ? "s" : ""} sur vous, vos préférences et vos projets, communs à tous les modèles. Les documents vont dans l'onglet Connaissances.
            {state.embedding_active
              ? ` Recherche sémantique : ${state.embedding_active} (${state.stats.memories_semantic}/${state.stats.memories} indexés).`
              : " Recherche par mots et similarité locale ; un modèle d'embeddings (onglet Modèles) ajoute la recherche par le sens."}</p></div>
        <div className="row">
          {state.embedding_active && state.stats.memories_semantic < state.stats.memories &&
            <button className="btn" onClick={reindex}><Icon name="refresh" /> Indexer</button>}
          {toSphere && <button className="btn" onClick={toSphere}><Icon name="target" /> Sphère 3D</button>}
          <button className="btn primary" onClick={() => setEditing({ content: "", category: "divers", importance: 3, kind: "durable" })}><Icon name="plus" /> Ajouter</button>
        </div>
      </header>

      <div className="row filters">
        <div className="field-icon grow">
          <Icon name="search" size={15} />
          <input placeholder="Chercher un souvenir" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Chercher un souvenir" />
        </div>
        <select className="select" value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Catégorie">
          <option value="">Toutes les catégories</option>
          {state.categories.map((c) => <option key={c} value={c}>{c} ({state.stats.by_category[c] || 0})</option>)}
        </select>
      </div>

      {editing && (
        <div className="card editor">
          <textarea autoFocus rows={3} value={editing.content} placeholder="Ex. : L'utilisateur préfère les explications avec un exemple chiffré."
            onChange={(e) => setEditing({ ...editing, content: e.target.value })} aria-label="Contenu du souvenir" />
          <div className="row wrap">
            <label>Catégorie
              <select className="select" value={editing.category} onChange={(e) => setEditing({ ...editing, category: e.target.value })}>
                {state.categories.map((c) => <option key={c}>{c}</option>)}
              </select></label>
            <label>Importance
              <select className="select" value={editing.importance} onChange={(e) => setEditing({ ...editing, importance: +e.target.value })}>
                {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}{n === 5 ? " — toujours rappelé" : ""}</option>)}
              </select></label>
            <label>Durée
              <select className="select" value={editing.kind} onChange={(e) => setEditing({ ...editing, kind: e.target.value })}>
                {state.kinds.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
              </select></label>
            {editing.kind === "temporaire" && (
              <label>Expire dans (jours)
                <input className="input num" type="number" min={1} max={365} value={editing.days ?? 14}
                  onChange={(e) => setEditing({ ...editing, days: +e.target.value })} /></label>
            )}
            <span className="grow" />
            <button className="btn" onClick={() => setEditing(null)}>Annuler</button>
            <button className="btn primary" onClick={save}><Icon name="check" /> Enregistrer</button>
          </div>
        </div>
      )}

      <div className="mem-list">
        {items.length === 0 && !loading && (
          <p className="empty">{q || cat ? "Aucun souvenir ne correspond." : "Aucun souvenir pour l'instant. Ils s'ajoutent automatiquement pendant vos conversations, ou à la main avec « Ajouter »."}</p>
        )}
        {items.map((m) => (
          <article key={m.id} className="mem">
            <div className="mem-body">
              <p>{m.content.length > 420 ? m.content.slice(0, 420) + "…" : m.content}</p>
              <div className="mem-meta">
                <span className="cat">{m.category}</span>
                <span className="imp" title={`Importance ${m.importance}/5`}>{"●".repeat(m.importance)}<i>{"●".repeat(5 - m.importance)}</i></span>
                <span>{KIND_LABEL[m.kind]}{m.expires_at ? ` · expire le ${fmtDate(m.expires_at)}` : ""}</span>
                <span>{m.source === "auto" ? "appris en conversation" : "ajouté à la main"}</span>
                <span>utilisé {m.use_count} fois</span>
                {m.similarity !== undefined && <span>proximité {(m.similarity * 100).toFixed(0)} %</span>}
              </div>
            </div>
            <div className="mem-actions">
              <button className="icon-btn" title="Modifier" aria-label="Modifier" onClick={() => setEditing({ ...m })}><Icon name="edit" size={15} /></button>
              <button className="icon-btn" title="Supprimer" aria-label="Supprimer" onClick={() => remove(m)}><Icon name="trash" size={15} /></button>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Modèles
export function ModelsView({ state, setState, toast }: P) {
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [emb, setEmb] = useState(state.settings.embedding_model);
  const s = state.settings;

  const run = async (tag: string, fn: () => Promise<AppState>) => {
    setBusy(tag);
    try { setState(await fn()); } catch (e: any) { toast(e.message, "error"); }
    setBusy("");
  };
  const patch = (p: Partial<Settings>) => run("settings", () => api("POST", "/api/settings", p));
  const refresh = (pid?: string) => run(pid || "all", () => api("POST", "/api/models/refresh", pid ? { provider: pid } : {}));
  useEffect(() => { refresh(); }, []);

  const saveProvider = async (p: Provider) => {
    setBusy(p.id);
    try {
      const url = urls[p.id];
      if (url !== undefined && url.trim() !== p.base_url) {
        await api("POST", "/api/settings", { providers: { [p.id]: { base_url: url.trim() } } });
      }
      const k = keys[p.id];
      const st = k !== undefined && k.trim()
        ? await api<AppState>("POST", "/api/keys", { provider: p.id, key: k.trim() })
        : await api<AppState>("POST", "/api/models/refresh", { provider: p.id });
      setState(st);
      setKeys({ ...keys, [p.id]: "" });
      const np = st.providers.find((x) => x.id === p.id)!;
      toast(np.error ? `${p.label} : ${np.error}` : `${p.label} : ${np.models.length} modèle(s) disponible(s).`, np.error ? "error" : "info");
    } catch (e: any) { toast(e.message, "error"); }
    setBusy("");
  };

  return (
    <div className="panel">
      <header className="panel-head">
        <div><h2>Modèles</h2>
          <p className="muted">Jarvis n'est lié à aucun fournisseur : changez de modèle à tout moment, la mémoire et les conversations restent les mêmes.</p></div>
        <button className="btn" onClick={() => refresh()} disabled={!!busy}><Icon name="refresh" /> Actualiser</button>
      </header>

      <div className="grid2">
        {state.providers.map((p) => (
          <div key={p.id} className="card provider">
            <div className="row">
              <h3>{p.label}</h3>
              <span className={`pill ${p.models.length ? "ok" : p.enabled && (p.has_key || !p.needs_key) ? "bad" : "off"}`}>
                {busy === p.id || busy === "all" ? "Vérification…" : p.models.length ? `${p.models.length} modèle${p.models.length > 1 ? "s" : ""}` : !p.enabled ? "Désactivé" : p.error || "Non vérifié"}
              </span>
            </div>
            <p className="muted small">{p.local
              ? "Modèles installés sur ce PC avec Ollama. Rien ne sort de la machine ; fonctionne hors ligne."
              : "Service en ligne : vos messages sont envoyés à ce fournisseur et facturés sur votre clé API."}</p>
            {p.needs_key && (
              <label>Clé API {p.has_key && <em className="saved">enregistrée</em>}
                <input className="input" type="password" autoComplete="off" value={keys[p.id] || ""}
                  placeholder={p.has_key ? "•••••••• (saisir pour remplacer)" : "Coller la clé ici"}
                  onChange={(e) => setKeys({ ...keys, [p.id]: e.target.value })} /></label>
            )}
            <label>Adresse du service
              <input className="input mono" value={urls[p.id] ?? p.base_url} onChange={(e) => setUrls({ ...urls, [p.id]: e.target.value })} /></label>
            <div className="row">
              <Toggle checked={p.enabled} label="Activé" onChange={(v) => run(p.id, async () => {
                await api("POST", "/api/settings", { providers: { [p.id]: { enabled: v } } });
                return api("POST", "/api/models/refresh", { provider: p.id });
              })} />
              <span className="grow" />
              {p.has_key && <button className="btn" onClick={() => run(p.id, () => api("POST", "/api/keys", { provider: p.id, key: "" }))}>Retirer la clé</button>}
              <button className="btn primary" disabled={busy === p.id} onClick={() => saveProvider(p)}>Enregistrer et tester</button>
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>Choix du modèle</h3>
        <div className="form">
          <label>Modèle par défaut
            <ModelSelect providers={state.providers} value={s.default_model} emptyLabel="Premier modèle disponible"
              onChange={(v) => patch({ default_model: v })} className="select" /></label>
          <label>Modèle utilitaire <small>mémoire, plans et vérification — un modèle rapide suffit</small>
            <ModelSelect providers={state.providers} value={s.utility_model} emptyLabel="Le même que pour la réponse"
              onChange={(v) => patch({ utility_model: v })} className="select" /></label>
        </div>
        <Toggle checked={s.auto_route} label="Sélection automatique selon la tâche"
          hint="Jarvis reconnaît le type de demande (mots-clés et longueur) et utilise le modèle que vous avez associé ci-dessous. Si ce modèle échoue, il bascule sur le modèle par défaut."
          onChange={(v) => patch({ auto_route: v })} />
        {s.auto_route && (
          <div className="form routes">
            {Object.entries(state.routes).map(([k, label]) => (
              <label key={k}>{label}
                <ModelSelect providers={state.providers} value={s.routes[k] || ""} emptyLabel="Modèle par défaut"
                  onChange={(v) => patch({ routes: { ...s.routes, [k]: v } })} className="select" /></label>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <h3>Recherche sémantique de la mémoire</h3>
        <p className="muted small">Facultatif. Indiquez un modèle d'embeddings sous la forme <code>fournisseur:modèle</code>, par exemple
          {" "}<code>ollama:nomic-embed-text</code> (local), <code>openai:text-embedding-3-small</code> ou <code>gemini:gemini-embedding-001</code>.
          Sans lui, la mémoire reste pleinement utilisable avec la recherche locale par mots.</p>
        <div className="row">
          <input className="input mono grow" value={emb === "auto" ? "" : emb} placeholder="Aucun (recherche locale)" onChange={(e) => setEmb(e.target.value)} aria-label="Modèle d'embeddings" />
          <button className="btn primary" onClick={() => patch({ embedding_model: emb.trim() || "auto" })}>Enregistrer</button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Tableau de bord
export function DashboardView({ state, active, refreshState, phase }: P & { phase: Phase }) {
  useEffect(() => { if (active) refreshState(); }, [active]);
  const st = state.stats;
  const maxCat = Math.max(1, ...Object.values(st.by_category));
  const used = Object.entries(st.models_used).sort((a, b) => b[1] - a[1]);
  const maxUsed = Math.max(1, ...used.map((u) => u[1]));
  const online = state.providers.filter((p) => p.models.length);
  return (
    <div className="panel">
      <header className="panel-head"><div><h2>Tableau de bord</h2>
        <p className="muted">État réel du noyau — version {state.version}.</p></div></header>
      <div className="dash-top">
        <div className="card orb-card">
          <Orb phase={phase} size={130} />
          <div><b>{PHASE_LABEL[phase]}</b>
            <p className="muted small">{online.length ? `${online.length} fournisseur(s) connecté(s) : ${online.map((p) => p.label).join(", ")}` : "Aucun fournisseur connecté"}</p></div>
        </div>
        <div className="tiles">
          <div className="tile"><span>{st.conversations}</span>Conversations</div>
          <div className="tile"><span>{st.messages}</span>Messages</div>
          <div className="tile"><span>{st.memories}</span>Souvenirs</div>
          <div className="tile"><span>{fmtBytes(st.db_bytes)}</span>Base locale</div>
        </div>
      </div>
      <div className="grid2">
        <div className="card"><h3>Mémoire par catégorie</h3>
          {st.memories === 0 ? <p className="muted small">Aucun souvenir pour l'instant.</p> :
            state.categories.filter((c) => st.by_category[c]).map((c) => (
              <div key={c} className="bar"><span>{c}</span><div><i style={{ width: `${(st.by_category[c] / maxCat) * 100}%` }} /></div><b>{st.by_category[c]}</b></div>
            ))}
        </div>
        <div className="card"><h3>Réponses par modèle</h3>
          {used.length === 0 ? <p className="muted small">Aucune réponse pour l'instant.</p> :
            used.map(([m, n]) => (
              <div key={m} className="bar"><span title={m}>{m.split(":").slice(1).join(":")}</span><div><i style={{ width: `${(n / maxUsed) * 100}%` }} /></div><b>{n}</b></div>
            ))}
        </div>
      </div>
      <div className="card"><h3>Où sont vos données</h3>
        <p className="muted small">Tout est enregistré dans ce dossier de votre PC : <code>{state.data_dir}</code></p>
        <p className="muted small">Clés API : {state.key_storage === "dpapi"
          ? "chiffrées par Windows (DPAPI), lisibles uniquement depuis votre session Windows."
          : "stockées dans un fichier réservé à votre compte (non chiffré sur ce système)."}</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Réglages
export function SettingsView({ state, setState, toast, active }: P) {
  const s = state.settings;
  const [draft, setDraft] = useState({ assistant_name: s.assistant_name, user_name: s.user_name, personality: s.personality });
  const [backups, setBackups] = useState<Backup[]>([]);
  const loadBackups = () => api<Backup[]>("GET", "/api/backups").then(setBackups).catch(() => {});
  useEffect(() => { if (active) loadBackups(); }, [active]);
  const patch = async (p: Partial<Settings>, msg?: string) => {
    try { setState(await api("POST", "/api/settings", p)); if (msg) toast(msg); } catch (e: any) { toast(e.message, "error"); }
  };
  const dirty = draft.assistant_name !== s.assistant_name || draft.user_name !== s.user_name || draft.personality !== s.personality;

  const restore = async (b: Backup) => {
    if (!window.confirm(`Restaurer la sauvegarde du ${fmtDate(b.created)} ?\n\nLes conversations, la mémoire et les réglages actuels seront remplacés. Une sauvegarde de l'état actuel est faite juste avant.`)) return;
    try {
      await api("POST", "/api/backups/restore", { name: b.name });
      window.location.reload();
    } catch (e: any) { toast(e.message, "error"); }
  };

  return (
    <div className="panel">
      <header className="panel-head"><div><h2>Réglages</h2><p className="muted">Identité, comportement et sauvegardes.</p></div></header>

      <div className="card">
        <h3>Identité et personnalité</h3>
        <div className="form">
          <label>Nom de l'assistant
            <input className="input" value={draft.assistant_name} maxLength={40} onChange={(e) => setDraft({ ...draft, assistant_name: e.target.value })} /></label>
          <label>Votre prénom
            <input className="input" value={draft.user_name} maxLength={40} onChange={(e) => setDraft({ ...draft, user_name: e.target.value })} /></label>
        </div>
        <label>Personnalité et consignes permanentes <small>{"{name}"} et {"{user}"} sont remplacés par les noms ci-dessus</small>
          <textarea className="input" rows={6} value={draft.personality} onChange={(e) => setDraft({ ...draft, personality: e.target.value })} /></label>
        <div className="row">
          <button className="btn" onClick={() => setDraft({ ...draft, personality: state.default_personality })}>Personnalité d'origine</button>
          <span className="grow" />
          <button className="btn primary" disabled={!dirty} onClick={() => patch({ ...draft, assistant_name: draft.assistant_name.trim() || "Jarvis" }, "Réglages enregistrés.")}>Enregistrer</button>
        </div>
      </div>

      <div className="card">
        <h3>Comportement</h3>
        <Toggle checked={s.memory_auto_extract} label="Mémoriser automatiquement"
          hint="Après chaque réponse, un appel supplémentaire au modèle utilitaire relève ce qui mérite d'être retenu." onChange={(v) => patch({ memory_auto_extract: v })} />
        <Toggle checked={s.search_past_conversations} label="Consulter les anciennes conversations"
          hint="Les passages d'anciennes discussions en rapport avec la question sont transmis au modèle." onChange={(v) => patch({ search_past_conversations: v })} />
        <Toggle checked={s.deep_mode_default} label="Réflexion approfondie par défaut"
          hint="Plan, réponse, vérification, correction : plus fiable sur les problèmes difficiles, mais 3 à 4 appels au lieu d'un. S'applique aux nouvelles fenêtres." onChange={(v) => patch({ deep_mode_default: v })} />
        <div className="form three">
          <label>Créativité (température) : {s.temperature.toFixed(1)}
            <input type="range" min={0} max={1.5} step={0.1} value={s.temperature} onChange={(e) => patch({ temperature: +e.target.value })} /></label>
          <label>Longueur maximale d'une réponse (tokens)
            <select className="select" value={s.max_tokens} onChange={(e) => patch({ max_tokens: +e.target.value })}>
              {[1024, 2048, 4096, 8192, 16000].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
          <label>Souvenirs transmis par message
            <select className="select" value={s.memory_top_k} onChange={(e) => patch({ memory_top_k: +e.target.value })}>
              {[4, 8, 12, 20].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        </div>
      </div>

      <UpdateSettings state={state} patch={patch} toast={toast} />
      <VoiceSettings state={state} patch={patch} toast={toast} />
      <AppsSettings state={state} patch={patch} toast={toast} active={active} />

      <div className="card">
        <div className="row"><h3>Sauvegardes</h3><span className="grow" />
          <button className="btn primary" onClick={async () => {
            try { await api("POST", "/api/backups", {}); toast("Sauvegarde créée."); loadBackups(); } catch (e: any) { toast(e.message, "error"); }
          }}><Icon name="plus" /> Sauvegarder maintenant</button></div>
        <p className="muted small">Une sauvegarde automatique est faite au plus une fois par jour au démarrage ({s.backup_keep} conservées). Dossier : <code>{state.data_dir}/backups</code></p>
        {backups.length === 0 ? <p className="muted small">Aucune sauvegarde pour l'instant.</p> : (
          <table className="table"><tbody>
            {backups.map((b) => (
              <tr key={b.name}><td>{fmtDate(b.created)}</td>
                <td className="muted">{b.name.includes("-auto") ? "automatique" : b.name.includes("avantrestauration") ? "avant restauration" : "manuelle"}</td>
                <td className="muted">{fmtBytes(b.bytes)}</td>
                <td className="right"><button className="btn" onClick={() => restore(b)}>Restaurer</button></td></tr>
            ))}</tbody></table>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Voix et applications
function VoiceSettings({ state, patch, toast }: { state: AppState; patch: (p: any, msg?: string) => void; toast: (t: string, k?: string) => void }) {
  const s = state.settings as any;
  const sup = voiceSupport();
  const [voices, setVoices] = useState(frenchVoices());
  const [wake, setWake] = useState(s.voice_wake_word);
  useEffect(() => {
    const upd = () => setVoices(frenchVoices());
    window.speechSynthesis?.addEventListener?.("voiceschanged", upd);
    return () => window.speechSynthesis?.removeEventListener?.("voiceschanged", upd);
  }, []);
  const test = () => {
    if (!sup.tts) return;
    const u = new SpeechSynthesisUtterance(`Bonjour${state.settings.user_name ? " " + state.settings.user_name : ""}, je suis ${state.settings.assistant_name}. Voici ma voix.`);
    const v = voices.find((x) => x.name === s.voice_name) || voices[0];
    if (v) u.voice = v;
    u.lang = v?.lang || "fr-FR"; u.rate = s.voice_rate;
    window.speechSynthesis.cancel(); window.speechSynthesis.speak(u);
  };
  return (
    <div className="card">
      <h3>Mode vocal</h3>
      {!sup.stt && <div className="notice warn"><Icon name="warn" /><span>La reconnaissance vocale n'est pas disponible dans ce navigateur. Ouvrez Jarvis avec Edge ou Chrome.</span></div>}
      <p className="muted small">La reconnaissance vocale est celle du navigateur : pendant l'écoute, l'audio du micro est envoyé au service de Microsoft (Edge) ou de Google (Chrome) pour être transcrit. Elle demande une connexion Internet.</p>
      <Toggle checked={s.voice_handsfree} label="Mains libres : écouter en permanence le mot-clé"
        hint="Le micro reste ouvert tant que la fenêtre de Jarvis est ouverte. Dites le mot-clé puis votre demande. Désactivé, le micro ne s'ouvre que quand vous cliquez dessus." onChange={(v) => patch({ voice_handsfree: v })} />
      <Toggle checked={s.voice_speak} label="Lire les réponses à voix haute" hint="Uniquement pour les demandes faites à la voix. Les réponses tapées au clavier restent silencieuses." onChange={(v) => patch({ voice_speak: v })} />
      <Toggle checked={s.voice_barge_in} label="Pouvoir couper la parole en parlant"
        hint="Avec des haut-parleurs, le micro peut capter la voix de l'assistant. Jarvis ignore ce qui ressemble à sa propre phrase ; un casque évite le problème. « Stop » et le mot-clé coupent toujours." onChange={(v) => patch({ voice_barge_in: v })} />
      <div className="form three">
        <label>Mot-clé d'activation
          <div className="row"><input className="input" value={wake} maxLength={30} onChange={(e) => setWake(e.target.value)} />
            <button className="btn" disabled={wake.trim().toLowerCase() === s.voice_wake_word} onClick={() => patch({ voice_wake_word: wake }, "Mot-clé enregistré.")}>OK</button></div></label>
        <label>Voix
          <select className="select" value={s.voice_name} onChange={(e) => patch({ voice_name: e.target.value })} disabled={!voices.length}>
            <option value="">{voices.length ? "Automatique (première voix française)" : "Aucune voix française installée"}</option>
            {voices.map((v) => <option key={v.name} value={v.name}>{v.name.replace(/^Microsoft |^Google /, "")}</option>)}</select></label>
        <label>Débit : {Number(s.voice_rate).toFixed(2)}
          <input type="range" min={0.7} max={1.5} step={0.05} value={s.voice_rate} onChange={(e) => patch({ voice_rate: +e.target.value })} /></label>
        <label>Silence de fin de phrase
          <select className="select" value={s.voice_silence_ms} onChange={(e) => patch({ voice_silence_ms: +e.target.value })}>
            {[[900, "Court (0,9 s)"], [1300, "Normal (1,3 s)"], [2000, "Long (2 s)"]].map(([n, l]) => <option key={n} value={n}>{l}</option>)}</select></label>
        <label>Écoute après une réponse (mains libres)
          <select className="select" value={s.voice_followup_s} onChange={(e) => patch({ voice_followup_s: +e.target.value })}>
            {[6, 12, 20, 40].map((n) => <option key={n} value={n}>{n} secondes</option>)}</select></label>
      </div>
      <div className="row"><button className="btn" onClick={test} disabled={!sup.tts}><Icon name="volume" /> Écouter la voix</button>
        <span className="muted small">Commandes reconnues sans passer par le modèle : « nouvelle conversation », « ouvre la mémoire / les connaissances / les réglages », « mode silencieux », « stop », « lance » + une application autorisée.</span></div>
    </div>
  );
}

function AppsSettings({ state, patch, toast, active }: { state: AppState; patch: (p: any, msg?: string) => void; toast: (t: string, k?: string) => void; active: boolean }) {
  const apps: { name: string; target: string }[] = (state.settings as any).apps || [];
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [log, setLog] = useState<any[]>([]);
  useEffect(() => { if (active) api("GET", "/api/actions").then(setLog).catch(() => {}); }, [active, apps.length]);
  return (
    <div className="card">
      <h3>Applications que {state.settings.assistant_name} peut lancer</h3>
      <p className="muted small">À la voix : « lance Excel », « ouvre la calculatrice ». Seules les applications de cette liste peuvent être lancées, sans paramètre. La cible est un programme (<code>excel.exe</code>), un chemin complet ou une adresse web.</p>
      {apps.length === 0 ? <p className="muted small">Aucune application autorisée.</p> : (
        <table className="table"><tbody>{apps.map((a) => (
          <tr key={a.name}><td><b>{a.name}</b></td><td className="muted"><code>{a.target}</code></td>
            <td className="right nowrap">
              <button className="btn" onClick={async () => { try { const r = await api("POST", "/api/apps/launch", { query: a.name }); toast(r.launched ? `${a.name} lancé.` : r.error || "Échec du lancement.", r.launched ? "info" : "error"); api("GET", "/api/actions").then(setLog); } catch (e: any) { toast(e.message, "error"); } }}>Essayer</button>{" "}
              <button className="icon-btn" aria-label={`Retirer ${a.name}`} title="Retirer" onClick={() => patch({ apps: apps.filter((x) => x.name !== a.name) })}><Icon name="trash" size={15} /></button></td></tr>))}</tbody></table>)}
      <div className="row wrap">
        <input className="input grow" placeholder="Nom à prononcer (ex. SolidWorks)" value={name} onChange={(e) => setName(e.target.value)} aria-label="Nom de l'application" />
        <input className="input mono grow" placeholder="Cible (ex. C:\\Program Files\\…\\SLDWORKS.exe)" value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Cible de l'application" />
        <button className="btn primary" disabled={!name.trim() || !target.trim()} onClick={() => { patch({ apps: [...apps, { name, target }] }); setName(""); setTarget(""); }}><Icon name="plus" /> Ajouter</button>
      </div>
      {log.length > 0 && (<><h4>Dernières actions</h4>
        <table className="table"><tbody>{log.slice(0, 8).map((l) => <tr key={l.id}><td className="muted nowrap">{fmtDate(l.at)}</td><td>{l.detail}</td><td className="right">{l.ok ? <span className="pill ok">fait</span> : <span className="pill err">échec</span>}</td></tr>)}</tbody></table></>)}
    </div>
  );
}

// ---------------------------------------------------------------- Premier démarrage
export function Setup({ state, setState, toast, onDone }: { state: AppState; setState: (s: AppState) => void; toast: (t: string, k?: string) => void; onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState(state.settings.assistant_name);
  const [user, setUser] = useState("");
  const [pid, setPid] = useState("anthropic");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const p = state.providers.find((x) => x.id === pid)!;
  const connected = state.providers.filter((x) => x.models.length);

  const identity = async () => {
    if (!user.trim()) { toast("Indiquez votre prénom.", "error"); return; }
    setState(await api("POST", "/api/settings", { assistant_name: name.trim() || "Jarvis", user_name: user.trim() }));
    setStep(1);
  };
  const connect = async () => {
    setBusy(true);
    try {
      const st = p.needs_key
        ? await api<AppState>("POST", "/api/keys", { provider: pid, key: key.trim() })
        : await api<AppState>("POST", "/api/models/refresh", { provider: pid });
      setState(st);
      const np = st.providers.find((x) => x.id === pid)!;
      if (np.models.length) { setKey(""); toast(`${np.label} connecté : ${np.models.length} modèle(s).`); }
      else toast(`${np.label} : ${np.error || "aucun modèle"}`, "error");
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };

  return (
    <div className="overlay">
      <div className="setup card">
        <Orb phase="thinking" size={90} />
        {step === 0 ? (<>
          <h2>Configuration initiale</h2>
          <p className="muted">Deux réglages et votre assistant est prêt. Tout reste modifiable ensuite.</p>
          <label>Comment s'appelle votre assistant ?
            <input className="input" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} /></label>
          <label>Et vous, quel est votre prénom ?
            <input className="input" autoFocus value={user} maxLength={40} onChange={(e) => setUser(e.target.value)} onKeyDown={(e) => e.key === "Enter" && identity()} /></label>
          <div className="row"><span className="grow" /><button className="btn primary" onClick={identity}>Continuer</button></div>
        </>) : (<>
          <h2>Connecter un premier modèle</h2>
          <p className="muted">{name || "Jarvis"} a besoin d'au moins un modèle pour répondre. Vous pourrez en ajouter d'autres dans l'onglet Modèles.</p>
          <div className="seg">
            {state.providers.map((x) => (
              <button key={x.id} className={x.id === pid ? "on" : ""} onClick={() => { setPid(x.id); setKey(""); }}>
                {x.label.split(" (")[0]}{x.models.length ? " ✓" : ""}</button>
            ))}
          </div>
          {p.needs_key ? (
            <label>Clé API {p.label}
              <input className="input" type="password" autoComplete="off" value={key} placeholder="Coller la clé ici" onChange={(e) => setKey(e.target.value)} /></label>
          ) : (
            <p className="muted small">Ollama doit être installé et démarré sur ce PC, avec au moins un modèle téléchargé (par exemple <code>ollama pull llama3.2</code>). Adresse testée : <code>{p.base_url}</code></p>
          )}
          {connected.length > 0 && <p className="okline"><Icon name="check" size={15} /> Connecté : {connected.map((c) => `${c.label} (${c.models.length})`).join(", ")}</p>}
          <div className="row">
            <button className="btn" onClick={onDone}>{connected.length ? "Terminer" : "Plus tard"}</button>
            <span className="grow" />
            <button className="btn primary" disabled={busy || (p.needs_key && !key.trim())} onClick={connect}>{busy ? "Vérification…" : p.needs_key ? "Enregistrer et tester" : "Tester la connexion"}</button>
          </div>
        </>)}
      </div>
    </div>
  );
}
