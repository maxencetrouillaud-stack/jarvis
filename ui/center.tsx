import React, { useEffect, useState } from "react";
import { api, download, fmtBytes, fmtDate } from "./api";
import { Icon, Toggle } from "./common";
import { Cat, Ctx, Job, host, moduleColor, moduleLabel } from "./kbtypes";

const STATUS_PILL: Record<string, string> = { "terminé": "ok", erreur: "err", doublon: "bad", "en cours": "ok", "annulé": "off" };

export function JobsTable({ jobs, onRetry }: { jobs: Job[]; onRetry?: (j: Job) => void }) {
  return (
    <table className="table jobs"><tbody>
      {jobs.map((j) => (
        <tr key={j.id}>
          <td><span className={`pill ${STATUS_PILL[j.status] || "off"}`}>{j.status}</span></td>
          <td className="muted">{j.kind}</td>
          <td><b>{j.label}</b>{j.detail && <div className="muted small">{j.detail}</div>}</td>
          <td className="muted right nowrap">{fmtDate(j.created)}
            {onRetry && j.status === "erreur" && ["fichier", "page web", "wikipédia", "dossier"].includes(j.kind) &&
              <div><button className="link" onClick={() => onRetry(j)}>Relancer</button></div>}</td>
        </tr>
      ))}
    </tbody></table>
  );
}

