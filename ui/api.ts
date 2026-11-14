const TOKEN = document.querySelector('meta[name="jarvis-token"]')?.getAttribute("content") || "";

export interface Provider {
  id: string; label: string; local: boolean; needs_key: boolean; enabled: boolean;
  base_url: string; has_key: boolean; models: string[]; error: string | null; checked?: number;
}
export interface Settings {
  assistant_name: string; user_name: string; personality: string; default_model: string;
  auto_route: boolean; routes: Record<string, string>; utility_model: string; embedding_model: string;
  memory_auto_extract: boolean; memory_top_k: number; search_past_conversations: boolean;
  deep_mode_default: boolean; temperature: number; max_tokens: number; backup_keep: number;
  orchestrator_decompose: string; orchestrator_verify: string; orchestrator_max_steps: number; orchestrator_max_calls: number;
  orchestrator_max_seconds: number; memory_update_mode: string; memory_query_expansion: boolean; habits_in_context: boolean;
  providers: Record<string, { enabled: boolean; base_url: string }>;
}
export interface Stats {
  conversations: number; messages: number; memories: number; memories_semantic: number;
  by_category: Record<string, number>; models_used: Record<string, number>; db_bytes: number;
  by_nature: Record<string, number>; projects: number; proposals: number; versions: number;
}
export interface AppState {
  version: string; settings: Settings; providers: Provider[]; routes: Record<string, string>;
  categories: string[]; kinds: string[]; natures: string[]; strategies: Record<string, string>; stats: Stats; kb: Record<string, any>; modules: { key: string; label: string; color: string }[]; key_storage: string; data_dir: string;
  default_personality: string; embedding_active: string; first_run: boolean;
}
export interface Memory {
  id: number; content: string; category: string; importance: number; kind: string; source: string;
  created: number; updated: number; expires_at: number | null; use_count: number; title?: string;
  similarity?: number; vec_model?: string | null; nature?: string; project?: string | null; found_by?: string;
}
export interface Analysis {
  objective: string; route: string; complexity: string; reasons: string[]; strategy: string; strategy_label: string;
  verify: boolean; verify_reason: string; resources: string[]; notes?: string[];
  tools: { tool: string; label: string; name?: string; query?: string; needs_confirmation: boolean }[];
  repeated: { index: number; text: string } | null;
}
export interface Step { n: number; title: string; state: string; note?: string; summary?: string; depends?: number[] }
export interface Meta {
  route?: string; route_label?: string; reason?: string; deep?: boolean; plan?: string;
  memories?: any[]; recalls?: { conv_id: string; title: string }[]; kb_miss?: boolean;
  sources?: { n: number; doc_id: number; title: string; locator: string; source: string; source_type: string; reliability: number; excerpt: string }[];
  verify?: { checked: boolean; ok: boolean; problemes: string[]; note?: string; corrected?: boolean };
  modules?: { key: string; label: string; used: boolean; hits: number; note: string }[];
  domain?: string; research_offer?: string; research_id?: number;
  analysis?: Analysis; steps?: Step[]; controls?: { level: string; text: string }[]; strategy?: string; launched?: string;
  conflicts?: { label: string; a: { n: number; text: string; title: string }; b: { n: number; text: string; title: string } }[];
}
export interface Message {
  id?: number; role: "user" | "assistant"; content: string; model?: string | null; meta: Meta;
  created?: number; pending?: boolean; status?: string; error?: string;
}
export interface Conversation { id: string; title: string; updated: number; n?: number; messages?: Message[] }
export interface Backup { name: string; bytes: number; created: number }

/** Télécharge un fichier servi par le moteur (l'en-tête d'accès ne peut pas être porté par un simple lien). */
export async function download(path: string, name: string) {
  const r = await fetch(path, { headers: { "X-Jarvis-Token": TOKEN } });
  if (!r.ok) throw new Error(`Téléchargement impossible (${r.status})`);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Jarvis-Token": TOKEN },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({ error: `Réponse illisible (${r.status})` }));
  if (!r.ok) throw new Error(j.error || `Erreur ${r.status}`);
  return j as T;
}

/** Lit le flux d'événements d'un tour de conversation. */
export async function chatStream(
  body: { conversation_id: string; message: string; model: string; deep: boolean },
  onEvent: (ev: any) => void, signal: AbortSignal,
) {
  const r = await fetch("/api/chat", {
    method: "POST", signal,
    headers: { "Content-Type": "application/json", "X-Jarvis-Token": TOKEN },
    body: JSON.stringify(body),
  });
  if (!r.ok || !r.body) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error || `Erreur ${r.status}`);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      if (block.startsWith("data: ")) {
        try { onEvent(JSON.parse(block.slice(6))); } catch { /* bloc incomplet ignoré */ }
      }
    }
  }
}

export const fmtDate = (t: number) =>
  new Date(t * 1000).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
export const fmtBytes = (n: number) =>
  n < 1024 ? `${n} o` : n < 1048576 ? `${(n / 1024).toFixed(1)} Ko` : `${(n / 1048576).toFixed(1)} Mo`;
export const shortModel = (ref?: string | null) => (ref ? ref.split(":").slice(1).join(":") : "");
export const allModels = (ps: Provider[]) =>
  ps.flatMap((p) => p.models.map((m) => ({ ref: `${p.id}:${m}`, name: m, provider: p.label, local: p.local })));
