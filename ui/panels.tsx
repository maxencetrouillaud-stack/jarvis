import React, { useEffect, useRef, useState } from "react";
import { UpdateSettings } from "./update";
import { api, AppState, Backup, fmtBytes, fmtDate, Memory, Provider, Settings } from "./api";
import { Icon, ModelSelect, Orb, Phase, PHASE_LABEL, Toggle } from "./common";
import { frenchVoices, voiceSupport } from "./voice";

interface P { state: AppState; active: boolean; setState: (s: AppState) => void; toast: (t: string, k?: string) => void; refreshState: () => void }

const KIND_LABEL: Record<string, string> = { durable: "Durable", temporaire: "Temporaire" };

// ---------------------------------------------------------------- Mémoire
const NATURE_LABEL: Record<string, string> = { personnelle: "Personnelle", projet: "Projet", technique: "Technique", temporaire: "Temporaire" };
const REASON_LABEL = (r: string) => r.charAt(0).toUpperCase() + r.slice(1);

function Proposals({ toast, onChange }: { toast: (t: string, k?: string) => void; onChange: () => void }) {
  const [items, setItems] = useState<any[]>([]);
  const load = () => api<any[]>("GET", "/api/memories/proposals").then(setItems).catch(() => {});
  useEffect(() => { load(); }, []);
  const decide = async (id: number, action: string) => {
    try { await api("POST", `/api/memories/proposals/${id}`, { action }); toast(action === "accepter" ? "Souvenir mis à jour. L'ancienne version reste dans son historique." : "Proposition refusée : le souvenir n'a pas changé.", "memory"); load(); onChange(); }
    catch (e: any) { toast(e.message, "error"); }
  };
  if (!items.length) return null;
  return (
    <div className="card proposals">
      <h3>Mises à jour à valider ({items.length})</h3>
      <p className="muted small">Pendant vos conversations, Jarvis a relevé des informations qui corrigent des souvenirs existants. Rien n'est remplacé sans votre accord (réglage modifiable dans Réglages › Cerveau central).</p>
      {items.map((p) => (
        <div key={p.id} className="proposal">
          <div>{p.old_content && <p className="old"><span>Actuel</span>{p.old_content}</p>}
            <p className="new"><span>{p.old_content ? "Proposé" : "Nouveau"}</span>{p.content}</p>
            {p.reason && <p className="muted small">Raison : {p.reason}</p>}</div>
          <div className="row"><button className="btn" onClick={() => decide(p.id, "refuser")}>Refuser</button>
            <button className="btn primary" onClick={() => decide(p.id, "accepter")}><Icon name="check" /> Accepter</button></div>
        </div>
      ))}
    </div>
  );
}

function History({ id, toast, onChange }: { id: number; toast: (t: string, k?: string) => void; onChange: () => void }) {
  const [vs, setVs] = useState<any[] | null>(null);
  useEffect(() => { api("GET", `/api/memories/${id}/versions`).then((r) => setVs(r.versions)).catch((e) => toast(e.message, "error")); }, [id]);
  const restore = async (v: any) => {
    if (!window.confirm("Remettre cette version ? L'état actuel est conservé dans l'historique.")) return;
    try { await api("POST", `/api/memories/${id}/restore`, { version_id: v.id }); toast("Version restaurée.", "memory"); onChange(); }
    catch (e: any) { toast(e.message, "error"); }
  };
  if (vs === null) return <p className="muted small">Chargement…</p>;
  if (!vs.length) return <p className="muted small history">Aucune version antérieure : ce souvenir n'a jamais été modifié.</p>;
  return (
    <ul className="history">{vs.map((v) => (
      <li key={v.id}><span className="muted small">{fmtDate(v.at)} — {REASON_LABEL(v.reason)}</span><p>{v.content}</p>
        <button className="link" onClick={() => restore(v)}>Restaurer cette version</button></li>))}</ul>
  );
}

