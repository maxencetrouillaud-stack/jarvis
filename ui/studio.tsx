import React, { useEffect, useRef, useState } from "react";
import { api, download, fmtDate, MissionStep, TOKEN } from "./api";
import { Icon } from "./common";

// ------------------------------------------------------------------ types de l'espace de travail
export interface Resource {
  id: number; kind: string; title: string; src?: string; url?: string; mime?: string; saved: number; bytes: number;
  meta: any; project_id: number | null; conv_id?: string | null; mission_id?: number | null; created: number;
}
export interface Artifact {
  id: number; title: string; kind: string; kind_label: string; version: number; shown_version?: number; content?: string;
  versions?: { n: number; note: string; author: string; created: number; size: number }[]; project_id: number | null;
  project?: string | null; conv_id?: string | null; updated: number; created: number; sandbox_url?: string; images?: number[];
}
export interface Project {
  id: number; name: string; objective: string; status: string; notes: string; updated: number; created: number;
  progress: number | null; counts: Record<string, number>;
}
export interface Mission {
  id: number; goal: string; status: string; project_id: number | null; conv_id: string | null; created: number;
  updated: number; finished?: number | null; error?: string | null; message_id?: number | null; progress?: number;
  plan: { objective?: string; planner?: string; steps: (MissionStep & { approval?: { action: string; detail: string }; result?: any })[] };
  log?: { id: number; at: number; agent: string; tool: string | null; action: string; detail: string; ok: number }[];
}
export type Ev = { id: number; topic: string; at: number; [k: string]: any };

// ------------------------------------------------------------------ flux d'événements (un seul pour toute l'interface)
const listeners = new Set<(e: Ev) => void>();
let started = false;
let lastId = -1;   // -1 : seulement les événements à venir

async function loop() {
  for (;;) {
    try {
      const r = await fetch(`/api/events?since=${lastId}`, { headers: { "X-Jarvis-Token": TOKEN } });
      if (!r.ok || !r.body) throw new Error(String(r.status));
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i: number;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, i);
          buf = buf.slice(i + 2);
          const data = block.split("\n").find((l) => l.startsWith("data: "));
          if (!data) continue;
          try {
            const ev = JSON.parse(data.slice(6));
            if (block.startsWith("event: hello")) { if (lastId < 0) lastId = ev.last; continue; }
            if (typeof ev.id === "number") lastId = ev.id;
            listeners.forEach((fn) => { try { fn(ev); } catch { /* un abonné défaillant n'arrête pas les autres */ } });
          } catch { /* bloc incomplet */ }
        }
      }
    } catch { /* coupure : on se reconnecte */ }
    await new Promise((res) => setTimeout(res, 1500));
  }
}

export function onEvent(fn: (e: Ev) => void) {
  listeners.add(fn);
  if (!started) { started = true; loop(); }
  return () => { listeners.delete(fn); };
}

/** S'abonne aux événements (la fonction la plus récente est toujours appelée). */
export function useEvents(fn: (e: Ev) => void) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => onEvent((e) => ref.current(e)), []);
}

export function useResources(ids: number[] | undefined) {
  const [list, setList] = useState<Resource[]>([]);
  const key = (ids || []).join(",");
  useEffect(() => {
    if (!key) { setList([]); return; }
    api<Resource[]>("GET", `/api/resources?ids=${key}`).then(setList).catch(() => setList([]));
  }, [key]);
  return [list, setList] as const;
}

export const AGENT_COLOR: Record<string, string> = { jarvis: "#5cd0ff", chercheur: "#f472b6", concepteur: "#facc15",
  developpeur: "#3ddc97", ingenieur: "#ff9a3c", verificateur: "#a78bfa" };
const STATE_ICON: Record<string, string> = { "en attente": "○", "en cours": "◔", "terminée": "●", "échec": "✕", "refusée": "⊘",
  "autorisation": "?", "annulée": "–", "ignorée": "–" };

export function AgentBadge({ agent, label }: { agent: string; label?: string }) {
  return <span className="agent" style={{ "--ag": AGENT_COLOR[agent] || "#7f93b0" } as any}>{label || agent}</span>;
}

