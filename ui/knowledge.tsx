import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, AppState, fmtDate } from "./api";
import { Dashboard, Indexing, JobsTable, Sources } from "./center";
import { Icon } from "./common";
import { Cat, Ctx, Doc, DOC_MODULES, isUrl, KbState, LibFilter, moduleColor, moduleLabel } from "./kbtypes";
import { ResearchTab } from "./research";

interface P {
  state: AppState; setState: (s: AppState) => void; toast: (t: string, k?: string) => void; focusDoc: { id: number; n: number } | null;
  openTab?: { tab: string; n: number } | null; toSphere: () => void; goModels: () => void;
}

const TABS = [["overview", "Tableau de bord"], ["docs", "Bibliothèque"], ["import", "Importer"], ["search", "Recherche"],
  ["research", "Chercheur"], ["sources", "Sources"], ["graph", "Graphe"], ["todo", "À traiter"], ["index", "Indexation"], ["journal", "Journal"]];
const KIND_LABEL: Record<string, string> = { formule: "Formules", tableau: "Tableaux", "donnée": "Données chiffrées" };

function b64(buf: ArrayBuffer) {
  const bytes = new Uint8Array(buf);
  let out = "";
  for (let i = 0; i < bytes.length; i += 0x8000) out += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(out);
}

export function CatSelect({ cats, value, onChange, empty }: { cats: Cat[]; value: number | null; onChange: (v: number | null) => void; empty: string }) {
  const tops = cats.filter((c) => c.parent_id === null);
  return (
    <select className="select" value={value ?? ""} onChange={(e) => onChange(e.target.value ? +e.target.value : null)}>
      <option value="">{empty}</option>
      {tops.map((t) => (
        <optgroup key={t.id} label={t.name}>
          <option value={t.id}>{t.name} (général)</option>
          {cats.filter((c) => c.parent_id === t.id).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </optgroup>
      ))}
    </select>
  );
}

export function KnowledgeView({ state, setState, toast, focusDoc, openTab, toSphere, goModels }: P) {
  const [tab, setTab] = useState("overview");
  const [kb, setKb] = useState<KbState | null>(null);
  const [docId, setDocId] = useState<number | null>(null);
  const [lib, setLib] = useState<LibFilter>({ n: 0 });
  const load = () => api<KbState>("GET", "/api/kb/state").then(setKb).catch((e) => toast(e.message, "error"));
  useEffect(() => { load(); }, []);
  useEffect(() => { if (focusDoc) { setTab("docs"); setDocId(focusDoc.id); } }, [focusDoc]);
  useEffect(() => { if (openTab) { setTab(openTab.tab); setDocId(null); } }, [openTab]);
  const active = kb?.stats.jobs_active || 0;
  useEffect(() => { if (!active) return; const t = setInterval(load, 1500); return () => clearInterval(t); }, [active]);
  if (!kb) return <div className="panel"><p className="muted">Chargement…</p></div>;
  const todo = kb.stats.duplicates + kb.stats.proposals + kb.stats.gaps + kb.stats.stale + kb.stats.unclassified + kb.stats.obsolete;
  const ctx: Ctx = {
    kb, load, toast, state, setState, toSphere, goModels,
    openDoc: (id: number) => { setTab("docs"); setDocId(id); },
    go: (t: string) => { setTab(t); setDocId(null); },
    openLibrary: (f) => { setLib({ ...f, n: Date.now() }); setTab("docs"); setDocId(null); },
  };

  return (
    <div className="panel">
      <header className="panel-head">
        <div><h2>Centre de connaissances</h2>
          <p className="muted">Le cerveau de {state.settings.assistant_name} : bibliothèque technique, mémoire vectorielle, chercheur autonome. Chaque connaissance garde sa source et apparaît dans la sphère.</p></div>
        {active > 0 && <span className="pill ok"><span className="dots"><i /><i /><i /></span> {active} indexation{active > 1 ? "s" : ""} en cours</span>}
      </header>
      <div className="tabs" role="tablist">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""} onClick={() => { setTab(id); setDocId(null); }}>
            {label}{id === "todo" && todo > 0 && <b>{todo}</b>}{id === "docs" && <i>{kb.stats.documents}</i>}
            {id === "research" && kb.pending_findings > 0 && <b>{kb.pending_findings}</b>}{id === "index" && kb.stats.job_errors > 0 && <b>{kb.stats.job_errors}</b>}
          </button>
        ))}
      </div>
      {tab === "overview" && <Dashboard {...ctx} />}
      {tab === "docs" && (docId ? <DocDetail {...ctx} id={docId} back={() => setDocId(null)} /> : <Docs {...ctx} preset={lib} />)}
      {tab === "import" && <Import {...ctx} />}
      {tab === "search" && <Search {...ctx} />}
      {tab === "research" && <ResearchTab {...ctx} />}
      {tab === "sources" && <Sources {...ctx} />}
      {tab === "graph" && <Graph {...ctx} />}
      {tab === "todo" && <Todo {...ctx} />}
      {tab === "index" && <Indexing {...ctx} />}
      {tab === "journal" && <Journal {...ctx} />}
    </div>
  );
}

function Jobs({ kb, go }: Ctx) {
  if (!kb.jobs.length) return null;
  return (
    <div className="card">
      <div className="row"><h3>Suivi des indexations</h3><span className="grow" />
        <button className="btn" onClick={() => go("index")}>Gérer l'indexation</button></div>
      <JobsTable jobs={kb.jobs.slice(0, 8)} />
    </div>
  );
}

