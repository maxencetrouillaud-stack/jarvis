import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, AppState } from "./api";
import { ChatView } from "./chat";
import { Icon, Orb, Phase, useToasts } from "./common";
import { KnowledgeView } from "./knowledge";
import { DashboardView, MemoryView, ModelsView, SettingsView, Setup } from "./panels";
import { UpdateBanner } from "./update";
import { SphereView } from "./sphere";
import { ArtifactsView } from "./artifacts";
import { BrowserView } from "./browser";
import { ImagesView } from "./images";
import { ProjectsView } from "./projects";

const VIEWS = [
  { id: "chat", label: "Chat", icon: "chat" },
  { id: "artifacts", label: "Artefacts", icon: "artifact" },
  { id: "browser", label: "Navigateur", icon: "globe" },
  { id: "images", label: "Images", icon: "image" },
  { id: "projects", label: "Projets", icon: "folder" },
  { id: "memory", label: "Mémoire", icon: "memory" },
  { id: "kb", label: "Connaissances", icon: "book" },
  { id: "models", label: "Modèles", icon: "models" },
  { id: "dash", label: "Tableau de bord", icon: "dash" },
  { id: "settings", label: "Paramètres", icon: "settings" },
];

/** Recherche globale : base de connaissances, mémoire personnelle et conversations. */
function GlobalSearch({ onDoc, onConv, onMemory }: { onDoc: (id: number) => void; onConv: (id: string) => void; onMemory: () => void }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<any | null>(null);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (q.trim().length < 2) { setRes(null); return; }
    const t = setTimeout(() => api("GET", `/api/global-search?q=${encodeURIComponent(q)}`).then((r) => { setRes(r); setOpen(true); }).catch(() => setRes(null)), 220);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    const out = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); box.current?.querySelector("input")?.focus(); }
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", out); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", out); document.removeEventListener("keydown", key); };
  }, []);
  const pick = (fn: () => void) => { setOpen(false); setQ(""); fn(); };
  const empty = res && !res.knowledge.length && !res.memories.length && !res.messages.length;
  return (
    <div className="gsearch" ref={box}>
      <div className="field-icon"><Icon name="search" size={15} />
        <input value={q} placeholder="Rechercher partout — connaissances, mémoire, conversations  (Ctrl+K)" aria-label="Recherche globale"
          onChange={(e) => setQ(e.target.value)} onFocus={() => res && setOpen(true)} /></div>
      {open && res && (
        <div className="gresults">
          {empty && <p className="muted pad">Aucun résultat.</p>}
          {res.knowledge.length > 0 && <h4>Connaissances</h4>}
          {res.knowledge.map((r: any) => <button key={`k${r.id}`} onClick={() => pick(() => onDoc(r.doc_id))}><b>{r.title}{r.locator ? ` — ${r.locator}` : ""}</b><span>{r.content.slice(0, 150)}</span></button>)}
          {res.memories.length > 0 && <h4>Mémoire personnelle</h4>}
          {res.memories.map((r: any) => <button key={`m${r.id}`} onClick={() => pick(onMemory)}><b>{r.category}</b><span>{r.content.slice(0, 150)}</span></button>)}
          {res.messages.length > 0 && <h4>Conversations</h4>}
          {res.messages.map((r: any) => <button key={`c${r.id}`} onClick={() => pick(() => onConv(r.conv_id))}><b>{r.title}</b><span>{r.content.slice(0, 150)}</span></button>)}
        </div>
      )}
    </div>
  );
}

