import React, { useEffect, useRef, useState } from "react";
import { api, fmtBytes, fmtDate, TOKEN } from "./api";
import { Icon } from "./common";
import { Resource, useEvents } from "./studio";

type Toast = (t: string, k?: string) => void;
interface Tab {
  id: string; engine: string; owner: string; mission_id: number | null; url: string; title: string; kind: string;
  loading: boolean; error: string; seq: number; can_back: boolean; can_forward: boolean;
  doc?: { html?: string; description?: string; results?: { title: string; url: string; snippet: string; provider: string }[];
    query?: string; note?: string; file?: { name: string; mime: string; bytes: number; blocked?: boolean }; images?: number };
}
interface BState {
  tabs: Tab[]; favorites: { id: number; url: string; title: string }[]; search_engine: string; web_search: boolean;
  engines: { lecture: { available: boolean }; chromium: { available: boolean; reason: string; installed: boolean; channel: string } };
  install: { state: string; log: string[] };
}
const KEYS = new Set(["Enter", "Backspace", "Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Delete",
  "Home", "End", "PageUp", "PageDown"]);

/** Navigateur intégré : onglets (les vôtres et ceux de l'agent), mode lecture ou vrai Chromium affiché en direct. */
export function BrowserView({ toast, active, openUrl, askJarvis, goSettings }: {
  toast: Toast; active: boolean; openUrl: { url: string; n: number } | null;
  askJarvis: (text: string, tabId: string) => void; goSettings: () => void;
}) {
  const [st, setSt] = useState<BState | null>(null);
  const [cur, setCur] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab | null>(null);
  const [addr, setAddr] = useState("");
  const [side, setSide] = useState<"" | "historique" | "favoris" | "telechargements" | "agent">("");
  const [history, setHistory] = useState<any[]>([]);
  const [downloads, setDownloads] = useState<Resource[]>([]);
  const [agentLog, setAgentLog] = useState<any[]>([]);
  const [frame, setFrame] = useState<string>("");
  const [follow, setFollow] = useState(true);   // suivre automatiquement l'onglet de l'agent qui travaille
  const box = useRef<HTMLDivElement>(null);
  const keys = useRef<HTMLInputElement>(null);
  const busyFrame = useRef(false);

  const loadState = async () => {
    try { const s = await api<BState>("GET", "/api/browser"); setSt(s); return s; } catch (e: any) { toast(e.message, "error"); return null; }
  };
  const loadTab = async (id: string | null) => {
    if (!id) { setTab(null); return; }
    try { const t = await api<Tab>("GET", `/api/browser/tabs/${id}`); setTab(t); setAddr(t.url.startsWith("jarvis:") ? (t.doc?.query || "") : t.url); }
    catch { setTab(null); }
  };
  useEffect(() => {
    if (!active) return;
    loadState().then((s) => { if (s && !cur && s.tabs.length) setCur(s.tabs[s.tabs.length - 1].id); });
  }, [active]);
  useEffect(() => { loadTab(cur); setFrame(""); }, [cur]);
  useEffect(() => { if (openUrl && active) newTab(openUrl.url); }, [openUrl]);

  useEvents((e) => {
    if (!active || e.topic !== "browser") {
      if (active && e.topic === "agent" && side === "agent") setAgentLog((l) => [e, ...l].slice(0, 80));
      return;
    }
    if (e.action === "installation") { loadState(); return; }
    if (e.action === "ouvert" || e.action === "fermé") loadState();
    if (e.owner === "agent" && follow && e.action === "ouvert") setCur(e.tab_id);
    if (e.tab_id === cur && e.action !== "chargement") loadTab(cur);
    if (e.tab_id === cur && e.action === "chargement") setTab((t) => t && { ...t, loading: true, url: e.url });
    if (e.action === "téléchargé") toast(`Téléchargé : ${e.file}`);
    if (e.action !== "chargement") setSt((s) => s && { ...s, tabs: s.tabs.map((t) => t.id === e.tab_id ? { ...t, title: e.title, url: e.url, owner: e.owner } : t) });
  });

  // rendu en direct du moteur Chromium (images JPEG du vrai navigateur)
  const grab = async () => {
    if (!tab || tab.engine !== "chromium" || busyFrame.current) return;
    busyFrame.current = true;
    try {
      const r = await fetch(`/api/browser/tabs/${tab.id}/frame`, { headers: { "X-Jarvis-Token": TOKEN } });
      if (r.ok) { const u = URL.createObjectURL(await r.blob()); setFrame((old) => { if (old) URL.revokeObjectURL(old); return u; }); }
    } catch { /* image suivante */ }
    busyFrame.current = false;
  };
  useEffect(() => {
    if (!active || !tab || tab.engine !== "chromium" || tab.kind === "accueil") return;
    grab();
    const t = setInterval(() => { if (!document.hidden) grab(); }, 600);
    return () => clearInterval(t);
  }, [active, tab?.id, tab?.engine, tab?.kind]);

  const call = async (path: string, body: any = {}) => {
    if (!cur) return;
    try { const t = await api<Tab>("POST", `/api/browser/tabs/${cur}${path}`, body); setTab(t); setAddr(t.url.startsWith("jarvis:") ? (t.doc?.query || "") : t.url); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const newTab = async (target = "", engine?: string) => {
    try { const t = await api<Tab>("POST", "/api/browser/tabs", { target, engine }); await loadState(); setCur(t.id); setTab(t); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const close = async (id: string) => {
    try { await api("DELETE", `/api/browser/tabs/${id}`); const s = await loadState(); if (id === cur) setCur(s?.tabs.length ? s.tabs[s.tabs.length - 1].id : null); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const go = (target: string) => { if (!cur) newTab(target); else call("/navigate", { target }); };

  const input = async (action: string, extra: any) => {
    if (!tab) return;
    try { await api("POST", `/api/browser/tabs/${tab.id}/input`, { action, ...extra }); setTimeout(grab, 150); setTimeout(grab, 700); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const coords = (e: React.MouseEvent<HTMLImageElement>) => {
    const r = (e.target as HTMLImageElement).getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * 1280, y: ((e.clientY - r.top) / r.height) * 800 };
  };

  const fav = st?.favorites.find((f) => tab && f.url === tab.url);
  const toggleFav = async () => {
    if (!tab || !/^https?:/.test(tab.url)) return;
    try {
      const favs = fav ? await api("DELETE", `/api/browser/favorites/${fav.id}`) : await api("POST", "/api/browser/favorites", { url: tab.url, title: tab.title });
      setSt((s) => s && { ...s, favorites: favs });
    } catch (e: any) { toast(e.message, "error"); }
  };
  const openSide = async (s: typeof side) => {
    const next = side === s ? "" : s;
    setSide(next);
    if (next === "historique") setHistory(await api("GET", "/api/browser/history").catch(() => []));
    if (next === "telechargements") setDownloads(await api("GET", "/api/resources?kind=telechargement").catch(() => []));
    if (next === "agent") setAgentLog(await api("GET", "/api/agents/log?limit=60").catch(() => []));
  };
  const dl = async (url: string) => {
    try { const r = await api<Resource>("POST", "/api/browser/download", { url }); toast(`Téléchargé dans le dossier de Jarvis : ${r.title}`); }
    catch (e: any) { toast(e.message, "error"); }
  };

  const readerClick = (e: React.MouseEvent) => {
    const a = (e.target as HTMLElement).closest("a[data-href]") as HTMLAnchorElement | null;
    if (!a) { if ((e.target as HTMLElement).closest("a")) e.preventDefault(); return; }
    e.preventDefault();
    const url = a.getAttribute("data-href")!;
    if (e.ctrlKey || e.metaKey || e.button === 1) newTab(url); else go(url);
  };

  const chromium = st?.engines.chromium;
  return (
    <div className="browser" ref={box}>
      <div className="btabs" role="tablist">
        {(st?.tabs || []).map((t) => (
          <div key={t.id} className={`btab ${t.id === cur ? "on" : ""} ${t.owner === "agent" ? "agent" : ""}`}>
            <button role="tab" aria-selected={t.id === cur} onClick={() => setCur(t.id)} title={t.url}>
              {t.owner === "agent" && <span className="agent-dot" title="Onglet ouvert par l'agent de Jarvis">IA</span>}
              {t.engine === "chromium" && <Icon name="globe" size={12} />}
              <span>{t.title || "Nouvel onglet"}</span>
            </button>
            <button className="icon-btn" aria-label="Fermer l'onglet" onClick={() => close(t.id)}><Icon name="x" size={12} /></button>
          </div>
        ))}
        <button className="icon-btn" title="Nouvel onglet" aria-label="Nouvel onglet" onClick={() => newTab()}><Icon name="plus" size={16} /></button>
        <span className="grow" />
        <label className="follow" title="Afficher automatiquement les pages ouvertes par l'agent de Jarvis">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Suivre l'agent</label>
      </div>
      <div className="btool">
        <button className="icon-btn" aria-label="Précédente" disabled={!tab?.can_back} onClick={() => call("/back")}><Icon name="back" /></button>
        <button className="icon-btn" aria-label="Suivante" disabled={!tab?.can_forward} onClick={() => call("/forward")}><Icon name="forward" /></button>
        <button className="icon-btn" aria-label="Actualiser" disabled={!tab} onClick={() => call("/reload")}><Icon name="refresh" /></button>
        <button className="icon-btn" aria-label="Accueil" onClick={() => (cur ? call("/home") : newTab())}><Icon name="home" /></button>
        <form className="addr" onSubmit={(e) => { e.preventDefault(); if (addr.trim()) go(addr.trim()); }}>
          {tab?.loading ? <span className="dots"><i /><i /><i /></span> : <Icon name={tab && /^https:/.test(tab.url) ? "shield" : "search"} size={14} />}
          <input value={addr} onChange={(e) => setAddr(e.target.value)} placeholder="Adresse ou recherche" aria-label="Adresse ou recherche" spellCheck={false} />
        </form>
        <button className={`icon-btn ${fav ? "on" : ""}`} aria-label={fav ? "Retirer des favoris" : "Ajouter aux favoris"} title={fav ? "Retirer des favoris" : "Ajouter aux favoris"} disabled={!tab || !/^https?:/.test(tab.url)} onClick={toggleFav}><Icon name="star" /></button>
        <div className="seg two small-seg" aria-label="Moteur d'affichage">
          <button className={tab?.engine !== "chromium" ? "on" : ""} disabled={!tab} onClick={() => call("/engine", { engine: "lecture" })} title="Page nettoyée, sans scripts">Lecture</button>
          <button className={tab?.engine === "chromium" ? "on" : ""} disabled={!tab || !chromium?.available} title={chromium?.available ? `Vrai navigateur (${chromium.channel || "Chromium"})` : chromium?.reason}
            onClick={() => call("/engine", { engine: "chromium" })}>Chromium</button>
        </div>
        <button className="icon-btn" aria-label="Demander à Jarvis sur cette page" title="Demander à Jarvis à propos de cette page" disabled={!tab || tab.kind === "accueil"}
          onClick={() => { const q = window.prompt("Que voulez-vous savoir sur cette page ?", "Résume cette page et donne les points essentiels."); if (q && tab) askJarvis(q, tab.id); }}><Icon name="sparkle" /></button>
        <button className="icon-btn" aria-label="Ouvrir dans le navigateur du PC" title="Ouvrir dans votre navigateur habituel" disabled={!tab || !/^https?:/.test(tab.url)} onClick={() => tab && window.open(tab.url, "_blank", "noopener")}><Icon name="external" /></button>
        <button className="icon-btn" aria-label="Plein écran" onClick={() => box.current?.requestFullscreen?.()}><Icon name="expand" /></button>
        <span className="sep" />
        {(["historique", "favoris", "telechargements", "agent"] as const).map((s) => (
          <button key={s} className={`icon-btn ${side === s ? "on" : ""}`} aria-pressed={side === s}
            aria-label={{ historique: "Historique", favoris: "Liste des favoris", telechargements: "Téléchargements", agent: "Actions des agents" }[s]}
            title={{ historique: "Historique", favoris: "Liste des favoris", telechargements: "Téléchargements", agent: "Actions des agents" }[s]}
            onClick={() => openSide(s)}><Icon name={{ historique: "history", favoris: "star", telechargements: "download", agent: "activity" }[s]} /></button>))}
      </div>

      <div className="bbody">
        <div className="bpage">
          {!tab && <Home st={st} go={(u) => newTab(u)} goSettings={goSettings} />}
          {tab?.error && <div className="notice error slim"><Icon name="warn" size={15} /><span>{tab.error}</span></div>}
          {tab && tab.kind === "accueil" && <Home st={st} go={go} goSettings={goSettings} />}
          {tab && tab.engine === "chromium" && tab.kind !== "accueil" && (
            <div className="live" onClick={() => keys.current?.focus()}>
              {frame ? <img src={frame} alt={`Rendu de ${tab.title}`} draggable={false}
                onClick={(e) => input("click", coords(e))} onDoubleClick={(e) => input("click", { ...coords(e), double: true })}
                onWheel={(e) => input("scroll", { ...coords(e as any), dy: e.deltaY, dx: e.deltaX })} />
                : <p className="muted pad">Rendu en cours…</p>}
              <input ref={keys} className="keysink" aria-label="Clavier transmis à la page"
                onKeyDown={(e) => {
                  if (KEYS.has(e.key)) { e.preventDefault(); input("key", { key: e.key }); }
                  else if ((e.ctrlKey || e.metaKey) && e.key.length === 1) { e.preventDefault(); input("key", { key: `Control+${e.key}` }); }
                }}
                onChange={(e) => { const v = e.target.value; e.target.value = ""; if (v) input("type", { text: v }); }} />
              <p className="live-hint muted small">Cliquez dans la page puis tapez au clavier : vos actions sont transmises au vrai navigateur. Jarvis ne saisit jamais de mot de passe à votre place.</p>
            </div>
          )}
          {tab && tab.engine !== "chromium" && tab.kind === "page" && tab.doc && (
            <article className="reader" onClick={readerClick} onAuxClick={readerClick}>
              <h1>{tab.title}</h1>
              {tab.doc.description && <p className="muted">{tab.doc.description}</p>}
              <div dangerouslySetInnerHTML={{ __html: tab.doc.html || "" }} />
            </article>
          )}
          {tab && tab.kind === "recherche" && tab.doc && (
            <div className="results">
              <h2>Résultats pour « {tab.doc.query} »</h2>
              {tab.doc.note && <div className="notice info slim"><Icon name="search" size={15} /><span>{tab.doc.note}</span></div>}
              {(tab.doc.results || []).map((r) => (
                <div key={r.url} className="result">
                  <button className="link big" onClick={(e) => (e.ctrlKey ? newTab(r.url) : go(r.url))}>{r.title}</button>
                  <i>{r.url.replace(/^https?:\/\//, "").slice(0, 80)} · {r.provider}</i>
                  <p>{r.snippet}</p>
                </div>))}
            </div>
          )}
          {tab && tab.kind === "fichier" && tab.doc?.file && (
            <div className="file-view">
              <Icon name="download" size={30} />
              <h3>{tab.doc.file.name}</h3>
              <p className="muted">{tab.doc.file.mime} · {fmtBytes(tab.doc.file.bytes)}</p>
              {tab.doc.file.blocked ? <div className="notice warn slim"><Icon name="warn" size={15} /><span>Ce type de fichier peut exécuter du code : Jarvis ne le télécharge pas.</span></div>
                : <button className="btn primary" onClick={() => dl(tab.url)}><Icon name="download" size={14} /> Télécharger</button>}
              {tab.doc.html && <article className="reader" dangerouslySetInnerHTML={{ __html: tab.doc.html }} />}
            </div>
          )}
        </div>
        {side && (
          <aside className="bside">
            {side === "historique" && <><h4>Historique</h4>
              <button className="link" onClick={async () => { if (window.confirm("Effacer tout l'historique de navigation ?")) { await api("DELETE", "/api/browser/history"); setHistory([]); } }}>Effacer l'historique</button>
              {history.map((h) => <button key={h.id} className="bitem" onClick={() => go(h.url)} title={h.url}><b>{h.agent ? "🤖 " : ""}{h.title || h.url}</b><i>{fmtDate(h.at)}</i></button>)}</>}
            {side === "favoris" && <><h4>Favoris</h4>{(st?.favorites || []).length === 0 && <p className="muted small">Aucun favori : étoile dans la barre d'adresse.</p>}
              {(st?.favorites || []).map((f) => <button key={f.id} className="bitem" onClick={() => go(f.url)} title={f.url}><b>{f.title}</b><i>{f.url.replace(/^https?:\/\//, "")}</i></button>)}</>}
            {side === "telechargements" && <><h4>Téléchargements</h4>{downloads.length === 0 && <p className="muted small">Aucun fichier téléchargé.</p>}
              {downloads.map((d) => <div key={d.id} className="bitem"><b>{d.title}</b><i>{fmtBytes(d.bytes)} · {fmtDate(d.created)} · {d.meta?.saved_to}</i></div>)}</>}
            {side === "agent" && <><h4>Actions des agents</h4>{agentLog.map((l: any, i) => <div key={l.id || l.log_id || i} className={`bitem log ${l.ok === false || l.ok === 0 ? "bad" : ""}`}><b>{l.action}</b><i>{l.agent}{l.detail ? ` — ${String(l.detail).slice(0, 120)}` : ""}</i></div>)}</>}
          </aside>
        )}
      </div>
    </div>
  );
}

function Home({ st, go, goSettings }: { st: BState | null; go: (u: string) => void; goSettings: () => void }) {
  const ch = st?.engines.chromium;
  return (
    <div className="bhome">
      <Icon name="globe" size={40} />
      <h2>Navigateur de Jarvis</h2>
      <p className="muted">Tapez une adresse ou une recherche. Les pages ouvertes par l'agent de Jarvis apparaissent en onglets marqués « IA ».</p>
      {!st?.web_search && <div className="notice info slim"><Icon name="search" size={15} /><span>Recherche web générale : ajoutez une clé Brave Search (offre gratuite) dans Connaissances › Chercheur. Sans clé, les recherches en mode lecture se limitent à Wikipédia et aux sources scientifiques.</span></div>}
      {ch && !ch.available && <div className="notice info slim"><Icon name="bolt" size={15} />
        <span>Moteur Chromium indisponible : {ch.reason}. Le mode lecture fonctionne sans installation.</span>
        <button className="btn" onClick={goSettings}>Installer</button></div>}
      {(st?.favorites || []).length > 0 && <div className="favgrid">{st!.favorites.map((f) => (
        <button key={f.id} onClick={() => go(f.url)} title={f.url}><b>{f.title}</b><i>{f.url.replace(/^https?:\/\//, "").split("/")[0]}</i></button>))}</div>}
    </div>
  );
}