function Deleted({ toast, onChange }: { toast: (t: string, k?: string) => void; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => { if (open) api<any[]>("GET", "/api/memories/deleted").then(setItems).catch(() => {}); }, [open]);
  const restore = async (v: any) => {
    try { await api("POST", `/api/memories/${v.memory_id}/restore`, { version_id: v.id }); toast("Souvenir récupéré.", "memory"); setItems(items.filter((x) => x.id !== v.id)); onChange(); }
    catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <div className="deleted">
      <button className="link" onClick={() => setOpen(!open)}>{open ? "Masquer" : "Afficher"} les souvenirs supprimés ou expirés (récupérables)</button>
      {open && (items.length === 0 ? <p className="muted small">Aucun.</p> : (
        <ul className="history">{items.map((v) => <li key={v.id}><span className="muted small">{v.reason} le {fmtDate(v.at)}</span><p>{v.content}</p>
          <button className="link" onClick={() => restore(v)}>Récupérer</button></li>)}</ul>))}
    </div>
  );
}

export function MemoryView({ state, active, toast, refreshState, startAdd, editId, toSphere }: P & { startAdd?: boolean; editId?: number; toSphere?: () => void }) {
  const [items, setItems] = useState<Memory[]>([]);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [nature, setNature] = useState("");
  const [project, setProject] = useState("");
  const [projects, setProjects] = useState<{ project: string; n: number }[]>([]);
  const [hist, setHist] = useState<number | null>(null);
  const [editing, setEditing] = useState<Partial<Memory> & { days?: number } | null>(
    startAdd ? { content: "", category: "divers", importance: 3, kind: "durable", nature: "", project: "" } : null);
  const opened = useRef(false);
  const [loading, setLoading] = useState(false);
  const load = async (query = q, category = cat, nat = nature, proj = project) => {
    setLoading(true);
    try {
      const p = new URLSearchParams();
      if (query.trim()) p.set("q", query.trim());
      if (category) p.set("category", category);
      if (nat) p.set("nature", nat);
      if (proj) p.set("project", proj);
      const list = await api<Memory[]>("GET", `/api/memories?${p}`);
      setItems(list);
      if (editId && !opened.current) { opened.current = true; const m = list.find((x) => x.id === editId); if (m) setEditing({ ...m }); }
      api("GET", "/api/memories/projects").then(setProjects).catch(() => {});
    } catch (e: any) { toast(e.message, "error"); }
    setLoading(false);
  };
  const reload = () => { load(); refreshState(); };
  useEffect(() => { if (active) load(); }, [active]);
  useEffect(() => { const t = setTimeout(() => active && load(q, cat, nature, project), 250); return () => clearTimeout(t); }, [q, cat, nature, project]);

  const save = async () => {
    if (!editing?.content?.trim()) { toast("Le souvenir est vide.", "error"); return; }
    try {
      const body = { content: editing.content, category: editing.category, importance: editing.importance, kind: editing.kind, days: editing.days,
        nature: editing.nature || undefined, project: (editing.project || "").trim() };
      if (editing.id) await api("PATCH", `/api/memories/${editing.id}`, body);
      else await api("POST", "/api/memories", body);
      setEditing(null);
      reload();
    } catch (e: any) { toast(e.message, "error"); }
  };

  const remove = async (m: Memory) => {
    if (!window.confirm("Supprimer ce souvenir ?\n\nJarvis ne l'utilisera plus. Il reste récupérable en bas de cette page (souvenirs supprimés).")) return;
    await api("DELETE", `/api/memories/${m.id}`).catch((e) => toast(e.message, "error"));
    reload();
  };

  const reindex = async () => {
    try {
      const r = await api("POST", "/api/memories/reindex", {});
      toast(`${r.indexed} souvenir(s) indexé(s) avec ${r.model}${r.failed ? `, ${r.failed} échec(s)` : ""}.`, "memory");
      reload();
    } catch (e: any) { toast(e.message, "error"); }
  };
  const st = state.stats;

  return (
    <div className="panel">
      <header className="panel-head">
        <div><h2>Mémoire</h2>
          <p className="muted">Mémoire personnelle : {st.memories} souvenir{st.memories > 1 ? "s" : ""} sur vous, vos préférences et vos projets, communs à tous les modèles. Les documents vont dans l'onglet Connaissances.
            {state.embedding_active
              ? ` Recherche sémantique : ${state.embedding_active} (${st.memories_semantic}/${st.memories} indexés).`
              : " Recherche par mots, racines, synonymes et similarité locale ; un modèle d'embeddings (onglet Modèles) ajoute la recherche par le sens."}</p>
          <p className="muted small">{(state.natures || []).map((n) => `${NATURE_LABEL[n]} : ${st.by_nature?.[n] || 0}`).join(" · ")}{st.projects ? ` · ${st.projects} projet${st.projects > 1 ? "s" : ""}` : ""}{st.versions ? ` · ${st.versions} version${st.versions > 1 ? "s" : ""} antérieure${st.versions > 1 ? "s" : ""} conservée${st.versions > 1 ? "s" : ""}` : ""}</p></div>
        <div className="row">
          {state.embedding_active && st.memories_semantic < st.memories &&
            <button className="btn" onClick={reindex}><Icon name="refresh" /> Indexer</button>}
          {toSphere && <button className="btn" onClick={toSphere}><Icon name="target" /> Sphère 3D</button>}
          <button className="btn primary" onClick={() => setEditing({ content: "", category: "divers", importance: 3, kind: "durable", nature: "", project: "" })}><Icon name="plus" /> Ajouter</button>
        </div>
      </header>

      {st.proposals > 0 && <Proposals toast={toast} onChange={reload} />}

      <div className="row filters wrap">
        <div className="field-icon grow">
          <Icon name="search" size={15} />
          <input placeholder="Chercher un souvenir (même formulé autrement)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Chercher un souvenir" />
        </div>
        <select className="select" value={nature} onChange={(e) => setNature(e.target.value)} aria-label="Nature">
          <option value="">Toutes les natures</option>
          {(state.natures || []).map((n) => <option key={n} value={n}>{NATURE_LABEL[n]} ({st.by_nature?.[n] || 0})</option>)}
        </select>
        <select className="select" value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Catégorie">
          <option value="">Toutes les catégories</option>
          {state.categories.map((c) => <option key={c} value={c}>{c} ({st.by_category[c] || 0})</option>)}
        </select>
        {projects.length > 0 && (
          <select className="select" value={project} onChange={(e) => setProject(e.target.value)} aria-label="Projet">
            <option value="">Tous les projets</option>
            {projects.map((p) => <option key={p.project} value={p.project}>{p.project} ({p.n})</option>)}
          </select>
        )}
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
            <label>Nature
              <select className="select" value={editing.kind === "temporaire" ? "temporaire" : editing.nature || ""} disabled={editing.kind === "temporaire"}
                onChange={(e) => setEditing({ ...editing, nature: e.target.value })}>
                <option value="">Automatique (selon la catégorie)</option>
                {(state.natures || []).filter((n) => n !== "temporaire").map((n) => <option key={n} value={n}>{NATURE_LABEL[n]}</option>)}
                {editing.kind === "temporaire" && <option value="temporaire">Temporaire</option>}
              </select></label>
            <label>Projet
              <input className="input" list="mem-projects" value={editing.project || ""} maxLength={80} placeholder="aucun"
                onChange={(e) => setEditing({ ...editing, project: e.target.value })} />
              <datalist id="mem-projects">{projects.map((p) => <option key={p.project} value={p.project} />)}</datalist></label>
            <label>Importance
              <select className="select" value={editing.importance} onChange={(e) => setEditing({ ...editing, importance: +e.target.value })}>
                {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}{n === 5 ? " — toujours rappelé" : n === 4 ? " — toujours rappelé si c'est une préférence" : ""}</option>)}
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
          {editing.id && <p className="muted small">La version actuelle sera conservée dans l'historique du souvenir.</p>}
        </div>
      )}

      <div className="mem-list">
        {items.length === 0 && !loading && (
          <p className="empty">{q || cat || nature || project ? "Aucun souvenir ne correspond." : "Aucun souvenir pour l'instant. Ils s'ajoutent automatiquement pendant vos conversations, ou à la main avec « Ajouter »."}</p>
        )}
        {items.map((m) => (
          <article key={m.id} className="mem">
            <div className="mem-body">
              <p>{m.content.length > 420 ? m.content.slice(0, 420) + "…" : m.content}</p>
              <div className="mem-meta">
                {m.nature && <span className={`nat n-${m.nature}`}>{NATURE_LABEL[m.nature] || m.nature}</span>}
                <span className="cat">{m.category}</span>
                {m.project && <span className="cat proj">projet : {m.project}</span>}
                <span className="imp" title={`Importance ${m.importance}/5`}>{"●".repeat(m.importance)}<i>{"●".repeat(5 - m.importance)}</i></span>
                <span>{KIND_LABEL[m.kind]}{m.expires_at ? ` · expire le ${fmtDate(m.expires_at)}` : ""}</span>
                <span>{m.source === "auto" ? "appris en conversation" : m.source === "restauré" ? "récupéré" : "ajouté à la main"}</span>
                <span>utilisé {m.use_count} fois</span>
                {m.similarity !== undefined && <span>proximité {(m.similarity * 100).toFixed(0)} %</span>}
                <button className="link" onClick={() => setHist(hist === m.id ? null : m.id)}>{hist === m.id ? "Masquer l'historique" : "Historique"}</button>
              </div>
              {hist === m.id && <History id={m.id} toast={toast} onChange={() => { setHist(null); reload(); }} />}
            </div>
            <div className="mem-actions">
              <button className="icon-btn" title="Modifier" aria-label="Modifier" onClick={() => setEditing({ ...m, project: m.project || "" })}><Icon name="edit" size={15} /></button>
              <button className="icon-btn" title="Supprimer" aria-label="Supprimer" onClick={() => remove(m)}><Icon name="trash" size={15} /></button>
            </div>
          </article>
        ))}
      </div>
      <Deleted toast={toast} onChange={reload} />
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
const STRAT_NAME: Record<string, string> = { directe: "Réponse directe", contextuelle: "Avec contexte", approfondie: "Réflexion approfondie", "décomposée": "Décomposée en étapes" };

