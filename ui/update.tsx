import React, { useEffect, useState } from "react";
import { api, AppState, fmtBytes, fmtDate } from "./api";
import { Icon, Toggle } from "./common";

export interface UpdateInfo {
  current: string; available: string | null; notes: string; checked: number | null; error: string; busy: string;
  progress: string; installed: string | null; changes: number; bytes: number; repo: string; can_rollback: boolean;
  previous: string | null; app_dir: string; published?: string;
}

/** Redémarre le moteur puis recharge la fenêtre quand la nouvelle version répond. */
export async function restartJarvis(toast: (t: string, k?: string) => void) {
  try { await api("POST", "/api/restart", {}); } catch (e: any) { toast(e.message, "error"); return; }
  toast("Redémarrage de Jarvis…");
  const t0 = Date.now();
  await new Promise((r) => setTimeout(r, 1500));
  while (Date.now() - t0 < 60000) {
    try {
      const r = await fetch("/api/ping", { cache: "no-store" });
      if (r.ok) { location.reload(); return; }
    } catch { /* pas encore redémarré */ }
    await new Promise((r) => setTimeout(r, 700));
  }
  toast("Jarvis n'a pas redémarré : relancez-le depuis son raccourci.", "error");
}

async function install(toast: (t: string, k?: string) => void, setU: (u: UpdateInfo) => void) {
  try {
    const r = await api("POST", "/api/update/install", {});
    setU(r);
    toast(`Version ${r.installed} installée (${r.files} fichier(s) remplacé(s)).`);
    await restartJarvis(toast);
  } catch (e: any) { toast(e.message, "error"); setU(await api("GET", "/api/update")); }
}

/** Bandeau discret affiché quand une nouvelle version est disponible ou vient d'être installée. */
export function UpdateBanner({ state, toast }: { state: AppState; toast: (t: string, k?: string) => void }) {
  const [u, setU] = useState<UpdateInfo>((state as any).update);
  const [hide, setHide] = useState(false);
  useEffect(() => { setU((state as any).update); }, [(state as any).update?.available, (state as any).update?.installed]);
  useEffect(() => {   // la vérification se fait quelques secondes après le démarrage : on relit l'état ensuite
    const t = setTimeout(() => api<UpdateInfo>("GET", "/api/update").then(setU).catch(() => {}), 15000);
    return () => clearTimeout(t);
  }, []);
  if (hide || !u) return null;
  if (u.installed && u.installed !== u.current) {
    return (
      <div className="update-banner" role="status"><Icon name="check" /><span>La version <b>{u.installed}</b> est installée. Redémarrez Jarvis pour l'utiliser.</span>
        <button className="btn primary" onClick={() => restartJarvis(toast)}>Redémarrer maintenant</button></div>
    );
  }
  if (!u.available) return null;
  return (
    <div className="update-banner" role="status"><Icon name="refresh" />
      <span>Jarvis <b>{u.available}</b> est disponible{u.notes ? ` : ${u.notes.split("\n")[0]}` : ""}</span>
      {u.busy ? <span className="muted small">{u.busy} — {u.progress}</span> : <>
        <button className="btn primary" onClick={async () => { setU({ ...u, busy: "téléchargement", progress: "" }); await install(toast, setU); }}>Installer la mise à jour</button>
        <button className="link" onClick={() => setHide(true)}>Plus tard</button></>}
    </div>
  );
}

export function UpdateSettings({ state, patch, toast }: { state: AppState; patch: (p: any, msg?: string) => void; toast: (t: string, k?: string) => void }) {
  const s = state.settings as any;
  const [u, setU] = useState<UpdateInfo>((state as any).update);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!u?.busy) return;
    const t = setInterval(() => api<UpdateInfo>("GET", "/api/update").then(setU).catch(() => {}), 800);
    return () => clearInterval(t);
  }, [u?.busy]);
  const check = async () => {
    setBusy(true);
    try { const r = await api<UpdateInfo>("POST", "/api/update/check", {}); setU(r); if (!r.error && !r.available) toast("Jarvis est à jour."); }
    catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  if (!u) return null;
  const pending = u.installed && u.installed !== u.current;
  return (
    <div className="card">
      <div className="row"><h3>Mises à jour</h3><span className="grow" />
        <button className="btn" disabled={busy || !!u.busy} onClick={check}><Icon name="refresh" /> {busy ? "Vérification…" : "Vérifier maintenant"}</button></div>
      <ul className="facts">
        <li>Version utilisée : <b>{u.current}</b>{pending && <> — la version <b>{u.installed}</b> est installée et attend un redémarrage</>}</li>
        <li>{u.available ? <>Nouvelle version disponible : <b>{u.available}</b>{u.published ? ` (publiée le ${u.published})` : ""} — {u.changes} fichier(s) à remplacer, {fmtBytes(u.bytes)} à télécharger</>
          : u.checked ? (u.error ? <>Dernière vérification ({fmtDate(u.checked)}) : <span className="warn-text">{u.error}</span></> : <>À jour (vérifié le {fmtDate(u.checked)})</>)
          : "Pas encore vérifié depuis le démarrage."}</li>
        {u.available && u.notes && <li>Nouveautés : {u.notes}</li>}
        {u.busy && <li>{u.busy} — {u.progress}</li>}
      </ul>
      <div className="row wrap">
        {u.available && !pending && <button className="btn primary" disabled={!!u.busy} onClick={async () => { setU({ ...u, busy: "téléchargement", progress: "" }); await install(toast, setU); }}><Icon name="check" /> Installer et redémarrer</button>}
        {pending && <button className="btn primary" onClick={() => restartJarvis(toast)}>Redémarrer maintenant</button>}
        {u.can_rollback && <button className="btn" onClick={async () => {
          if (!window.confirm(`Revenir à la version ${u.previous} ? Vos données ne changent pas.`)) return;
          try { setU(await api("POST", "/api/update/rollback", {})); await restartJarvis(toast); } catch (e: any) { toast(e.message, "error"); }
        }}>Revenir à la version {u.previous}</button>}
      </div>
      <p className="muted small">Seuls les fichiers modifiés sont téléchargés ; l'empreinte de chacun est vérifiée avant de remplacer quoi que ce soit, et la version actuelle est gardée pour pouvoir revenir en arrière. Vos données (mémoire, connaissances, clés, réglages) ne sont jamais touchées. Application : <code>{u.app_dir}</code></p>
      <Toggle checked={s.update_check} label="Chercher les mises à jour automatiquement"
        hint="Au démarrage puis toutes les 6 heures. L'installation attend toujours votre clic." onChange={(x) => patch({ update_check: x })} />
    </div>
  );
}
