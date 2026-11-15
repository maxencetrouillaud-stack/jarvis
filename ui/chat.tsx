import React, { useEffect, useRef, useState } from "react";
import { api, AppState, chatStream, Conversation, fmtDate, Message, MissionStep, shortModel } from "./api";
import { ArtifactViewer, Selection } from "./artifacts";
import { Icon, Markdown, ModelSelect, Orb, Phase } from "./common";
import { Artifact, ImageTile, MissionSteps, ProjectSelect, Resource, useEvents, useProjects, useResources, WebSources } from "./studio";
import { norm, SentenceStream, Voice, voiceSupport, VState } from "./voice";

const NAV: [string, string, string][] = [["memoire", "memory", "la mémoire"], ["connaissances", "kb", "les connaissances"], ["reglages", "settings", "les réglages"],
  ["tableau de bord", "dash", "le tableau de bord"], ["modeles", "models", "les modèles"], ["conversation", "chat", "la conversation"]];
const VLABEL: Record<VState, string> = { off: "", standby: "En veille — dites le mot-clé", listening: "Je vous écoute", thinking: "Je réfléchis",
  speaking: "Je parle — parlez pour m'interrompre", error: "Micro indisponible" };

interface Props {
  state: AppState; active: boolean; setPhase: (p: Phase) => void; phase: Phase;
  toast: (t: string, kind?: string) => void; refreshState: () => void; goModels: () => void;
  jump: { id: string; n: number } | null; goDoc: (id: number) => void;
  setState: (s: AppState) => void; go: (view: string) => void; goResearch: () => void;
  ask: { text: string; n: number; tool?: string; tab_id?: string } | null;
  openArtifact: { id: number; n: number } | null; openUrl: (u: string) => void;
}
type PanelItem = { kind: "artifact" | "image"; id: number; title: string };
const TOOLS: [string, string, string][] = [["", "Auto", "Jarvis choisit seul les outils"], ["image", "Image", "Générer ou modifier une image"],
  ["artifact", "Artefact", "Créer une application, un outil, un schéma, une présentation…"], ["web", "Web", "Chercher et vérifier sur Internet"]];

