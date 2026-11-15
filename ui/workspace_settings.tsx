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
  const [cf, setCf] = useState({ account: "", token: "" });
  const load = async () => {
    try { setStudio(await api("GET", "/api/studio")); setBrowser(await api("GET", "/api/browser")); } catch { /* moteur en cours de démarrage */ }
  };
  useEffect(() => { if (active) load(); }, [active]);
  useEvents((e) => { if ((e.topic === "browser" || e.topic === "images") && e.action === "installation") load(); });
  useEffect(() => { if (studio?.images?.cf_account) setCf((c) => ({ ...c, account: c.account || studio.images.cf_account })); }, [studio?.images?.cf_account]);
  const saveCf = async () => {
    try { await api("POST", "/api/images/cloudflare", cf); setCf({ ...cf, token: "" }); toast("Cloudflare Workers AI enregistré : il devient le générateur d'images."); load(); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const installPillow = async () => {
    if (!window.confirm("Installer Pillow ?\n\nJarvis va exécuter « pip install pillow » (environ 3 Mo depuis pypi.org). Ce module sert à réduire les images avant de les envoyer à Cloudflare pour une modification.")) return;
    try { await api("POST", "/api/images/install-pillow", {}); toast("Installation de Pillow lancée."); setTimeout(load, 1500); } catch (e: any) { toast(e.message, "error"); }
  };
  const install = async () => {
    if (!window.confirm("Installer le moteur Chromium ?\n\nJarvis va exécuter « pip install playwright » (environ 40 Mo depuis pypi.org). Sous Windows, il utilisera ensuite Microsoft Edge déjà présent sur votre PC ; sinon, il téléchargera Chromium (environ 150 Mo).")) return;
    try { await api("POST", "/api/browser/install", {}); toast("Installation lancée : suivez la progression ci-dessous."); load(); } catch (e: any) { toast(e.message, "error"); }
  };
  const listModels = async () => {
    try {
      const r = await api("POST", "/api/images/models", {}); setModels(r.models);
      toast(r.models.length ? `${r.models.length} modèle(s) d'images disponible(s).` : (r.error || "Aucun modèle d'images listé (clé absente ou refusée)."), r.models.length ? undefined : "error");
    }
    catch (e: any) { toast(e.message, "error"); }
  };
  const ch = browser?.engines?.chromium;
  const ins = browser?.install;
  const img = studio?.images;
  const imgModels = Array.from(new Set([...(models.length ? models : img?.providers?.find((p: any) => p.id === "gemini")?.models || []), s.image_model].filter(Boolean)));
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
      <div className="form three">
        <label>Fournisseur
          <select className="select" value={s.image_provider} onChange={(e) => { patch({ image_provider: e.target.value }); setModels([]); setTimeout(load, 300); }}>
            <option value="comfyui">ComfyUI sur ce PC — carte graphique (gratuit, illimité)</option>
            <option value="cloudflare">Cloudflare Workers AI — FLUX (gratuit, limite quotidienne)</option>
            <option value="gemini">Google Gemini — Nano Banana (payant)</option></select></label>
      </div>
      <div className={`notice ${img?.available ? "info" : "warn"} slim`}><Icon name="image" size={15} />
        <span>{img?.available ? `${img?.providers?.find((p: any) => p.id === s.image_provider)?.label} prêt.` : `Indisponible : ${img?.reason || "…"}.`} {img?.providers?.find((p: any) => p.id === s.image_provider)?.note}</span></div>
      {s.image_provider === "comfyui" && <>
        <ol className="steps small">
          <li>Installez <b>ComfyUI Desktop</b> pour Windows depuis <b>comfy.org</b> (gratuit), choisissez « NVIDIA » à l'installation.</li>
          <li>Dans ComfyUI, téléchargez un modèle (checkpoint). Avec une RTX 4050 (6 Go), prenez un modèle <b>SDXL « Lightning » ou « Turbo »</b> (une image en quelques dizaines de secondes) ou <b>SD 1.5</b> (plus rapide, moins détaillé).</li>
          <li>Laissez ComfyUI ouvert pendant que Jarvis génère. L'application écoute sur <code>http://127.0.0.1:8000</code> (la version portable sur <code>:8188</code>).</li>
        </ol>
        <div className="form three">
          <label>Adresse de ComfyUI
            <input className="input" defaultValue={s.image_comfy_url} spellCheck={false} onBlur={(e) => e.target.value.trim() !== s.image_comfy_url && patch({ image_comfy_url: e.target.value.trim() })} /></label>
          <label>Modèle (checkpoint)
            <select className="select" value={s.image_comfy_model} onChange={(e) => patch({ image_comfy_model: e.target.value })}>
              <option value="">Le premier disponible dans ComfyUI</option>
              {Array.from(new Set([...models, s.image_comfy_model].filter(Boolean))).map((m) => <option key={m} value={m}>{m}</option>)}</select></label>
          <div className="field-end"><button className="btn" onClick={listModels}><Icon name="refresh" size={14} /> Tester et lister les modèles</button></div>
        </div>
        <p className="muted small">Les descriptions sont traduites en anglais par votre modèle de texte (les modèles Stable Diffusion comprennent mal le français). « Modifier une image » redessine l'image en suivant la consigne : moins précis qu'un modèle d'édition dédié.</p>
      </>}
      {s.image_provider === "cloudflare" && <>
        <ol className="steps small">
          <li>Créez un compte gratuit sur <b>dash.cloudflare.com</b> (aucune carte bancaire n'est demandée pour l'offre gratuite).</li>
          <li>Copiez votre <b>identifiant de compte</b> (Account ID : 32 caractères, affiché sur la page d'accueil du compte ou de Workers AI).</li>
          <li>Créez un <b>jeton d'API</b> : Mon profil › Jetons d'API › Créer un jeton › modèle « Workers AI ». Collez-le ici, pas dans une conversation.</li>
        </ol>
        <div className="form three">
          <label>Identifiant de compte<input className="input" value={cf.account} spellCheck={false} placeholder="32 caractères" onChange={(e) => setCf({ ...cf, account: e.target.value.trim() })} /></label>
          <label>Jeton d'API {img?.cf_token && <small>(enregistré, chiffré — laissez vide pour le garder)</small>}
            <input className="input" type="password" autoComplete="off" value={cf.token} placeholder={img?.cf_token ? "••••••••" : "jeton Workers AI"} onChange={(e) => setCf({ ...cf, token: e.target.value.trim() })} /></label>
          <div className="field-end"><button className="btn primary" disabled={cf.account.length !== 32 || (!cf.token && !img?.cf_token)} onClick={saveCf}>Enregistrer les accès Cloudflare</button></div>
        </div>
        <div className={`notice ${img?.pillow ? "info" : "warn"} slim`}><Icon name="edit" size={15} />
          <span>{img?.pillow ? "Modification d'images prête (module Pillow présent)." : "Pour modifier une image existante, Cloudflare exige une image de moins de 512 pixels : Jarvis a besoin du module Pillow pour la réduire. La création d'images fonctionne sans."}</span>
          {!img?.pillow && <button className="btn" onClick={installPillow} disabled={img?.install?.state === "en cours"}><Icon name="download" size={14} /> Installer Pillow</button>}</div>
        {img?.install && img.install.state !== "idle" && <pre className="install-log">{`Installation : ${img.install.state}\n` + img.install.log.join("\n")}</pre>}
      </>}
      <div className="form three">
        {s.image_provider !== "comfyui" && <label>Modèle d'images
          {s.image_provider === "cloudflare"
            ? <select className="select" value={s.image_cf_model} onChange={(e) => patch({ image_cf_model: e.target.value })}>
                <option value="">FLUX.2 [klein] 4B — crée et modifie (≈ 100 images/jour gratuites)</option>
                <option value="@cf/black-forest-labs/flux-1-schnell">FLUX.1 [schnell] — crée seulement, plus économe</option>
                {models.filter((m) => !["@cf/black-forest-labs/flux-2-klein-4b", "@cf/black-forest-labs/flux-1-schnell"].includes(m)).map((m) => <option key={m} value={m}>{m}</option>)}</select>
            : <select className="select" value={s.image_model} onChange={(e) => patch({ image_model: e.target.value })}>
                <option value="">Par défaut ({img?.providers?.find((p: any) => p.id === "gemini")?.models?.[0] || "…"})</option>
                {imgModels.map((m) => <option key={m} value={m}>{m}</option>)}</select>}</label>}
        <label>Taille par défaut
          <select className="select" value={s.image_size} onChange={(e) => patch({ image_size: e.target.value })}>
            <option value="">Définie par le modèle</option>{["1K", "2K", "4K"].map((x) => <option key={x}>{x}</option>)}</select></label>
        <label>Images non enregistrées conservées
          <select className="select" value={s.images_keep_days} onChange={(e) => patch({ images_keep_days: +e.target.value })}>
            {[1, 3, 7, 14, 30].map((n) => <option key={n} value={n}>{n} jour{n > 1 ? "s" : ""}</option>)}</select></label>
      </div>
      {s.image_provider !== "comfyui" && <div className="row"><button className="btn" onClick={listModels}><Icon name="refresh" size={14} /> Lister les modèles d'images de ma clé</button></div>}
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
