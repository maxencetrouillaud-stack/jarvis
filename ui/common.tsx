import React from "react";
import { marked } from "marked";
import { Provider } from "./api";

const esc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

// Le texte des modèles n'est jamais interprété comme du HTML ; les images distantes ne sont pas chargées.
marked.use({
  gfm: true,
  breaks: true,
  walkTokens(token: any) {
    if ((token.type === "link" || token.type === "image") && !/^(https?:|mailto:)/i.test(token.href || "")) {
      token.href = "#";
    }
  },
  renderer: {
    html(token: any) { return esc(token.text || token.raw || ""); },
    image(token: any) { return `<a href="${esc(token.href || "#")}">[image : ${esc(token.text || token.href || "")}]</a>`; },
  },
  hooks: {
    postprocess(html: string) { return html.replace(/<a /g, '<a target="_blank" rel="noopener noreferrer" '); },
  },
} as any);

export function Markdown({ text }: { text: string }) {
  const html = React.useMemo(() => marked.parse(text, { async: false }) as string, [text]);
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}

const PATHS: Record<string, string> = {
  chat: "M4 5h16v11H9l-5 4z",
  memory: "M12 3a4 4 0 0 0-4 4v1a3 3 0 0 0-2 5 3 3 0 0 0 2 5 4 4 0 0 0 8 0 3 3 0 0 0 2-5 3 3 0 0 0-2-5V7a4 4 0 0 0-4-4zM12 3v18",
  models: "M12 2 3 7v10l9 5 9-5V7zM3 7l9 5 9-5M12 12v10",
  dash: "M4 13h6V4H4zM14 20h6V4h-6zM4 20h6v-4H4z",
  settings: "M4 7h10M18 7h2M4 17h4M12 17h8M16 4v6M10 14v6",
  plus: "M12 5v14M5 12h14",
  send: "M5 12h14M13 6l6 6-6 6",
  stop: "M7 7h10v10H7z",
  trash: "M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM16 16l5 5",
  check: "M5 12l5 5 9-10",
  copy: "M9 9h11v11H9zM5 15V4h11",
  refresh: "M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5",
  x: "M6 6l12 12M18 6 6 18",
  upload: "M12 16V4M7 9l5-5 5 5M4 20h16",
  bolt: "M13 2 5 14h6l-1 8 8-12h-6z",
  warn: "M12 3 2 20h20zM12 10v5M12 17.5v.5",
  book: "M5 4h9a4 4 0 0 1 4 4v12H9a4 4 0 0 1-4-4zM5 16a4 4 0 0 1 4-4h9M9 8h5",
  minus: "M5 12h14",
  target: "M12 3v4M12 17v4M3 12h4M17 12h4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  expand: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3M8 21h8",
  volume: "M4 9v6h4l5 4V5L8 9zM16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11",
  artifact: "M4 5h16v14H4zM4 9h16M8 13l-2 2 2 2M16 13l2 2-2 2M13 12l-2 6",
  globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18",
  image: "M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4M15.5 8.5h.01",
  folder: "M3 6h6l2 2h10v11H3z",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  back: "M15 5l-7 7 7 7",
  forward: "M9 5l7 7-7 7",
  home: "M4 11l8-7 8 7M6 9.5V20h12V9.5",
  star: "M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.8l-5.2 2.8 1-5.8-4.3-4.1 5.9-.8z",
  download: "M12 4v11M7 10l5 5 5-5M4 20h16",
  cursor: "M5 3l13 7-5.5 1.8L10.7 18z",
  code: "M8 7l-5 5 5 5M16 7l5 5-5 5",
  eye: "M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  history: "M3.5 12a8.5 8.5 0 1 0 2.5-6M3 4v5h5M12 8v4l3 2",
  panel: "M4 4h16v16H4zM14 4v16",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z",
  play: "M7 5v14l11-7z",
  pause: "M8 5v14M16 5v14",
  shield: "M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z",
};

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}

export type Phase = "idle" | "thinking" | "streaming" | "listening" | "speaking" | "error";
export const PHASE_LABEL: Record<Phase, string> = { idle: "En veille", thinking: "Réflexion en cours", streaming: "Réponse en cours",
  listening: "À l'écoute", speaking: "Parole en cours", error: "Erreur" };

/** Visualisation animée de l'assistant : l'état affiché est l'état réel du moteur. */
export function Orb({ phase, size = 120 }: { phase: Phase; size?: number }) {
  return (
    <div className={`orb orb-${phase}`} style={{ width: size, height: size }} role="img"
      aria-label={PHASE_LABEL[phase]}>
      <span className="orb-ring r1" /><span className="orb-ring r2" /><span className="orb-ring r3" />
      <span className="orb-core" />
    </div>
  );
}

export function ModelSelect(props: {
  providers: Provider[]; value: string; onChange: (v: string) => void;
  emptyLabel?: string; className?: string; id?: string;
}) {
  const groups = props.providers.filter((p) => p.models.length);
  const known = groups.some((p) => p.models.some((m) => `${p.id}:${m}` === props.value));
  return (
    <select id={props.id} className={props.className} value={props.value} onChange={(e) => props.onChange(e.target.value)}>
      {props.emptyLabel !== undefined && <option value="">{props.emptyLabel}</option>}
      {props.value && !known && <option value={props.value}>{props.value} (indisponible)</option>}
      {groups.map((p) => (
        <optgroup key={p.id} label={p.label}>
          {p.models.map((m) => <option key={m} value={`${p.id}:${m}`}>{m}</option>)}
        </optgroup>
      ))}
    </select>
  );
}

export function Toggle({ checked, onChange, label, hint }: {
  checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string;
}) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle-track"><span className="toggle-dot" /></span>
      <span className="toggle-text"><b>{label}</b>{hint && <small>{hint}</small>}</span>
    </label>
  );
}

export function useToasts() {
  const [toasts, setToasts] = React.useState<{ id: number; text: string; kind: string }[]>([]);
  const push = React.useCallback((text: string, kind = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 8000 : 4500);
  }, []);
  const view = (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => <div key={t.id} className={`toast toast-${t.kind}`}>{t.text}</div>)}
    </div>
  );
  return { push, view };
}
