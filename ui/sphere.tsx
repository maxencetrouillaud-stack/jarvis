import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, AppState, fmtDate } from "./api";
import { Icon } from "./common";
import { SEdge, SNode, SphereEngine } from "./sphere3d";

interface Data {
  version: string; nodes: SNode[]; edges: SEdge[]; truncated: { shown: number; total: number } | null;
  families: { key: string; label: string; color: string; count: number }[];
  modules: { key: string; label: string; color: string; count: number }[];
  stats: { documents: number; memories: number; concepts: number; relations: number; links: number; connections: number; zones: number; research: number };
}
interface Props {
  state: AppState; toast: (t: string, k?: string) => void; refreshState: () => void;
  goDoc: (id: number) => void; ask: (text: string) => void; goImport: () => void; goResearch: () => void;
  toList: (opts?: { add?: boolean; editId?: number }) => void;
}
const VIEWS: [string, string, string][] = [
  ["global", "Vue globale", "Toutes les connaissances et tous les souvenirs"],
  ["categories", "Catégories", "Les grandes familles ; cliquez sur l'une d'elles pour l'explorer"],
  ["relations", "Relations", "Les concepts du graphe et leurs connexions"],
  ["perso", "Mémoire perso.", "Vue mémoire personnelle : souvenirs et préférences"],
  ["projets", "Projets", "Vos projets et ce qui s'y rattache"],
  ["documents", "Documents", "Fichiers et pages indexés"],
];
const KIND: Record<string, string> = { memory: "Souvenir", doc: "Document", concept: "Concept", family: "Famille", category: "Catégorie",
  research: "Recherche du chercheur", cluster: "Zone vectorielle" };
const LINK_KIND: Record<string, string> = { "sémantique": "contenu proche", recherche: "issu de cette recherche" };
const norm = (s: string) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");

