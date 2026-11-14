// Moteur de rendu 3D de la sphère (WebGL, sans bibliothèque externe).
// Un seul appel de dessin pour tous les nœuds, un pour tous les liens : plusieurs milliers d'éléments restent fluides.

export interface SNode { id: string; k: string; l: string; f: string; p: [number, number, number]; s: number; m?: string }
export type SEdge = [number, number, number];

const VS_POINT = `
attribute vec3 aPos; attribute vec3 aCol; attribute float aSize; attribute float aAlpha;
uniform mat4 uMvp; uniform float uScale; uniform vec3 uEye;
varying vec3 vCol; varying float vAlpha;
void main() {
  gl_Position = uMvp * vec4(aPos, 1.0);
  float d = distance(aPos, uEye);
  gl_PointSize = max(1.5, aSize * uScale / d);
  vCol = aCol;
  vAlpha = aAlpha * clamp(1.75 - d * 0.32, 0.3, 1.0);   // les éléments lointains s'estompent : profondeur
}`;
const FS_POINT = `
precision mediump float; varying vec3 vCol; varying float vAlpha; uniform float uGain;
void main() {
  float r = length(gl_PointCoord - 0.5) * 2.0;
  if (r > 1.0) discard;
  float core = smoothstep(0.55, 0.0, r), halo = pow(1.0 - r, 2.2);
  float i = (core + halo * 0.7) * vAlpha * uGain;
  gl_FragColor = vec4(mix(vCol, vec3(1.0), core * 0.55) * i, min(1.0, i));   // couleur prémultipliée : pas de disque opaque
}`;
const VS_LINE = `
attribute vec3 aPos; attribute vec4 aCol; uniform mat4 uMvp; uniform vec3 uEye; varying vec4 vCol;
void main() { gl_Position = uMvp * vec4(aPos, 1.0); float d = distance(aPos, uEye);
  vCol = vec4(aCol.rgb, aCol.a * clamp(1.75 - d * 0.32, 0.25, 1.0)); }`;
const FS_LINE = `precision mediump float; varying vec4 vCol; void main() { gl_FragColor = vec4(vCol.rgb * vCol.a, vCol.a); }`;

type M4 = Float32Array;
function perspective(fov: number, aspect: number, near: number, far: number): M4 {
  const f = 1 / Math.tan(fov / 2), m = new Float32Array(16);
  m[0] = f / aspect; m[5] = f; m[10] = (far + near) / (near - far); m[11] = -1; m[14] = (2 * far * near) / (near - far);
  return m;
}
function lookAt(e: number[], c: number[]): M4 {
  let zx = e[0] - c[0], zy = e[1] - c[1], zz = e[2] - c[2];
  let l = Math.hypot(zx, zy, zz) || 1; zx /= l; zy /= l; zz /= l;
  let xx = zz, xy = 0, xz = -zx;                      // up = (0,1,0)  →  x = up × z
  l = Math.hypot(xx, xy, xz) || 1; xx /= l; xz /= l;
  const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
  return new Float32Array([xx, yx, zx, 0, xy, yy, zy, 0, xz, yz, zz, 0,
    -(xx * e[0] + xy * e[1] + xz * e[2]), -(yx * e[0] + yy * e[1] + yz * e[2]), -(zx * e[0] + zy * e[1] + zz * e[2]), 1]);
}
function mul(a: M4, b: M4): M4 {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
  }
  return o;
}
const hex = (h: string) => [parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255];
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
// 0 appartenance · 1 relation du graphe · 2 concept–document · 3 voisinage · 4 connexion de sens · 5 zone vectorielle · 6 recherche
const EDGE_COL = [[0.35, 0.55, 0.95, 0.16], [0.36, 0.82, 1, 0.6], [1, 0.75, 0.35, 0.3], [0.75, 0.6, 1, 0.26],
  [0.3, 0.95, 0.78, 0.3], [0.13, 0.83, 0.93, 0.1], [0.96, 0.45, 0.71, 0.45]];
