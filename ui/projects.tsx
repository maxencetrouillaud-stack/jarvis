import React, { useEffect, useState } from "react";
import { api, download, fmtBytes, fmtDate } from "./api";
import { Icon } from "./common";
import { AgentBadge, Artifact, ImageTile, Mission, MissionSteps, Project, Resource, useEvents, useProjects, when } from "./studio";

type Toast = (t: string, k?: string) => void;
const STATUSES = ["actif", "en pause", "terminé", "archivé"];
const MSTATE: Record<string, string> = { "en cours": "ok", "en attente": "bad", "terminée": "ok", "échec": "err", "arrêtée": "off", "interrompue": "bad" };

export function MissionCard({ m, toast, onChange, openConv, openArtifact }: {
  m: Mission; toast: Toast; onChange: () => void; openConv?: (id: string) => void; openArtifact?: (id: number) => void;
}) {
  const [open, setOpen] = useState(m.status === "en cours" || m.status === "en attente");
  const [log, setLog] = useState<Mission["log"]>([]);
  const approval = m.plan.steps.find((s: any) => s.state === "autorisation");
  const showLog = async () => { const v = await api<Mission>("GET", `/api/missions/${m.id}`).catch(() => null); setLog(v?.log || []); };
  const arts = m.plan.steps.flatMap((s: any) => s.result?.artifacts || []);
  return (
    <div className={`mission card st-${m.status.replace(/\s/g, "-")}`}>
      <div className="row">
        <button className="mission-goal" onClick={() => setOpen(!open)}><b>{m.goal}</b>
          <i>n° {m.id} · {when(m.created)}{m.plan.planner ? ` · ${m.plan.planner}` : ""}</i></button>
        <span className={`pill ${MSTATE[m.status] || "off"}`}>{m.status}</span>
      </div>
      <div className="mbar"><i style={{ width: `${m.progress ?? 0}%` }} /></div>
      {open && <>
        <MissionSteps steps={m.plan.steps as any} missionId={m.id} status={m.status} toast={toast} onChange={onChange}
          approval={approval ? { n: approval.n, ...(approval as any).approval } : null} />
        {m.error && <p className="ms-err small">{m.error}</p>}
        <div className="row wrap small">
          {m.conv_id && openConv && <button className="link" onClick={() => openConv(m.conv_id!)}><Icon name="chat" size={13} /> Conversation</button>}
          {arts.map((a: number) => openArtifact && <button key={a} className="link" onClick={() => openArtifact(a)}><Icon name="artifact" size={13} /> Artefact n° {a}</button>)}
          <button className="link" onClick={showLog}><Icon name="activity" size={13} /> Journal des agents</button>
        </div>
        {log && log.length > 0 && <ul className="alog">{log.map((l) => <li key={l.id} className={l.ok ? "" : "bad"}><AgentBadge agent={l.agent} />
          <span>{l.action}{l.detail ? <small> — {l.detail.slice(0, 200)}</small> : null}</span><i>{fmtDate(l.at)}</i></li>)}</ul>}
      </>}
    </div>
  );
}