/** Tableau de bord du cerveau : tout ce que Jarvis sait, d'où ça vient et ce qui demande votre attention. */
export function Dashboard(c: Ctx) {
  const { kb, state, setState, toast, load } = c;
  const [d, setD] = useState<any | null>(null);
  const [busy, setBusy] = useState("");
  const [exports, setExports] = useState<any[]>([]);
  const reload = () => {
    api("GET", "/api/hub/dashboard").then(setD).catch((e) => toast(e.message, "error"));
    api("GET", "/api/hub/exports").then(setExports).catch(() => {});
  };
  useEffect(reload, [kb.stats.documents, kb.stats.jobs_active, kb.stats.links, kb.pending_findings]);
  const active = d?.research.active || 0;
  useEffect(() => { if (!active) return; const t = setInterval(reload, 2500); return () => clearInterval(t); }, [active]);
  if (!d) return <p className="muted">Chargement…</p>;
  const s = state.settings as any;
  const t = d.totals, v = d.vector, st = kb.stats;
  const patch = async (p: any) => { try { setState(await api("POST", "/api/settings", p)); } catch (e: any) { toast(e.message, "error"); } };
  const act = async (key: string, fn: () => Promise<any>) => { setBusy(key); try { await fn(); } catch (e: any) { toast(e.message, "error"); } setBusy(""); reload(); load(); };
  const tops = kb.categories.filter((x) => x.parent_id === null);
  const total = (x: Cat) => x.n + kb.categories.filter((y) => y.parent_id === x.id).reduce((a, y) => a + y.n, 0);
  const todo = Object.values(d.todo as Record<string, number>).reduce((a, b) => a + b, 0);
  const ext = t.extracts || {};

  return (<>
    <div className="tiles">
      <div className="tile" title="Documents actifs, souvenirs personnels et concepts du graphe"><span>{t.knowledge}</span>Connaissances au total</div>
      <div className="tile" title={`${t.passages} passages indexés`}><span>{t.documents}</span>Documents indexés</div>
      <div className="tile"><span>{t.memories}</span>Souvenirs personnels</div>
      <div className="tile" title={`${t.graph_relations} relations entre concepts, ${t.links} connexions entre connaissances`}><span>{t.relations}</span>Relations</div>
      <div className="tile" title={`${t.categories_used} catégories contiennent au moins un document, sur ${t.categories}`}><span>{t.categories_used}<small> / {t.categories}</small></span>Catégories utilisées</div>
      <div className="tile"><span>{fmtBytes(d.storage.total)}</span>Espace utilisé</div>
    </div>
    {t.documents === 0 && (
      <div className="notice info"><Icon name="upload" /><span>La bibliothèque est vide. Rien n'y est mis d'office : importez vos fichiers ou un dossier entier (onglet <b>Importer</b>), ou lancez une recherche (onglet <b>Chercheur</b>).</span></div>
    )}
    <div className="row wrap">
      <button className="btn" onClick={c.toSphere}><Icon name="memory" /> Voir dans la sphère</button>
      <button className="btn" onClick={() => c.go("import")}><Icon name="upload" /> Importer des fichiers</button>
      <button className="btn" onClick={() => c.go("search")}><Icon name="search" /> Rechercher dans toute la base</button>
      <span className="grow" />
      <button className="btn" disabled={busy === "save"} onClick={() => act("save", async () => { await api("POST", "/api/backups", {}); toast("Sauvegarde créée (mémoire, connaissances, réglages)."); })}><Icon name="check" /> Sauvegarder maintenant</button>
      <button className="btn primary" disabled={busy === "export"} onClick={() => act("export", async () => {
        const r = await api("POST", "/api/hub/export", {});
        await download(`/api/hub/exports/${r.name}`, r.name);
        toast(`Export de ${r.documents} document(s) : ${fmtBytes(r.bytes)}.`);
      })}><Icon name="upload" /> {busy === "export" ? "Export en cours…" : "Exporter la base"}</button>
    </div>

    <div className="grid2">
      <div className="card"><h3>Modules du cerveau</h3>
        {d.modules.map((m: any) => (
          <button key={m.key} className="modrow" disabled={!["bibliotheque", "generale", "recherche"].includes(m.key) && m.key !== "vectorielle"}
            onClick={() => (m.key === "vectorielle" ? c.go("index") : c.openLibrary({ module: m.key }))}>
            <i style={{ background: moduleColor(state, m.key), boxShadow: `0 0 8px ${moduleColor(state, m.key)}` }} />
            <span>{m.label}</span><b>{m.count}</b><em>{m.unit}</em>
          </button>
        ))}
        <p className="muted small">Un seul index pour tous les modules : une question interroge ceux qui la concernent, et chaque élément apparaît dans la sphère.</p>
      </div>
      <div className="card"><h3>À traiter {todo > 0 && <span className="tag warn">{todo}</span>}</h3>
        <ul className="facts">
          <li>{d.todo.findings} connaissance(s) du chercheur en attente de validation {d.todo.findings > 0 && <button className="link" onClick={() => c.go("research")}>Valider</button>}</li>
          <li>{d.todo.gaps} question(s) restée(s) sans source · {d.todo.proposals} proposition(s) d'enrichissement</li>
          <li>{d.todo.duplicates} doublon(s) probable(s) · {d.todo.obsolete} ancienne(s) version(s) à archiver</li>
          <li>{d.todo.stale} document(s) à revérifier · {d.todo.unclassified} non classé(s)</li>
        </ul>
        <div className="row"><button className="btn" onClick={() => c.go("todo")}>Ouvrir « À traiter »</button></div>
      </div>
    </div>

    <div className="grid2">
      <div className="card"><h3>Dernières connaissances ajoutées</h3>
        {d.latest.length === 0 ? <p className="muted small">Rien pour l'instant.</p> : (
          <table className="table"><tbody>{d.latest.map((x: any) => (
            <tr key={x.node}>
              <td className="nowrap"><span className="cat" style={{ color: moduleColor(state, x.module) }}>{moduleLabel(state, x.module).split(" ")[0]}</span></td>
              <td>{x.kind === "document" ? <button className="link" onClick={() => c.openDoc(x.id)}>{x.title}</button> : x.title}<div className="muted small">{x.detail}</div></td>
              <td className="muted small right nowrap">{fmtDate(x.at)}</td>
            </tr>))}</tbody></table>)}
      </div>
      <div className="card"><div className="row"><h3>Dernières recherches du chercheur</h3><span className="grow" />
        <button className="btn" onClick={() => c.go("research")}>Ouvrir le chercheur</button></div>
        {d.research.runs.length === 0 ? <p className="muted small">Aucune recherche pour l'instant. Le chercheur interroge Wikipédia, HAL, OpenAlex et arXiv, recoupe les sources et vous soumet ce qu'il trouve.</p> : (
          <table className="table"><tbody>{d.research.runs.map((r: any) => (
            <tr key={r.id}><td><span className={`pill ${STATUS_PILL[r.status] || "off"}`}>{r.status}</span></td>
              <td>{r.question}<div className="muted small">{r.n_sources} source(s) · {r.n_findings} affirmation(s){r.n_pending ? ` · ${r.n_pending} à valider` : ""}{r.n_integrated ? ` · ${r.n_integrated} intégrée(s)` : ""}</div></td>
              <td className="muted small right nowrap">{fmtDate(r.created)}</td></tr>))}</tbody></table>)}
      </div>
    </div>

    <div className="grid2">
      <div className="card"><div className="row"><h3>Documents à actualiser ({d.to_update.length})</h3><span className="grow" />
        {d.to_update.some((x: any) => x.source_type === "page web" || x.source_type === "wikipédia") &&
          <button className="btn" onClick={() => act("stale", async () => { const r = await api("POST", "/api/kb/refresh-stale", {}); toast(`${r.queued} actualisation(s) mise(s) en file.`); })}><Icon name="refresh" /> Actualiser les documents web</button>}</div>
        {d.to_update.length === 0 ? <p className="muted small">Tout est à jour : aucun document trop ancien ni remplacé par une version plus récente.</p> :
          d.to_update.map((x: any) => <div key={`${x.id}${x.why}`} className="row hit-row"><div className="grow"><button className="link" onClick={() => c.openDoc(x.id)}>{x.title}</button>
            <div className="muted small">{x.why} · vérifié le {fmtDate(x.verified).slice(0, 10)}</div></div></div>)}
      </div>
      <div className="card"><div className="row"><h3>Erreurs d'indexation ({d.errors.length + d.file_errors.length})</h3><span className="grow" />
        <button className="btn" onClick={() => c.go("index")}>Gérer l'indexation</button></div>
        {d.errors.length + d.file_errors.length === 0 ? <p className="muted small">Aucune erreur.</p> : (<>
          {d.errors.map((j: any) => <div key={j.id} className="row hit-row"><div className="grow"><b>{j.label}</b><div className="muted small">{j.detail}</div></div>
            {["fichier", "page web", "wikipédia", "dossier"].includes(j.kind) && <button className="btn" onClick={() => act("retry", async () => { await api("POST", `/api/kb/jobs/${j.id}/retry`, {}); toast("Tâche relancée."); })}>Relancer</button>}</div>)}
          {d.file_errors.map((f: any) => <div key={f.path} className="row hit-row"><div className="grow"><code>{f.path}</code><div className="muted small">{f.status}</div></div></div>)}
        </>)}
      </div>
    </div>

    <div className="grid2">
      <div className="card"><h3>Mémoire vectorielle</h3>
        <ul className="facts">
          <li>{v.chunks + v.memories} éléments indexés : {v.chunks} passages de documents, {v.memories} souvenirs.</li>
          <li>Recherche hybride : mots (plein texte) + vecteurs, fusion des classements puis reclassement pour éviter les redites.</li>
          {v.model
            ? <li>Recherche par le sens : <b>{v.model}</b> — {v.chunks_semantic}/{v.chunks} passages et {v.memories_semantic}/{v.memories} souvenirs indexés.
              {(v.chunks_semantic < v.chunks) && <> <button className="link" onClick={() => act("reindex", async () => { await api("POST", "/api/kb/reindex", {}); toast("Indexation sémantique mise en file."); })}>Indexer ce qui manque</button></>}</li>
            : <li>Recherche par le sens : <b>limitée</b>. Sans modèle d'embeddings, la proximité se calcule sur les mots et leurs variantes, pas sur le sens. Pour retrouver une idée sans ses mots exacts, choisissez un modèle d'embeddings (Ollama : <code>nomic-embed-text</code>, gratuit et local). <button className="link" onClick={c.goModels}>Ouvrir Modèles</button></li>}
          <li>Index de voisinage approché : {v.ann ? "actif (grande base)" : "en réserve — la base est assez petite pour une comparaison exacte de tous les vecteurs"}.</li>
          <li>Les vecteurs sont stockés sur ce PC et ne dépendent pas du modèle qui répond : changer d'IA ne fait rien perdre.</li>
        </ul>
      </div>
      <div className="card"><h3>Espace de stockage</h3>
        {[["Base de connaissances", d.storage.knowledge_db], ["Mémoire et conversations", d.storage.memory_db], ["Fichiers d'origine", d.storage.originals],
          ["Sauvegardes", d.storage.backups], ["Exports", d.storage.exports]].map(([label, n]) => (
          <div key={label as string} className="bar wide"><span>{label}</span><div><i style={{ width: `${d.storage.total ? ((n as number) / d.storage.total) * 100 : 0}%` }} /></div><b>{fmtBytes(n as number)}</b></div>))}
        <p className="muted small">Relevé dans les documents : {ext.formule || 0} formule(s), {ext.tableau || 0} tableau(x), {ext["donnée"] || 0} donnée(s) chiffrée(s). Fichiers d'origine conservés dans <code>{kb.originals_dir}</code></p>
        {exports.length > 0 && <p className="muted small">Dernier export : <button className="link" onClick={() => download(`/api/hub/exports/${exports[0].name}`, exports[0].name).catch((e) => toast(e.message, "error"))}>{exports[0].name}</button> ({fmtBytes(exports[0].bytes)})</p>}
      </div>
    </div>

    <div className="grid2">
      <div className="card"><h3>Explorer les catégories</h3>
        {tops.map((x) => (
          <button key={x.id} className="bar click" onClick={() => c.openLibrary({ category: x.id })} title={`Ouvrir les documents de « ${x.name} »`}><span>{x.name}</span>
            <div><i style={{ width: `${st.documents ? (total(x) / st.documents) * 100 : 0}%` }} /></div><b>{total(x)}</b></button>
        ))}
        <p className="muted small">{kb.categories.length - tops.length} sous-catégories. {st.unclassified > 0 && `${st.unclassified} document(s) non classé(s).`}</p>
      </div>
      <div className="card"><h3>État de la base</h3>
        <ul className="facts">
          <li>Origine : {Object.entries(st.by_source).map(([k, n]) => `${n} ${k}`).join(", ") || "—"}</li>
          <li>{t.sources} adresse(s) web d'origine · {st.archived} document(s) archivé(s)</li>
          <li>Lecture des PDF : {kb.pdf_ready ? "disponible" : <>indisponible — dans une invite de commandes : <code>py -m pip install pypdf</code>, puis relancer</>}</li>
          <li>Classement automatique : {kb.llm_ready ? "par le modèle, sinon par le vocabulaire du document" : "par le vocabulaire du document (aucun modèle ne répond)"}</li>
          <li>Graphe de concepts : {kb.llm_ready ? "extraction disponible" : "extraction indisponible tant qu'aucun modèle ne répond"}</li>
        </ul>
      </div>
    </div>

    <div className="card"><h3>Réglages de la base</h3>
      <Toggle checked={s.kb_enabled} label="Consulter la base pendant les conversations"
        hint="Les passages en rapport avec la question sont transmis au modèle, qui les cite par leur numéro [S1], [S2]…" onChange={(x) => patch({ kb_enabled: x })} />
      <Toggle checked={s.kb_auto_categorize} label="Classer automatiquement les documents importés"
        hint="Par le modèle utilitaire s'il répond, sinon d'après le vocabulaire du document. Un document sans vocabulaire reconnaissable reste non classé." onChange={(x) => patch({ kb_auto_categorize: x })} />
      <Toggle checked={s.kb_auto_links} label="Relier chaque nouvelle connaissance à ce qui existe déjà"
        hint="À l'import, Jarvis cherche les documents et souvenirs au contenu proche et crée les connexions affichées dans la sphère." onChange={(x) => patch({ kb_auto_links: x })} />
      <Toggle checked={s.kb_graph_auto} label="Extraire les relations entre concepts à l'import"
        hint="Un appel au modèle utilitaire par document (début du texte). Seules les relations dont les deux concepts figurent dans le texte sont gardées." onChange={(x) => patch({ kb_graph_auto: x })} />
      <Toggle checked={s.kb_require_validation} label="Demander mon accord avant d'ajouter une proposition"
        hint="Désactivé, les articles proposés par l'enrichissement sont importés sans validation." onChange={(x) => patch({ kb_require_validation: x })} />
      <div className="form three">
        <label>Passages transmis par question
          <select className="select" value={s.kb_top_k} onChange={(e) => patch({ kb_top_k: +e.target.value })}>{[3, 6, 10, 15].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        <label>À revérifier après (jours)
          <select className="select" value={s.kb_stale_days} onChange={(e) => patch({ kb_stale_days: +e.target.value })}>{[30, 90, 180, 365].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        <label>Langue de Wikipédia
          <select className="select" value={s.kb_wikipedia_lang} onChange={(e) => patch({ kb_wikipedia_lang: e.target.value })}><option value="fr">Français</option><option value="en">Anglais</option></select></label>
      </div>
    </div>
  </>);
}

/** Suivi des sources : d'où viennent les connaissances, quelle confiance leur accorder, lesquelles écarter. */
export function Sources({ toast, kb }: Ctx) {
  const [rows, setRows] = useState<any[] | null>(null);
  const [domain, setDomain] = useState("");
  const [rel, setRel] = useState(4);
  const reload = () => api("GET", "/api/kb/sources").then(setRows).catch((e) => toast(e.message, "error"));
  useEffect(() => { reload(); }, []);
  const rule = async (dom: string, p: any) => { try { setRows(await api("POST", "/api/kb/sources", { domain: dom, ...p })); } catch (e: any) { toast(e.message, "error"); } };
  if (!rows) return <p className="muted">Chargement…</p>;
  return (<>
    <div className="card"><h3>Régler la confiance accordée à un site</h3>
      <p className="muted small">Par défaut, la fiabilité est estimée d'après le type de site (organisme officiel ou universitaire : 4, encyclopédie collaborative : 3, autre site : 2). Votre réglage s'applique aux prochaines recherches du chercheur. Un site bloqué n'est plus jamais consulté.</p>
      <div className="row wrap">
        <input className="input mono grow" value={domain} placeholder="Nom du site, ex. freecad.org" aria-label="Nom de domaine" onChange={(e) => setDomain(e.target.value)} />
        <select className="select" value={rel} onChange={(e) => setRel(+e.target.value)} aria-label="Fiabilité">
          {Object.entries(kb.reliability).map(([n, l]) => <option key={n} value={n}>{n} — {l}</option>)}</select>
        <button className="btn primary" disabled={!domain.trim()} onClick={() => rule(domain.trim(), { reliability: rel }).then(() => setDomain(""))}>Enregistrer</button>
      </div>
    </div>
    <div className="card"><h3>Sources suivies ({rows.length})</h3>
      {rows.length === 0 ? <p className="muted small">Aucune source pour l'instant. Elles apparaissent ici dès qu'une page web est importée ou qu'une recherche est lancée.</p> : (
        <table className="table"><thead><tr><th>Site</th><th>Fiabilité</th><th className="right">Documents importés</th><th className="right">Lus par le chercheur</th><th className="right">Cités comme preuve</th><th></th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.domain} className={r.rule?.blocked ? "dim" : ""}>
              <td><b>{r.domain}</b>{r.providers.length > 0 && <div className="muted small">via {r.providers.join(", ")}</div>}{r.rule?.note && <div className="muted small">{r.rule.note}</div>}</td>
              <td className="nowrap">
                <select className="select compact" value={r.rule?.reliability || ""} aria-label={`Fiabilité de ${r.domain}`} onChange={(e) => rule(r.domain, { reliability: e.target.value ? +e.target.value : 0 })}>
                  <option value="">Automatique ({r.auto}/5)</option>{[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}/5 (réglé)</option>)}</select></td>
              <td className="right num">{r.documents}</td><td className="right num">{r.read}</td><td className="right num">{r.used}</td>
              <td className="right nowrap">
                {r.rule?.blocked ? <span className="tag warn">Bloqué</span> : null}{" "}
                <button className="link" onClick={() => rule(r.domain, { blocked: !r.rule?.blocked })}>{r.rule?.blocked ? "Débloquer" : "Bloquer"}</button>
                {r.rule && <>{" · "}<button className="link" onClick={() => api("DELETE", `/api/kb/sources/${r.domain}`).then(setRows).catch((e) => toast(e.message, "error"))}>Oublier le réglage</button></>}
              </td>
            </tr>))}</tbody></table>)}
    </div>
  </>);
}