function BrainCard({ active }: { active: boolean }) {
  const [d, setD] = useState<any>(null);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => { if (active) api("GET", "/api/brain?limit=12").then(setD).catch(() => {}); }, [active]);
  if (!d) return null;
  const st = d.stats, h = d.habits;
  const maxS = Math.max(1, ...Object.values(st.by_strategy).map((x: any) => x.n));
  const maxP = Math.max(1, ...Object.values(h.periods || {}).map((x: any) => x as number));
  return (
    <div className="grid2">
      <div className="card"><h3>Cerveau central</h3>
        {st.runs === 0 ? <p className="muted small">Aucune réponse orchestrée pour l'instant : les chiffres apparaîtront après vos premières conversations.</p> : (<>
          <p className="muted small">{st.runs} réponse{st.runs > 1 ? "s" : ""} pilotée{st.runs > 1 ? "s" : ""} · {st.calls} appel{st.calls > 1 ? "s" : ""} aux modèles · {st.steps} étape{st.steps > 1 ? "s" : ""} exécutée{st.steps > 1 ? "s" : ""} · durée moyenne {st.avg_s} s
            {st.verified ? ` · ${st.verified} vérifiée${st.verified > 1 ? "s" : ""}, dont ${st.verified_ok} sans problème restant` : ""}{st.failed ? ` · ${st.failed} interrompue${st.failed > 1 ? "s" : ""} ou signalée${st.failed > 1 ? "s" : ""} par les contrôles` : ""}</p>
          {Object.entries(st.by_strategy).sort((a: any, b: any) => b[1].n - a[1].n).map(([k, v]: any) => (
            <div key={k} className="bar"><span>{STRAT_NAME[k] || k}</span><div><i style={{ width: `${(v.n / maxS) * 100}%` }} /></div><b>{v.n}</b></div>))}
          <h4 className="sub">Dernières exécutions</h4>
          <ul className="runs">{d.runs.map((r: any) => (
            <li key={r.id}><button className="link" onClick={() => setOpen(open === r.id ? null : r.id)}>
              <span className={`dot ${r.ok ? "ok" : "warn"}`} />{fmtDate(r.at)} — {STRAT_NAME[r.strategy] || r.strategy} · {r.complexity} · {r.calls} appel{r.calls > 1 ? "s" : ""} · {r.duration} s</button>
              {open === r.id && <div className="run-detail">
                <p><b>Objectif :</b> {r.trace.analysis?.objective}</p>
                {r.title && <p className="muted small">Conversation « {r.title} » · modèle {r.model}</p>}
                {(r.trace.steps || []).length > 0 && <ol>{r.trace.steps.map((x: any) => <li key={x.n}>{x.title} — {x.state}{x.note ? ` (${x.note})` : ""}</li>)}</ol>}
                {(r.trace.controls || []).map((c: any, i: number) => <p key={i} className="muted small">Contrôle : {c.text}</p>)}
                {r.trace.verify && <p className="muted small">Vérification : {r.trace.verify.checked ? (r.trace.verify.ok ? "aucun problème relevé" : `${r.trace.verify.problemes.length} problème(s)${r.trace.verify.corrected ? ", corrigé(s)" : ""}`) : r.trace.verify.note}</p>}
                {r.trace.error && <p className="muted small">Erreur : {r.trace.error}</p>}
              </div>}</li>))}</ul>
        </>)}
      </div>
      <div className="card"><h3>Habitudes observées</h3>
        <p className="muted small">Statistiques calculées sur vos {h.messages} derniers messages — pas des souvenirs, rien n'est deviné.
          {h.enough ? " Ce résumé est transmis au modèle (désactivable dans Réglages › Cerveau central)." : ` Le résumé est établi à partir de ${h.min_messages} messages.`}</p>
        {h.summary && <p className="habits-sum">{h.summary}</p>}
        {h.messages > 0 && Object.entries(h.periods).map(([k, v]: any) => (
          <div key={k} className="bar"><span>{k}</span><div><i style={{ width: `${(v / maxP) * 100}%` }} /></div><b>{v}</b></div>))}
      </div>
    </div>
  );
}

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
      <BrainCard active={active} />
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

      <div className="card">
        <h3>Cerveau central</h3>
        <p className="muted small">Chaque demande est analysée (objectif, complexité, ressources utiles), puis traitée avec la stratégie la plus adaptée. Le détail est visible sous chaque réponse (« Détail ») et dans le tableau de bord.</p>
        <div className="form three">
          <label>Tâches complexes
            <select className="select" value={s.orchestrator_decompose} onChange={(e) => patch({ orchestrator_decompose: e.target.value })}>
              <option value="auto">Découper en étapes exécutées dans l'ordre</option><option value="jamais">Répondre en un seul passage</option></select></label>
          <label>Vérification par un second appel
            <select className="select" value={s.orchestrator_verify} onChange={(e) => patch({ orchestrator_verify: e.target.value })}>
              <option value="auto">Tâches complexes et réflexion approfondie</option><option value="toujours">Toujours (plus lent, plus coûteux)</option>
              <option value="jamais">Seulement en réflexion approfondie</option></select></label>
          <label>Étapes au plus
            <select className="select" value={s.orchestrator_max_steps} onChange={(e) => patch({ orchestrator_max_steps: +e.target.value })}>
              {[3, 4, 5, 6, 8].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
          <label>Appels aux modèles au plus par réponse
            <select className="select" value={s.orchestrator_max_calls} onChange={(e) => patch({ orchestrator_max_calls: +e.target.value })}>
              {[6, 8, 12, 16, 24].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
          <label>Durée maximale des étapes
            <select className="select" value={s.orchestrator_max_seconds} onChange={(e) => patch({ orchestrator_max_seconds: +e.target.value })}>
              {[60, 120, 300, 600].map((n) => <option key={n} value={n}>{n / 60} min</option>)}</select></label>
          <label>Souvenir corrigé par une conversation
            <select className="select" value={s.memory_update_mode} onChange={(e) => patch({ memory_update_mode: e.target.value })}>
              <option value="valider">Me demander de valider</option><option value="auto">Mettre à jour (ancienne version gardée)</option></select></label>
        </div>
        <Toggle checked={s.memory_query_expansion} label="Recherche reformulée en mémoire"
          hint="Quand une question personnelle ne trouve aucun souvenir, le modèle utilitaire propose d'autres mots pour relancer la recherche (un appel de plus, seulement dans ce cas)." onChange={(v) => patch({ memory_query_expansion: v })} />
        <Toggle checked={s.habits_in_context} label="Tenir compte des habitudes observées"
          hint="Transmet au modèle un résumé chiffré de vos habitudes (heures, types de demandes), calculé sur vos messages, à partir de 20 messages." onChange={(v) => patch({ habits_in_context: v })} />
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
