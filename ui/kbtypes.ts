import { AppState } from "./api";

export interface Cat { id: number; name: string; parent_id: number | null; n: number }
export interface Job { id: number; kind: string; label: string; status: string; detail: string; created: number; finished: number | null }
export interface Extract { id: number; kind: string; label: string; content: string; locator: string; value: number | null; unit: string | null }
export interface Doc {
  id: number; title: string; description: string; category: string; category_id: number | null; keywords: string;
  source_type: string; source: string; license: string; refs: string; published: string; imported: number;
  verified: number; updated: number; reliability: number; n_chunks: number; chars: number; version: number;
  dup_of: number | null; dup_title?: string; original: string; graph_done: number;
  module: string; archived: number; superseded_by: number | null; newer_title?: string; class_how: string;
  chunks?: { id: number; ord: number; locator: string; content: string }[]; chunks_total?: number;
  versions?: { id: number; version: number; saved: number; bytes: number }[];
  extracts?: Extract[]; links?: { node: string; title: string; kind: string; score: number }[];
}
export interface FolderState {
  folder: string; exists: boolean; watch: boolean; last_scan: number | null; files: number; indexed: number; errors: number; missing: number;
}
export interface KbState {
  stats: Record<string, any>; categories: Cat[]; jobs: Job[]; reliability: Record<string, string>;
  formats: string[]; pdf_ready: boolean; embedding_active: string; llm_ready: boolean; originals_dir: string;
  folder: FolderState; pending_findings: number;
}
export interface LibFilter { module?: string; category?: number | null; state?: string; n: number }
export type Ctx = {
  kb: KbState; load: () => void; toast: (t: string, k?: string) => void; state: AppState; setState: (s: AppState) => void;
  openDoc: (id: number) => void; go: (tab: string) => void; openLibrary: (f: Omit<LibFilter, "n">) => void;
  toSphere: () => void; goModels: () => void;
};

export const DOC_MODULES: [string, string][] = [["bibliotheque", "Bibliothèque technique"], ["generale", "Connaissances générales"], ["recherche", "Recherches du chercheur"]];
export const moduleLabel = (state: AppState, key: string) => state.modules.find((m) => m.key === key)?.label || key;
export const moduleColor = (state: AppState, key: string) => state.modules.find((m) => m.key === key)?.color || "#7f93b0";
export const isUrl = (s: string) => /^https?:\/\//.test(s);
export const host = (s: string) => s.replace(/^https?:\/\//, "").replace(/^www\./, "");