/** Projets : objectif, plan, conversations, recherches, images, fichiers, artefacts, missions, état d'avancement. */
export function ProjectsView({ toast, active, openConv, openArtifact, openUrl }: {
  toast: Toast; active: boolean; openConv: (id: string) => void; openArtifact: (id: number) => void; openUrl: (u: string) => void;
}) {
  const [projects, reload] = useProjects(active);
  const [cur, setCur] = useState<number | null>(null);
  const [d, setD] = useState<any>(null);
  const [tab, setTab] = useState("missions");
  const [form, setForm] = useState({ name: "", objective: "" });
  const [goal, setGoal] = useState("");
  const [activity, setActivity] = useState<any[]>([]);
  const [running, setRunning] = useState<Mission[]>([]);

  const loadDetail = async (id = cur) => { if (!id) { setD(null); return; } try { setD(await api("GET", `/api/projects/${id}`)); } catch (e: any) { toast(e.message, "error"); } };
  const loadActivity = async () => {
    setActivity(await api("GET", "/api/agents/log?limit=80").catch(() => []));
    setRunning(await api("GET", "/api/missions?limit=30").catch(() => []));
  };
  useEffect(() => { loadDetail(); }, [cur]);
  useEffect(() => { if (active && !cur) loadActivity(); }, [active, cur]);
  useEvents((e) => {
    if (!active) return;
    if (e.topic === "agent") setActivity((a) => [{ ...e, id: e.log_id }, ...a].slice(0, 120));
    if (e.topic === "mission" && ["plan", "step", "done", "approval", "decision", "status"].includes(e.kind)) {
      if (cur) loadDetail(); else api("GET", "/api/missions?limit=30").then(setRunning).catch(() => {});
    }
    if (cur && ["artifact", "resource"].includes(e.topic)) loadDetail();
  });

  const create = async () => {
    try { const p = await api<Project>("POST", "/api/projects", form); setForm({ name: "", objective: "" }); reload(); setCur(p.id); toast("Projet créé."); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const patch = async (body: any) => {
    try { await api("PATCH", `/api/projects/${cur}`, body); loadDetail(); reload(); } catch (e: any) { toast(e.message, "error"); }
  };
  const launch = async () => {
    try { await api("POST", `/api/projects/${cur}/missions`, { goal }); setGoal(""); loadDetail(); toast("Mission lancée : suivez les agents ci-dessous."); }
    catch (e: any) { toast(e.message, "error"); }
  };

  if (cur && d) {
    const c = d.counts;
    return (
      <div className="panel">
        <header className="panel-head">
          <div className="grow">
            <button className="link" onClick={() => { setCur(null); reload(); }}><Icon name="back" size={13} /> Tous les projets</button>
            <h2 contentEditable suppressContentEditableWarning onBlur={(e) => { const n = e.currentTarget.textContent?.trim(); if (n && n !== d.name) patch({ name: n }); }}>{d.name}</h2>
            <p className="muted small">Créé le {fmtDate(d.created)} · mis à jour le {fmtDate(d.updated)}{d.progress !== null ? ` · missions terminées : ${d.progress} %` : ""}</p>
          </div>
          <select className="select compact" value={d.status} onChange={(e) => patch({ status: e.target.value })} aria-label="État du projet">
            {STATUSES.map((s) => <option key={s}>{s}</option>)}</select>
          <button className="btn" onClick={() => download(`/api/projects/${cur}/export`, `${d.name}.zip`).catch((e) => toast(e.message, "error"))}><Icon name="download" size={14} /> Exporter</button>
        </header>
        <div className="card">
          <label>Objectif<textarea className="input" rows={2} defaultValue={d.objective} onBlur={(e) => e.target.value !== d.objective && patch({ objective: e.target.value })} /></label>
          <div className="row">
            <input className="input" value={goal} placeholder="Nouvelle mission pour ce projet (ex. « recherche les normes de levage et crée une fiche de synthèse interactive »)"
              onChange={(e) => setGoal(e.target.value)} onKeyDown={(e) => e.key === "Enter" && goal.trim().length > 3 && launch()} aria-label="Nouvelle mission" />
            <button className="btn primary" disabled={goal.trim().length < 4} onClick={launch}><Icon name="sparkle" size={14} /> Lancer</button>
          </div>
        </div>
        <div className="tabs">
          {[["missions", "Missions", c.missions], ["conversations", "Conversations", c.conversations], ["artefacts", "Artefacts", c.artifacts],
            ["images", "Images", c.images], ["sources", "Recherches et sources", c.sources], ["fichiers", "Fichiers", c.files],
            ["memoire", "Mémoire", c.memories], ["notes", "Notes", null]].map(([k, l, n]) => (
            <button key={k as string} className={tab === k ? "on" : ""} onClick={() => setTab(k as string)}>{l}{n !== null && <i>{n}</i>}</button>))}
        </div>
        <div className="ptab">
          {tab === "missions" && (d.missions.length === 0 ? <p className="muted pad">Aucune mission. Décrivez un objectif ci-dessus : Jarvis choisit les agents et les outils.</p>
            : d.missions.map((m: Mission) => <MissionCard key={m.id} m={m} toast={toast} onChange={() => loadDetail()} openConv={openConv} openArtifact={openArtifact} />))}
          {tab === "conversations" && (d.conversations.length === 0 ? <p className="muted pad">Aucune conversation rattachée. Dans une conversation, choisissez ce projet en haut du fil.</p>
            : <div className="plist">{d.conversations.map((x: any) => <button key={x.id} className="bitem" onClick={() => openConv(x.id)}><b>{x.title}</b><i>{fmtDate(x.updated)}</i></button>)}</div>)}
          {tab === "artefacts" && (d.artifacts.length === 0 ? <p className="muted pad">Aucun artefact.</p>
            : <div className="plist">{d.artifacts.map((a: Artifact) => <button key={a.id} className="bitem" onClick={() => openArtifact(a.id)}><b>{a.title}</b><i>{a.kind} · v{a.version} · {fmtDate(a.updated)}</i></button>)}</div>)}
          {tab === "images" && (d.images.length === 0 ? <p className="muted pad">Aucune image.</p>
            : <div className="igrid">{d.images.map((r: Resource) => <ImageTile key={r.id} r={r} toast={toast} onChange={() => loadDetail()} />)}</div>)}
          {tab === "sources" && (d.sources.length === 0 ? <p className="muted pad">Aucune source consultée.</p>
            : <div className="plist">{d.sources.map((s: Resource) => (
              <div key={s.id} className="bitem row">
                <span className="grow"><button className="link" onClick={() => openUrl(s.url!)}>{s.title}</button>
                  <i>{s.url} · {fmtDate(s.created)}{s.meta?.verified_quotes ? ` · ${s.meta.verified_quotes} citation(s) vérifiée(s)` : ""}</i>
                  {s.meta?.excerpt && <small className="muted">« {String(s.meta.excerpt).slice(0, 220)} »</small>}</span>
                <button className="btn" title="Importer cette page dans la bibliothèque technique (avec votre accord : ce clic)"
                  onClick={async () => { try { await api("POST", `/api/resources/${s.id}/to-library`, {}); toast("Page envoyée à la bibliothèque."); } catch (e: any) { toast(e.message, "error"); } }}>
                  <Icon name="book" size={14} /> Bibliothèque</button>
              </div>))}</div>)}
          {tab === "fichiers" && (d.files.length === 0 ? <p className="muted pad">Aucun fichier.</p>
            : <div className="plist">{d.files.map((f: Resource) => <div key={f.id} className="bitem"><b>{f.title}</b><i>{fmtBytes(f.bytes)} · {fmtDate(f.created)} · {f.meta?.saved_to || ""}</i>
              {f.src && <button className="link" onClick={() => download(f.src + "&dl=1", f.title).catch((e) => toast(e.message, "error"))}>Télécharger</button>}</div>)}</div>)}
          {tab === "memoire" && (d.memories.length === 0 ? <p className="muted pad">Aucun souvenir lié à ce projet. Les souvenirs dont le champ « projet » porte ce nom apparaissent ici.</p>
            : <ul className="facts">{d.memories.map((x: any) => <li key={x.id}>{x.content} <span className="cat">{x.nature}</span></li>)}</ul>)}
          {tab === "notes" && <textarea className="input" rows={14} defaultValue={d.notes} placeholder="Notes libres sur le projet (enregistrées en quittant le champ)"
            onBlur={(e) => e.target.value !== d.notes && patch({ notes: e.target.value })} />}
        </div>
      </div>
    );
  }

  return (
    <div className="panel">
      <header className="panel-head"><div><h2>Projets</h2><p className="muted">Chaque mission range ici ses conversations, recherches, images, fichiers et artefacts.</p></div></header>
      <div className="grid2">
        <div className="card">
          <h3>Nouveau projet</h3>
          <div className="form">
            <label>Nom<input className="input" value={form.name} maxLength={80} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <label>Objectif<input className="input" value={form.objective} onChange={(e) => setForm({ ...form, objective: e.target.value })} /></label>
          </div>
          <div className="row"><span className="grow" /><button className="btn primary" disabled={!form.name.trim()} onClick={create}><Icon name="plus" size={14} /> Créer</button></div>
        </div>
        <div className="card">
          <h3>Agents en activité</h3>
          {running.filter((m) => m.status === "en cours" || m.status === "en attente").length === 0 && <p className="muted small">Aucune mission en cours.</p>}
          {running.filter((m) => m.status === "en cours" || m.status === "en attente").map((m) => <MissionCard key={m.id} m={m} toast={toast} onChange={loadActivity} openConv={openConv} openArtifact={openArtifact} />)}
          <ul className="alog">{activity.slice(0, 14).map((l, i) => <li key={l.id || i} className={l.ok === false || l.ok === 0 ? "bad" : ""}><AgentBadge agent={l.agent} />
            <span>{l.action}{l.detail ? <small> — {String(l.detail).slice(0, 140)}</small> : null}</span><i>{fmtDate(l.at)}</i></li>)}</ul>
        </div>
      </div>
      <div className="pgrid">
        {projects.length === 0 && <div className="empty">Aucun projet. Les projets nommés dans votre mémoire apparaissent ici automatiquement.</div>}
        {projects.map((p) => (
          <button key={p.id} className="pcard" onClick={() => setCur(p.id)}>
            <div className="row"><b className="grow">{p.name}</b><span className={`pill ${p.status === "actif" ? "ok" : "off"}`}>{p.status}</span></div>
            {p.objective && <p className="muted small">{p.objective}</p>}
            <div className="pcounts">
              <span><Icon name="chat" size={13} /> {p.counts.conversations}</span><span><Icon name="artifact" size={13} /> {p.counts.artifacts}</span>
              <span><Icon name="image" size={13} /> {p.counts.images}</span><span><Icon name="globe" size={13} /> {p.counts.sources}</span>
              <span><Icon name="memory" size={13} /> {p.counts.memories}</span><span><Icon name="sparkle" size={13} /> {p.counts.missions}</span>
            </div>
            {p.progress !== null && <div className="mbar"><i style={{ width: `${p.progress}%` }} /></div>}
            <i className="muted small">Mis à jour le {fmtDate(p.updated)}</i>
          </button>))}
      </div>
      {running.filter((m) => !(m.status === "en cours" || m.status === "en attente")).length > 0 && <>
        <h3 className="pad-h">Missions récentes</h3>
        {running.filter((m) => !(m.status === "en cours" || m.status === "en attente")).slice(0, 8).map((m) =>
          <MissionCard key={m.id} m={m} toast={toast} onChange={loadActivity} openConv={openConv} openArtifact={openArtifact} />)}
      </>}
    </div>
  );
}