export function ChatView({ state, active, setPhase, phase, toast, refreshState, goModels, jump, goDoc, setState, go, ask, goResearch, openArtifact, openUrl }: Props) {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [model, setModel] = useState("");
  const [deep, setDeep] = useState(state.settings.deep_mode_default);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<any[] | null>(null);
  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const box = useRef<HTMLTextAreaElement>(null);
  const [vstate, setVstate] = useState<VState>("off");
  const [vinfo, setVinfo] = useState("");
  const [heard, setHeard] = useState("");
  const [bars, setBars] = useState<number[]>([]);
  const voice = useRef<Voice | null>(null);
  const live = useRef<any>({});
  const [panel, setPanel] = useState<{ open: boolean; items: PanelItem[]; active: number }>({ open: false, items: [], active: 0 });
  const [tool, setTool] = useState("");
  const [convProject, setConvProject] = useState<number | null>(null);
  const [wsCount, setWsCount] = useState(0);
  const [projects] = useProjects(active);
  const activeItem = panel.open ? panel.items[panel.active] : undefined;
  const openItem = (it: PanelItem) => setPanel((p) => {
    const i = p.items.findIndex((x) => x.kind === it.kind && x.id === it.id);
    if (i >= 0) return { open: true, items: p.items.map((x, k) => (k === i ? { ...x, title: it.title || x.title } : x)), active: i };
    return { open: true, items: [...p.items, it].slice(-8), active: Math.min(p.items.length, 7) };
  });
  const closeItem = (i: number) => setPanel((p) => {
    const items = p.items.filter((_, k) => k !== i);
    return { open: items.length > 0 && p.open, items, active: Math.max(0, Math.min(p.active, items.length - 1)) };
  });
  const loadWorkspace = async (cid: string) => {
    try {
      const w = await api("GET", `/api/conversations/${cid}/workspace`);
      setConvProject(w.project_id); setWsCount(w.artifacts.length + w.images.length);
      return w;
    } catch { return null; }
  };
  useEffect(() => { if (openArtifact) api<Artifact>("GET", `/api/artifacts/${openArtifact.id}`).then((a) => openItem({ kind: "artifact", id: a.id, title: a.title })).catch((e) => toast(e.message, "error")); }, [openArtifact]);
  const S = state.settings as any;
  const hasModels = state.providers.some((p) => p.models.length);
  const name = state.settings.assistant_name || "Jarvis";

  const loadConvs = () => api<Conversation[]>("GET", "/api/conversations").then(setConvs).catch((e) => toast(e.message, "error"));
  useEffect(() => { loadConvs(); }, []);
  useEffect(() => { if (active) box.current?.focus(); }, [active, current]);
  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const open = async (id: string) => {
    if (busy) return;
    try {
      const c = await api<Conversation>("GET", `/api/conversations/${id}`);
      setCurrent(id);
      setMessages(c.messages || []);
      setPanel({ open: false, items: [], active: 0 });
      loadWorkspace(id);
      stick.current = true;
      setHits(null);
      setQuery("");
    } catch (e: any) { toast(e.message, "error"); }
  };

  useEffect(() => { if (jump) open(jump.id); }, [jump]);

  const fresh = () => { if (!busy) { setCurrent(null); setMessages([]); setHits(null); setQuery(""); setConvProject(null); setWsCount(0); setPanel({ open: false, items: [], active: 0 }); } };

  const rename = async (c: Conversation) => {
    const title = window.prompt("Nouveau titre de la conversation", c.title);
    if (title && title.trim()) {
      await api("PATCH", `/api/conversations/${c.id}`, { title: title.trim() }).catch((e) => toast(e.message, "error"));
      loadConvs();
    }
  };

  const remove = async (c: Conversation) => {
    if (!window.confirm(`Supprimer définitivement la conversation « ${c.title} » ?`)) return;
    await api("DELETE", `/api/conversations/${c.id}`).catch((e) => toast(e.message, "error"));
    if (current === c.id) { setCurrent(null); setMessages([]); }
    loadConvs();
    refreshState();
  };

  const search = async (q: string) => {
    setQuery(q);
    if (q.trim().length < 3) { setHits(null); return; }
    try { setHits(await api("GET", `/api/search?q=${encodeURIComponent(q)}`)); } catch { setHits([]); }
  };

  const patchLast = (fn: (m: Message) => Message) =>
    setMessages((ms) => ms.map((m, i) => (i === ms.length - 1 ? fn(m) : m)));

  const send = async (override?: string, viaVoice = false, extra: { tool?: string; tab_id?: string; selection?: Selection | null; artifact_id?: number } = {}) => {
    const text = (override ?? input).trim();
    const v = viaVoice ? voice.current : null;
    if (!text || busy) { v?.endReply(); return; }
    if (!hasModels) {
      toast("Aucun modèle n'est connecté. Ouvrez l'onglet Modèles.", "error");
      if (v) { v.beginReply(); if (S.voice_speak) v.speak("Aucun modèle n'est connecté."); v.endReply(); }
      return;
    }
    const talk = !!v && S.voice_speak;
    const sentences = new SentenceStream();
    v?.beginReply();
    setBusy(true);
    if (override === undefined) setInput("");
    stick.current = true;
    let cid = current;
    try {
      if (!cid) {
        cid = (await api<Conversation>("POST", "/api/conversations", {})).id;
        setCurrent(cid);
        if (convProject) await api("POST", `/api/conversations/${cid}/project`, { project_id: convProject }).catch(() => {});
      }
    } catch (e: any) { toast(e.message, "error"); setBusy(false); setInput(text); return; }

    setMessages((ms) => [...ms, { role: "user", content: text, meta: {} },
      { role: "assistant", content: "", meta: {}, pending: true, status: "Recherche en mémoire…" }]);
    setPhase("thinking");
    const ctl = new AbortController();
    abort.current = ctl;
    let finished = false; // la réponse est terminée ; le flux peut rester ouvert pour la mise en mémoire
    try {
      const artifactId = extra.artifact_id ?? (activeItem?.kind === "artifact" ? activeItem.id : null);
      const useTool = extra.tool ?? (tool || null);
      if (override === undefined && tool) setTool("");
      await chatStream({ conversation_id: cid!, message: text, model: model || "auto", deep, artifact_id: artifactId,
        selection: extra.selection || null, tool: useTool, tab_id: extra.tab_id || null }, (ev) => {
        if (ev.type === "analysis") {
          const { type, ...an } = ev;
          patchLast((m) => ({ ...m, status: an.strategy === "décomposée" ? "Tâche complexe : découpage en étapes…" : m.status,
            meta: { ...m.meta, analysis: an, strategy: an.strategy } }));
        } else if (ev.type === "steps_plan") {
          patchLast((m) => ({ ...m, meta: { ...m.meta, analysis: m.meta.analysis && { ...m.meta.analysis, objective: ev.objective },
            steps: ev.steps.map((x: any) => ({ ...x, state: "en attente" })) } }));
        } else if (ev.type === "step") {
          patchLast((m) => ({ ...m, status: ev.state === "en cours" ? `Étape ${ev.n} : ${ev.title}…` : m.status,
            meta: { ...m.meta, steps: (m.meta.steps || []).map((x) => (x.n === ev.n ? { ...x, state: ev.state, note: ev.note, summary: ev.summary } : x)) } }));
        } else if (ev.type === "strategy") {
          patchLast((m) => ({ ...m, meta: { ...m.meta, strategy: ev.strategy,
            analysis: m.meta.analysis && { ...m.meta.analysis, strategy: ev.strategy, strategy_label: ev.label, notes: [...(m.meta.analysis.notes || []), ev.note] } } }));
        } else if (ev.type === "controls") {
          patchLast((m) => ({ ...m, meta: { ...m.meta, controls: ev.items } }));
        } else if (ev.type === "meta") {
          patchLast((m) => ({ ...m, model: ev.model, status: "",
            meta: { ...m.meta, route: ev.route, route_label: ev.route_label, reason: ev.reason, memories: ev.memories, recalls: ev.recalls, sources: ev.sources, kb_miss: ev.kb_miss,
              modules: ev.modules, domain: ev.domain, conflicts: ev.conflicts, research_offer: ev.research_offer, strategy: ev.strategy } }));
        } else if (ev.type === "status") {
          setPhase("thinking");
          patchLast((m) => ({ ...m, status: ev.text }));
        } else if (ev.type === "plan") {
          patchLast((m) => ({ ...m, meta: { ...m.meta, plan: ev.text, deep: true } }));
        } else if (ev.type === "delta") {
          if (!v) setPhase("streaming");
          if (talk) sentences.push(ev.text).forEach((s) => v!.speak(s));
          patchLast((m) => ({ ...m, content: m.content + ev.text, status: "" }));
        } else if (ev.type === "verify") {
          patchLast((m) => ({ ...m, meta: { ...m.meta, verify: ev } }));
        } else if (ev.type === "mission") {
          setPhase("thinking");
          if (ev.kind === "artifact") openItem({ kind: "artifact", id: ev.artifact_id, title: ev.title });
          patchLast((m) => {
            const lv = m.meta.live || { steps: [] as MissionStep[] };
            let st = m.status;
            let nl: any = { ...lv };
            if (ev.kind === "plan") { nl = { ...lv, objective: ev.objective, planner: ev.planner, steps: ev.steps.map((x: any) => ({ ...x, state: "en attente" })) }; st = "Mission planifiée…"; }
            if (ev.kind === "step") {
              nl.steps = lv.steps.map((x) => (x.n === ev.n ? { ...x, state: ev.state, summary: ev.summary, error: ev.error } : x));
              if (ev.state === "en cours") st = `${ev.agent_label} : ${ev.label}…`;
            }
            if (ev.kind === "approval") { nl.approval = { n: ev.n, action: ev.action, detail: ev.detail }; nl.steps = lv.steps.map((x) => (x.n === ev.n ? { ...x, state: "autorisation" } : x)); st = "En attente de votre autorisation"; }
            if (ev.kind === "decision") nl.approval = null;
            if (ev.kind === "progress") st = ev.text;
            if (ev.kind === "status") st = ev.text;
            if (ev.kind === "done") { nl.status = ev.status; st = ""; }
            return { ...m, status: st, meta: { ...m.meta, mission_id: ev.mission_id, live: nl } };
          });
        } else if (ev.type === "done") {
          patchLast((m) => ({ ...m, pending: false, status: "", id: ev.message_id, model: ev.model,
            content: m.content || ev.content || "", meta: ev.meta ? { ...m.meta, ...ev.meta } : m.meta }));
          if (ev.meta?.artifacts?.length || ev.meta?.images?.length) loadWorkspace(cid!);
          finished = true;
          abort.current = null;
          if (talk) sentences.flush().forEach((s) => v!.speak(s));
          if (v) v.endReply(); else setPhase("idle");
          setBusy(false);
          loadConvs();
        } else if (ev.type === "memories") {
          if (ev.saved?.length) {
            toast(`${ev.saved.length} souvenir${ev.saved.length > 1 ? "s" : ""} enregistré${ev.saved.length > 1 ? "s" : ""} : ${ev.saved.map((s: any) => s.content).join(" · ")}`, "memory");
          }
          if (ev.updated?.length) toast(`Souvenir mis à jour (l'ancienne version est gardée dans son historique) : ${ev.updated.map((s: any) => s.content).join(" · ")}`, "memory");
          if (ev.proposed?.length) toast(`${ev.proposed.length} mise${ev.proposed.length > 1 ? "s" : ""} à jour de souvenir à valider dans Mémoire : ${ev.proposed.map((s: any) => s.content).join(" · ")}`, "memory");
          if (ev.saved?.length || ev.updated?.length || ev.proposed?.length) refreshState();
        } else if (ev.type === "error" && !finished) {
          patchLast((m) => ({ ...m, pending: false, status: "", error: ev.message }));
          if (talk) v!.speak("Une erreur s'est produite. Le détail est affiché à l'écran.");
        }
      }, ctl.signal);
    } catch (e: any) {
      if (finished) { /* la réponse était déjà complète */ }
      else if (e.name === "AbortError") patchLast((m) => ({ ...m, pending: false, status: "", meta: { ...m.meta, reason: "interrompu" } }));
      else patchLast((m) => ({ ...m, pending: false, status: "", error: e.message }));
    } finally {
      if (!finished) {
        patchLast((m) => ({ ...m, pending: false, status: "" }));
        abort.current = null;
        setBusy(false);
        if (v) v.endReply(); else setPhase("idle");
      }
      loadConvs();
      refreshState();
    }
  };

  // ----- mode vocal -----
  const say = (t: string) => { const v = voice.current; if (!v) return; v.beginReply(); if (S.voice_speak) v.speak(t); v.endReply(); };
  const utterance = async (text: string) => {
    const n = norm(text);
    let m: RegExpMatchArray | null;
    if (/^nouvelle (conversation|discussion)$/.test(n)) { fresh(); say("Nouvelle conversation."); return; }
    if ((m = n.match(/^(?:ouvre|affiche|montre|va dans|va sur)(?: moi)? (?:la |le |les |l )?(.+)$/))) {
      const hit = NAV.find(([k]) => m![1] === k);
      if (hit) { go(hit[1]); say(`Voici ${hit[2]}.`); return; }
    }
    if (n === "mode silencieux" || n === "mode vocal") {
      const on = n === "mode vocal";
      try { setState(await api("POST", "/api/settings", { voice_speak: on })); } catch { /* réglage inchangé */ }
      toast(on ? "Réponses lues à voix haute." : "Mode silencieux : les réponses ne sont plus lues.");
      const v = voice.current; if (v) { v.beginReply(); if (on) v.speak("Mode vocal activé."); v.endReply(); }
      return;
    }
    if (/^(ouvre|ouvrir|lance|lancer|demarre|demarrer|execute)( |$)/.test(n) && n.split(" ").length <= 7) {
      try {
        const r = await api("POST", "/api/apps/launch", { query: text });
        if (r.launched) { toast(`${r.name} lancé.`); say(`J'ouvre ${r.name}.`); return; }
        if (r.error) { toast(r.error, "error"); say(`Je n'ai pas pu lancer ${r.name}.`); return; }
      } catch { /* on laisse le modèle répondre */ }
    }
    live.current.send(text, true);
  };
  live.current = { send, utterance, S, busy };
  if (!voice.current) {
    voice.current = new Voice({
      cfg: () => { const s = live.current.S; return { wake: s.voice_wake_word, handsfree: s.voice_handsfree, bargeIn: s.voice_barge_in, voiceName: s.voice_name, rate: s.voice_rate, silenceMs: s.voice_silence_ms, followupS: s.voice_followup_s }; },
      onState: (s, info) => { setVstate(s); setVinfo(info || ""); },
      onHeard: setHeard,
      onUtterance: (t) => { setHeard(t); live.current.utterance(t); },
      onInterrupt: () => abort.current?.abort(),
      onLevels: setBars,
    });
  }
  useEffect(() => {
    setPhase(vstate === "listening" ? "listening" : vstate === "speaking" ? "speaking" : vstate === "thinking" ? "thinking" : vstate === "error" ? "error" : "idle");
  }, [vstate]);
  useEffect(() => { if (S.voice_handsfree && voiceSupport().stt && vstate === "off") voice.current!.start(false); }, [S.voice_handsfree]);
  useEffect(() => () => voice.current?.stop(), []);
  useEffect(() => { if (ask) send(ask.text, false, { tool: ask.tool, tab_id: ask.tab_id }); }, [ask]);
  useEvents((e) => {   // mission terminée hors du flux (fenêtre rechargée, mission lancée ailleurs) : on recharge le fil
    if (e.topic !== "mission" || e.kind !== "done" || !e.conv_id || e.conv_id !== current || busy) return;
    if (e.message_id && messages.some((m) => m.id === e.message_id)) return;   // déjà affichée par le flux
    api<Conversation>("GET", `/api/conversations/${e.conv_id}`).then((c) => { if (!live.current.busy) setMessages(c.messages || []); }).catch(() => {});
    loadWorkspace(e.conv_id);
  });
  const micClick = () => {
    const v = voice.current!;
    if (vstate === "off" || vstate === "error" || vstate === "standby") v.start(true);
    else if (vstate === "speaking" || vstate === "thinking") { v.cancelSpeech(); abort.current?.abort(); }
    else if (S.voice_handsfree) v.start(false);
    else v.stop();
  };
  const micTitle = vstate === "off" || vstate === "error" ? "Parler à " + name : vstate === "standby" ? "Écouter maintenant, sans le mot-clé"
    : vstate === "listening" ? (S.voice_handsfree ? "Revenir en veille" : "Couper le micro") : "Interrompre";

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); }
  };

  return (
    <div className={`chat ${panel.open && activeItem ? "with-panel" : ""}`}>
      <aside className="convs">
        <button className="btn primary wide" onClick={fresh} disabled={busy}><Icon name="plus" /> Nouvelle conversation</button>
        <div className="field-icon">
          <Icon name="search" size={15} />
          <input placeholder="Chercher dans les conversations" value={query} onChange={(e) => search(e.target.value)}
            aria-label="Chercher dans les conversations" />
        </div>
        <div className="conv-list">
          {hits !== null ? (
            hits.length === 0 ? <p className="muted pad">Aucun message ne correspond.</p> :
              hits.map((h) => (
                <button key={h.id} className="conv hit" onClick={() => open(h.conv_id)}>
                  <span className="conv-title">{h.title}</span>
                  <span className="conv-sub">{h.content.slice(0, 110)}</span>
                </button>
              ))
          ) : convs.length === 0 ? <p className="muted pad">Vos conversations apparaîtront ici.</p> : (
            convs.map((c) => (
              <div key={c.id} className={`conv ${c.id === current ? "on" : ""}`}>
                <button className="conv-main" onClick={() => open(c.id)}>
                  <span className="conv-title">{c.title}</span>
                  <span className="conv-sub">{fmtDate(c.updated)}</span>
                </button>
                <span className="conv-actions">
                  <button className="icon-btn" title="Renommer" aria-label="Renommer" onClick={() => rename(c)}><Icon name="edit" size={14} /></button>
                  <button className="icon-btn" title="Supprimer" aria-label="Supprimer" onClick={() => remove(c)}><Icon name="trash" size={14} /></button>
                </span>
              </div>
            ))
          )}
        </div>
      </aside>

      <section className="thread">
        {current && (
          <div className="thread-head">
            <span className="conv-name">{convs.find((c) => c.id === current)?.title || "Conversation"}</span>
            <ProjectSelect projects={projects} value={convProject} empty="Sans projet" onChange={async (pid) => {
              try { await api("POST", `/api/conversations/${current}/project`, { project_id: pid }); setConvProject(pid); toast(pid ? "Conversation rattachée au projet." : "Conversation détachée du projet."); }
              catch (e: any) { toast(e.message, "error"); }
            }} />
            <span className="grow" />
            <button className={`btn ${panel.open ? "on" : ""}`} onClick={async () => {
              if (panel.open) { setPanel({ ...panel, open: false }); return; }
              if (panel.items.length) { setPanel({ ...panel, open: true }); return; }
              const w = await loadWorkspace(current);
              const items: PanelItem[] = [...(w?.artifacts || []).map((a: any) => ({ kind: "artifact", id: a.id, title: a.title })),
                ...(w?.images || []).slice(0, 4).map((r: any) => ({ kind: "image", id: r.id, title: r.title }))];
              if (!items.length) { toast("Aucun artefact ni image dans cette conversation pour l'instant."); return; }
              setPanel({ open: true, items, active: 0 });
            }} title="Artefacts et images de cette conversation"><Icon name="panel" size={15} /> Espace de travail{wsCount ? ` (${wsCount})` : ""}</button>
          </div>
        )}
        <div className="messages" ref={scroller}
          onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
          {messages.length === 0 ? (
            <div className="welcome">
              <Orb phase={phase} size={150} />
              <h1>{state.settings.user_name ? `Bonjour ${state.settings.user_name}.` : "Bonjour."}</h1>
              <p>{name} est prêt. {state.stats.memories > 0
                ? `${state.stats.memories} souvenir${state.stats.memories > 1 ? "s" : ""} en mémoire.`
                : "Sa mémoire est encore vide : elle se remplira au fil de vos échanges."}</p>
              {!hasModels && (
                <div className="notice warn">
                  <Icon name="warn" /> <span>Aucun modèle n'est connecté pour l'instant.</span>
                  <button className="btn" onClick={goModels}>Connecter un modèle</button>
                </div>
              )}
            </div>
          ) : messages.map((m, i) => <Bubble key={i} m={m} name={name} goDoc={goDoc} goResearch={goResearch} toast={toast} openUrl={openUrl}
            openItem={openItem} editImage={(r) => { setInput(`Modifie cette image (jarvis-image:${r.id}) : `); box.current?.focus(); }}
            launch={async (query) => {
              try {
                const r = await api("POST", "/api/apps/launch", { query });
                if (r.launched) { toast(`${r.name} lancé.`); setMessages((ms) => ms.map((x, k) => (k === i ? { ...x, meta: { ...x.meta, launched: r.name } } : x))); }
                else toast(r.error || "Aucune application autorisée ne correspond.", "error");
              } catch (e: any) { toast(e.message, "error"); }
            }}
            research={async (mode) => {
              const question = [...messages.slice(0, i)].reverse().find((x) => x.role === "user")?.content || "";
              try {
                const r = await api("POST", "/api/research", { question, mode, origin: "conversation" });
                setMessages((ms) => ms.map((x, k) => (k === i ? { ...x, meta: { ...x.meta, research_id: r.id } } : x)));
                toast("Recherche lancée. Les résultats attendront votre validation dans Connaissances › Chercheur.");
              } catch (e: any) { toast(e.message, "error"); }
            }} />)}
        </div>

        {vstate !== "off" && (
          <div className={`voicebar v-${vstate}`} role="status" aria-live="polite">
            <Orb phase={vstate === "listening" ? "listening" : vstate === "speaking" ? "speaking" : vstate === "thinking" ? "thinking" : vstate === "error" ? "error" : "idle"} size={46} />
            <div className="voice-main">
              <b>{VLABEL[vstate]}{vstate === "standby" && <> « {S.voice_wake_word} »</>}</b>
              <span className="voice-heard">{vstate === "error" ? vinfo : heard || (vstate === "listening" ? "Parlez, je m'arrête quand vous avez fini votre phrase." : "")}</span>
            </div>
            <div className="voice-bars" aria-hidden="true">{(bars.length ? bars : Array(24).fill(0.04)).map((b, i) => <i key={i} style={{ height: `${Math.max(6, b * 100)}%` }} />)}</div>
            <button className="btn" onClick={() => voice.current!.stop()}>Arrêter le mode vocal</button>
          </div>
        )}
        <div className="composer">
          {activeItem?.kind === "artifact" && <div className="composer-ctx"><Icon name="artifact" size={13} /> Artefact ouvert : <b>{activeItem.title}</b> — vos demandes de modification s'y appliquent.</div>}
          <textarea ref={box} rows={1} value={input} placeholder={`Écrire à ${name}…  (Entrée pour envoyer, Maj+Entrée pour une nouvelle ligne)`}
            onChange={(e) => { setInput(e.target.value); e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 220) + "px"; }}
            onKeyDown={onKey} aria-label="Message" />
          <div className="composer-bar">
            <ModelSelect providers={state.providers} value={model} onChange={setModel}
              emptyLabel={state.settings.auto_route ? "Auto — choix selon la tâche" : "Modèle par défaut"} className="select compact" />
            <div className="tools-pick" role="radiogroup" aria-label="Outil">
              {TOOLS.map(([k, l, t]) => <button key={k} role="radio" aria-checked={tool === k} className={`chip ${tool === k ? "on" : ""}`} title={t} onClick={() => setTool(k)}>
                {k && <Icon name={{ image: "image", artifact: "artifact", web: "globe" }[k]!} size={13} />}{l}</button>)}
            </div>
            <button className={`chip-toggle ${deep ? "on" : ""}`} onClick={() => setDeep(!deep)} aria-pressed={deep}
              title="Décompose le problème en étapes, puis fait vérifier la réponse et la corrige si besoin (plus lent)">
              <Icon name="bolt" size={14} /> Réflexion approfondie
            </button>
            <span className="grow" />
            <button className={`mic ${vstate !== "off" ? "on" : ""} m-${vstate}`} onClick={micClick} title={micTitle} aria-label={micTitle} aria-pressed={vstate !== "off"}
              disabled={!voiceSupport().stt}><Icon name="mic" size={18} /><span className="mic-ring" /></button>
            {busy ? (
              <button className="btn danger" onClick={() => abort.current?.abort()}><Icon name="stop" /> Interrompre</button>
            ) : (
              <button className="btn primary" onClick={() => send()} disabled={!input.trim()}><Icon name="send" /> Envoyer</button>
            )}
          </div>
        </div>
      </section>
      {panel.open && activeItem && (
        <aside className="wpanel" aria-label="Espace de travail">
          <div className="wtabs">
            {panel.items.map((it, i) => (
              <div key={`${it.kind}${it.id}`} className={`wtab ${i === panel.active ? "on" : ""}`}>
                <button onClick={() => setPanel({ ...panel, active: i })} title={it.title}><Icon name={it.kind === "artifact" ? "artifact" : "image"} size={13} /><span>{it.title || `n° ${it.id}`}</span></button>
                <button className="icon-btn" aria-label="Fermer" onClick={() => closeItem(i)}><Icon name="x" size={12} /></button>
              </div>))}
            <span className="grow" />
            <button className="icon-btn" title="Masquer le panneau" aria-label="Masquer le panneau" onClick={() => setPanel({ ...panel, open: false })}><Icon name="panel" size={16} /></button>
          </div>
          {activeItem.kind === "artifact"
            ? <ArtifactViewer key={activeItem.id} id={activeItem.id} toast={toast} compact
                onAsk={(instruction, selection) => send(instruction, false, { artifact_id: activeItem.id, selection })} />
            : <PanelImage id={activeItem.id} toast={toast} onEdit={(r) => { setInput(`Modifie cette image (jarvis-image:${r.id}) : `); box.current?.focus(); }} />}
        </aside>
      )}
    </div>
  );
}