/** Gestion des indexations : file d'attente, erreurs, index vectoriel, connexions, dossier surveillé. */
export function Indexing(c: Ctx) {
  const { kb, load, toast, state, setState } = c;
  const s = state.settings as any;
  const [folder, setFolder] = useState<string>(s.library_folder || "");
  const [busy, setBusy] = useState(false);
  const f = kb.folder, st = kb.stats;
  const patch = async (p: any) => { try { setState(await api("POST", "/api/settings", p)); load(); } catch (e: any) { toast(e.message, "error"); } };
  const post = async (path: string, msg: string) => { try { await api("POST", path, {}); toast(msg); load(); } catch (e: any) { toast(e.message, "error"); } };
  const scan = async () => {
    setBusy(true);
    try {
      if (folder.trim() !== (s.library_folder || "")) setState(await api("POST", "/api/settings", { library_folder: folder.trim() }));
      const r = await api("POST", "/api/kb/folder/scan", {});
      toast(`${r.files} fichier(s) lisible(s) : ${r.new} nouveau(x), ${r.changed} modifié(s)${r.missing ? `, ${r.missing} disparu(s) du dossier` : ""}${r.skipped ? `, ${r.skipped} ignoré(s)` : ""}.`);
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(false); load();
  };
  return (<>
    <div className="card"><h3>Dossier de la bibliothèque</h3>
      <p className="muted small">Indiquez un dossier de ce PC (cours, documentations, normes…). Jarvis indexe tous les fichiers lisibles qu'il contient, sous-dossiers compris, puis seulement les fichiers nouveaux ou modifiés. Un fichier retiré du dossier reste dans la base : il est seulement signalé.</p>
      <div className="row wrap">
        <input className="input mono grow" value={folder} placeholder="Ex. : C:\Users\Maxence\Documents\Cours" aria-label="Dossier de la bibliothèque" onChange={(e) => setFolder(e.target.value)} />
        <button className="btn primary" disabled={busy || !folder.trim()} onClick={scan}><Icon name="refresh" /> {busy ? "Analyse…" : "Analyser maintenant"}</button>
      </div>
      <Toggle checked={!!s.library_watch} label={`Réanalyser ce dossier toutes les ${s.library_scan_minutes} minutes`}
        hint="Tant que Jarvis est ouvert. Sinon, l'analyse se fait seulement quand vous la demandez." onChange={(x) => patch({ library_watch: x })} />
      {f.folder && (
        <ul className="facts">
          <li>Dossier réglé : <code>{f.folder}</code> {f.exists ? "" : <b className="warn-text">— introuvable sur ce PC</b>}</li>
          <li>{f.files} fichier(s) suivis · {f.indexed} indexé(s) · {f.errors} en erreur · {f.missing} absent(s) du dossier</li>
          <li>Dernière analyse : {f.last_scan ? fmtDate(f.last_scan) : "pas encore depuis l'ouverture de Jarvis"}</li>
        </ul>)}
    </div>
    <div className="grid2">
      <div className="card"><h3>Index vectoriel</h3>
        <ul className="facts">
          <li>{st.chunks} passages indexés pour {st.documents} documents ; plein texte et vecteurs mis à jour à chaque import.</li>
          <li>{kb.embedding_active ? `Modèle d'embeddings : ${kb.embedding_active} — ${st.chunks_semantic}/${st.chunks} passages.` : "Aucun modèle d'embeddings : recherche par mots et similarité locale."}</li>
          <li>{st.ann_active ? "Recherche approchée par signatures : active." : "Recherche exacte : chaque question est comparée à tous les passages (base de moins de 6 000 passages)."}</li>
        </ul>
        <div className="row wrap">
          <button className="btn" disabled={!kb.embedding_active} title={kb.embedding_active ? "" : "Choisissez d'abord un modèle d'embeddings (onglet Modèles)"}
            onClick={() => post("/api/kb/reindex", "Indexation sémantique mise en file.")}><Icon name="refresh" /> Réindexer avec le modèle d'embeddings</button>
          {!kb.embedding_active && <button className="link" onClick={c.goModels}>Choisir un modèle</button>}
        </div>
      </div>
      <div className="card"><h3>Connexions entre connaissances</h3>
        <p className="muted small">{st.links} connexion(s). À chaque import, Jarvis relie le nouveau document aux documents et souvenirs dont le contenu est proche. Recalculez tout après avoir changé de modèle d'embeddings ou désactivé puis réactivé l'option.</p>
        <div className="row"><button className="btn" onClick={() => post("/api/kb/rebuild-links", "Recalcul des connexions mis en file.")}><Icon name="refresh" /> Recalculer toutes les connexions</button></div>
      </div>
    </div>
    <div className="card">
      <div className="row"><h3>File d'indexation</h3>{st.jobs_active > 0 && <span className="tag ok">{st.jobs_active} en cours</span>}{st.job_errors > 0 && <span className="tag warn">{st.job_errors} en erreur</span>}<span className="grow" />
        <button className="btn" onClick={() => api("POST", "/api/kb/jobs/clear", {}).then(load)}>Effacer les tâches terminées</button></div>
      {kb.jobs.length === 0 ? <p className="muted small">Aucune tâche.</p> :
        <JobsTable jobs={kb.jobs} onRetry={(j) => post(`/api/kb/jobs/${j.id}/retry`, "Tâche relancée.")} />}
    </div>
  </>);
}

export const hostOf = host;
