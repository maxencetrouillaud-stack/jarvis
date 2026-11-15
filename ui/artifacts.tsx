import React, { useEffect, useRef, useState } from "react";
import { api, download, fmtDate } from "./api";
import { Icon } from "./common";
import { Artifact, Mission, MissionSteps, Project, ProjectSelect, useEvents, useProjects } from "./studio";

type Toast = (t: string, k?: string) => void;
export interface Selection { selector: string; tag: string; text: string; html: string }

/** Artefact affiché dans un cadre isolé (aperçu réel), avec code, versions, sélection d'élément, export. */
export function ArtifactViewer({ id, toast, onAsk, onClose, projects, compact }: {
  id: number; toast: Toast; onClose?: () => void; projects?: Project[]; compact?: boolean;
  /** demande de modification envoyée à Jarvis (conversation) ; sinon une mission est lancée depuis le visualiseur */
  onAsk?: (instruction: string, selection: Selection | null) => void;
}) {
  const [a, setA] = useState<Artifact | null>(null);
  const [mode, setMode] = useState<"apercu" | "code">("apercu");
  const [version, setVersion] = useState<number | null>(null);   // null : dernière version
  const [code, setCode] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [picking, setPicking] = useState(false);
  const [sel, setSel] = useState<Selection | null>(null);
  const [ask, setAsk] = useState("");
  const [mission, setMission] = useState<Mission | null>(null);
  const [check, setCheck] = useState<any>(null);
  const [checking, setChecking] = useState(false);
  const [frameKey, setFrameKey] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);
  const box = useRef<HTMLDivElement>(null);

  const load = async (v: number | null = version) => {
    try {
      const x = await api<Artifact>("GET", `/api/artifacts/${id}${v ? `?v=${v}` : ""}`);
      setA(x); setCode(x.content || ""); setErrors([]); setFrameKey((k) => k + 1);
    } catch (e: any) { toast(e.message, "error"); }
  };
  useEffect(() => { setVersion(null); setSel(null); setCheck(null); setMission(null); load(null); }, [id]);

  // l'aperçu se met à jour dès qu'une nouvelle version existe (modification par Jarvis ou par vous)
  useEvents((e) => {
    if (e.topic === "artifact" && e.artifact_id === id && version === null) load(null);
    if (e.topic === "mission" && mission && e.mission_id === mission.id) {
      api<Mission>("GET", `/api/missions/${mission.id}`).then(setMission).catch(() => {});
    }
  });

  // messages de l'iframe isolée : erreurs JavaScript, élément sélectionné
  useEffect(() => {
    const on = (e: MessageEvent) => {
      if (!frame.current || e.source !== frame.current.contentWindow || !e.data || e.data.jarvis !== true) return;
      if (e.data.kind === "error") setErrors((xs) => xs.length > 20 ? xs : [...xs, String(e.data.message).slice(0, 300)]);
      if (e.data.kind === "selected") { setSel({ selector: e.data.selector, tag: e.data.tag, text: e.data.text, html: e.data.html }); setPicking(false); }
    };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, []);

  const pick = () => {
    const on = !picking;
    setPicking(on);
    setMode("apercu");
    frame.current?.contentWindow?.postMessage({ jarvis: "pick", on }, "*");
  };

  const submitAsk = async (text = ask, selection = sel) => {
    const t = text.trim();
    if (!t) return;
    if (onAsk) { onAsk(t, selection); setAsk(""); setSel(null); return; }
    try {
      const m = await api<Mission>("POST", `/api/artifacts/${id}/edit`, { instruction: t, selection });
      setMission(m); setAsk(""); setSel(null);
      toast("Modification confiée au développeur de Jarvis.");
    } catch (e: any) { toast(e.message, "error"); }
  };

  const saveCode = async () => {
    try {
      const x = await api<Artifact>("PATCH", `/api/artifacts/${id}`, { content: code, note: "modification manuelle du code" });
      setA(x); setVersion(null); setMode("apercu"); setFrameKey((k) => k + 1);
      toast(`Version ${x.version} enregistrée.`);
    } catch (e: any) { toast(e.message, "error"); }
  };

  const restore = async (n: number) => {
    try {
      const x = await api<Artifact>("POST", `/api/artifacts/${id}/restore`, { n });
      setA(x); setVersion(null); setCode(x.content || ""); setFrameKey((k) => k + 1);
      toast(`Version ${n} rétablie (enregistrée comme version ${x.version}).`);
    } catch (e: any) { toast(e.message, "error"); }
  };

  const runCheck = async () => {
    setChecking(true);
    try { setCheck(await api("POST", `/api/artifacts/${id}/check`, { runtime: true })); } catch (e: any) { toast(e.message, "error"); }
    setChecking(false);
  };

  const rename = async () => {
    const t = window.prompt("Titre de l'artefact", a?.title || "");
    if (!t || !t.trim()) return;
    try { setA({ ...(await api<Artifact>("PATCH", `/api/artifacts/${id}`, { title: t.trim() })), content: a?.content }); } catch (e: any) { toast(e.message, "error"); }
  };

  const full = () => { box.current?.requestFullscreen?.().catch(() => toast("Plein écran refusé par le système.", "error")); };
  if (!a) return <div className="aview"><p className="muted pad">Chargement…</p></div>;
  const latest = a.version;
  const shown = a.shown_version || latest;
  const dirty = code !== (a.content || "");
  const running = mission && (mission.status === "en cours" || mission.status === "en attente");
  return (
    <div className={`aview ${compact ? "compact" : ""}`} ref={box}>
      <header className="aview-head">
        <div className="aview-title">
          <b title={a.title} onDoubleClick={rename}>{a.title}</b>
          <span className="tag ghost">{a.kind_label}</span>
          <select className="select compact" value={shown} aria-label="Version"
            onChange={(e) => { const n = +e.target.value; const v = n === latest ? null : n; setVersion(v); load(v); }}>
            {(a.versions || []).map((v) => <option key={v.n} value={v.n}>v{v.n}{v.n === latest ? " (actuelle)" : ""} — {v.note.slice(0, 40)}</option>)}
          </select>
        </div>
        <div className="seg two" role="tablist" aria-label="Affichage">
          <button className={mode === "apercu" ? "on" : ""} onClick={() => setMode("apercu")}><Icon name="eye" size={14} /> Aperçu</button>
          <button className={mode === "code" ? "on" : ""} onClick={() => setMode("code")}><Icon name="code" size={14} /> Code</button>
        </div>
        <span className="aview-tools">
          <button className={`icon-btn ${picking ? "on" : ""}`} title="Sélectionner un élément de l'aperçu pour le modifier" aria-label="Sélectionner un élément" onClick={pick}><Icon name="cursor" size={16} /></button>
          <button className="icon-btn" title="Recharger l'aperçu" aria-label="Recharger" onClick={() => { setErrors([]); setFrameKey((k) => k + 1); }}><Icon name="refresh" size={16} /></button>
          <button className="icon-btn" title="Contrôler (code et exécution)" aria-label="Contrôler" onClick={runCheck} disabled={checking}><Icon name="shield" size={16} /></button>
          <button className="icon-btn" title="Télécharger en HTML autonome" aria-label="Télécharger" onClick={() => download(`/api/artifacts/${id}/export?format=html${version ? `&v=${version}` : ""}`, `${a.title}.html`).catch((e) => toast(e.message, "error"))}><Icon name="download" size={16} /></button>
          <button className="icon-btn" title="Exporter (archive : page, source et images)" aria-label="Exporter" onClick={() => download(`/api/artifacts/${id}/export?format=zip`, `${a.title}.zip`).catch((e) => toast(e.message, "error"))}><Icon name="upload" size={16} /></button>
          <button className="icon-btn" title="Plein écran" aria-label="Plein écran" onClick={full}><Icon name="expand" size={16} /></button>
          {onClose && <button className="icon-btn" title="Fermer" aria-label="Fermer" onClick={onClose}><Icon name="x" size={16} /></button>}
        </span>
      </header>

      {version && version !== latest && (
        <div className="notice info slim"><Icon name="history" size={15} /><span>Vous regardez la version {version}.</span>
          <button className="btn" onClick={() => restore(version)}>Revenir à cette version</button>
          <button className="link" onClick={() => { setVersion(null); load(null); }}>Version actuelle</button></div>)}
      {picking && <div className="notice info slim"><Icon name="cursor" size={15} /><span>Cliquez sur un élément de l'aperçu.</span><button className="link" onClick={pick}>Annuler</button></div>}

      <div className="aview-body">
        <iframe key={frameKey} ref={frame} title={a.title} className={mode === "apercu" ? "" : "hidden-frame"}
          sandbox="allow-scripts allow-modals allow-downloads allow-pointer-lock" src={`${a.sandbox_url}`}
          referrerPolicy="no-referrer" />
        {mode === "code" && (
          <div className="aview-code">
            <textarea value={code} spellCheck={false} onChange={(e) => setCode(e.target.value)} aria-label="Code de l'artefact" />
            <div className="row">
              <span className="muted small">{code.length.toLocaleString("fr-FR")} caractères · une modification enregistrée crée une nouvelle version</span>
              <span className="grow" />
              <button className="btn" onClick={() => navigator.clipboard.writeText(code).then(() => toast("Code copié."))}><Icon name="copy" size={14} /> Copier</button>
              <button className="btn primary" disabled={!dirty} onClick={saveCode}>Enregistrer la version</button>
            </div>
          </div>
        )}
      </div>

      {(errors.length > 0 || check) && (
        <div className="aview-checks">
          {errors.length > 0 && <div className="notice error slim"><Icon name="warn" size={15} /><span>{errors.length} erreur{errors.length > 1 ? "s" : ""} JavaScript dans l'aperçu : {errors[0]}</span>
            <button className="btn" onClick={() => submitAsk("Corrige ces erreurs JavaScript sans rien changer d'autre :\n- " + errors.slice(0, 6).join("\n- "), null)}>Faire corriger</button></div>}
          {check && <div className={`notice ${check.ok ? "info" : "warn"} slim`}><Icon name="shield" size={15} />
            <span>{check.ok ? "Contrôle réussi" : "Problèmes relevés"} — {check.runtime?.checked ? "exécuté dans un navigateur isolé" : `code seulement (${check.runtime?.note || ""})`}
              {check.issues?.length > 0 && <>: {check.issues.map((i: any) => i.text).join(" · ")}</>}</span>
            <button className="link" onClick={() => setCheck(null)}>Fermer</button></div>}
        </div>
      )}

      {mission && <div className="aview-mission"><MissionSteps steps={mission.plan.steps as any} missionId={mission.id} status={mission.status} toast={toast} compact
        approval={mission.plan.steps.find((s) => s.approval) ? { n: 0, ...(mission.plan.steps.find((s) => s.approval)!.approval as any) } : null} />
        {!running && <button className="link" onClick={() => setMission(null)}>Masquer</button>}</div>}

      <footer className="aview-ask">
        {sel && <div className="sel-chip"><Icon name="cursor" size={13} /> <code>{sel.selector}</code><span className="muted">{sel.text.slice(0, 60)}</span>
          <button className="icon-btn" aria-label="Retirer la sélection" onClick={() => setSel(null)}><Icon name="x" size={13} /></button></div>}
        <div className="row">
          <input className="input" value={ask} placeholder={sel ? "Que faut-il changer sur cet élément ?" : "Modifier : décrivez le changement (ex. « mets le bouton en rouge »)"}
            onChange={(e) => setAsk(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") submitAsk(); }} aria-label="Modification demandée" />
          <button className="btn primary" disabled={!ask.trim() || !!running} onClick={() => submitAsk()}><Icon name="sparkle" size={14} /> Modifier</button>
        </div>
        {!compact && projects && (
          <div className="row small muted">
            Projet : <ProjectSelect projects={projects} value={a.project_id} onChange={async (pid) => {
              try { const x = await api<Artifact>("PATCH", `/api/artifacts/${id}`, { project_id: pid }); setA({ ...a, project_id: x.project_id }); toast("Artefact rangé dans le projet."); }
              catch (e: any) { toast(e.message, "error"); }
            }} />
            <span className="grow" /><span>créé le {fmtDate(a.created)} · modifié le {fmtDate(a.updated)}</span>
          </div>)}
      </footer>
    </div>
  );
}

const KIND_ICON: Record<string, string> = { html: "artifact", react: "code", svg: "image", mermaid: "activity", markdown: "book", slides: "panel" };

/** Bibliothèque personnelle des artefacts. */
export function ArtifactsView({ toast, active, openId, askJarvis }: {
  toast: Toast; active: boolean; openId: { id: number; n: number } | null; askJarvis: (text: string, tool?: string) => void;
}) {
  const [list, setList] = useState<Artifact[]>([]);
  const [q, setQ] = useState("");
  const [pf, setPf] = useState<number | null>(null);
  const [current, setCurrent] = useState<number | null>(null);
  const [newKind, setNewKind] = useState("html");
  const [prompt, setPrompt] = useState("");
  const [projects] = useProjects(active);
  const load = () => api<Artifact[]>("GET", `/api/artifacts?${pf ? `project_id=${pf}&` : ""}${q ? `q=${encodeURIComponent(q)}` : ""}`).then(setList).catch((e) => toast(e.message, "error"));
  useEffect(() => { if (active) load(); }, [active, pf, q]);
  useEffect(() => { if (openId) setCurrent(openId.id); }, [openId]);
  useEvents((e) => { if (e.topic === "artifact") load(); });

  const blank = async () => {
    const tpl: Record<string, string> = {
      html: "<!doctype html>\n<html lang=\"fr\">\n<head>\n<title>Nouvelle page</title>\n<style>body{font-family:system-ui;padding:2rem}</style>\n</head>\n<body>\n<h1>Nouvelle page</h1>\n</body>\n</html>",
      react: "import React, { useState } from \"react\";\n\nexport default function App() {\n  const [n, setN] = useState(0);\n  return <button onClick={() => setN(n + 1)}>Cliqué {n} fois</button>;\n}",
      svg: "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 200 100\"><rect x=\"10\" y=\"10\" width=\"180\" height=\"80\" rx=\"8\" fill=\"none\" stroke=\"#5cd0ff\"/></svg>",
      mermaid: "flowchart LR\n  A[Début] --> B{Décision}\n  B -->|oui| C[Action]\n  B -->|non| D[Fin]",
      markdown: "# Nouveau document\n\nTexte…", slides: "# Titre\n\n---\n\n## Deuxième diapositive\n\n- point",
    };
    try {
      const a = await api<Artifact>("POST", "/api/artifacts", { title: "Sans titre", kind: newKind, content: tpl[newKind], project_id: pf });
      setCurrent(a.id); load();
    } catch (e: any) { toast(e.message, "error"); }
  };

  const remove = async (a: Artifact) => {
    if (!window.confirm(`Supprimer l'artefact « ${a.title} » ? Il disparaît de la bibliothèque.`)) return;
    try { await api("DELETE", `/api/artifacts/${a.id}`); if (current === a.id) setCurrent(null); load(); } catch (e: any) { toast(e.message, "error"); }
  };

  return (
    <div className="studio-split">
      <aside className="studio-side">
        <div className="side-head"><h2>Artefacts</h2><span className="muted small">{list.length} création{list.length > 1 ? "s" : ""}</span></div>
        <div className="ask-box">
          <textarea className="input" rows={3} value={prompt} placeholder="Décrivez une application, un outil, une simulation, un schéma, une présentation…"
            onChange={(e) => setPrompt(e.target.value)} aria-label="Demande d'artefact" />
          <button className="btn primary wide" disabled={prompt.trim().length < 4} onClick={() => { askJarvis(prompt.trim(), "artifact"); setPrompt(""); }}>
            <Icon name="sparkle" size={14} /> Créer avec Jarvis</button>
          <div className="row small">
            <select className="select compact" value={newKind} onChange={(e) => setNewKind(e.target.value)} aria-label="Type">
              {["html", "react", "svg", "mermaid", "markdown", "slides"].map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
            <button className="btn" onClick={blank}><Icon name="plus" size={14} /> Vide</button>
          </div>
        </div>
        <div className="row">
          <div className="field-icon grow"><Icon name="search" size={14} /><input value={q} placeholder="Chercher" onChange={(e) => setQ(e.target.value)} aria-label="Chercher un artefact" /></div>
          <ProjectSelect projects={projects} value={pf} onChange={setPf} empty="Tous" />
        </div>
        <div className="alist">
          {list.length === 0 && <p className="muted pad">Aucun artefact. Demandez-en un à Jarvis, ici ou dans la conversation.</p>}
          {list.map((a) => (
            <div key={a.id} className={`aitem ${current === a.id ? "on" : ""}`}>
              <button onClick={() => setCurrent(a.id)}>
                <Icon name={KIND_ICON[a.kind] || "artifact"} size={16} />
                <span><b>{a.title}</b><i>{a.kind_label} · v{a.version}{a.project ? ` · ${a.project}` : ""} · {fmtDate(a.updated)}</i></span>
              </button>
              <button className="icon-btn" aria-label="Supprimer" title="Supprimer" onClick={() => remove(a)}><Icon name="trash" size={14} /></button>
            </div>
          ))}
        </div>
      </aside>
      <section className="studio-main">
        {current ? <ArtifactViewer id={current} toast={toast} projects={projects} />
          : <div className="empty big"><Icon name="artifact" size={34} /><p>Sélectionnez un artefact, ou demandez-en un à Jarvis.</p>
            <p className="muted small">Les artefacts s'exécutent dans un cadre isolé : sans accès à vos données ni à Internet, à part trois bibliothèques publiques (cdnjs, jsDelivr, unpkg).</p></div>}
      </section>
    </div>
  );
}