function PanelImage({ id, toast, onEdit }: { id: number; toast: (t: string, k?: string) => void; onEdit: (r: Resource) => void }) {
  const [list, setList] = useResources([id]);
  const r = list[0];
  if (!r) return <p className="muted pad">Image introuvable.</p>;
  return (
    <div className="pimage">
      <img src={r.src} alt={r.title} />
      {r.meta?.prompt && <p className="muted small">« {r.meta.prompt} » — {r.meta.model}</p>}
      <ImageTile r={r} toast={toast} small onChange={(x) => x && setList([x])} onEdit={onEdit} />
    </div>
  );
}

function MissionBlock({ m, toast }: { m: Message; toast: (t: string, k?: string) => void }) {
  const lv = m.meta.live;
  const steps = (lv?.steps?.length ? lv.steps : m.meta.mission?.steps) || [];
  const [status, setStatus] = useState<string | undefined>(undefined);
  useEffect(() => {
    if (!m.pending && m.meta.mission_id) api("GET", `/api/missions/${m.meta.mission_id}`).then((x) => setStatus(x.status)).catch(() => {});
  }, [m.pending, m.meta.mission_id]);
  if (!steps.length) return null;
  return (
    <div className="mission-block">
      <h4>Mission{(lv?.objective || m.meta.mission?.objective) ? "" : ""} — {steps.filter((x) => x.state === "terminée").length}/{steps.length} étape{steps.length > 1 ? "s" : ""}
        {(lv?.planner || m.meta.mission?.planner) && <small className="muted"> · {lv?.planner || m.meta.mission?.planner}</small>}</h4>
      <MissionSteps steps={steps} approval={lv?.approval || null} missionId={m.meta.mission_id} status={m.pending ? "en cours" : status} toast={toast} compact={m.pending} />
    </div>
  );
}

