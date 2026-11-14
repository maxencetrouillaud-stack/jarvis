// Mode vocal : reconnaissance et synthèse du navigateur (Edge / Chrome), sans dépendance.
// La reconnaissance vocale du navigateur envoie l'audio au service de son éditeur pour le transcrire.

export type VState = "off" | "standby" | "listening" | "thinking" | "speaking" | "error";
export interface VoiceCfg {
  wake: string; handsfree: boolean; bargeIn: boolean; voiceName: string; rate: number; silenceMs: number; followupS: number;
}
interface Hooks {
  cfg: () => VoiceCfg;
  onState: (s: VState, info?: string) => void;
  onHeard: (text: string) => void;                 // transcription en cours, pour l'affichage
  onUtterance: (text: string) => void;             // phrase terminée : à traiter
  onInterrupt: () => void;                         // l'utilisateur a coupé la parole ou dit « stop »
  onLevels: (bars: number[]) => void;              // niveaux pour la visualisation
}

const STOP = /^(stop|stoppe|arrete|arrete toi|tais toi|silence|annule|c est bon|ca suffit|merci ca suffit)$/;
export const norm = (s: string) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();

/** Texte lisible à voix haute : sans code, symboles Markdown, renvois de sources ni adresses. */
export function speakable(md: string) {
  return md.replace(/```[\s\S]*?(```|$)/g, " (bloc de code) ").replace(/`([^`]*)`/g, "$1").replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[S\d+\]/g, "").replace(/https?:\/\/\S+/g, " (lien) ").replace(/^\s*\|.*\|\s*$/gm, " ").replace(/^#{1,6}\s*/gm, "")
    .replace(/^\s*[-*•]\s+/gm, "").replace(/^\s*---+\s*$/gm, "").replace(/[*_~>#]+/g, "").replace(/\s+/g, " ").trim();
}

/** Découpe un texte reçu en flux en phrases complètes, prononcées au fur et à mesure. */
export class SentenceStream {
  private buf = ""; private fence = false;
  push(delta: string): string[] {
    this.buf += delta;
    const out: string[] = [];
    for (;;) {
      const m = this.buf.match(/^([\s\S]*?(?:[.!?…:;](?=\s)|\n+))/);
      if (!m) break;
      const piece = m[1];
      this.buf = this.buf.slice(piece.length);
      const ticks = (piece.match(/```/g) || []).length;
      if (ticks % 2) { this.fence = !this.fence; if (this.fence) { const pre = speakable(piece.split("```")[0]); if (pre) out.push(pre); out.push("Je vous affiche le code à l'écran."); } continue; }
      if (this.fence) continue;
      const s = speakable(piece);
      if (s.length > 1) out.push(s);
    }
    return out;
  }
  flush(): string[] { const s = this.fence ? "" : speakable(this.buf); this.buf = ""; this.fence = false; return s.length > 1 ? [s] : []; }
}

export function voiceSupport() {
  const w = window as any;
  return { stt: !!(w.SpeechRecognition || w.webkitSpeechRecognition), tts: !!(w.speechSynthesis && w.SpeechSynthesisUtterance) };
}
export function frenchVoices(): SpeechSynthesisVoice[] {
  const all = (window.speechSynthesis?.getVoices?.() || []) as SpeechSynthesisVoice[];
  return all.filter((v) => /^fr/i.test(v.lang)).sort((a, b) => Number(/natural|online/i.test(b.name)) - Number(/natural|online/i.test(a.name)));
}

export class Voice {
  state: VState = "off";
  private rec: any = null; private active = false; private restartT = 0;
  private finals = ""; private interim = ""; private silenceT = 0; private idleT = 0;
  private queue = 0; private ended = true; private spoken = new Set<string>();
  private stream: MediaStream | null = null; private actx: AudioContext | null = null; private analyser: AnalyserNode | null = null;
  private levelT = 0; private pulse = 0; private conversation = false;

  constructor(private h: Hooks) {}

  private set(s: VState, info?: string) { this.state = s; this.h.onState(s, info); }

