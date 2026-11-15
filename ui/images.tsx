import React, { useEffect, useState } from "react";
import { api } from "./api";
import { Icon } from "./common";
import { ImageTile, ProjectSelect, Resource, useEvents, useProjects } from "./studio";

type Toast = (t: string, k?: string) => void;
interface IState {
  available: boolean; reason: string; ratios: string[]; formats: Record<string, string>; sizes: string[];
  styles: Record<string, string>; provider: string; model: string;
  providers: { id: string; label: string; has_key: boolean; note: string; models: string[] }[];
}
const FORMAT_LABEL: [string, string][] = [["carré", "Carré"], ["portrait", "Portrait"], ["paysage", "Paysage"], ["vertical", "Vertical"], ["perso", "Personnalisé"]];

/** Génération et modification d'images, galerie personnelle. */
export function ImagesView({ toast, active, goModels, askJarvis }: { toast: Toast; active: boolean; goModels: () => void; askJarvis: (text: string) => void }) {
  const [st, setSt] = useState<IState | null>(null);
  const [tab, setTab] = useState<"creer" | "galerie">("creer");
  const [prompt, setPrompt] = useState("");
  const [fmt, setFmt] = useState("carré");
  const [ratio, setRatio] = useState("4:5");
  const [style, setStyle] = useState("libre");
  const [size, setSize] = useState("");
  const [n, setN] = useState(1);
  const [refs, setRefs] = useState<Resource[]>([]);
  const [pid, setPid] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<Resource[]>([]);
  const [gallery, setGallery] = useState<Resource[]>([]);
  const [recent, setRecent] = useState<Resource[]>([]);
  const [editing, setEditing] = useState<Resource | null>(null);
  const [instr, setInstr] = useState("");
  const [view, setView] = useState<Resource | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [projects] = useProjects(active);

  const load = async () => {
    try {
      const r = await api("GET", "/api/images?saved=1");
      setSt(r.state); setGallery(r.images);
      setRecent((await api("GET", "/api/images?saved=0&limit=24")).images);
    } catch (e: any) { toast(e.message, "error"); }
  };
  useEffect(() => { if (active) load(); }, [active]);
  useEvents((e) => { if (active && e.topic === "resource" && e.kind === "image") load(); });

  const run = async (path: string, body: any) => {
    setBusy(true); setErrors([]);
    try {
      const out = await api("POST", path, body);
      setResults(out.images); setErrors(out.errors || []);
      toast(`${out.images.length} image${out.images.length > 1 ? "s" : ""} générée${out.images.length > 1 ? "s" : ""} (${out.model}). Cliquez sur l'étoile pour les garder dans la galerie.`);
      load();
    } catch (e: any) { toast(e.message, "error"); setErrors([e.message]); }
    setBusy(false);
  };
  const generate = () => run("/api/images/generate", { prompt, style, format: fmt === "perso" ? ratio : fmt, size, n, refs: refs.map((r) => r.id), project_id: pid });
  const edit = () => { if (editing && instr.trim()) { run(`/api/images/${editing.id}/edit`, { instruction: instr.trim(), style, n }); setInstr(""); setEditing(null); } };
  const upscale = (r: Resource, s: string) => run(`/api/images/${r.id}/upscale`, { size: s });

  const upload = async (files: FileList | null) => {
    for (const f of Array.from(files || []).slice(0, 6)) {
      if (f.size > 15 * 1024 * 1024) { toast(`${f.name} : 15 Mo au maximum.`, "error"); continue; }
      const data = await new Promise<string>((res) => { const fr = new FileReader(); fr.onload = () => res(String(fr.result).split(",")[1]); fr.readAsDataURL(f); });
      try { const r = await api<Resource>("POST", "/api/images/upload", { name: f.name, data, project_id: pid }); setRefs((x) => [...x, r]); }
      catch (e: any) { toast(`${f.name} : ${e.message}`, "error"); }
    }
  };

  const replace = (r: Resource | null, list: Resource[]) => (r ? list.map((x) => (x.id === r.id ? r : x)) : list);
  const tileProps = (r: Resource) => ({
    r, toast, small: false,
    onChange: (x: Resource | null) => { setResults((l) => replace(x, l)); setRecent((l) => replace(x, l)); load(); },
    onEdit: (x: Resource) => { setEditing(x); setTab("creer"); },
    onUse: (x: Resource) => { setRefs((l) => (l.some((y) => y.id === x.id) ? l : [...l, x])); setTab("creer"); },
    onOpen: setView,
  });

  const noKey = st && !st.available;
  return (
    <div className="studio-split">
      <aside className="studio-side">
        <div className="side-head"><h2>Images</h2><span className="muted small">{st ? `${st.providers.find((p) => p.id === st.provider)?.label} · ${st.model}` : ""}</span></div>
        {noKey && <div className="notice warn slim"><Icon name="warn" size={15} /><span>{st!.reason}. {st!.providers[0]?.note}</span><button className="btn" onClick={goModels}>Ajouter la clé</button></div>}
        {editing ? (
          <div className="ask-box">
            <div className="row"><img className="thumb" src={editing.src} alt="" /><b className="grow">Modifier « {editing.title} »</b>
              <button className="icon-btn" aria-label="Annuler" onClick={() => setEditing(null)}><Icon name="x" size={14} /></button></div>
            <textarea className="input" rows={3} value={instr} placeholder="Ex. « ajoute un deuxième personnage à gauche », « passe en style aquarelle », « retire le fond »"
              onChange={(e) => setInstr(e.target.value)} aria-label="Modification" />
            <button className="btn primary wide" disabled={busy || !instr.trim() || !!noKey} onClick={edit}>{busy ? "Génération…" : "Appliquer la modification"}</button>
            <div className="row small">Haute résolution (régénérée par le modèle) :
              {["2K", "4K"].map((s) => <button key={s} className="btn" disabled={busy || !!noKey} onClick={() => upscale(editing, s)}>{s}</button>)}</div>
          </div>
        ) : (
          <div className="ask-box">
            <textarea className="input" rows={4} value={prompt} placeholder="Décrivez l'image en français : sujet, cadrage, ambiance, couleurs… (ex. « une maison moderne en bois au bord d'un lac, lumière du soir »)"
              onChange={(e) => setPrompt(e.target.value)} aria-label="Description" />
            <label>Format
              <div className="seg five">{FORMAT_LABEL.map(([k, l]) => <button key={k} className={fmt === k ? "on" : ""} onClick={() => setFmt(k)}>{l}</button>)}</div>
            </label>
            {fmt === "perso" && <label>Proportions <select className="select" value={ratio} onChange={(e) => setRatio(e.target.value)}>
              {(st?.ratios || []).map((r) => <option key={r} value={r}>{r}</option>)}</select>
              <small>Le modèle accepte ces proportions ; la taille exacte en pixels dépend du modèle.</small></label>}
            <label>Style
              <div className="chips">{Object.entries(st?.styles || {}).map(([k, l]) => <button key={k} className={`chip ${style === k ? "on" : ""}`} onClick={() => setStyle(k)}>{l}</button>)}</div>
            </label>
            <div className="form three">
              <label>Taille <select className="select" value={size} onChange={(e) => setSize(e.target.value)}>
                <option value="">Par défaut</option>{(st?.sizes || []).map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
              <label>Variantes <select className="select" value={n} onChange={(e) => setN(+e.target.value)}>{[1, 2, 3, 4].map((x) => <option key={x} value={x}>{x}</option>)}</select></label>
              <label>Projet <ProjectSelect projects={projects} value={pid} onChange={setPid} className="select" /></label>
            </div>
            <label>Images de référence <small>Importez une image à transformer ou un modèle de style (PNG, JPEG, WebP).</small>
              <div className="refs">
                {refs.map((r) => <span key={r.id} className="ref"><img src={r.src} alt={r.title} /><button className="icon-btn" aria-label="Retirer" onClick={() => setRefs(refs.filter((x) => x.id !== r.id))}><Icon name="x" size={12} /></button></span>)}
                <label className="ref add" title="Importer"><Icon name="upload" size={16} /><input type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple hidden onChange={(e) => { upload(e.target.files); e.target.value = ""; }} /></label>
              </div></label>
            <button className="btn primary wide" disabled={busy || prompt.trim().length < 3 || !!noKey} onClick={generate}>
              {busy ? <><span className="dots"><i /><i /><i /></span> Génération en cours…</> : <><Icon name="sparkle" size={14} /> Générer</>}</button>
          </div>
        )}
        {errors.length > 0 && <div className="notice error slim"><Icon name="warn" size={15} /><span>{errors.join(" · ")}</span></div>}
      </aside>
      <section className="studio-main scroll">
        <div className="tabs">
          <button className={tab === "creer" ? "on" : ""} onClick={() => setTab("creer")}>Résultats et récentes</button>
          <button className={tab === "galerie" ? "on" : ""} onClick={() => setTab("galerie")}>Galerie <i>{gallery.length}</i></button>
        </div>
        {tab === "creer" && <>
          {results.length > 0 && <><h4 className="pad-h">Dernière génération</h4><div className="igrid big">{results.map((r) => <ImageTile key={r.id} {...tileProps(r)} />)}</div></>}
          <h4 className="pad-h">Générées récemment, non enregistrées <small className="muted">— effacées automatiquement après le délai de conservation</small></h4>
          {recent.length === 0 ? <p className="muted pad">Aucune.</p> : <div className="igrid">{recent.map((r) => <ImageTile key={r.id} {...tileProps(r)} />)}</div>}
        </>}
        {tab === "galerie" && (gallery.length === 0 ? <div className="empty big"><Icon name="image" size={34} /><p>Votre galerie est vide : enregistrez une image avec l'étoile.</p></div>
          : <div className="igrid">{gallery.map((r) => <ImageTile key={r.id} {...tileProps(r)} />)}</div>)}
      </section>
      {view && (
        <div className="overlay" onClick={() => setView(null)}>
          <div className="lightbox" onClick={(e) => e.stopPropagation()}>
            <img src={view.src} alt={view.title} />
            <div className="lb-info">
              <b>{view.title}</b>
              {view.meta?.prompt && <p className="muted small">« {view.meta.prompt} »</p>}
              <p className="muted small">{view.meta?.model || view.meta?.origin} · {view.meta?.aspect || ""} {view.meta?.size || ""}</p>
              <div className="row wrap">
                <button className="btn" onClick={() => { setEditing(view); setView(null); setTab("creer"); }}><Icon name="edit" size={14} /> Modifier</button>
                <button className="btn" onClick={() => { askJarvis(`Crée un artefact qui met en valeur cette image : jarvis-image:${view.id} (${view.title})`); setView(null); }}><Icon name="artifact" size={14} /> Utiliser dans un artefact</button>
                <button className="btn" onClick={() => setView(null)}>Fermer</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