function DocRow({ d, onOpen, extra }: { d: Doc; onOpen: () => void; extra?: React.ReactNode }) {
  return (
    <article className="mem">
      <div className="mem-body">
        <button className="doc-title" onClick={onOpen}>{d.title}</button>
        <div className="mem-meta">
          <span className="cat">{d.category}</span>
          {d.archived ? <span className="tag ghost">archivé</span> : d.superseded_by ? <span className="tag warn">version plus récente disponible</span> : null}
          <span>{d.source_type}{d.source && d.source !== d.source_type ? ` · ${d.source.replace(/^https?:\/\//, "").slice(0, 60)}` : ""}</span>
          <span>{d.n_chunks} passage{d.n_chunks > 1 ? "s" : ""}</span>
          <span title="Niveau de fiabilité">fiabilité {d.reliability}/5</span>
          <span>vérifié le {fmtDate(d.verified).slice(0, 10)}</span>
          {d.version > 1 && <span>version {d.version}</span>}
        </div>
      </div>
      {extra && <div className="row">{extra}</div>}
    </article>
  );
}

function Docs({ kb, toast, openDoc, state, preset }: Ctx & { preset: LibFilter }) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [cat, setCat] = useState<number | null>(preset.category ?? null);
  const [mod, setMod] = useState(preset.module || "");
  const [st, setSt] = useState(preset.state || "active");
  const [q, setQ] = useState("");
  const [tree, setTree] = useState(false);
  useEffect(() => { if (preset.n) { setCat(preset.category ?? null); setMod(preset.module || ""); setSt(preset.state || "active"); } }, [preset.n]);
  useEffect(() => {
    const p = new URLSearchParams();
    if (cat) p.set("category", String(cat));
    if (mod) p.set("module", mod);
    if (st) p.set("state", st);
    if (q.trim()) p.set("q", q.trim());
    const t = setTimeout(() => api<Doc[]>("GET", `/api/kb/documents?${p}`).then(setDocs).catch((e) => toast(e.message, "error")), q ? 220 : 0);
    return () => clearTimeout(t);
  }, [cat, mod, st, q, kb.stats.documents, kb.stats.archived, kb.stats.jobs_active]);
  const tops = kb.categories.filter((c) => c.parent_id === null);
  return (<>
    <div className="row filters wrap">
      <div className="field-icon grow"><Icon name="search" size={15} /><input placeholder="Filtrer par titre" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filtrer les documents par titre" /></div>
      <select className="select" value={mod} onChange={(e) => setMod(e.target.value)} aria-label="Module">
        <option value="">Tous les modules</option>{DOC_MODULES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      <CatSelect cats={kb.categories} value={cat} onChange={setCat} empty="Toutes les catégories" />
      <select className="select" value={st} onChange={(e) => setSt(e.target.value)} aria-label="État">
        <option value="active">En service</option><option value="obsolete">Anciennes versions</option><option value="archived">Archivés</option><option value="">Tous</option></select>
      <button className={`chip-toggle ${tree ? "on" : ""}`} aria-pressed={tree} onClick={() => setTree(!tree)}>Explorer les catégories</button>
    </div>
    {tree && (
      <div className="card cattree">
        {tops.map((t) => (
          <div key={t.id}><button className={`link ${cat === t.id ? "on" : ""}`} onClick={() => setCat(t.id)}><b>{t.name}</b></button>
            <div>{kb.categories.filter((c) => c.parent_id === t.id).map((c) => (
              <button key={c.id} className={`chip ${c.n ? "done" : ""} ${cat === c.id ? "sel" : ""}`} onClick={() => setCat(cat === c.id ? null : c.id)} disabled={!c.n && cat !== c.id}>{c.name}{c.n > 0 && ` · ${c.n}`}</button>))}</div></div>
        ))}
      </div>
    )}
    <p className="muted small">{docs.length} document{docs.length > 1 ? "s" : ""}{docs.length === 500 ? " (500 premiers)" : ""}{mod ? ` · ${moduleLabel(state, mod)}` : ""}</p>
    <div className="mem-list">
      {docs.length === 0 && <p className="empty">Aucun document ne correspond à ces filtres.</p>}
      {docs.map((d) => <DocRow key={d.id} d={d} onOpen={() => openDoc(d.id)} />)}
    </div>
  </>);
}

