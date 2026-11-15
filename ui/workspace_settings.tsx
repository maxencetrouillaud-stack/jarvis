import React, { useEffect, useState } from "react";
import { api, AppState } from "./api";
import { Icon, Toggle } from "./common";
import { useEvents } from "./studio";

type Patch = (p: any, msg?: string) => void;

/** Paramètres de l'espace de travail : outils automatiques, missions, navigateur, images. */
export function WorkspaceSettings({ state, patch, toast, active }: { state: AppState; patch: Patch; toast: (t: string, k?: string) => void; active: boolean }) {
  const s = state.settings as any;
  const [studio, setStudio] = useState<any>(null);
  const [browser, setBrowser] = useState<any>(null);
  const [models, setModels] = useState<string[]>([]);
  const load = async () => {
    try { setStudio(await api("GET", "/api/studio")); setBrowser(await api("GET", "/api/browser")); } catch { /* moteur en cours de démarrage */ }
  };
  useEffect(() => { if (active) load(); }, [active]);
  useEvents((e) => { if (e.topic === "browser" && e.action === "installation") load(); });
  const install = async () => {
    if (!window.confirm("Installer le moteur Chromium ?\n\nJarvis va exécuter « pip install playwright » (environ 40 Mo depuis pypi.org). Sous Windows, il utilisera ensuite Microsoft Edge déjà présent sur votre PC ; sinon, il téléchargera Chromium (environ 150 Mo).")) return;
    try { await api("POST", "/api/browser/install", {}); toast("Installation lancée : suivez la progression ci-dessous."); load(); } catch (e: any) { toast(e.message, "error"); }
  };
  const listModels = async () => {
    try { const r = await api("POST", "/api/images/models", {}); setModels(r.models); toast(r.models.length ? `${r.models.length} modèle(s) d'images disponible(s) avec votre clé.` : "Aucun modèle d'images listé (clé absente ou refusée)."); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const ch = browser?.engines?.chromium;
  const ins = browser?.install;
  const img = studio?.images;
  const imgModels = Array.from(new Set([...(models.length ? models : img?.providers?.[0]?.models || []), s.image_model].filter(Boolean)));
  return (
    <div className="card">
      <h3>Espace de travail : outils, missions, navigateur, images</h3>
      <p className="muted small">Jarvis choisit seul les outils (navigateur, génération d'images, artefacts) quand votre demande l'exige, et confie chaque étape à un agent spécialisé. Le suivi est visible sous la réponse et dans Projets.</p>
      <Toggle checked={s.mission_auto_tools} label="Choix automatique des outils dans la conversation"
        hint="Désactivé : les outils ne servent que si vous les choisissez sous la zone de saisie (Image, Artefact, Web)." onChange={(v) => patch({ mission_auto_tools: v })} />
      <Toggle checked={s.mission_web_auto} label="Recherche web automatique"
        hint="Une demande qui exige des informations récentes ou un site précis lance une recherche web vérifiée (citations contrôlées dans les pages)." onChange={(v) => patch({ mission_web_auto: v })} />
      <Toggle checked={s.mission_confirm_plan} label="Valider chaque plan de mission avant exécution"
        hint="Jarvis présente le plan (outils et agents) et attend votre accord avant d'agir. Les actions sensibles (téléchargement…) demandent toujours votre accord." onChange={(v) => patch({ mission_confirm_plan: v })} />
      <Toggle checked={s.mission_runtime_check} label="Exécuter les artefacts dans un navigateur isolé pour les contrôler"
        hint="Le vérificateur charge l'artefact dans Chromium sans fenêtre et relève les erreurs JavaScript (nécessite le moteur Chromium)." onChange={(v) => patch({ mission_runtime_check: v })} />
      <div className="form three">
        <label>Étapes au plus par mission
          <select className="select" value={s.mission_max_steps} onChange={(e) => patch({ mission_max_steps: +e.target.value })}>
            {[4, 6, 8, 10].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        <label>Durée maximale d'une mission
          <select className="select" value={s.mission_max_seconds} onChange={(e) => patch({ mission_max_seconds: +e.target.value })}>
            {[300, 600, 900, 1800, 3600].map((n) => <option key={n} value={n}>{n / 60} min</option>)}</select></label>
        <label>Pages lues par recherche web
          <select className="select" value={s.mission_pages} onChange={(e) => patch({ mission_pages: +e.target.value })}>
            {[2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        <label>Corrections automatiques d'un artefact
          <select className="select" value={s.mission_fix_attempts} onChange={(e) => patch({ mission_fix_attempts: +e.target.value })}>
            {[0, 1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
      </div>

      <h4 className="sub">Navigateur</h4>
      <div className="form three">
        <label>Moteur des nouveaux onglets
          <select className="select" value={s.browser_engine} onChange={(e) => patch({ browser_engine: e.target.value })}>
            <option value="auto">Automatique (Chromium s'il est installé)</option><option value="lecture">Mode lecture</option><option value="chromium">Chromium</option></select></label>
        <label>Moteur de l'agent web
          <select className="select" value={s.browser_agent_engine} onChange={(e) => patch({ browser_agent_engine: e.target.value })}>
            <option value="lecture">Mode lecture (rapide, sans scripts)</option><option value="chromium">Chromium</option></select></label>
        <label>Navigateur piloté
          <select className="select" value={s.browser_chromium_channel} onChange={(e) => patch({ browser_chromium_channel: e.target.value })}>
            <option value="auto">Automatique (Edge sous Windows)</option><option value="msedge">Microsoft Edge</option><option value="chrome">Google Chrome</option><option value="chromium">Chromium de Playwright</option></select></label>
        <label>Recherche depuis la barre d'adresse (Chromium)
          <select className="select" value={s.browser_search_engine} onChange={(e) => patch({ browser_search_engine: e.target.value })}>
            {Object.entries(studio?.search_pages || { duckduckgo: "DuckDuckGo" }).map(([k, l]: any) => <option key={k} value={k}>{l}</option>)}</select></label>
      </div>
      <div className={`notice ${ch?.available ? "info" : "warn"} slim`}><Icon name="globe" size={15} />
        <span>{ch?.available ? `Moteur Chromium prêt${ch.channel ? ` (${ch.channel})` : ""}.` : `Moteur Chromium indisponible : ${ch?.reason || "…"}`}
          {" "}Le mode lecture fonctionne toujours, sans installation.</span>
        {!ch?.installed && <button className="btn" onClick={install} disabled={ins?.state === "en cours"}><Icon name="download" size={14} /> Installer le moteur Chromium</button>}
      </div>
      {ins && ins.state !== "idle" && <pre className="install-log">{`Installation : ${ins.state}\n` + ins.log.join("\n")}</pre>}
      <p className="muted small">L'agent web respecte robots.txt, ne se connecte à aucun compte, ne remplit aucun formulaire et ne télécharge rien sans votre accord. Les fichiers exécutables (.exe, .msi, .bat, .ps1…) ne sont jamais téléchargés.</p>

      <h4 className="sub">Génération d'images</h4>
      <div className={`notice ${img?.available ? "info" : "warn"} slim`}><Icon name="image" size={15} />
        <span>{img?.available ? "Google Gemini prêt." : `Indisponible : ${img?.reason || "…"}.`} {img?.providers?.[0]?.note}</span></div>
      <div className="form three">
        <label>Modèle d'images
          <select className="select" value={s.image_model} onChange={(e) => patch({ image_model: e.target.value })}>
            <option value="">Par défaut ({img?.providers?.[0]?.models?.[0] || "…"})</option>
            {imgModels.map((m) => <option key={m} value={m}>{m}</option>)}</select></label>
        <label>Taille par défaut
          <select className="select" value={s.image_size} onChange={(e) => patch({ image_size: e.target.value })}>
            <option value="">Définie par le modèle</option>{["1K", "2K", "4K"].map((x) => <option key={x}>{x}</option>)}</select></label>
        <label>Images non enregistrées conservées
          <select className="select" value={s.images_keep_days} onChange={(e) => patch({ images_keep_days: +e.target.value })}>
            {[1, 3, 7, 14, 30].map((n) => <option key={n} value={n}>{n} jour{n > 1 ? "s" : ""}</option>)}</select></label>
      </div>
      <div className="row"><button className="btn" onClick={listModels}><Icon name="refresh" size={14} /> Lister les modèles d'images de ma clé</button></div>
    </div>
  );
}

/** Carte du tableau de bord : chiffres réels de l'espace de travail. */
export function WorkspaceCard({ state }: { state: AppState }) {
  const w = state.workspace;
  if (!w) return null;
  return (
    <div className="card"><h3>Espace de travail</h3>
      <div className="tiles">
        <div className="tile"><span>{w.projects}</span>Projets</div>
        <div className="tile"><span>{w.artifacts}</span>Artefacts</div>
        <div className="tile"><span>{w.images}</span>Images enregistrées</div>
        <div className="tile"><span>{w.sources}</span>Sources web</div>
        <div className="tile"><span>{w.missions}</span>Missions{w.missions_running ? ` (${w.missions_running} en cours)` : ""}</div>
      </div>
    </div>
  );
}