const SPAWN_MS = 2600;

export class SphereEngine {
  private gl: WebGLRenderingContext;
  private ctx: CanvasRenderingContext2D;
  private progP: WebGLProgram; private progL: WebGLProgram;
  private bufs: Record<string, WebGLBuffer> = {};
  private nodes: SNode[] = []; private edges: SEdge[] = []; private colors: Record<string, string> = {};
  private alpha = new Float32Array(0); private visible: Uint8Array = new Uint8Array(0);
  private highlight: Set<number> | null = null; private selected = -1; private hover = -1;
  private edgeCount = 0; private decoCount = 0; private dustCount = 0;
  private yaw = 0.6; private pitch = 0.25; private dist = 7; private target = [0, 0, 0];
  private anim: { t0: number; dur: number; from: number[]; to: number[] } | null = null;
  private raf = 0; private last = 0; private fps = 60; private slow = 0; private dustAngle = 0;
  private drag: { x: number; y: number; pan: boolean; moved: boolean } | null = null;
  private mvp: M4 = new Float32Array(16); private eye = [0, 0, 5];
  private ro: ResizeObserver; private dpr = 1; private w = 1; private h = 1; private born = performance.now();
  autoRotate = true; simplified = false; frames = 0;
  private gain = 1;   // plus il y a de nœuds, moins chacun brille : les amas denses restent lisibles
  private known: Set<string> | null = null;            // identifiants déjà affichés : sert à repérer les nouveautés
  private spawn = new Map<number, number>();           // nœud nouveau -> instant de son apparition
  private sizeBase = new Float32Array(0);
  private tint: ((n: SNode) => string) | null = null;
  private shownItems = 0;
  spawned = 0;                                         // nombre total de nœuds apparus depuis l'ouverture
  onSelect: (i: number) => void = () => {};
  onPerf: (fps: number, shouldSimplify: boolean) => void = () => {};

  constructor(private canvas: HTMLCanvasElement, private overlay: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl", { antialias: true, alpha: true, premultipliedAlpha: true });
    if (!gl) throw new Error("WebGL indisponible sur cette machine");
    this.gl = gl;
    this.ctx = overlay.getContext("2d")!;
    this.progP = this.program(VS_POINT, FS_POINT);
    this.progL = this.program(VS_LINE, FS_LINE);
    for (const k of ["pos", "col", "size", "alpha", "lpos", "lcol", "dpos", "dcol", "upos", "ucol", "usize", "ualpha"]) this.bufs[k] = gl.createBuffer()!;
    this.buildDecor();
    canvas.addEventListener("pointerdown", this.down);
    canvas.addEventListener("pointermove", this.move);
    canvas.addEventListener("pointerup", this.up);
    canvas.addEventListener("pointerleave", this.leave);
    canvas.addEventListener("wheel", this.wheel, { passive: false });
    canvas.addEventListener("contextmenu", this.noMenu);
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(canvas);
    this.resize();
    this.flyHome(1400);
    this.raf = requestAnimationFrame(this.frame);
  }