function DocDetail({ kb, load, toast, id, back, state, openDoc, toSphere }: Ctx & { id: number; back: () => void }) {
  const [d, setD] = useState<Doc | null>(null);
  const [draft, setDraft] = useState<any>({});
  const [old, setOld] = useState<string | null>(null);
  const reload = () => api<Doc>("GET", `/api/kb/documents/${id}`).then((x) => { setD(x); setDraft({}); }).catch((e) => { toast(e.message, "error"); back(); });
  useEffect(() => { reload(); }, [id, kb.stats.jobs_active]);
  if (!d) return <p className="muted">Chargement…</p>;
  const v = (k: string) => (k in draft ? draft[k] : (d as any)[k]);
  const set = (k: string, val: any) => setDraft({ ...draft, [k]: val });
  const save = async (p: any) => { try { await api("PATCH", `/api/kb/documents/${id}`, p); reload(); load(); } catch (e: any) { toast(e.message, "error"); } };
  const job = async (path: string, msg: string) => { try { await api("POST", `/api/kb/documents/${id}/${path}`, {}); toast(msg); load(); } catch (e: any) { toast(e.message, "error"); } };
  const web = d.source_type === "page web" || d.source_type === "wikipédia";
  return (<>
    <div className="row"><button className="btn" onClick={back}>← Tous les documents</button><span className="grow" />
      {web && <button className="btn" onClick={() => job("refresh", "Actualisation mise en file.")}><Icon name="refresh" /> Actualiser depuis la source</button>}
      <button className="btn" onClick={() => job("graph", "Extraction des relations mise en file.")} disabled={!kb.llm_ready} title={kb.llm_ready ? "" : "Aucun modèle disponible"}>Extraire les relations</button>
      <button className="btn" onClick={() => save({ mark_verified: true })}><Icon name="check" /> Marquer comme vérifié</button>
      <button className="btn danger" onClick={async () => {
        if (!window.confirm(`Supprimer définitivement « ${d.title} » et ses ${d.n_chunks} passages ?`)) return;
        try { await api("DELETE", `/api/kb/documents/${id}`); load(); back(); } catch (e: any) { toast(e.message, "error"); }
      }}><Icon name="trash" /> Supprimer</button></div>
    {d.archived ? (
      <div className="notice warn"><Icon name="warn" /><span>Document archivé : il est conservé mais n'est plus utilisé dans les réponses.</span>
        <button className="btn" onClick={() => save({ archived: false })}>Remettre en service</button></div>
    ) : d.superseded_by ? (
      <div className="notice warn"><Icon name="warn" /><span>Une version plus récente semble exister : <button className="link" onClick={() => openDoc(d.superseded_by!)}>{d.newer_title}</button>. Rien n'est retiré sans votre décision.</span>
        <button className="btn" onClick={() => save({ archived: true })}>Archiver cette ancienne version</button>
        <button className="btn" onClick={() => save({ not_obsolete: true })}>Ce sont deux documents distincts</button></div>
    ) : null}
    <div className="card">
      <div className="row wrap"><span className="cat" style={{ color: moduleColor(state, d.module) }}>{moduleLabel(state, d.module)}</span>
        {d.class_how && <span className="tag ghost" title="Comment la catégorie a été choisie">classé par : {d.class_how}</span>}
        <span className="grow" /><button className="link" onClick={toSphere}>Voir dans la sphère</button></div>
      <div className="form">
        <label>Titre<input className="input" value={v("title")} onChange={(e) => set("title", e.target.value)} /></label>
        <label>Catégorie<CatSelect cats={kb.categories} value={v("category_id")} onChange={(x) => set("category_id", x)} empty="Non classé" /></label>
        <label>Source d'origine<input className="input mono" value={v("source")} onChange={(e) => set("source", e.target.value)} /></label>
        <label>Niveau de fiabilité
          <select className="select" value={v("reliability")} onChange={(e) => set("reliability", +e.target.value)}>
            {Object.entries(kb.reliability).map(([n, l]) => <option key={n} value={n}>{n} — {l}</option>)}</select></label>
        <label>Licence / droits<input className="input" value={v("license")} placeholder="Ex. : CC BY-SA, cours personnel, usage interne" onChange={(e) => set("license", e.target.value)} /></label>
        <label>Date de publication<input className="input" value={v("published")} placeholder="Si connue" onChange={(e) => set("published", e.target.value)} /></label>
        <label>Mots-clés<input className="input" value={v("keywords")} onChange={(e) => set("keywords", e.target.value)} /></label>
        <label>Références bibliographiques<input className="input" value={v("refs")} onChange={(e) => set("refs", e.target.value)} /></label>
      </div>
      <label>Description<textarea className="input" rows={2} value={v("description")} onChange={(e) => set("description", e.target.value)} /></label>
      <div className="row"><span className="muted small">Importé le {fmtDate(d.imported)} · vérifié le {fmtDate(d.verified)} · version {d.version} · {d.chars.toLocaleString("fr-FR")} caractères
        {isUrl(d.source) && <> · <a href={d.source} target="_blank" rel="noopener noreferrer">ouvrir la source</a></>}{d.original && <> · fichier d'origine : <code>{d.original}</code></>}</span>
        <span className="grow" /><button className="btn primary" disabled={!Object.keys(draft).length} onClick={() => save(draft)}>Enregistrer</button></div>
    </div>
    {d.versions!.length > 0 && (
      <div className="card"><h3>Versions archivées</h3>
        {d.versions!.map((x) => <div key={x.id} className="row"><span>Version {x.version}, remplacée le {fmtDate(x.saved)}</span><span className="grow" />
          <button className="btn" onClick={() => api("GET", `/api/kb/versions/${x.id}`).then((r) => setOld(r.text)).catch((e) => toast(e.message, "error"))}>Lire</button></div>)}
        {old !== null && <pre className="passage">{old}</pre>}
      </div>
    )}
    {d.extracts!.length > 0 && (
      <div className="card"><h3>Formules, tableaux et données relevés ({d.extracts!.length})</h3>
        <p className="muted small">Relevé automatique dans le texte du document : rien n'est déduit ni complété.</p>
        {(["formule", "donnée", "tableau"] as const).map((k) => {
          const list = d.extracts!.filter((e) => e.kind === k);
          if (!list.length) return null;
          return (<div key={k} className="extracts"><h4>{KIND_LABEL[k]} ({list.length})</h4>
            {k === "tableau" ? list.map((e) => <div key={e.id} className="passage-row"><span className="cat">{e.locator || "tableau"}</span><TableView text={e.content} /></div>)
              : <div className="chips">{list.map((e) => <span key={e.id} className={`chip ${k === "formule" ? "mono" : ""}`} title={e.locator}>{e.content}</span>)}</div>}
          </div>);
        })}
      </div>
    )}
    {d.links!.length > 0 && (
      <div className="card"><h3>Connexions ({d.links!.length})</h3>
        <p className="muted small">Éléments reliés à ce document dans la sphère.</p>
        {d.links!.map((l) => <div key={l.node} className="row hit-row"><div className="grow">
          {l.node.startsWith("d:") ? <button className="link" onClick={() => openDoc(+l.node.slice(2))}>{l.title}</button> : l.title}
          <div className="muted small">{l.node.startsWith("m:") ? "souvenir personnel" : l.node.startsWith("r:") ? "recherche du chercheur" : "document"} · {l.kind === "sémantique" ? `contenu proche (${Math.round(l.score * 100)} %)` : l.kind}</div></div></div>)}
      </div>
    )}
    <div className="card"><h3>Passages indexés ({d.chunks_total})</h3>
      {d.chunks!.map((ch) => <div key={ch.id} className="passage-row"><span className="cat">{ch.locator || `n° ${ch.ord + 1}`}</span><pre className="passage">{ch.content}</pre></div>)}
      {d.chunks_total! > d.chunks!.length && <p className="muted small">{d.chunks_total! - d.chunks!.length} passages suivants non affichés.</p>}
    </div>
  </>);
}

function TableView({ text }: { text: string }) {
  const rows = text.split("\n").map((r) => r.replace(/^\||\|$/g, "").split("|").map((x) => x.trim()));
  return (
    <div className="tablewrap"><table className="table data"><tbody>
      {rows.slice(0, 30).map((r, i) => <tr key={i}>{r.map((x, j) => (i === 0 ? <th key={j}>{x}</th> : <td key={j}>{x}</td>))}</tr>)}
    </tbody></table>{rows.length > 30 && <p className="muted small">{rows.length - 30} lignes suivantes non affichées.</p>}</div>
  );
}

function Import(c: Ctx) {
  const { kb, load, toast, state } = c;
  const [cat, setCat] = useState<number | null>(null);
  const [rel, setRel] = useState(3);
  const [lic, setLic] = useState("");
  const [url, setUrl] = useState("");
  const [wq, setWq] = useState("");
  const [hits, setHits] = useState<any[] | null>(null);
  const [starter, setStarter] = useState<any[] | null>(null);
  const [over, setOver] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const lang = (state.settings as any).kb_wikipedia_lang;

  const sendFiles = async (files: FileList | File[]) => {
    let n = 0;
    for (const f of Array.from(files)) {
      if (f.size > 30 * 1024 * 1024) { toast(`${f.name} : trop volumineux (30 Mo au maximum).`, "error"); continue; }
      try {
        await api("POST", "/api/kb/import/file", { name: f.name, data: b64(await f.arrayBuffer()), category_id: cat, reliability: rel, license: lic });
        n++;
      } catch (e: any) { toast(`${f.name} : ${e.message}`, "error"); }
    }
    if (n) toast(`${n} fichier${n > 1 ? "s" : ""} en cours d'indexation.`);
    load();
  };
  const wiki = async (titles: string[], items?: any[]) => {
    try { const r = await api("POST", "/api/kb/import/wikipedia", { titles, items, lang, category_id: cat }); toast(`${r.jobs.length} article(s) en cours d'import.`); load(); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const pending = starter ? starter.flatMap((g) => g.titles.filter((t: any) => !t.imported).map((t: any) => ({ title: t.title, category_id: g.category_id }))) : [];

  return (<>
    <div className="card">
      <h3>Classement des prochains imports</h3>
      <div className="form three">
        <label>Catégorie<CatSelect cats={kb.categories} value={cat} onChange={setCat} empty={kb.llm_ready && (state.settings as any).kb_auto_categorize ? "Automatique" : "Non classé"} /></label>
        <label>Fiabilité de mes fichiers
          <select className="select" value={rel} onChange={(e) => setRel(+e.target.value)}>{Object.entries(kb.reliability).map(([n, l]) => <option key={n} value={n}>{n} — {l}</option>)}</select></label>
        <label>Licence / droits <small>facultatif</small><input className="input" value={lic} placeholder="Ex. : cours de l'IUT, usage personnel" onChange={(e) => setLic(e.target.value)} /></label>
      </div>
    </div>
    <div className="grid2">
      <div className="card">
        <h3>Mes fichiers</h3>
        <div className={`drop ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); sendFiles(e.dataTransfer.files); }}>
          <Icon name="upload" size={26} />
          <p>Déposez des fichiers ici</p>
          <button className="btn primary" onClick={() => file.current?.click()}>Choisir des fichiers</button>
          <input ref={file} type="file" multiple hidden accept={kb.formats.join(",")} onChange={(e) => { if (e.target.files?.length) sendFiles(e.target.files); e.target.value = ""; }} />
        </div>
        <p className="muted small">PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), OpenDocument (.odt, .ods), RTF, CSV, HTML, Markdown, texte et fichiers techniques. Les formules, tableaux et données chiffrées sont relevés à l'import. Les PDF scannés (images) et les images ne sont pas lus.
          {!kb.pdf_ready && <b> Lecture des PDF indisponible : installez le module pypdf (voir le tableau de bord).</b>}</p>
        <p className="muted small">Pour indexer un dossier entier de votre PC et le garder à jour : <button className="link" onClick={() => c.go("index")}>Indexation › Dossier de la bibliothèque</button>{kb.folder.folder ? ` (réglé : ${kb.folder.indexed} fichier(s) indexé(s))` : ""}.</p>
      </div>
      <div className="card">
        <h3>Une page web</h3>
        <p className="muted small">Documentation officielle, cours en ligne, article… La page est téléchargée une fois, son adresse est gardée comme source. N'importez que des contenus que vous avez le droit de copier pour votre usage.</p>
        <div className="row"><input className="input mono grow" value={url} placeholder="https://…" onChange={(e) => setUrl(e.target.value)} aria-label="Adresse de la page" />
          <button className="btn primary" disabled={!isUrl(url.trim())} onClick={async () => {
            try { await api("POST", "/api/kb/import/url", { url: url.trim(), category_id: cat }); setUrl(""); toast("Page en cours d'indexation."); load(); } catch (e: any) { toast(e.message, "error"); }
          }}>Importer</button></div>
      </div>
    </div>
    <div className="card">
      <h3>Wikipédia</h3>
      <p className="muted small">Articles sous licence CC BY-SA : la source, la licence et la date de révision sont enregistrées avec chaque article (fiabilité 3/5 par défaut).</p>
      <div className="row"><input className="input grow" value={wq} placeholder="Chercher un article" aria-label="Chercher un article Wikipédia"
        onChange={(e) => setWq(e.target.value)} onKeyDown={(e) => e.key === "Enter" && wq.trim() && api("GET", `/api/kb/wikipedia/search?q=${encodeURIComponent(wq)}&lang=${lang}`).then(setHits).catch((er) => toast(er.message, "error"))} />
        <button className="btn" disabled={!wq.trim()} onClick={() => api("GET", `/api/kb/wikipedia/search?q=${encodeURIComponent(wq)}&lang=${lang}`).then(setHits).catch((e) => toast(e.message, "error"))}><Icon name="search" /> Chercher</button></div>
      {hits !== null && (hits.length === 0 ? <p className="muted small">Aucun article trouvé.</p> :
        hits.map((h) => <div key={h.url} className="row hit-row"><div className="grow"><b>{h.title}</b><div className="muted small">{h.snippet}</div></div>
          <button className="btn" onClick={() => wiki([h.title])}>Importer</button></div>))}
      <div className="row"><b>Premier corpus proposé</b><span className="grow" />
        {!starter ? <button className="btn" onClick={() => api("GET", "/api/kb/starter").then(setStarter).catch((e) => toast(e.message, "error"))}>Voir la liste</button>
          : <button className="btn primary" disabled={!pending.length} onClick={() => wiki(pending.map((p) => p.title), pending)}>Importer les {pending.length} articles restants</button>}</div>
      {starter && (<>
        <p className="muted small">Liste de titres préparée pour un étudiant en génie mécanique et informatique. Elle n'a pas pu être vérifiée contre Wikipédia : un titre introuvable apparaîtra en erreur dans le suivi, sans rien ajouter à la base.</p>
        <div className="starter">{starter.map((g) => <div key={g.category}><h4>{g.category}</h4>
          {g.titles.map((t: any) => <span key={t.title} className={`chip ${t.imported ? "done" : ""}`}>{t.imported && "✓ "}{t.title}</span>)}</div>)}</div>
      </>)}
    </div>
    <Jobs {...c} />
  </>);
}

const FOUND: Record<string, string> = { mots: "trouvé par les mots", sens: "trouvé par le sens", "mots et sens": "trouvé par les mots et le sens",
  "proximité": "trouvé par proximité de vocabulaire", "mots et proximité": "trouvé par les mots et la proximité de vocabulaire" };

function Search({ kb, toast, openDoc, state }: Ctx) {
  const [q, setQ] = useState("");
  const [mods, setMods] = useState<Set<string>>(new Set());
  const [what, setWhat] = useState("all");
  const [res, setRes] = useState<any[] | null>(null);
  useEffect(() => {
    if (q.trim().length < 2 && what === "all") { setRes(null); return; }
    const path = what === "all"
      ? `/api/hub/search?q=${encodeURIComponent(q)}${mods.size ? `&modules=${[...mods].join(",")}` : ""}`
      : `/api/kb/extracts?q=${encodeURIComponent(q)}&kind=${encodeURIComponent(what)}`;
    const t = setTimeout(() => api("GET", path).then(setRes).catch((e) => toast(e.message, "error")), 250);
    return () => clearTimeout(t);
  }, [q, mods, what]);
  const toggle = (k: string) => { const x = new Set(mods); x.has(k) ? x.delete(k) : x.add(k); setMods(x); };
  const ex = kb.stats.extracts || {};
  return (<>
    <div className="row filters"><div className="field-icon grow"><Icon name="search" size={15} />
      <input autoFocus placeholder="Chercher dans tout le cerveau : documents, recherches, souvenirs, conversations" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Chercher dans la base" /></div>
      <select className="select" value={what} onChange={(e) => { setRes(null); setWhat(e.target.value); }} aria-label="Type de résultat">
        <option value="all">Passages et souvenirs</option><option value="formule">Formules ({ex.formule || 0})</option>
        <option value="donnée">Données chiffrées ({ex["donnée"] || 0})</option><option value="tableau">Tableaux ({ex.tableau || 0})</option></select></div>
    {what === "all" && (
      <div className="row wrap">{state.modules.concat([{ key: "conversations", label: "Conversations", color: "#7f93b0" }]).map((m) => (
        <button key={m.key} className={`chip-toggle ${mods.has(m.key) ? "on" : ""}`} aria-pressed={mods.has(m.key)} onClick={() => toggle(m.key)}
          title={m.key === "vectorielle" ? "Uniquement les passages retrouvés par les vecteurs, sans les mots exacts de la question" : `Limiter à : ${m.label}`}>{m.label}</button>))}
        {mods.size > 0 && <button className="link" onClick={() => setMods(new Set())}>Tous les modules</button>}</div>
    )}
    <div className="mem-list">
      {res === null ? <p className="empty">Recherche hybride (mots + vecteurs) dans {kb.stats.chunks} passages et {state.stats.memories} souvenirs.{kb.embedding_active ? "" : " Sans modèle d'embeddings, la recherche par le sens se limite aux mots et à leurs variantes."}</p>
        : res.length === 0 ? <p className="empty">Aucun résultat.</p>
        : what !== "all" ? res.map((r) => (
          <article key={r.id} className="mem"><div className="mem-body">
            {r.kind === "tableau" ? <TableView text={r.content} /> : <p className={`excerpt ${r.kind === "formule" ? "mono" : ""}`}>{r.content}</p>}
            <div className="mem-meta"><span className="cat">{r.kind}</span><button className="link" onClick={() => openDoc(r.doc_id)}>{r.title}</button>{r.locator && <span>{r.locator}</span>}</div>
          </div></article>))
        : res.map((r, i) => (
          <article key={i} className="mem"><div className="mem-body">
            {r.doc_id ? <button className="doc-title" onClick={() => openDoc(r.doc_id)}>{r.title}</button> : <b>{r.title}</b>}
            <p className="excerpt">{r.excerpt}{r.excerpt.length >= 420 ? "…" : ""}</p>
            <div className="mem-meta"><span className="cat" style={{ color: moduleColor(state, r.module) }}>{r.module === "conversations" ? "Conversation" : moduleLabel(state, r.module)}</span>
              {r.category && <span>{r.category}</span>}<span>{r.source}</span>{r.reliability && <span>fiabilité {r.reliability}/5</span>}<span>{FOUND[r.found_by]}</span></div>
          </div></article>
        ))}
    </div>
  </>);
}

function layout(nodes: any[], edges: any[], w: number, h: number) {
  const pos: Record<number, { x: number; y: number }> = {};
  nodes.forEach((n, i) => { const a = (i / nodes.length) * Math.PI * 2; pos[n.id] = { x: w / 2 + Math.cos(a) * w * 0.32, y: h / 2 + Math.sin(a) * h * 0.32 }; });
  const k = Math.sqrt((w * h) / Math.max(1, nodes.length)) * 0.7;
  for (let it = 0; it < 220; it++) {
    const d: Record<number, { x: number; y: number }> = {};
    nodes.forEach((n) => (d[n.id] = { x: 0, y: 0 }));
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const a = pos[nodes[i].id], b = pos[nodes[j].id];
      let dx = a.x - b.x, dy = a.y - b.y; const dist = Math.max(8, Math.hypot(dx, dy)); const f = (k * k) / dist;
      dx /= dist; dy /= dist; d[nodes[i].id].x += dx * f; d[nodes[i].id].y += dy * f; d[nodes[j].id].x -= dx * f; d[nodes[j].id].y -= dy * f;
    }
    edges.forEach((e) => {
      const a = pos[e.src], b = pos[e.dst]; if (!a || !b) return;
      const dx = a.x - b.x, dy = a.y - b.y, dist = Math.max(8, Math.hypot(dx, dy)), f = (dist * dist) / k;
      d[e.src].x -= (dx / dist) * f; d[e.src].y -= (dy / dist) * f; d[e.dst].x += (dx / dist) * f; d[e.dst].y += (dy / dist) * f;
    });
    const temp = 28 * (1 - it / 220) + 1;
    nodes.forEach((n) => {
      const v = d[n.id], m = Math.max(1, Math.hypot(v.x, v.y)), p = pos[n.id];
      p.x = Math.min(w - 70, Math.max(70, p.x + (v.x / m) * Math.min(m, temp) + (w / 2 - p.x) * 0.01));
      p.y = Math.min(h - 24, Math.max(24, p.y + (v.y / m) * Math.min(m, temp) + (h / 2 - p.y) * 0.01));
    });
  }
  return pos;
}

function Graph({ kb, load, toast, openDoc }: Ctx) {
  const [q, setQ] = useState("");
  const [g, setG] = useState<{ nodes: any[]; edges: any[] }>({ nodes: [], edges: [] });
  const [sel, setSel] = useState<number | null>(null);
  const [rel, setRel] = useState({ source: "", relation: "", cible: "" });
  const fetchG = (query = q) => api("GET", `/api/kb/graph?q=${encodeURIComponent(query)}`).then((x) => { setG(x); setSel(null); }).catch((e) => toast(e.message, "error"));
  useEffect(() => { fetchG(""); }, [kb.stats.relations]);
  const W = 980, H = 560;
  const pos = useMemo(() => layout(g.nodes, g.edges, W, H), [g]);
  const near = sel ? new Set(g.edges.filter((e) => e.src === sel || e.dst === sel).flatMap((e) => [e.src, e.dst])) : null;
  const name = (id: number) => g.nodes.find((n) => n.id === id)?.name;
  const shown = sel ? g.edges.filter((e) => e.src === sel || e.dst === sel) : g.edges.slice(0, 40);
  return (<>
    <div className="row filters"><div className="field-icon grow"><Icon name="search" size={15} />
      <input placeholder="Centrer sur un concept (ex. acier)" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && fetchG()} aria-label="Concept" /></div>
      <button className="btn" onClick={() => fetchG()}>Afficher</button>{q && <button className="btn" onClick={() => { setQ(""); fetchG(""); }}>Tout le graphe</button>}</div>
    {g.nodes.length === 0 ? (
      <p className="empty">{kb.stats.relations === 0
        ? "Le graphe est vide. Les relations sont extraites des documents importés quand un modèle est disponible, ou ajoutées à la main ci-dessous."
        : "Aucun concept ne correspond."}</p>
    ) : (
      <div className="card graph-card">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Graphe de ${g.nodes.length} concepts et ${g.edges.length} relations`}>
          <defs><marker id="arr" viewBox="0 0 10 10" refX="19" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="#3b5f96" /></marker></defs>
          {g.edges.map((e) => pos[e.src] && pos[e.dst] && (
            <line key={e.id} x1={pos[e.src].x} y1={pos[e.src].y} x2={pos[e.dst].x} y2={pos[e.dst].y} markerEnd="url(#arr)"
              className={`edge ${sel && (e.src === sel || e.dst === sel) ? "on" : ""} ${sel && !(e.src === sel || e.dst === sel) ? "dim" : ""}`}><title>{`${name(e.src)} — ${e.label} → ${name(e.dst)}`}</title></line>
          ))}
          {g.nodes.map((n) => (
            <g key={n.id} transform={`translate(${pos[n.id].x},${pos[n.id].y})`} className={`node t-${n.type} ${sel === n.id ? "on" : ""} ${near && !near.has(n.id) ? "dim" : ""}`}
              onClick={() => setSel(sel === n.id ? null : n.id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setSel(sel === n.id ? null : n.id)}>
              <circle r={7} /><text y={-11} textAnchor="middle">{n.name.length > 26 ? n.name.slice(0, 25) + "…" : n.name}</text><title>{`${n.name} (${n.type})`}</title>
            </g>
          ))}
        </svg>
        <p className="muted small">{g.nodes.length} concepts, {g.edges.length} relations{g.nodes.length >= 140 ? " (les 140 concepts les plus reliés)" : ""}. Cliquez sur un concept pour isoler ses relations.</p>
        <table className="table"><tbody>
          {shown.map((e) => <tr key={e.id}><td><b>{name(e.src)}</b></td><td className="muted">{e.label}</td><td><b>{name(e.dst)}</b></td>
            <td className="muted small">{e.doc_id ? <button className="link" onClick={() => openDoc(e.doc_id)}>{e.title}</button> : "ajoutée à la main"}</td>
            <td className="right"><button className="icon-btn" aria-label="Supprimer la relation" title="Supprimer" onClick={() => api("DELETE", `/api/kb/relations/${e.id}`).then(() => { load(); fetchG(); })}><Icon name="trash" size={14} /></button></td></tr>)}
        </tbody></table>
      </div>
    )}
    <div className="card"><h3>Ajouter une relation</h3>
      <div className="row wrap">
        <input className="input grow" placeholder="Concept (ex. acier)" value={rel.source} onChange={(e) => setRel({ ...rel, source: e.target.value })} aria-label="Concept source" />
        <input className="input grow" placeholder="Relation (ex. a pour propriété)" value={rel.relation} onChange={(e) => setRel({ ...rel, relation: e.target.value })} aria-label="Relation" />
        <input className="input grow" placeholder="Concept lié (ex. résistance à la traction)" value={rel.cible} onChange={(e) => setRel({ ...rel, cible: e.target.value })} aria-label="Concept cible" />
        <button className="btn primary" disabled={!rel.source.trim() || !rel.relation.trim() || !rel.cible.trim()} onClick={async () => {
          try { await api("POST", "/api/kb/relations", rel); setRel({ source: "", relation: "", cible: "" }); load(); fetchG(); } catch (e: any) { toast(e.message, "error"); }
        }}>Ajouter</button>
      </div></div>
  </>);
}

function Todo(c: Ctx) {
  const { kb, load, toast, openDoc } = c;
  const [gaps, setGaps] = useState<any[]>([]);
  const [props, setProps] = useState<any[]>([]);
  const [dups, setDups] = useState<Doc[]>([]);
  const [stale, setStale] = useState<Doc[]>([]);
  const [uncl, setUncl] = useState<Doc[]>([]);
  const [obs, setObs] = useState<Doc[]>([]);
  const [conf, setConf] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const reload = () => {
    api("GET", "/api/kb/gaps").then(setGaps); api("GET", "/api/kb/proposals").then(setProps);
    api("GET", "/api/kb/documents?filter=dups").then(setDups); api("GET", "/api/kb/documents?filter=stale").then(setStale);
    api("GET", "/api/kb/documents?filter=unclassified").then(setUncl);
    api("GET", "/api/kb/documents?filter=obsolete").then(setObs); api("GET", "/api/kb/conflicts").then(setConf);
  };
  useEffect(reload, [kb.stats.documents, kb.stats.proposals, kb.stats.gaps, kb.stats.duplicates, kb.stats.obsolete, kb.stats.archived]);
  const act = async (fn: () => Promise<any>, msg?: string) => { try { await fn(); if (msg) toast(msg); } catch (e: any) { toast(e.message, "error"); } load(); reload(); };
  const suggest = async () => {
    setBusy(true);
    try {
      const r = await api("POST", "/api/kb/suggest", {});
      toast(r.error ? `Recherche interrompue : ${r.error}` : `${r.proposals} nouvelle(s) proposition(s) après ${r.searches} recherche(s) sur Wikipédia.`, r.error ? "error" : "info");
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(false); load(); reload();
  };
  return (<>
    {kb.pending_findings > 0 && (
      <div className="notice info"><Icon name="search" /><span>{kb.pending_findings} connaissance(s) trouvée(s) par le chercheur attendent votre validation.</span>
        <button className="btn" onClick={() => c.go("research")}>Ouvrir le chercheur</button></div>
    )}
    <div className="card"><h3>Anciennes versions ({obs.length})</h3>
      {obs.length === 0 ? <p className="muted small">Aucune. Quand un document importé porte le même titre qu'un autre (au numéro de version ou à l'année près) avec un contenu proche mais différent, le plus ancien est signalé ici. Rien n'est archivé sans votre accord.</p> :
        obs.map((d) => <DocRow key={d.id} d={d} onOpen={() => openDoc(d.id)} extra={<>
          <span className="muted small">remplacé par « {d.newer_title} »</span>
          <button className="btn" onClick={() => act(() => api("PATCH", `/api/kb/documents/${d.id}`, { not_obsolete: true }))}>Garder les deux</button>
          <button className="btn primary" onClick={() => act(() => api("PATCH", `/api/kb/documents/${d.id}`, { archived: true }), "Ancienne version archivée : elle n'est plus utilisée dans les réponses.")}>Archiver l'ancienne</button></>} />)}
    </div>
    <div className="card"><h3>Données contradictoires ({conf.length})</h3>
      {conf.length === 0 ? <p className="muted small">Aucune. Sont comparées ici les données chiffrées relevées dans deux documents différents : même grandeur, même unité, écart de plus de 10 %.</p> :
        conf.map((x, i) => <div key={i} className="row hit-row"><div className="grow">
          <div><b>{x.a.content}</b> — <button className="link" onClick={() => openDoc(x.a.doc_id)}>{x.a.title}</button> <span className="muted small">(fiabilité {x.a.reliability}/5)</span></div>
          <div><b>{x.b.content}</b> — <button className="link" onClick={() => openDoc(x.b.doc_id)}>{x.b.title}</button> <span className="muted small">(fiabilité {x.b.reliability}/5)</span></div></div></div>)}
    </div>
    <div className="card">
      <div className="row"><h3>Propositions d'enrichissement ({props.length})</h3><span className="grow" />
        <button className="btn primary" onClick={suggest} disabled={busy}><Icon name="search" /> {busy ? "Recherche…" : "Chercher de quoi combler les manques"}</button></div>
      <p className="muted small">Jarvis cherche sur Wikipédia des articles pour les questions restées sans source et les catégories vides. {(c.state.settings as any).kb_require_validation ? "Rien n'est importé sans votre accord." : "La validation est désactivée : les propositions sont importées directement."}</p>
      {props.map((p) => <div key={p.id} className="row hit-row"><div className="grow"><b>{p.title}</b> <a className="small" href={p.url} target="_blank" rel="noopener noreferrer">voir l'article</a>
        <div className="muted small">{p.reason}{p.snippet && ` — ${p.snippet}`}</div></div>
        <button className="btn" onClick={() => act(() => api("POST", `/api/kb/proposals/${p.id}`, { action: "reject" }))}>Refuser</button>
        <button className="btn primary" onClick={() => act(() => api("POST", `/api/kb/proposals/${p.id}`, { action: "accept" }), "Import mis en file.")}>Accepter</button></div>)}
    </div>
    <div className="card"><h3>Questions restées sans source ({gaps.length})</h3>
      <p className="muted small">Questions posées en conversation pour lesquelles la base ne contenait aucun passage.</p>
      {gaps.map((g) => <div key={g.id} className="row hit-row"><div className="grow">{g.question}<div className="muted small">posée {g.count} fois, dernière le {fmtDate(g.last)}</div></div>
        <button className="btn" onClick={() => act(() => api("POST", "/api/research", { question: g.question, origin: "lacune", gap_id: g.id }), "Recherche lancée (onglet Chercheur).")}>Rechercher</button>
        <button className="btn" onClick={() => act(() => api("POST", `/api/kb/gaps/${g.id}`, { status: "ignoré" }))}>Ignorer</button>
        <button className="btn" onClick={() => act(() => api("POST", `/api/kb/gaps/${g.id}`, { status: "traité" }))}>Traité</button></div>)}
    </div>
    <div className="card"><h3>Doublons probables ({dups.length})</h3>
      {dups.length === 0 ? <p className="muted small">Aucun. Un fichier strictement identique à un document existant est refusé à l'import ; ici apparaissent les textes presque identiques.</p> :
        dups.map((d) => <DocRow key={d.id} d={d} onOpen={() => openDoc(d.id)} extra={<>
          <span className="muted small">ressemble à « {d.dup_title} »</span>
          <button className="btn" onClick={() => act(() => api("PATCH", `/api/kb/documents/${d.id}`, { not_duplicate: true }))}>Garder les deux</button>
          <button className="btn danger" onClick={() => window.confirm(`Supprimer « ${d.title} » ?`) && act(() => api("DELETE", `/api/kb/documents/${d.id}`))}>Supprimer</button></>} />)}
    </div>
    <div className="card">
      <div className="row"><h3>À revérifier ({stale.length})</h3><span className="grow" />
        {stale.some((d) => d.source_type !== "fichier" && d.source_type !== "texte") &&
          <button className="btn" onClick={() => act(() => api("POST", "/api/kb/refresh-stale", {}).then((r) => toast(`${r.queued} actualisation(s) mise(s) en file.`)))}><Icon name="refresh" /> Actualiser les documents web</button>}</div>
      <p className="muted small">Documents non vérifiés depuis plus de {(c.state.settings as any).kb_stale_days} jours.</p>
      {stale.map((d) => <DocRow key={d.id} d={d} onOpen={() => openDoc(d.id)} />)}
    </div>
    {uncl.length > 0 && <div className="card"><h3>Non classés ({uncl.length})</h3>{uncl.map((d) => <DocRow key={d.id} d={d} onOpen={() => openDoc(d.id)} />)}</div>}
  </>);
}

function Journal({ toast, openDoc }: Ctx) {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => { api("GET", "/api/kb/journal").then(setRows).catch((e) => toast(e.message, "error")); }, []);
  return (
    <div className="card"><h3>Journal des modifications</h3>
      {rows.length === 0 ? <p className="muted small">Aucune modification pour l'instant.</p> : (
        <table className="table"><tbody>{rows.map((r) => <tr key={r.id}><td className="muted nowrap">{fmtDate(r.at)}</td><td><span className="cat">{r.action}</span></td>
          <td>{r.doc_id && r.action !== "supprimé" ? <button className="link" onClick={() => openDoc(r.doc_id)}>{r.title || `document n° ${r.doc_id}`}</button> : r.title}</td>
          <td className="muted small">{r.detail}</td></tr>)}</tbody></table>)}
    </div>
  );
}
