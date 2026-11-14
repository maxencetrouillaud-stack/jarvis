import React, { useEffect, useState } from "react";
import { api, fmtDate } from "./api";
import { Icon, Toggle } from "./common";
import { Ctx, host } from "./kbtypes";

interface Cite { n: number; quote: string; title: string; url: string; provider: string; site: string; reliability: number; published: string }
interface Finding {
  id: number; research_id: number; claim: string; status: string; confidence: number; support: Cite[]; contra: Cite[];
  decision: string; doc_id: number | null; note: string; origin: string; question?: string;
}
interface Run {
  id: number; question: string; mode: string; origin: string; status: string; created: number; finished: number | null;
  summary: string; error: string; log: { at: number; step: string; detail: string }[]; queries: { fr?: string[]; en?: string[] };
  n_sources?: number; n_findings?: number; n_pending?: number; n_integrated?: number;
  sources?: any[]; findings?: Finding[]; category?: string;
}
interface RState {
  runs: Run[]; providers: any[]; pending: Finding[]; watches: any[]; llm_ready: boolean;
  summary: { pending: number; active: number; total: number; integrated: number };
}
const PILL: Record<string, string> = { "terminé": "ok", erreur: "err", "en cours": "ok", "en file": "off", "annulé": "off", "archivé": "off" };
const STATUS_TAG: Record<string, string> = { "vérifié": "ok", "à confirmer": "warn", contradictoire: "warn" };
const STATUS_HELP: Record<string, string> = {
  "vérifié": "Au moins deux sources indépendantes disent la même chose, citation retrouvée dans chacune.",
  "à confirmer": "Une seule source l'affirme : à prendre comme une piste, pas comme un fait établi.",
  contradictoire: "Une autre source dit le contraire ou donne une autre valeur.",
};
const ORIGIN: Record<string, string> = { manuel: "demandée par vous", lacune: "question restée sans source", veille: "veille", conversation: "depuis une conversation" };

function Cites({ list, label }: { list: Cite[]; label: string }) {
  if (!list.length) return null;
  return (
    <div className="cites"><h5>{label}</h5>
      {list.map((c, i) => (
        <div key={i} className="cite"><blockquote>« {c.quote} »</blockquote>
          <div className="muted small"><a href={c.url} target="_blank" rel="noopener noreferrer">{c.title}</a> — {c.provider}, {host(c.site)}{c.published ? `, ${c.published}` : ""} · fiabilité {c.reliability}/5</div></div>
      ))}
    </div>
  );
}

function FindingCard({ f, c, reload }: { f: Finding; c: Ctx; reload: () => void }) {
  const [edit, setEdit] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const decide = async (action: string) => {
    setBusy(true);
    try {
      const r = await api("POST", `/api/research/findings/${f.id}`, { action, claim: edit ?? undefined });
      c.toast(r.decision === "intégré" ? "Connaissance ajoutée à la base, avec ses sources." : r.decision === "déjà connu" ? "Déjà présente dans la base : rien n'a été ajouté." : "Affirmation refusée.");
    } catch (e: any) { c.toast(e.message, "error"); }
    setBusy(false); setEdit(null); reload(); c.load();
  };
  return (
    <article className={`finding s-${f.status === "vérifié" ? "ok" : "warn"}`}>
      <div className="row wrap">
        <span className={`tag ${STATUS_TAG[f.status] || "ghost"}`} title={STATUS_HELP[f.status]}>{f.status}</span>
        <span className="tag ghost" title="Confiance calculée d'après la fiabilité des sources et leur nombre">confiance {Math.round(f.confidence * 100)} %</span>
        <span className="tag ghost">{f.origin === "modèle" ? "extraite par le modèle, citations contrôlées" : "donnée relevée dans le texte"}</span>
        {f.decision !== "en attente" && <span className={`tag ${f.decision === "intégré" ? "ok" : "ghost"}`}>{f.decision}</span>}
      </div>
      {edit === null ? <p className="claim">{f.claim}</p> :
        <textarea className="input" rows={2} value={edit} aria-label="Reformuler l'affirmation" onChange={(e) => setEdit(e.target.value)} />}
      {f.question && <p className="muted small">Recherche : {f.question}</p>}
      {f.note && <p className="notice warn"><Icon name="warn" /> <span>{f.note}</span></p>}
      <Cites list={f.support} label={`Appuyée par ${f.support.length} citation${f.support.length > 1 ? "s" : ""}`} />
      <Cites list={f.contra} label="En désaccord" />
      <div className="row wrap">
        {f.decision === "en attente" && (<>
          <button className="btn primary" disabled={busy} onClick={() => decide("valider")}><Icon name="check" /> {edit !== null ? "Valider cette formulation" : "Valider et ajouter à la base"}</button>
          {edit === null ? <button className="btn" onClick={() => setEdit(f.claim)}><Icon name="edit" /> Reformuler</button> : <button className="btn" onClick={() => setEdit(null)}>Annuler</button>}
          <button className="btn" disabled={busy} onClick={() => decide("refuser")}>Refuser</button>
        </>)}
        {f.doc_id && <button className="btn" onClick={() => c.openDoc(f.doc_id!)}><Icon name="book" /> Ouvrir la fiche</button>}
      </div>
    </article>
  );
}