export function SphereView({ state, toast, refreshState, goDoc, ask, goImport, goResearch, toList }: Props) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [view, setView] = useState("global");
  const [fams, setFams] = useState<Set<string>>(new Set());
  const [mods, setMods] = useState<Set<string>>(new Set());
  const [byModule, setByModule] = useState(false);
  const [sel, setSel] = useState(-1);
  const [depth, setDepth] = useState(1);
  const [detail, setDetail] = useState<any | null>(null);
  const [q, setQ] = useState("");
  const [auto, setAuto] = useState(true);
  const [simple, setSimple] = useState(false);
  const [fps, setFps] = useState(60);
  const canvas = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const engine = useRef<SphereEngine | null>(null);
  const selId = useRef<string | null>(null);
  const dataRef = useRef<Data | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api<Data>("GET", "/api/sphere");
      dataRef.current = d;
      setData(d);
    } catch (e: any) { setError(e.message); }
  }, []);

  // moteur 3D : créé une fois, libéré à la sortie de l'onglet
  useEffect(() => {
    try {
      const eng = new SphereEngine(canvas.current!, overlay.current!);
      engine.current = eng;
      eng.onPerf = (f, should) => { setFps(Math.round(f)); if (should) { setSimple(true); toast("Affichage ralenti : passage en mode simplifié.", "info"); } };
      (window as any).__jarvisSphere = {
        screenOf: (id: string) => { const i = dataRef.current?.nodes.findIndex((n) => n.id === id) ?? -1; const s = i >= 0 ? eng.screenOf(i) : null; const r = canvas.current!.getBoundingClientRect(); return s ? { x: s.x + r.left, y: s.y + r.top } : null; },
        count: () => dataRef.current?.nodes.length || 0, frames: () => eng.frames, fps: () => eng.getFps(), spawned: () => eng.spawned, visible: () => eng.visibleCount(),
      };
    } catch (e: any) { setError(e.message); }
    load();
    return () => { engine.current?.dispose(); engine.current = null; delete (window as any).__jarvisSphere; };
  }, []);

  // synchronisation : la sphère se recharge dès que la base change (import, souvenir appris, relation…)
  useEffect(() => {
    const t = setInterval(async () => {
      try { const v = await api<{ version: string }>("GET", "/api/sphere/version"); if (dataRef.current && v.version !== dataRef.current.version) load(); } catch { /* moteur momentanément injoignable */ }
    }, 4000);
    return () => clearInterval(t);
  }, []);

  const adj = useMemo(() => {
    const a: number[][] = (data?.nodes || []).map(() => []);
    data?.edges.forEach(([x, y]) => { a[x].push(y); a[y].push(x); });
    return a;
  }, [data]);
  const colors = useMemo(() => Object.fromEntries((data?.families || []).map((f) => [f.key, f.color])), [data]);
  const modColors = useMemo(() => Object.fromEntries((data?.modules || []).map((f) => [f.key, f.color])), [data]);
  const colorOf = (n: SNode) => (byModule && n.m && modColors[n.m]) || colors[n.f];

  const mask = useMemo(() => {
    if (!data) return new Uint8Array(0);
    const n = data.nodes, m = new Uint8Array(n.length);
    const linked = new Set<number>();
    if (view === "relations") data.edges.forEach(([a, b, t]) => { if (t === 2) { linked.add(a); linked.add(b); } });
    if (view === "projets") n.forEach((x, i) => { if (x.f === "projets" && x.k === "memory") { linked.add(i); adj[i].forEach((j) => linked.add(j)); } });
    n.forEach((x, i) => {
      const hub = x.k === "family" || x.k === "category";
      let ok = view === "global" ? true
        : view === "categories" ? hub
        : view === "relations" ? x.k === "concept" || (x.k === "doc" && linked.has(i))
        : view === "perso" ? x.f === "perso"
        : view === "projets" ? linked.has(i) || x.id === "f:projets"
        : x.k === "doc" || hub;
      if (ok && fams.size && !fams.has(x.f)) ok = false;
      if (ok && (x.k === "cluster" || x.k === "research") && view !== "global" && !(view === "documents" && x.k === "research")) ok = false;
      if (ok && mods.size && !hub && !(x.m && mods.has(x.m))) ok = false;
      m[i] = ok ? 1 : 0;
    });
    if (mods.size) {   // filtre par module : une catégorie ou une famille ne reste que si elle contient un élément affiché
      for (const kind of ["category", "family"]) n.forEach((x, i) => { if (x.k === kind && m[i]) m[i] = adj[i].some((j) => m[j] && n[j].k !== "family") ? 1 : 0; });
    }
    return m;
  }, [data, view, fams, mods, adj]);

  useEffect(() => {
    if (!data || !engine.current) return;
    engine.current.setData(data.nodes, data.edges, colors, byModule ? (n) => (n.m ? modColors[n.m] : "") : null);
    engine.current.setVisible(mask);
    const i = selId.current ? data.nodes.findIndex((n) => n.id === selId.current) : -1;
    setSel(i);
    if (i < 0) { setDetail(null); selId.current = null; }
  }, [data, byModule]);
  useEffect(() => { engine.current?.setVisible(mask); }, [mask]);
  useEffect(() => { if (engine.current) engine.current.autoRotate = auto; }, [auto]);
  useEffect(() => { engine.current?.setSimplified(simple); }, [simple]);

  const matches = useMemo(() => {
    const s = norm(q.trim());
    if (!data || s.length < 2) return [];
    const out: number[] = [];
    data.nodes.forEach((n, i) => { if (mask[i] && norm(n.l).includes(s)) out.push(i); });
    return out;
  }, [q, data, mask]);

  // surbrillance : résultat de recherche, ou voisinage de l'élément sélectionné jusqu'à la profondeur explorée
  const hood = useMemo(() => {
    if (sel < 0) return null;
    const seen = new Set<number>([sel]);
    let front = [sel];
    for (let d = 0; d < depth; d++) {
      const next: number[] = [];
      front.forEach((i) => adj[i]?.forEach((j) => { if (!seen.has(j) && mask[j]) { seen.add(j); next.push(j); } }));
      front = next;
    }
    return seen;
  }, [sel, depth, adj, mask]);
  useEffect(() => {
    engine.current?.setHighlight(sel >= 0 ? hood : matches.length ? new Set(matches) : null, sel);
  }, [hood, matches, sel, byModule]);

  const select = useCallback((i: number, fly = true) => {
    const d = dataRef.current;
    if (!d || i < 0 || !d.nodes[i]) { setSel(-1); setDetail(null); selId.current = null; return; }
    const n = d.nodes[i];
    selId.current = n.id;
    setSel(i); setDepth(1); setDetail(null);
    if (fly) engine.current?.flyTo(i);
    api("GET", `/api/sphere/node?id=${encodeURIComponent(n.id)}`).then((x) => { if (selId.current === n.id) setDetail(x); }).catch((e) => toast(e.message, "error"));
  }, []);
  useEffect(() => { if (engine.current) engine.current.onSelect = (i) => select(i); }, [select]);
  const selectId = (id: string) => { const i = data!.nodes.findIndex((n) => n.id === id); if (i >= 0) select(i); else toast("Cet élément n'est pas affiché dans la sphère.", "info"); };

  const remove = async () => {
    if (!detail) return;
    const what = detail.kind === "memory" ? "ce souvenir" : `le document « ${detail.name} » et tous ses passages`;
    if (!window.confirm(`Supprimer définitivement ${what} ?`)) return;
    try {
      await api("DELETE", detail.kind === "memory" ? `/api/memories/${detail.id.slice(2)}` : `/api/kb/documents/${detail.doc_id}`);
      select(-1); await load(); refreshState(); toast("Supprimé.");
    } catch (e: any) { toast(e.message, "error"); }
  };
  const removeRelation = async (r: any) => {
    if (!window.confirm(`Supprimer la relation « ${r.src} — ${r.label} → ${r.dst} » ?`)) return;
    try { await api("DELETE", `/api/kb/relations/${r.id}`); await load(); refreshState(); if (selId.current) { const i = dataRef.current!.nodes.findIndex((n) => n.id === selId.current); select(i, false); } } catch (e: any) { toast(e.message, "error"); }
  };

  const toggleFam = (k: string) => { const s = new Set(fams); s.has(k) ? s.delete(k) : s.add(k); setFams(s); };
  const toggleMod = (k: string) => { const s = new Set(mods); s.has(k) ? s.delete(k) : s.add(k); setMods(s); select(-1); };
  const real = data ? data.stats.documents + data.stats.memories + data.stats.concepts : 0;
  const node = sel >= 0 && data ? data.nodes[sel] : null;
  const st = data?.stats;

  return (
    <div className="sphere" ref={wrap}>
      <canvas ref={canvas} className="sphere-gl" aria-label="Sphère 3D de la mémoire" />
      <canvas ref={overlay} className="sphere-labels" aria-hidden="true" />

      <div className="sphere-top">
        <div className="field-icon sphere-search">
          <Icon name="search" size={15} />
          <input value={q} placeholder="Rechercher une connaissance ou un souvenir" aria-label="Rechercher dans la sphère"
            onChange={(e) => { setQ(e.target.value); if (sel >= 0) select(-1); }}
            onKeyDown={(e) => { if (e.key === "Enter" && matches.length) select(matches[0]); }} />
          {q.trim().length >= 2 && sel < 0 && (
            <div className="sphere-hits">
              {matches.length === 0 ? <p className="muted pad">Aucun élément ne correspond.</p> : (<>
                <p className="muted small pad">{matches.length} élément{matches.length > 1 ? "s" : ""} mis en évidence</p>
                {matches.slice(0, 8).map((i) => <button key={i} onClick={() => select(i)}><i style={{ background: colorOf(data!.nodes[i]) }} />{data!.nodes[i].l}<span>{KIND[data!.nodes[i].k]}</span></button>)}
              </>)}
            </div>
          )}
        </div>
        <div className="seg sphere-views" role="tablist" aria-label="Mode d'affichage">
          {VIEWS.map(([id, label, hint]) => <button key={id} role="tab" aria-selected={view === id} title={hint} className={view === id ? "on" : ""} onClick={() => { setView(id); select(-1); engine.current?.flyHome(); }}>{label}</button>)}
        </div>
        <button className="btn" onClick={() => toList()} title="Afficher la mémoire personnelle sous forme de liste">Liste</button>
      </div>

      <aside className="sphere-left">
        <h4>Modules</h4>
        {data?.modules.map((f) => (
          <button key={f.key} className={`fam ${mods.size && !mods.has(f.key) ? "off" : ""}`} onClick={() => toggleMod(f.key)} aria-pressed={mods.has(f.key)} disabled={!f.count}
            title={`Afficher uniquement : ${f.label}`}>
            <i style={{ background: byModule ? f.color : "#8aa0bd", boxShadow: byModule ? `0 0 8px ${f.color}` : "none" }} /><span>{f.label}</span><b>{f.count}</b>
          </button>
        ))}
        <div className="row">
          <button className={`chip-toggle ${byModule ? "on" : ""}`} aria-pressed={byModule} onClick={() => setByModule(!byModule)} title="Colorer les nœuds par module au lieu de par domaine">Couleurs par module</button>
          {mods.size > 0 && <button className="link" onClick={() => setMods(new Set())}>Tous les modules</button>}
        </div>
        <h4>Familles</h4>
        {data?.families.map((f) => (
          <button key={f.key} className={`fam ${fams.size && !fams.has(f.key) ? "off" : ""}`} onClick={() => toggleFam(f.key)} aria-pressed={fams.has(f.key)} disabled={!f.count}>
            <i style={{ background: f.color, boxShadow: `0 0 8px ${f.color}` }} /><span>{f.label}</span><b>{f.count}</b>
          </button>
        ))}
        {fams.size > 0 && <button className="link" onClick={() => setFams(new Set())}>Tout afficher</button>}
        {st && <div className="sphere-stats">
          <div><b>{st.documents}</b> documents</div><div><b>{st.memories}</b> souvenirs</div>
          <div><b>{st.concepts}</b> concepts</div><div><b>{st.relations}</b> relations</div>
          <div><b>{st.connections}</b> connexions</div><div><b>{st.zones}</b> zones</div>
        </div>}
        <div className="sphere-add">
          <button className="btn" onClick={() => toList({ add: true })}><Icon name="plus" size={15} /> Ajouter un souvenir</button>
          <button className="btn" onClick={goImport}><Icon name="upload" size={15} /> Importer un document</button>
        </div>
      </aside>

      <div className="sphere-ctl" role="toolbar" aria-label="Contrôles de la sphère">
        <button className="icon-btn" title="Zoom avant" aria-label="Zoom avant" onClick={() => engine.current?.zoom(0.75)}><Icon name="plus" /></button>
        <button className="icon-btn" title="Zoom arrière" aria-label="Zoom arrière" onClick={() => engine.current?.zoom(1.33)}><Icon name="minus" /></button>
        <button className="icon-btn" title="Recentrer" aria-label="Recentrer" onClick={() => { select(-1); engine.current?.flyHome(); }}><Icon name="target" /></button>
        <button className={`icon-btn ${auto ? "on" : ""}`} title="Rotation automatique" aria-label="Rotation automatique" aria-pressed={auto} onClick={() => setAuto(!auto)}><Icon name="refresh" /></button>
        <button className="icon-btn" title="Plein écran" aria-label="Plein écran" onClick={() => (document.fullscreenElement ? document.exitFullscreen() : wrap.current?.requestFullscreen())}><Icon name="expand" /></button>
        <button className={`icon-btn ${simple ? "on" : ""}`} title={`Mode simplifié (moins d'effets) — ${fps} images/s`} aria-label="Mode simplifié" aria-pressed={simple} onClick={() => setSimple(!simple)}><Icon name="bolt" /></button>
      </div>
      <p className="sphere-help">Glisser : tourner · Molette : zoom · Maj + glisser ou clic droit : déplacer · Clic : ouvrir</p>

      {error && <div className="sphere-msg notice error"><Icon name="warn" /><span>{error}</span></div>}
      {data && real === 0 && !error && (
        <div className="sphere-msg"><h3>La sphère est encore vide</h3>
          <p className="muted">Seules les familles sont affichées. Chaque souvenir appris en conversation et chaque document importé y apparaîtra comme un nœud.</p></div>
      )}
      {data?.truncated && <div className="sphere-trunc">{data.truncated.shown} éléments affichés sur {data.truncated.total} (les plus récents et les plus reliés)</div>}

      {node && (
        <aside className="sphere-detail" aria-live="polite">
          <div className="row"><span className="cat" style={{ color: colorOf(node) }}>{KIND[node.k]}</span>
            {detail?.module && <span className="cat ghost">{data!.modules.find((m) => m.key === detail.module)?.label}</span>}<span className="grow" />
            <button className="icon-btn" aria-label="Fermer" onClick={() => select(-1)}><Icon name="x" /></button></div>
          {!detail ? <p className="muted">Chargement…</p> : (<>
            <h3>{detail.name}</h3>
            {detail.description && <p className="sd-desc">{detail.description}</p>}
            <dl>
              <dt>Catégorie</dt><dd>{detail.category}</dd>
              {detail.keywords && <><dt>Mots-clés</dt><dd>{detail.keywords}</dd></>}
              {detail.source && <><dt>Source</dt><dd>{/^https?:/.test(detail.source) ? <a href={detail.source} target="_blank" rel="noopener noreferrer">{detail.source.replace(/^https?:\/\//, "").slice(0, 60)}</a> : detail.source}{detail.license ? ` — ${detail.license}` : ""}</dd></>}
              {detail.reliability && <><dt>Fiabilité</dt><dd>{detail.reliability}/5 · {detail.passages} passage{detail.passages > 1 ? "s" : ""}</dd></>}
              {detail.importance && <><dt>Importance</dt><dd>{detail.importance}/5 · utilisé {detail.use_count} fois</dd></>}
              {detail.extracts && (detail.extracts.formule + detail.extracts.tableau + detail.extracts["donnée"] > 0) && <><dt>Relevé</dt><dd>{detail.extracts.formule} formule(s) · {detail.extracts.tableau} tableau(x) · {detail.extracts["donnée"]} donnée(s)</dd></>}
              {detail.added && <><dt>Ajouté le</dt><dd>{fmtDate(detail.added)}</dd></>}
              {detail.verified && <><dt>{detail.kind === "memory" ? "Modifié le" : "Vérifié le"}</dt><dd>{fmtDate(detail.verified)}</dd></>}
            </dl>
            {detail.relations?.length > 0 && (<><h4>Relations ({detail.relations.length})</h4>
              <div className="sd-list">{detail.relations.map((r: any, i: number) => (
                <div key={i} className="sd-rel">
                  <button className="link" onClick={() => selectId(`e:${r.src_id}`)}>{r.src}</button><em>{r.label}</em><button className="link" onClick={() => selectId(`e:${r.dst_id}`)}>{r.dst}</button>
                  {detail.kind === "concept" && <button className="icon-btn" aria-label="Supprimer la relation" title="Supprimer cette relation" onClick={() => removeRelation(r)}><Icon name="trash" size={13} /></button>}
                </div>))}</div></>)}
            {detail.connections?.length > 0 && (<><h4>Connexions ({detail.connections.length})</h4>
              <div className="sd-list">{detail.connections.map((c: any) => (
                <div key={c.id} className="sd-rel"><button className="link" onClick={() => selectId(c.id)}>{c.label}</button><em>{LINK_KIND[c.kind] || c.kind}</em></div>))}</div></>)}
            {detail.docs?.length > 0 && (<><h4>Documents sources</h4>
              <div className="sd-list">{detail.docs.map((d: any) => <button key={d.doc_id} className="link" onClick={() => selectId(`d:${d.doc_id}`)}>{d.title}</button>)}</div></>)}
            {detail.children && (<><h4>{detail.kind === "research" ? "Connaissances validées" : "Éléments"} ({detail.children_total})</h4>
              <div className="sd-list">{detail.children.map((c: any) => <button key={c.id} className="link" onClick={() => selectId(c.id)}>{c.label}</button>)}
                {detail.children_total > detail.children.length && <span className="muted small">… et {detail.children_total - detail.children.length} autres</span>}</div></>)}
            <div className="sd-actions">
              <button className="btn primary" onClick={() => setDepth(depth + 1)} title="Étendre la surbrillance aux éléments reliés, un pas plus loin">
                <Icon name="target" size={15} /> Explorer plus loin <small>({(hood?.size || 1) - 1} relié{(hood?.size || 1) - 1 > 1 ? "s" : ""}, {depth} pas)</small></button>
              {(detail.kind === "family" || detail.kind === "category") && view === "categories" &&
                <button className="btn" onClick={() => { setFams(new Set([node.f])); setView("global"); }}>Développer cette famille</button>}
              {detail.kind === "doc" && <button className="btn" onClick={() => goDoc(detail.doc_id)}><Icon name="book" size={15} /> Ouvrir la fiche complète</button>}
              {detail.kind === "research" && <button className="btn" onClick={goResearch}><Icon name="search" size={15} /> Ouvrir dans le chercheur</button>}
              {detail.kind === "memory" && <button className="btn" onClick={() => toList({ editId: +detail.id.slice(2) })}><Icon name="edit" size={15} /> Modifier</button>}
              {detail.kind !== "cluster" && <button className="btn" onClick={() => ask(detail.kind === "memory" ? `Que sais-tu à ce sujet, et à quoi est-ce relié : « ${detail.name} » ?` : `Présente-moi ce que tu sais sur « ${detail.name} » et les connaissances qui y sont liées.`)}>
                <Icon name="chat" size={15} /> Demander à {state.settings.assistant_name}</button>}
              {(detail.kind === "memory" || detail.kind === "doc") && <button className="btn danger" onClick={remove}><Icon name="trash" size={15} /> Supprimer</button>}
            </div>
          </>)}
        </aside>
      )}
    </div>
  );
}