  private program(vs: string, fs: string) {
    const gl = this.gl, p = gl.createProgram()!;
    for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]] as [number, string][]) {
      const s = gl.createShader(type)!;
      gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error("Shader : " + gl.getShaderInfoLog(s));
      gl.attachShader(p, s);
    }
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error("Programme : " + gl.getProgramInfoLog(p));
    return p;
  }

  /** Décor : globe filaire et poussière lumineuse. Purement visuel, jamais cliquable, distinct des données. */
  private buildDecor() {
    const gl = this.gl, pos: number[] = [], col: number[] = [];
    const seg = (a: number[], b: number[], al: number) => { pos.push(...a, ...b); col.push(0.25, 0.5, 0.95, al, 0.25, 0.5, 0.95, al); };
    const R = 1.14, N = 64;
    for (let i = 1; i < 8; i++) {
      const lat = -Math.PI / 2 + (i * Math.PI) / 8, y = Math.sin(lat) * R, r = Math.cos(lat) * R;
      for (let j = 0; j < N; j++) { const a = (j / N) * 6.2832, b = ((j + 1) / N) * 6.2832; seg([Math.cos(a) * r, y, Math.sin(a) * r], [Math.cos(b) * r, y, Math.sin(b) * r], 0.085); }
    }
    for (let i = 0; i < 12; i++) {
      const lon = (i / 12) * 6.2832;
      for (let j = 0; j < N; j++) {
        const a = -Math.PI / 2 + (j / N) * Math.PI, b = -Math.PI / 2 + ((j + 1) / N) * Math.PI;
        seg([Math.cos(a) * Math.cos(lon) * R, Math.sin(a) * R, Math.cos(a) * Math.sin(lon) * R], [Math.cos(b) * Math.cos(lon) * R, Math.sin(b) * R, Math.cos(b) * Math.sin(lon) * R], 0.06);
      }
    }
    this.decoCount = pos.length / 3;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs.dpos); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(pos), gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs.dcol); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(col), gl.STATIC_DRAW);
    const n = 900, up = new Float32Array(n * 3), uc = new Float32Array(n * 3), us = new Float32Array(n), ua = new Float32Array(n);
    let seed = 7; const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let i = 0; i < n; i++) {
      const r = 1.5 + rnd() * 2.6, t = rnd() * 6.2832, ph = Math.acos(2 * rnd() - 1);
      up.set([r * Math.sin(ph) * Math.cos(t), r * Math.cos(ph), r * Math.sin(ph) * Math.sin(t)], i * 3);
      uc.set([0.45, 0.7, 1], i * 3); us[i] = 0.5 + rnd() * 0.7; ua[i] = 0.1 + rnd() * 0.22;
    }
    this.dustCount = n;
    for (const [k, d] of [["upos", up], ["ucol", uc], ["usize", us], ["ualpha", ua]] as [string, Float32Array][]) { gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs[k]); gl.bufferData(gl.ARRAY_BUFFER, d, gl.STATIC_DRAW); }
  }

  private colorOf(n: SNode) { return (this.tint && this.tint(n)) || this.colors[n.f] || "#64748b"; }

  /** tint : couleur imposée à un nœud (affichage par module) ; sinon la couleur de sa famille. */
  setData(nodes: SNode[], edges: SEdge[], colors: Record<string, string>, tint: ((n: SNode) => string) | null = null) {
    const gl = this.gl;
    this.nodes = nodes; this.edges = edges; this.colors = colors; this.tint = tint;
    // apparition progressive : les nœuds absents de l'affichage précédent grandissent un à un
    const now = performance.now();
    const fresh: number[] = [];
    if (this.known) nodes.forEach((n, i) => { if (!this.known!.has(n.id) && n.k !== "family" && n.k !== "category") fresh.push(i); });
    this.spawn.clear();
    if (fresh.length && fresh.length <= 400) {
      const step = Math.min(160, 3200 / fresh.length);
      fresh.forEach((i, k) => this.spawn.set(i, now + k * step));
      this.spawned += fresh.length;
    }
    this.known = new Set(nodes.map((n) => n.id));
    this.gain = Math.max(0.42, Math.min(1, 1.3 - 0.22 * Math.log10(Math.max(10, nodes.length))));
    const shrink = Math.sqrt(this.gain);
    const pos = new Float32Array(nodes.length * 3), col = new Float32Array(nodes.length * 3), size = new Float32Array(nodes.length);
    nodes.forEach((n, i) => {
      pos.set(n.p, i * 3); col.set(hex(this.colorOf(n)), i * 3);
      size[i] = n.k === "family" ? 30 * n.s / 3 : n.k === "category" ? 17 : (9 + n.s * 3.6) * shrink;
    });
    this.sizeBase = size;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs.pos); gl.bufferData(gl.ARRAY_BUFFER, pos, gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs.col); gl.bufferData(gl.ARRAY_BUFFER, col, gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs.size); gl.bufferData(gl.ARRAY_BUFFER, size, gl.STATIC_DRAW);
    this.alpha = new Float32Array(nodes.length);
    if (this.visible.length !== nodes.length) this.visible = new Uint8Array(nodes.length).fill(1);
    this.selected = -1; this.hover = -1; this.highlight = null;
    this.refresh();
  }

  setVisible(mask: Uint8Array) { this.visible = mask; this.refresh(); }
  setHighlight(set: Set<number> | null, selected: number) { this.highlight = set; this.selected = selected; this.refresh(); }

  /** Recalcule l'opacité des nœuds et la liste des liens à dessiner. */
  private refresh() {
    const gl = this.gl, hl = this.highlight;
    for (let i = 0; i < this.nodes.length; i++) this.alpha[i] = !this.visible[i] ? 0 : !hl ? 1 : hl.has(i) ? 1 : 0.1;
    this.shownItems = 0;
    for (let i = 0; i < this.nodes.length; i++) if (this.visible[i] && this.nodes[i].k !== "family" && this.nodes[i].k !== "category") this.shownItems++;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs.alpha); gl.bufferData(gl.ARRAY_BUFFER, this.alpha, gl.DYNAMIC_DRAW);
    const pos: number[] = [], col: number[] = [];
    for (const [a, b, t] of this.edges) {
      if (!this.visible[a] || !this.visible[b]) continue;
      const on = hl ? hl.has(a) && hl.has(b) : true;
      if (hl && !on && this.simplified) continue;
      if (!hl && this.simplified && t !== 1) continue;
      const c = EDGE_COL[t] || EDGE_COL[0], al = hl ? (on ? Math.min(1, c[3] * 3.2) : c[3] * 0.18) : c[3];
      pos.push(...this.nodes[a].p, ...this.nodes[b].p); col.push(c[0], c[1], c[2], al, c[0], c[1], c[2], al);
    }
    this.edgeCount = pos.length / 3;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs.lpos); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(pos), gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs.lcol); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(col), gl.DYNAMIC_DRAW);
  }

  setSimplified(v: boolean) { this.simplified = v; this.resize(); this.refresh(); }

  private resize() {
    const r = this.canvas.getBoundingClientRect();
    this.dpr = this.simplified ? 1 : Math.min(window.devicePixelRatio || 1, 2);
    this.w = Math.max(1, r.width); this.h = Math.max(1, r.height);
    for (const c of [this.canvas, this.overlay]) { c.width = Math.round(this.w * this.dpr); c.height = Math.round(this.h * this.dpr); }
    this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
  }

  // ----- caméra -----
  private cam() { return [this.yaw, this.pitch, this.dist, ...this.target]; }
  private animate(to: number[], dur: number) {
    const from = this.cam();
    while (to[0] - from[0] > Math.PI) to[0] -= 2 * Math.PI;
    while (to[0] - from[0] < -Math.PI) to[0] += 2 * Math.PI;
    this.anim = { t0: performance.now(), dur, from, to };
  }
  flyHome(dur = 800) { this.animate([this.yaw, 0.25, 3.5, 0, 0, 0], dur); }
  flyTo(i: number, dur = 850) {
    const p = this.nodes[i]?.p; if (!p) return;
    const r = Math.hypot(p[0], p[1], p[2]) || 1;
    const hub = this.nodes[i].k === "family" || this.nodes[i].k === "category";
    this.animate([Math.atan2(p[0], p[2]), Math.asin(Math.max(-1, Math.min(1, p[1] / r))) * 0.85, hub ? 2.5 : 1.75, p[0] * 0.55, p[1] * 0.55, p[2] * 0.55], dur);
  }
  zoom(f: number) { this.anim = null; this.dist = Math.max(0.35, Math.min(9, this.dist * f)); }

  private down = (e: PointerEvent) => {
    this.drag = { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.shiftKey, moved: false };
    this.canvas.setPointerCapture(e.pointerId); this.anim = null;
  };
  private move = (e: PointerEvent) => {
    const d = this.drag;
    if (!d) { const h = this.pick(e.clientX, e.clientY); if (h !== this.hover) { this.hover = h; this.canvas.style.cursor = h >= 0 ? "pointer" : "grab"; } return; }
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
    if (!d.moved) return;
    d.x = e.clientX; d.y = e.clientY;
    if (d.pan) {
      const k = this.dist * 0.0016, cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
      this.target[0] -= dx * k * cy; this.target[2] += dx * k * sy; this.target[1] += dy * k;
    } else { this.yaw -= dx * 0.006; this.pitch = Math.max(-1.45, Math.min(1.45, this.pitch + dy * 0.006)); }
  };
  private up = (e: PointerEvent) => {
    const d = this.drag; this.drag = null;
    if (d && !d.moved) this.onSelect(this.pick(e.clientX, e.clientY));
  };
  private leave = () => { this.hover = -1; };
  private wheel = (e: WheelEvent) => { e.preventDefault(); this.zoom(Math.exp(e.deltaY * 0.0012)); };
  private noMenu = (e: Event) => e.preventDefault();

  /** Position à l'écran d'un nœud (pixels CSS, relatifs au canevas), ou null s'il est derrière la caméra. */
  screenOf(i: number): { x: number; y: number; w: number } | null {
    const p = this.nodes[i]?.p, m = this.mvp; if (!p) return null;
    const w = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15];
    if (w <= 0.01) return null;
    return { x: ((m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12]) / w * 0.5 + 0.5) * this.w, y: (1 - ((m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13]) / w * 0.5 + 0.5)) * this.h, w };
  }
  private pick(cx: number, cy: number) {
    const r = this.canvas.getBoundingClientRect(), x = cx - r.left, y = cy - r.top;
    let best = -1, score = 1e9;
    for (let i = 0; i < this.nodes.length; i++) {
      if (this.alpha[i] < 0.5) continue;
      const s = this.screenOf(i); if (!s) continue;
      const rad = Math.max(9, (this.nodes[i].k === "family" ? 16 : 9) / s.w * 1.6), d = Math.hypot(s.x - x, s.y - y);
      if (d < rad && d + s.w * 4 < score) { score = d + s.w * 4; best = i; }
    }
    return best;
  }

  // ----- rendu -----
  private frame = (now: number) => {
    this.raf = requestAnimationFrame(this.frame);
    const dt = Math.min(100, now - (this.last || now)); this.last = now; this.frames++;
    if (dt > 0) this.fps = this.fps * 0.94 + (1000 / dt) * 0.06;
    if (now - this.born > 4000 && this.frames % 30 === 0) {
      this.slow = this.fps < 22 ? this.slow + 1 : 0;
      this.onPerf(this.fps, this.slow >= 4 && !this.simplified);
    }
    if (this.anim) {
      const t = Math.min(1, (now - this.anim.t0) / this.anim.dur), k = ease(t), f = this.anim.from, to = this.anim.to;
      [this.yaw, this.pitch, this.dist] = [0, 1, 2].map((i) => f[i] + (to[i] - f[i]) * k);
      this.target = [3, 4, 5].map((i) => f[i] + (to[i] - f[i]) * k);
      if (t >= 1) this.anim = null;
    } else if (this.autoRotate && !this.drag) this.yaw += dt * 0.00007;
    this.dustAngle += dt * 0.00002;

    const gl = this.gl, cp = Math.cos(this.pitch);
    this.eye = [this.target[0] + this.dist * cp * Math.sin(this.yaw), this.target[1] + this.dist * Math.sin(this.pitch), this.target[2] + this.dist * cp * Math.cos(this.yaw)];
    this.mvp = mul(perspective(0.85, this.w / this.h, 0.05, 40), lookAt(this.eye, this.target));
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.disable(gl.DEPTH_TEST); gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);
    const fade = Math.min(1, (now - this.born) / 900);

    const L = this.progL;
    gl.useProgram(L);
    gl.uniformMatrix4fv(gl.getUniformLocation(L, "uMvp"), false, this.mvp);
    gl.uniform3fv(gl.getUniformLocation(L, "uEye"), this.eye);
    const lines = (pb: WebGLBuffer, cb: WebGLBuffer, n: number) => {
      if (!n) return;
      const ap = gl.getAttribLocation(L, "aPos"), ac = gl.getAttribLocation(L, "aCol");
      gl.bindBuffer(gl.ARRAY_BUFFER, pb); gl.enableVertexAttribArray(ap); gl.vertexAttribPointer(ap, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, cb); gl.enableVertexAttribArray(ac); gl.vertexAttribPointer(ac, 4, gl.FLOAT, false, 0, 0);
      gl.drawArrays(gl.LINES, 0, n);
    };
    if (!this.simplified) lines(this.bufs.dpos, this.bufs.dcol, this.decoCount);
    lines(this.bufs.lpos, this.bufs.lcol, this.edgeCount);

    const P = this.progP;
    gl.useProgram(P);
    gl.uniform3fv(gl.getUniformLocation(P, "uEye"), this.eye);
    gl.uniform1f(gl.getUniformLocation(P, "uScale"), this.h * this.dpr * 0.0052 * fade);
    const points = (pre: string, n: number, mvp: M4) => {
      if (!n) return;
      gl.uniform1f(gl.getUniformLocation(P, "uGain"), pre ? 1 : this.gain);
      gl.uniformMatrix4fv(gl.getUniformLocation(P, "uMvp"), false, mvp);
      for (const [name, key, sz] of [["aPos", "pos", 3], ["aCol", "col", 3], ["aSize", "size", 1], ["aAlpha", "alpha", 1]] as [string, string, number][]) {
        const loc = gl.getAttribLocation(P, name);
        gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs[pre + key]); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, sz, gl.FLOAT, false, 0, 0);
      }
      gl.drawArrays(gl.POINTS, 0, n);
    };
    if (!this.simplified) {
      const c = Math.cos(this.dustAngle), s = Math.sin(this.dustAngle);
      points("u", this.dustCount, mul(this.mvp, new Float32Array([c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1])));
    }
    if (this.spawn.size) {   // taille des nœuds en cours d'apparition
      const sz = new Float32Array(this.sizeBase);
      this.spawn.forEach((t0, i) => {
        const t = (now - t0) / SPAWN_MS;
        if (t >= 1) { this.spawn.delete(i); return; }
        sz[i] = t <= 0 ? 0 : this.sizeBase[i] * ease(Math.min(1, t * 1.6)) * (1 + 1.4 * (1 - t));
      });
      gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs.size); gl.bufferData(gl.ARRAY_BUFFER, sz, gl.DYNAMIC_DRAW);
    }
    points("", this.nodes.length, this.mvp);
    this.labels(now);
  };

  private labels(now: number) {
    const c = this.ctx, d = this.dpr;
    c.setTransform(d, 0, 0, d, 0, 0); c.clearRect(0, 0, this.w, this.h);
    c.textAlign = "center"; c.lineJoin = "round";
    const boxes: number[][] = [];
    const draw = (i: number, strong: boolean) => {
      const s = this.screenOf(i); if (!s || this.alpha[i] < 0.5) return;
      const n = this.nodes[i], hub = n.k === "family" || n.k === "category";
      const txt = n.l.length > 34 ? n.l.slice(0, 33) + "…" : n.l;
      c.font = `${strong || n.k === "family" ? 600 : 400} ${n.k === "family" ? 13 : 11.5}px "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif`;
      const tw = c.measureText(txt).width / 2 + 4, ty = s.y - (hub ? 15 : 11) / Math.max(0.6, s.w * 0.45);
      const box = [s.x - tw, ty - 13, s.x + tw, ty + 4];   // deux libellés ne se superposent jamais
      if (!strong && boxes.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) return;
      boxes.push(box);
      const a = Math.max(0.25, Math.min(1, 1.9 - s.w * 0.36));
      c.globalAlpha = strong ? 1 : a;
      c.strokeStyle = "rgba(4,7,13,.9)"; c.lineWidth = 3.5; c.strokeText(txt, s.x, s.y - (hub ? 15 : 11) / Math.max(0.6, s.w * 0.45));
      c.fillStyle = strong ? "#ffffff" : this.colorOf(n); c.fillText(txt, s.x, s.y - (hub ? 15 : 11) / Math.max(0.6, s.w * 0.45));
    };
    const hl = this.highlight;
    if (this.selected >= 0) draw(this.selected, true);
    if (this.hover >= 0 && this.hover !== this.selected) draw(this.hover, true);
    for (let i = 0; i < this.nodes.length; i++) {
      const n = this.nodes[i];
      if (i === this.selected || i === this.hover) continue;
      if (n.k === "family" && n.s > 2) draw(i, false);
      else if (n.k === "category" && this.dist < 3.2) draw(i, false);
      else if (hl && hl.has(i) && hl.size <= 40) draw(i, false);
      else if (!hl && this.shownItems <= 30 && this.alpha[i] > 0.5) draw(i, false);   // peu d'éléments affichés : tous sont nommés
      else if (!hl && this.dist < 1.5 && n.k !== "family") { const s = this.screenOf(i); if (s && s.w < this.dist * 0.75) draw(i, false); }
    }
    this.spawn.forEach((t0, i) => {   // onde et libellé de chaque nouvelle connaissance
      const t = (now - t0) / SPAWN_MS, s = t > 0 && t < 1 && this.alpha[i] > 0.5 ? this.screenOf(i) : null;
      if (!s) return;
      c.globalAlpha = 0.85 * (1 - t); c.strokeStyle = this.colorOf(this.nodes[i]); c.lineWidth = 1.6;
      c.beginPath(); c.arc(s.x, s.y, 6 + 46 * t, 0, 6.2832); c.stroke();
      if (this.spawn.size <= 12) draw(i, true);
    });
    if (this.selected >= 0) {
      const s = this.screenOf(this.selected);
      if (s) {   // anneau pulsé autour de l'élément sélectionné
        const r = 13 / Math.max(0.5, s.w * 0.5) + Math.sin(now * 0.005) * 2;
        c.globalAlpha = 0.9; c.strokeStyle = "#ffffff"; c.lineWidth = 1.4; c.beginPath(); c.arc(s.x, s.y, r, 0, 6.2832); c.stroke();
        c.globalAlpha = 0.35; c.beginPath(); c.arc(s.x, s.y, r + 6 + ((now * 0.02) % 14), 0, 6.2832); c.stroke();
      }
    }
    c.globalAlpha = 1;
  }

  getFps() { return this.fps; }
  visibleCount() { let n = 0; for (let i = 0; i < this.alpha.length; i++) if (this.alpha[i] > 0) n++; return n; }

  dispose() {
    cancelAnimationFrame(this.raf);
    this.ro.disconnect();
    const cv = this.canvas;
    cv.removeEventListener("pointerdown", this.down); cv.removeEventListener("pointermove", this.move);
    cv.removeEventListener("pointerup", this.up); cv.removeEventListener("pointerleave", this.leave);
    cv.removeEventListener("wheel", this.wheel); cv.removeEventListener("contextmenu", this.noMenu);
    const gl = this.gl;
    Object.values(this.bufs).forEach((b) => gl.deleteBuffer(b));
    gl.deleteProgram(this.progP); gl.deleteProgram(this.progL);
    gl.getExtension("WEBGL_lose_context")?.loseContext();
  }
}