function RunDetail({ id, c, back, tick }: { id: number; c: Ctx; back: () => void; tick: number }) {
  const [r, setR] = useState<Run | null>(null);
  const reload = () => api<Run>("GET", `/api/research/${id}`).then(setR).catch((e) => { c.toast(e.message, "error"); back(); });
  useEffect(() => { reload(); }, [id, tick]);
  if (!r) return <p className="muted">Chargement…</p>;
  const live = r.status === "en cours" || r.status === "en file";
  return (<>
    <div className="row"><button className="btn" onClick={back}>← Toutes les recherches</button><span className="grow" />
      {live && <button className="btn danger" onClick={() => api("POST", `/api/research/${id}/cancel`, {}).then(reload)}><Icon name="stop" /> Arrêter</button>}
      {!live && <button className="btn danger" onClick={async () => {
        if (!window.confirm("Retirer cette recherche de l'historique ? Les connaissances déjà validées restent dans la base.")) return;
        try { await api("DELETE", `/api/research/${id}`); back(); } catch (e: any) { c.toast(e.message, "error"); }
      }}><Icon name="trash" /> Retirer de l'historique</button>}</div>
    <div className="card">
      <div className="row wrap"><span className={`pill ${PILL[r.status] || "off"}`}>{live && <span className="dots"><i /><i /><i /></span>}{r.status}</span>
        <span className="tag ghost">{r.mode}</span><span className="tag ghost">{ORIGIN[r.origin] || r.origin}</span>{r.category && <span className="cat">{r.category}</span>}</div>
      <h3>{r.question}</h3>
      {r.summary && <p>{r.summary}</p>}
      {r.error && <p className="notice error"><Icon name="warn" /> <span>{r.error}</span></p>}
      <h4>Déroulement</h4>
      <ol className="steps">{r.log.map((l, i) => <li key={i}><b>{l.step}</b>{l.detail && <span> — {l.detail}</span>}</li>)}
        {live && <li className="muted"><span className="dots"><i /><i /><i /></span> en cours…</li>}</ol>
    </div>
    {r.findings!.length > 0 && (
      <div className="card"><h3>Affirmations ({r.findings!.length})</h3>
        <p className="muted small">Chaque affirmation repose sur une citation retrouvée mot pour mot dans la source. Celles dont la citation était introuvable ont été écartées.</p>
        {r.findings!.map((f) => <FindingCard key={f.id} f={f} c={c} reload={reload} />)}
      </div>
    )}
    <div className="card"><h3>Sources consultées ({r.sources!.length})</h3>
      {r.sources!.length === 0 ? <p className="muted small">Aucune source retenue.</p> : r.sources!.map((s) => (
        <details key={s.id} className="src">
          <summary><b>[{s.n}]</b> {s.title} <span className="muted small">— {s.provider}, {s.kind} · fiabilité {s.reliability}/5{s.used ? " · citée" : ""}</span></summary>
          <p className="muted small"><a href={s.url} target="_blank" rel="noopener noreferrer">{s.url}</a>{s.authors ? ` · ${s.authors}` : ""}{s.published ? ` · ${s.published}` : ""}</p>
          <p className="muted small">{s.license}</p>
          <pre className="passage">{s.excerpt}</pre>
        </details>
      ))}
    </div>
  </>);
}

/** Chercheur autonome : lancer une recherche, suivre son déroulement, valider ce qu'il propose. */
export function ResearchTab(c: Ctx) {
  const { toast, state, setState, kb } = c;
  const s = state.settings as any;
  const [st, setSt] = useState<RState | null>(null);
  const [thin, setThin] = useState<any[]>([]);
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("rapide");
  const [open, setOpen] = useState<number | null>(null);
  const [topic, setTopic] = useState("");
  const [every, setEvery] = useState(7);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [tick, setTick] = useState(0);
  const reload = () => {
    api<RState>("GET", "/api/research").then((x) => { setSt(x); setTick((t) => t + 1); }).catch((e) => toast(e.message, "error"));
    api("GET", "/api/research/thin").then(setThin).catch(() => {});
  };
  useEffect(reload, []);
  const active = st?.summary.active || 0;
  useEffect(() => { if (!active) return; const t = setInterval(reload, 1500); return () => clearInterval(t); }, [active]);
  if (!st) return <p className="muted">Chargement…</p>;
  const patch = async (p: any) => { try { setState(await api("POST", "/api/settings", p)); reload(); } catch (e: any) { toast(e.message, "error"); } };
  const start = async (question: string, m = mode, extra: any = {}) => {
    try { const r = await api("POST", "/api/research", { question, mode: m, ...extra }); setQ(""); reload(); setOpen(r.id); } catch (e: any) { toast(e.message, "error"); }
  };
  if (open !== null) return <RunDetail id={open} c={c} back={() => { setOpen(null); reload(); }} tick={tick} />;
  const src = s.research_sources || {};

  return (<>
    <div className="card">
      <h3>Lancer une recherche</h3>
      <div className="row wrap">
        <input className="input grow" value={q} placeholder="Ex. : Quelle est la limite d'élasticité de l'acier S355 ?" aria-label="Question à rechercher"
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && q.trim().length > 3 && start(q.trim())} />
        <div className="seg" role="tablist" aria-label="Mode de recherche">
          <button role="tab" aria-selected={mode === "rapide"} className={mode === "rapide" ? "on" : ""} onClick={() => setMode("rapide")} title="2 requêtes par source, 6 sources lues">Rapide</button>
          <button role="tab" aria-selected={mode === "approfondi"} className={mode === "approfondi" ? "on" : ""} onClick={() => setMode("approfondi")} title="4 requêtes par source, en français et en anglais, 14 sources lues">Approfondie</button>
        </div>
        <button className="btn primary" disabled={q.trim().length < 4} onClick={() => start(q.trim())}><Icon name="search" /> Chercher</button>
      </div>
      <p className="muted small">Le chercheur interroge les sources activées, lit les passages en rapport avec la question, puis ne garde que les affirmations appuyées par une citation exacte. « Vérifié » exige deux sources indépendantes. {s.research_require_validation ? "Rien n'entre dans la base sans votre validation." : "Validation désactivée : seules les affirmations vérifiées, et sans conflit avec la base, sont intégrées d'office."}</p>
      {!st.llm_ready && <p className="notice warn"><Icon name="warn" /> <span>Aucun modèle ne répond : le chercheur collecte les sources et relève les données chiffrées (valeur + unité), mais ne peut pas formuler d'autres affirmations.</span></p>}
    </div>

    {st.pending.length > 0 && (
      <div className="card"><h3>À valider ({st.pending.length})</h3>
        {st.pending.map((f) => <FindingCard key={f.id} f={f} c={c} reload={reload} />)}
      </div>
    )}

    <div className="card"><h3>Recherches {active > 0 && <span className="tag ok">{active} en cours</span>}</h3>
      {st.runs.length === 0 ? <p className="muted small">Aucune recherche pour l'instant.</p> : (
        <table className="table"><tbody>{st.runs.map((r) => (
          <tr key={r.id}>
            <td><span className={`pill ${PILL[r.status] || "off"}`}>{(r.status === "en cours") && <span className="dots"><i /><i /><i /></span>}{r.status}</span></td>
            <td><button className="link" onClick={() => setOpen(r.id)}>{r.question}</button>
              <div className="muted small">{r.status === "en cours" && r.log.length ? `${r.log[r.log.length - 1].step}…` : r.error || r.summary || ORIGIN[r.origin]}</div></td>
            <td className="muted small nowrap">{r.mode} · {ORIGIN[r.origin] || r.origin}</td>
            <td className="muted small right nowrap">{fmtDate(r.created)}</td>
          </tr>))}</tbody></table>)}
    </div>

    <div className="grid2">
      <div className="card"><h3>Sujets insuffisamment documentés ({thin.length})</h3>
        {thin.length === 0 ? <p className="muted small">Rien à signaler : aucune question restée sans source, aucun concept ni aucune affirmation appuyés sur une seule source.</p> :
          thin.map((t, i) => <div key={i} className="row hit-row"><div className="grow">{t.label}<div className="muted small">{t.kind} · {t.detail}</div></div>
            <button className="btn" onClick={() => start(t.question, "rapide", t.gap_id ? { origin: "lacune", gap_id: t.gap_id } : {})}>Rechercher</button></div>)}
      </div>
      <div className="card"><h3>Veille</h3>
        <p className="muted small">Sujets que le chercheur relance régulièrement, en demandant d'abord les publications les plus récentes. {s.research_watch ? "La veille tourne tant que Jarvis est ouvert." : "La veille automatique est désactivée."}</p>
        <div className="row wrap">
          <input className="input grow" value={topic} placeholder="Ex. : impression 3D métal" aria-label="Sujet de veille" onChange={(e) => setTopic(e.target.value)} />
          <select className="select" value={every} onChange={(e) => setEvery(+e.target.value)} aria-label="Fréquence">
            <option value={1}>chaque jour</option><option value={7}>chaque semaine</option><option value={30}>chaque mois</option></select>
          <button className="btn" disabled={topic.trim().length < 3} onClick={() => api("POST", "/api/research/watch", { topic: topic.trim(), every_days: every }).then(() => { setTopic(""); reload(); }).catch((e) => toast(e.message, "error"))}>Suivre</button>
        </div>
        {st.watches.map((w) => (
          <div key={w.id} className="row hit-row"><div className="grow">{w.topic}<div className="muted small">tous les {w.every_days} jour(s) · {w.last_run ? `dernière recherche le ${fmtDate(w.last_run)}` : "jamais lancée"}{w.enabled ? "" : " · en pause"}</div></div>
            <button className="btn" onClick={() => api("POST", `/api/research/watch/${w.id}/run`, {}).then(reload)}>Lancer</button>
            <button className="btn" onClick={() => api("PATCH", `/api/research/watch/${w.id}`, { enabled: !w.enabled }).then(reload)}>{w.enabled ? "Mettre en pause" : "Reprendre"}</button>
            <button className="icon-btn" aria-label="Ne plus suivre ce sujet" title="Ne plus suivre" onClick={() => api("DELETE", `/api/research/watch/${w.id}`).then(reload)}><Icon name="trash" size={14} /></button></div>
        ))}
        <Toggle checked={!!s.research_watch} label="Relancer automatiquement les sujets suivis" onChange={(x) => patch({ research_watch: x })} />
      </div>
    </div>

    <div className="card"><h3>Sources interrogées</h3>
      {st.providers.map((p) => (
        <div key={p.key} className="provider">
          <Toggle checked={p.enabled} label={p.label} hint={p.description + (p.enabled && !p.ready ? ` — inactive : ${p.note}` : "")}
            onChange={(x) => patch({ research_sources: { ...src, [p.key]: x } })} />
          {(p.needs_key || p.key_optional) && (
            <div className="row wrap keyrow">
              <span className={`tag ${p.has_key ? "ok" : "ghost"}`}>{p.has_key ? "clé enregistrée" : p.key_optional ? "sans clé : quota réduit" : "clé manquante"}</span>
              <input className="input mono grow" type="password" autoComplete="off" value={keys[p.key] || ""} placeholder={p.key === "web" ? "Clé d'API Brave Search" : "Clé d'API OpenAlex (gratuite, facultative)"}
                aria-label={`Clé ${p.label}`} onChange={(e) => setKeys({ ...keys, [p.key]: e.target.value })} />
              <button className="btn" disabled={!(keys[p.key] || "").trim() && !p.has_key} onClick={async () => {
                try { await api("POST", "/api/research/keys", { name: p.key === "web" ? "brave" : "openalex", key: (keys[p.key] || "").trim() }); setKeys({ ...keys, [p.key]: "" }); toast((keys[p.key] || "").trim() ? "Clé enregistrée (chiffrée sur ce PC)." : "Clé effacée."); reload(); }
                catch (e: any) { toast(e.message, "error"); }
              }}>{(keys[p.key] || "").trim() ? "Enregistrer" : "Effacer la clé"}</button>
            </div>
          )}
        </div>
      ))}
      <Toggle checked={!!s.research_require_validation} label="Demander ma validation avant d'ajouter une connaissance"
        hint="Recommandé. Désactivé, seules les affirmations vérifiées par deux sources indépendantes, et qui ne contredisent rien dans la base, sont intégrées automatiquement." onChange={(x) => patch({ research_require_validation: x })} />
      <Toggle checked={s.research_on_gap !== "jamais"} label="Proposer une recherche quand la base ne sait pas répondre"
        hint="Dans la conversation, un bouton « Lancer une recherche » apparaît sous la réponse. Aucune recherche ne part sans votre clic." onChange={(x) => patch({ research_on_gap: x ? "proposer" : "jamais" })} />
      <p className="muted small">Classement des connaissances validées : {kb.llm_ready ? "domaine de la question" : "domaine de la question (d'après son vocabulaire)"}. Les références d'origine sont conservées avec chaque fiche.</p>
    </div>
  </>);
}