/** Suivi d'une mission : étapes, agents, autorisation en attente, arrêt et reprise. */
export function MissionSteps({ steps, approval, missionId, status, compact, toast, onChange }: {
  steps: MissionStep[]; approval?: { n: number; action: string; detail: string } | null; missionId?: number; status?: string;
  compact?: boolean; toast?: (t: string, k?: string) => void; onChange?: () => void;
}) {
  const decide = async (decision: string) => {
    try { await api("POST", `/api/missions/${missionId}/approve`, { decision }); onChange?.(); } catch (e: any) { toast?.(e.message, "error"); }
  };
  const act = async (what: "stop" | "resume") => {
    try { await api("POST", `/api/missions/${missionId}/${what}`, {}); onChange?.(); toast?.(what === "stop" ? "Mission arrêtée." : "Mission reprise."); }
    catch (e: any) { toast?.(e.message, "error"); }
  };
  return (
    <div className={`msteps ${compact ? "compact" : ""}`}>
      <ol>{steps.map((s) => (
        <li key={s.n} className={`ms-${s.state.replace(/\s/g, "-")}`}>
          <span className="ms-ico" aria-hidden="true">{STATE_ICON[s.state] || "○"}</span>
          <div className="ms-body">
            <div className="ms-head"><AgentBadge agent={s.agent} label={s.agent_label} /><b>{s.label}</b><i>{s.state}</i></div>
            {s.summary && <p>{s.summary}</p>}
            {s.error && <p className="ms-err">{s.error}</p>}
          </div>
        </li>))}
      </ol>
      {approval && missionId && (
        <div className="notice warn approval"><Icon name="shield" />
          <span><b>Autorisation demandée :</b> {approval.action}{approval.detail ? <small> — {approval.detail}</small> : null}</span>
          <button className="btn primary" onClick={() => decide("autoriser")}>Autoriser</button>
          <button className="btn" onClick={() => decide("refuser")}>Refuser</button>
        </div>
      )}
      {missionId && status && (status === "en cours" || status === "en attente") && !compact && (
        <button className="link" onClick={() => act("stop")}><Icon name="stop" size={13} /> Arrêter la mission</button>)}
      {missionId && status && ["échec", "interrompue", "arrêtée"].includes(status) && (
        <button className="btn" onClick={() => act("resume")}><Icon name="refresh" size={14} /> Reprendre à l'étape en échec</button>)}
    </div>
  );
}

/** Vignette d'image avec ses actions (enregistrer, télécharger, modifier, haute résolution, ouvrir). */
export function ImageTile({ r, toast, onChange, onEdit, onUse, onOpen, small }: {
  r: Resource; toast: (t: string, k?: string) => void; onChange?: (r: Resource | null) => void;
  onEdit?: (r: Resource) => void; onUse?: (r: Resource) => void; onOpen?: (r: Resource) => void; small?: boolean;
}) {
  const save = async () => {
    try { const x = await api<Resource>("PATCH", `/api/resources/${r.id}`, { saved: !r.saved }); onChange?.(x); toast(x.saved ? "Image enregistrée dans la galerie." : "Image retirée de la galerie (elle sera effacée après le délai de conservation)."); }
    catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <figure className={`itile ${small ? "small" : ""}`}>
      <button className="itile-img" onClick={() => onOpen?.(r)} title={r.meta?.prompt || r.title}>
        {r.src ? <img src={r.src} alt={r.title} loading="lazy" /> : <span className="muted">image indisponible</span>}
      </button>
      <figcaption>
        <span className="itile-title" title={r.meta?.prompt}>{r.title}</span>
        <span className="itile-actions">
          <button className={`icon-btn ${r.saved ? "on" : ""}`} title={r.saved ? "Retirer de la galerie" : "Enregistrer dans la galerie"} aria-label="Enregistrer" onClick={save}><Icon name="star" size={15} /></button>
          <button className="icon-btn" title="Télécharger" aria-label="Télécharger" onClick={() => r.src && download(r.src + "&dl=1", r.title + (r.mime === "image/jpeg" ? ".jpg" : ".png")).catch((e) => toast(e.message, "error"))}><Icon name="download" size={15} /></button>
          {onEdit && <button className="icon-btn" title="Modifier cette image" aria-label="Modifier" onClick={() => onEdit(r)}><Icon name="edit" size={15} /></button>}
          {onUse && <button className="icon-btn" title="Utiliser comme référence" aria-label="Référence" onClick={() => onUse(r)}><Icon name="plus" size={15} /></button>}
        </span>
      </figcaption>
    </figure>
  );
}

/** Sources web d'une réponse ([W1]…), avec l'ajout à la bibliothèque sur accord explicite. */
export function WebSources({ sources, cited, openUrl, toast }: {
  sources: { n: number; url: string; title: string; excerpt: string; resource_id?: number; quotes?: number; provider?: string }[];
  cited: Set<number>; openUrl?: (u: string) => void; toast: (t: string, k?: string) => void;
}) {
  const [sent, setSent] = useState<Record<number, boolean>>({});
  const toLib = async (rid?: number) => {
    if (!rid) return;
    try { await api("POST", `/api/resources/${rid}/to-library`, {}); setSent({ ...sent, [rid]: true }); toast("Page envoyée à la bibliothèque technique (importée en arrière-plan)."); }
    catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <div className="sources">
      <h4>Sources web</h4>
      {sources.map((s, i) => (
        <div key={s.url} className={`source web ${cited.has(i + 1) ? "" : "uncited"}`} title={s.excerpt}>
          <b>W{i + 1}</b>
          <span><button className="link" onClick={() => openUrl ? openUrl(s.url) : window.open(s.url, "_blank", "noopener")}>{s.title}</button>
            <i>{s.url.replace(/^https?:\/\//, "").slice(0, 60)}{s.quotes ? ` · ${s.quotes} citation${s.quotes > 1 ? "s" : ""} vérifiée${s.quotes > 1 ? "s" : ""}` : ""}{s.provider ? ` · ${s.provider}` : ""}</i></span>
          {s.resource_id && (sent[s.resource_id] ? <span className="tag ok">Ajoutée</span>
            : <button className="icon-btn" title="Ajouter à la bibliothèque technique (avec votre accord : ce clic)" aria-label="Ajouter à la bibliothèque" onClick={() => toLib(s.resource_id)}><Icon name="book" size={15} /></button>)}
        </div>
      ))}
    </div>
  );
}

export function ProjectSelect({ projects, value, onChange, empty = "Aucun projet", className = "select compact" }: {
  projects: Project[]; value: number | null | undefined; onChange: (v: number | null) => void; empty?: string; className?: string;
}) {
  return (
    <select className={className} value={value ?? ""} onChange={(e) => onChange(e.target.value ? +e.target.value : null)} aria-label="Projet">
      <option value="">{empty}</option>
      {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select>
  );
}

export function useProjects(active = true) {
  const [projects, setProjects] = useState<Project[]>([]);
  const load = () => api<Project[]>("GET", "/api/projects").then(setProjects).catch(() => {});
  useEffect(() => { if (active) load(); }, [active]);
  useEvents((e) => { if (e.topic === "project") load(); });
  return [projects, load] as const;
}

export const when = (t?: number | null) => (t ? fmtDate(t) : "");