function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [fatal, setFatal] = useState("");
  const [view, setView] = useState("chat");
  const [phase, setPhase] = useState<Phase>("idle");
  const [setup, setSetup] = useState(false);
  const [jump, setJump] = useState<{ id: string; n: number } | null>(null);
  const [focusDoc, setFocusDoc] = useState<{ id: number; n: number } | null>(null);
  const [ask, setAsk] = useState<{ text: string; n: number; tool?: string; tab_id?: string } | null>(null);
  const [openArt, setOpenArt] = useState<{ id: number; n: number } | null>(null);
  const [libArt, setLibArt] = useState<{ id: number; n: number } | null>(null);
  const [openUrl, setOpenUrl] = useState<{ url: string; n: number } | null>(null);
  const [memMode, setMemMode] = useState<{ list: boolean; add?: boolean; editId?: number; n: number }>({ list: false, n: 0 });
  const [kbTab, setKbTab] = useState<{ tab: string; n: number } | null>(null);
  const { push, view: toasts } = useToasts();

  const refreshState = useCallback(() => { api<AppState>("GET", "/api/state").then(setState).catch(() => {}); }, []);

  useEffect(() => {
    (async () => {
      try {
        const st = await api<AppState>("GET", "/api/state");
        setState(st);
        setSetup(st.first_run);
        setState(await api<AppState>("POST", "/api/models/refresh", {}));
      } catch (e: any) { setFatal(e.message); }
    })();
  }, []);

  useEffect(() => {   // signe de vie : en mode application, Jarvis s'arrête quand sa fenêtre est fermée
    const t = setInterval(() => { api("POST", "/api/heartbeat", {}).catch(() => {}); }, 20000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => { if (state) document.title = state.settings.assistant_name || "Jarvis"; }, [state?.settings.assistant_name]);

  if (fatal) return <div className="boot"><p>Impossible de joindre le moteur de Jarvis : {fatal}</p><button className="btn primary" onClick={() => location.reload()}>Réessayer</button></div>;
  if (!state) return <div className="boot"><Orb phase="thinking" size={110} /></div>;

  const goDoc = (id: number) => { setFocusDoc({ id, n: Date.now() }); setView("kb"); };
  const askJarvis = (text: string, tool?: string, tab_id?: string) => { setAsk({ text, n: Date.now(), tool, tab_id }); setView("chat"); };
  const goUrl = (url: string) => { setOpenUrl({ url, n: Date.now() }); setView("browser"); };
  const goConv = (id: string) => { setJump({ id, n: Date.now() }); setView("chat"); };
  const goArtifact = (id: number) => { setLibArt({ id, n: Date.now() }); setView("artifacts"); };
  const common = { state, setState, toast: push, refreshState };
  return (
    <div className="shell">
      <nav className="rail" aria-label="Navigation">
        <div className="brand" title={`${state.settings.assistant_name} — ${phase === "idle" ? "en veille" : "actif"}`}><Orb phase={phase} size={38} /></div>
        {VIEWS.map((v) => (
          <button key={v.id} className={`rail-btn ${view === v.id ? "on" : ""}`} onClick={() => setView(v.id)}
            aria-current={view === v.id ? "page" : undefined}>
            <Icon name={v.icon} size={20} /><span>{v.label}</span>
          </button>
        ))}
        <span className="grow" />
        <span className="ver">v{state.version}</span>
      </nav>
      <main className="stage">
        <UpdateBanner state={state} toast={push} />
        <GlobalSearch onDoc={goDoc} onMemory={() => setView("memory")} onConv={(id) => { setJump({ id, n: Date.now() }); setView("chat"); }} />
        <div hidden={view !== "chat"} className="view"><ChatView {...common} active={view === "chat"} phase={phase} setPhase={setPhase} goModels={() => setView("models")} jump={jump} goDoc={goDoc} go={setView} ask={ask} goResearch={() => { setKbTab({ tab: "research", n: Date.now() }); setView("kb"); }}
          openArtifact={openArt} openUrl={goUrl} /></div>
        {view === "artifacts" && <div className="view"><ArtifactsView toast={push} active openId={libArt} askJarvis={(t, tool) => askJarvis(t, tool)} /></div>}
        {view === "browser" && <div className="view"><BrowserView toast={push} active openUrl={openUrl} goSettings={() => setView("settings")}
          askJarvis={(t, tab) => askJarvis(t, "page", tab)} /></div>}
        {view === "images" && <div className="view"><ImagesView toast={push} active goModels={() => setView((state.settings as any).image_provider === "gemini" ? "models" : "settings")} askJarvis={(t) => askJarvis(t)} /></div>}
        {view === "projects" && <div className="view scroll"><ProjectsView toast={push} active openConv={goConv} openUrl={goUrl}
          openArtifact={goArtifact} /></div>}
        {view === "kb" && <div className="view scroll"><KnowledgeView {...common} focusDoc={focusDoc} openTab={kbTab}
          toSphere={() => { setMemMode({ list: false, n: Date.now() }); setView("memory"); }} goModels={() => setView("models")} /></div>}
        {view === "memory" && (memMode.list
          ? <div className="view scroll"><MemoryView {...common} active key={memMode.n} startAdd={memMode.add} editId={memMode.editId} toSphere={() => setMemMode({ list: false, n: Date.now() })} /></div>
          : <div className="view enter"><SphereView {...common} goDoc={goDoc} goImport={() => { setKbTab({ tab: "import", n: Date.now() }); setView("kb"); }}
              goResearch={() => { setKbTab({ tab: "research", n: Date.now() }); setView("kb"); }}
              ask={(text) => { setAsk({ text, n: Date.now() }); setView("chat"); }} toList={(o) => setMemMode({ list: true, ...o, n: Date.now() })} /></div>)}
        {view === "models" && <div className="view scroll"><ModelsView {...common} active /></div>}
        {view === "dash" && <div className="view scroll"><DashboardView {...common} active phase={phase} /></div>}
        {view === "settings" && <div className="view scroll"><SettingsView {...common} active /></div>}
      </main>
      {setup && <Setup state={state} setState={setState} toast={push} onDone={() => setSetup(false)} />}
      {toasts}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