function Creations({ m, toast, openItem, editImage }: { m: Message; toast: (t: string, k?: string) => void; openItem: (it: PanelItem) => void; editImage: (r: Resource) => void }) {
  const [imgs, setImgs] = useResources(m.meta.images);
  const [arts, setArts] = useState<Artifact[]>([]);
  const key = (m.meta.artifacts || []).join(",");
  useEffect(() => {
    if (!key) { setArts([]); return; }
    Promise.all(key.split(",").map((id) => api<Artifact>("GET", `/api/artifacts/${id}`).catch(() => null))).then((xs) => setArts(xs.filter(Boolean) as Artifact[]));
  }, [key]);
  if (!imgs.length && !arts.length) return null;
  return (
    <div className="creations">
      {arts.map((a) => (
        <button key={a.id} className="acard" onClick={() => openItem({ kind: "artifact", id: a.id, title: a.title })}>
          <Icon name="artifact" size={20} /><span><b>{a.title}</b><i>{a.kind_label} · version {a.version}</i></span><em>Ouvrir</em>
        </button>))}
      {imgs.length > 0 && <div className="igrid chat">{imgs.map((r) => <ImageTile key={r.id} r={r} toast={toast} small
        onChange={(x) => x && setImgs(imgs.map((y) => (y.id === x.id ? x : y)))} onEdit={editImage}
        onOpen={(x) => openItem({ kind: "image", id: x.id, title: x.title })} />)}</div>}
    </div>
  );
}