  /** conversation = écoute immédiate ; sinon veille sur le mot-clé (mains libres). */
  async start(conversation: boolean) {
    const w = window as any, SR = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!SR) { this.set("error", "La reconnaissance vocale n'est pas disponible dans ce navigateur. Utilisez Edge ou Chrome."); return; }
    this.stopRec();
    this.conversation = conversation; this.active = true; this.finals = ""; this.interim = "";
    const rec = new SR();
    rec.lang = "fr-FR"; rec.continuous = true; rec.interimResults = true; rec.maxAlternatives = 1;
    rec.onresult = (e: any) => this.result(e);
    rec.onerror = (e: any) => this.error(e.error);
    rec.onend = () => { if (this.active && this.rec === rec) { clearTimeout(this.restartT); this.restartT = window.setTimeout(() => { try { rec.start(); } catch { /* déjà relancée */ } }, 250); } };
    this.rec = rec;
    try { rec.start(); } catch (e: any) { this.set("error", "Impossible de démarrer le micro : " + (e?.message || e)); return; }
    this.set(conversation ? "listening" : "standby");
    this.armIdle();
    this.meter();
  }

  stop() {
    this.active = false; this.stopRec(); this.cancelSpeech(false);
    clearTimeout(this.silenceT); clearTimeout(this.idleT); clearInterval(this.levelT);
    this.stream?.getTracks().forEach((t) => t.stop()); this.stream = null;
    this.actx?.close().catch(() => {}); this.actx = null; this.analyser = null;
    this.h.onLevels([]);
    this.set("off");
  }
  private stopRec() { clearTimeout(this.restartT); const r = this.rec; this.rec = null; if (r) { r.onend = null; try { r.abort(); } catch { /* rien */ } } }

  private error(code: string) {
    if (code === "no-speech" || code === "aborted") return;
    const msg = code === "not-allowed" || code === "service-not-allowed" ? "Accès au micro refusé. Autorisez le micro pour cette fenêtre, puis réessayez."
      : code === "audio-capture" ? "Aucun micro détecté."
      : code === "network" ? "La reconnaissance vocale est injoignable : elle a besoin d'une connexion Internet."
      : code === "language-not-supported" ? "Le français n'est pas pris en charge par la reconnaissance vocale de ce navigateur."
      : `Erreur de reconnaissance vocale (${code}).`;
    this.active = false; this.stopRec(); clearInterval(this.levelT); this.h.onLevels([]);
    this.set("error", msg);
  }

  /** Niveau sonore réel du micro (écoute) ou enveloppe calée sur les mots prononcés (parole). */
  private async meter() {
    clearInterval(this.levelT);
    if (!this.stream) {
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
        this.actx = new AudioContext();
        this.analyser = this.actx.createAnalyser(); this.analyser.fftSize = 128; this.analyser.smoothingTimeConstant = 0.7;
        this.actx.createMediaStreamSource(this.stream).connect(this.analyser);
      } catch { this.stream = null; this.analyser = null; }
    }
    if (!this.active) { this.stream?.getTracks().forEach((t) => t.stop()); this.stream = null; return; }
    const data = new Uint8Array(64);
    this.levelT = window.setInterval(() => {
      const bars: number[] = [];
      if (this.state === "speaking") {
        this.pulse *= 0.86;
        for (let i = 0; i < 24; i++) bars.push(Math.max(0.05, this.pulse * (0.45 + 0.55 * Math.abs(Math.sin(i * 1.7 + performance.now() * 0.012)))));
      } else if (this.analyser && (this.state === "listening" || this.state === "standby")) {
        this.analyser.getByteFrequencyData(data);
        for (let i = 0; i < 24; i++) bars.push(Math.min(1, (data[2 + i * 2] / 255) * (this.state === "standby" ? 0.5 : 1.25)));
      }
      this.h.onLevels(bars);
    }, 50);
  }

  private armIdle() {
    clearTimeout(this.idleT);
    const cfg = this.h.cfg();
    if (this.state === "listening" && !this.conversation && cfg.handsfree) {
      this.idleT = window.setTimeout(() => { if (this.state === "listening" && !this.finals && !this.interim) { this.h.onHeard(""); this.set("standby"); } }, cfg.followupS * 1000);
    }
  }

  private isEcho(text: string) {
    const words = norm(text).split(" ").filter((w) => w.length > 2);
    if (!words.length) return true;
    return words.filter((w) => this.spoken.has(w)).length / words.length >= 0.6;
  }

  private result(e: any) {
    let fin = "", inter = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i][0].transcript;
      if (e.results[i].isFinal) fin += t + " "; else inter += t;
    }
    const cfg = this.h.cfg(), wake = norm(cfg.wake || "jarvis");
    const heard = norm(fin + " " + inter);
    if (!heard) return;

    if (this.state === "standby") {
      const at = (" " + heard + " ").indexOf(" " + wake + " ");
      if (at < 0) return;
      const rest = fin ? fin.slice(this.afterWake(fin, wake)).trim().replace(/^[,.:;!? ]+/, "") : "";
      this.set("listening");
      if (fin && norm(rest).length > 2) { this.finals = rest + " "; this.h.onHeard(rest); this.armSilence(); } else if (fin) { this.finals = ""; this.interim = ""; this.h.onHeard(""); this.armIdle(); }
      return;
    }
    if (this.state === "speaking" || this.state === "thinking") {
      const isStop = STOP.test(heard.replace(new RegExp("^" + wake + " "), ""));
      const hasWake = (" " + heard + " ").includes(" " + wake + " ");
      if (this.state === "thinking") { if (fin && isStop) { this.h.onInterrupt(); this.listenNow(); } return; }
      if (!cfg.bargeIn && !isStop && !hasWake) return;
      if (!isStop && !hasWake && (this.isEcho(heard) || heard.split(" ").length < 2)) return;   // sa propre voix captée par le micro
      this.cancelSpeech(false);
      this.h.onInterrupt();
      this.listenNow();
      if (isStop) return;
    }
    if (this.state !== "listening") return;
    if (fin) {
      let add = fin;
      if (!this.finals && norm(add).startsWith(wake + " ")) add = add.slice(this.afterWake(add, wake)).replace(/^[,.:;!? ]+/, "");
      this.finals += add;
    }
    this.interim = inter;
    this.h.onHeard((this.finals + inter).trim());
    clearTimeout(this.idleT);
    this.armSilence();
  }

  private afterWake(raw: string, wake: string) {
    const words = raw.split(/(\s+)/); let acc = "";
    for (let i = 0; i < words.length; i++) { acc += words[i]; if (norm(acc).endsWith(wake)) return acc.length; }
    return 0;
  }

  /** Fin de phrase : un silence après le dernier mot reconnu. */
  private armSilence() {
    clearTimeout(this.silenceT);
    const ms = this.h.cfg().silenceMs * (this.interim ? 1.7 : 1);
    this.silenceT = window.setTimeout(() => {
      const text = (this.finals + this.interim).trim();
      this.finals = ""; this.interim = "";
      if (norm(text).length < 2 || this.state !== "listening") return;
      if (STOP.test(norm(text))) { this.h.onHeard(""); this.armIdle(); return; }
      this.set("thinking");
      this.h.onUtterance(text);
    }, ms);
  }

  private listenNow() { this.finals = ""; this.interim = ""; this.h.onHeard(""); this.set("listening"); this.armIdle(); }

  // ----- synthèse -----
  beginReply() { this.spoken.clear(); this.ended = false; this.queue = 0; }

  speak(text: string) {
    const synth = window.speechSynthesis;
    if (!synth || !text.trim()) return;
    norm(text).split(" ").forEach((w) => w.length > 2 && this.spoken.add(w));
    const cfg = this.h.cfg(), u = new SpeechSynthesisUtterance(text);
    const voices = frenchVoices();
    const v = voices.find((x) => x.name === cfg.voiceName) || voices[0];
    if (v) u.voice = v;
    u.lang = v?.lang || "fr-FR"; u.rate = cfg.rate;
    u.onstart = () => { this.pulse = 0.8; if (this.state !== "speaking" && this.active) this.set("speaking"); };
    u.onboundary = () => { this.pulse = 1; };
    const done = () => { this.queue = Math.max(0, this.queue - 1); this.maybeFinish(); };
    u.onend = done; u.onerror = done;
    this.queue++;
    if (this.active && this.state !== "speaking") this.set("speaking");
    synth.speak(u);
  }

  /** Plus rien à dire pour cette réponse : dès que la file est vide, on réécoute. */
  endReply() { this.ended = true; this.maybeFinish(); }

  private maybeFinish() {
    if (this.queue > 0 || !this.ended) return;
    if (!this.active) return;
    if (this.state === "speaking" || this.state === "thinking") {
      if (this.conversation || this.h.cfg().handsfree) this.listenNow(); else this.set("standby");
    }
  }

  cancelSpeech(resume = true) {
    this.queue = 0; this.ended = true;
    try { window.speechSynthesis?.cancel(); } catch { /* rien */ }
    if (resume && this.active && (this.state === "speaking" || this.state === "thinking")) this.listenNow();
  }
}