const STRAT_TAG: Record<string, string> = { directe: "Réponse directe", contextuelle: "Avec contexte", approfondie: "Approfondie", "décomposée": "En étapes" };
const STEP_ICON: Record<string, string> = { "en attente": "○", "en cours": "◔", "terminée": "●", "échec": "✕", redondante: "≈", "non exécutée": "–" };

function Bubble({ m, name, goDoc, research, goResearch, launch, toast, openUrl, openItem, editImage }: { m: Message; name: string; goDoc: (id: number) => void; research: (mode: string) => void; goResearch: () => void; launch: (q: string) => void;
  toast: (t: string, k?: string) => void; openUrl: (u: string) => void; openItem: (it: PanelItem) => void; editImage: (r: Resource) => void }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  if (m.role === "user") return <div className="msg user"><div className="bubble">{m.content}</div></div>;
  const mems = m.meta.memories?.length || 0;
  const recalls = m.meta.recalls?.length || 0;
  const v = m.meta.verify;
  const srcs = m.meta.sources || [];
  const citedSet = new Set(Array.from(m.content.matchAll(/\[S(\d+)\]/g), (x) => +x[1]));
  const cited = srcs.filter((s) => citedSet.has(s.n));
  const uncited = srcs.filter((s) => !citedSet.has(s.n));
  const mods = m.meta.modules || [];
  const conflicts = m.meta.conflicts || [];
  const an = m.meta.analysis;
  const steps = m.meta.steps || [];
  const ctrls = m.meta.controls || [];
  const strat = an?.strategy || m.meta.strategy;
  const appTool = an?.tools?.find((t) => t.tool === "application");
  const details = (mems > 0 && typeof m.meta.memories![0] === "object") || recalls > 0 || m.meta.plan || (v && v.problemes?.length) || mods.length > 0 || !!an || steps.length > 0;
  return (
    <div className="msg assistant">
      <div className="msg-head">
        <span className="who">{name}</span>
        {m.model && <span className="tag" title={m.meta.reason}>{shortModel(m.model)}</span>}
        {m.meta.route_label && <span className="tag ghost">{m.meta.route_label}</span>}
        {strat && <span className="tag ghost" title={an ? `Complexité ${an.complexity} — ${an.reasons.join(", ")}` : ""}>{STRAT_TAG[strat] || strat}{steps.length ? ` · ${steps.filter((x) => x.state === "terminée").length}/${steps.length}` : ""}</span>}
        {ctrls.length > 0 && <span className={`tag ${ctrls.some((c) => c.level === "erreur") ? "warn" : "ghost"}`} title={ctrls.map((c) => c.text).join("\n")}>Contrôle : {ctrls.length} point{ctrls.length > 1 ? "s" : ""} d'attention</span>}
        {mems > 0 && <span className="tag ghost">{mems} souvenir{mems > 1 ? "s" : ""} utilisé{mems > 1 ? "s" : ""}</span>}
        {srcs.length > 0 && <span className="tag ok">{cited.length ? `${cited.length} source${cited.length > 1 ? "s" : ""} citée${cited.length > 1 ? "s" : ""}` : `${srcs.length} passage${srcs.length > 1 ? "s" : ""} consulté${srcs.length > 1 ? "s" : ""}`}</span>}
        {m.meta.kb_miss && <span className="tag ghost" title="La base de connaissances ne contenait aucun passage sur cette question">Sans source documentaire</span>}
        {v && (v.checked
          ? <span className={`tag ${v.ok ? "ok" : "warn"}`}>{v.ok ? "Vérifié" : v.corrected ? "Corrigé après vérification" : "Problèmes relevés"}</span>
          : <span className="tag ghost" title={v.note}>Non vérifié</span>)}
        {conflicts.length > 0 && <span className="tag warn" title="Deux sources donnent des valeurs différentes pour une même grandeur">Sources en désaccord</span>}
        {m.meta.reason === "interrompu" && <span className="tag warn">Interrompu</span>}
        {details && <button className="link" onClick={() => setOpen(!open)}>{open ? "Masquer le détail" : "Détail"}</button>}
      </div>
      {open && (
        <div className="trace">
          {an && (
            <div><h4>Analyse de la demande</h4>
              <ul className="mods">
                <li><b>Objectif</b><span>{an.objective}</span></li>
                <li><b>Complexité</b><span>{an.complexity}{an.reasons?.length ? ` — ${an.reasons.join(", ")}` : ""}</span></li>
                <li><b>Stratégie</b><span>{an.strategy_label}</span></li>
                <li><b>Vérification</b><span>{an.verify ? "oui" : "non"} — {an.verify_reason}</span></li>
                {an.resources?.length > 0 && <li><b>Ressources</b><span>{an.resources.join(" · ")}</span></li>}
                {an.tools?.length > 0 && <li><b>Outils envisagés</b><span>{an.tools.map((t) => t.label + (t.needs_confirmation ? " (avec votre accord)" : "")).join(" · ")}</span></li>}
                {an.repeated && <li><b>Question répétée</b><span>déjà posée dans cette conversation : la réponse doit compléter, pas répéter</span></li>}
                {(an.notes || []).map((n, i) => <li key={i}><b>Changement</b><span>{n}</span></li>)}
              </ul></div>
          )}
          {steps.length > 0 && (
            <div><h4>Étapes exécutées</h4>
              <ol className="steps">{steps.map((x) => <li key={x.n} className={`st-${x.state.replace(/\s/g, "-")}`}>
                <span className="st-ico">{STEP_ICON[x.state] || "○"}</span><b>{x.title}</b> <i>{x.state}{x.note ? ` — ${x.note}` : ""}</i>
                {x.summary && <p>{x.summary}{x.summary.length >= 280 ? "…" : ""}</p>}</li>)}</ol></div>
          )}
          {ctrls.length > 0 && <div><h4>Contrôles automatiques</h4><ul>{ctrls.map((c, i) => <li key={i}>{c.level === "erreur" ? "Erreur : " : "Attention : "}{c.text}</li>)}</ul></div>}
          {m.meta.plan && <div><h4>Plan de résolution</h4><pre>{m.meta.plan}</pre></div>}
          {mods.length > 0 && (
            <div><h4>Modules consultés{m.meta.domain ? ` — domaine identifié : ${m.meta.domain}` : ""}</h4>
              <ul className="mods">{mods.map((x) => <li key={x.key} className={x.used ? "" : "off"}><b>{x.label}</b>
                <span>{x.used ? `${x.hits} élément${x.hits > 1 ? "s" : ""} retenu${x.hits > 1 ? "s" : ""}` : "non consulté"}{x.note ? ` — ${x.note}` : ""}</span></li>)}</ul></div>
          )}
          {conflicts.length > 0 && (
            <div><h4>Données en désaccord entre les sources</h4>
              <ul>{conflicts.map((c, i) => <li key={i}>[S{c.a.n}] {c.a.text} · [S{c.b.n}] {c.b.text}</li>)}</ul></div>
          )}
          {mems > 0 && typeof m.meta.memories![0] === "object" && (
            <div><h4>Souvenirs transmis au modèle</h4>
              <ul>{m.meta.memories!.map((x: any) => <li key={x.id}><span className="cat">{x.category}</span>{x.project && <span className="cat">{x.project}</span>} {x.content.slice(0, 220)}{x.found_by === "reformulation" && <i className="muted"> — retrouvé par recherche reformulée</i>}</li>)}</ul></div>
          )}
          {recalls > 0 && <div><h4>Anciennes conversations consultées</h4><ul>{m.meta.recalls!.map((r, i) => <li key={i}>{r.title}</li>)}</ul></div>}
          {v && v.problemes?.length > 0 && <div><h4>Problèmes relevés par le contrôle</h4><ul>{v.problemes.map((p, i) => <li key={i}>{p}</li>)}</ul></div>}
        </div>
      )}
      {(m.meta.live || m.meta.mission) && <MissionBlock m={m} toast={toast} />}
      {m.content && <Markdown text={m.content} />}
      {!m.pending && <Creations m={m} toast={toast} openItem={openItem} editImage={editImage} />}
      {!m.pending && (m.meta.web_sources || []).length > 0 && <WebSources sources={m.meta.web_sources!} toast={toast} openUrl={openUrl}
        cited={new Set(Array.from(m.content.matchAll(/\[W(\d+)\]/g), (x) => +x[1]))} />}
      {!m.pending && srcs.length > 0 && (
        <div className="sources">
          <h4>{cited.length ? "Sources citées" : "Passages consultés (non cités dans la réponse)"}</h4>
          {(cited.length ? cited : uncited).map((s) => (
            <button key={s.n} className="source" onClick={() => goDoc(s.doc_id)} title={s.excerpt}>
              <b>S{s.n}</b><span>{s.title}{s.locator ? ` — ${s.locator}` : ""}</span><i>{s.source.replace(/^https?:\/\//, "").slice(0, 50)} · fiabilité {s.reliability}/5</i>
            </button>
          ))}
          {cited.length > 0 && uncited.length > 0 && <p className="muted small">{uncited.length} autre{uncited.length > 1 ? "s" : ""} passage{uncited.length > 1 ? "s" : ""} consulté{uncited.length > 1 ? "s" : ""}, non cité{uncited.length > 1 ? "s" : ""}.</p>}
        </div>
      )}
      {!m.pending && appTool && appTool.query && (
        <div className="notice info"><Icon name="bolt" />
          {m.meta.launched ? <span>{m.meta.launched} a été lancé à votre demande.</span> : (<>
            <span>{name} peut lancer {appTool.name}, une application de votre liste autorisée. Rien n'est lancé sans votre accord.</span>
            <button className="btn" onClick={() => launch(appTool.query!)}>Lancer {appTool.name}</button></>)}
        </div>
      )}
      {!m.pending && m.meta.research_offer && (
        <div className="notice info research-offer"><Icon name="search" />
          {m.meta.research_id ? <span>Recherche n° {m.meta.research_id} lancée. Rien ne sera ajouté à la base sans votre validation. <button className="link" onClick={goResearch}>Suivre la recherche</button></span> : (<>
            <span>{m.meta.research_offer === "nouveautés"
              ? "La base peut être en retard sur les nouveautés de ce sujet. Le chercheur peut interroger des sources récentes."
              : "La base de connaissances ne contenait rien de fiable sur cette question. Le chercheur peut interroger des sources et vous proposer des connaissances vérifiées."}</span>
            <button className="btn" onClick={() => research("rapide")}>Lancer une recherche</button>
            <button className="btn" onClick={() => research("approfondi")} title="Davantage de requêtes et de sources ; plus long">Recherche approfondie</button>
          </>)}
        </div>
      )}
      {m.status && <div className="status"><span className="dots"><i /><i /><i /></span>{m.status}</div>}
      {m.pending && !m.content && !m.status && <div className="status"><span className="dots"><i /><i /><i /></span></div>}
      {m.error && <div className="notice error"><Icon name="warn" /> <span>{m.error}</span></div>}
      {!m.pending && m.content && (
        <button className="link copy" onClick={() => {
          navigator.clipboard.writeText(m.content).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
        }}><Icon name={copied ? "check" : "copy"} size={13} /> {copied ? "Copié" : "Copier"}</button>
      )}
    </div>
  );
}
