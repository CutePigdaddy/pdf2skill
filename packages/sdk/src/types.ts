import type { StageKey } from './errors';

export type StageName = 'pdf_conversion' | 'llm_chunking' | 'tree_merging' | 'skill_generation';

/** Stages a run can be restarted from (Stage 4 has its own fine-grained checkpoint). */
export type ResettableStage = 'pdf_conversion' | 'llm_chunking' | 'tree_merging';

// ── LLM provider registry (mirrors settings.yaml llm.providers) ────

export interface ProviderConfig {
  base_url: string;
  api_key_env: string;
  chunking_model?: string;
  peeling_model?: string;
  skill_engine_model?: string;
  /** OpenAI Chat Completions `reasoning_effort`; empty/absent = omit the field. */
  chunking_reasoning_effort?: string;
  peeling_reasoning_effort?: string;
  skill_engine_reasoning_effort?: string;
}

export type StageRouter = 'chunking' | 'peeling' | 'skillEngine';

export interface RoutersConfig {
  chunking: string;
  peeling: string;
  skillEngine: string;
}

// ── MinerU ──────────────────────────────────────────────────────────

export interface MineruRemoteConfig {
  model_version: string;
  is_ocr: boolean;
  enable_formula: boolean;
  enable_table: boolean;
  page_ranges: string;
  extra_formats: string[];
}

export interface MineruLocalConfig {
  base_url: string;
  backend: string;
  parse_method: string;
  formula_enable: boolean;
  table_enable: boolean;
}

export interface MineruConfig {
  api_mode: 'remote' | 'local';
  language: string;
  remote: MineruRemoteConfig;
  local: MineruLocalConfig;
}

// ── PDF processing ──────────────────────────────────────────────────

export interface PdfConfig {
  page_limit: number;
  chunk_max_tokens: number;
  chunk_max_iterations: number;
  chunk_anchor_length: number;
  chunk_min_threshold: number;
  chunk_merge_threshold: number;
  request_interval: number;
}

// ── LLM ─────────────────────────────────────────────────────────────

export interface LlmConfig {
  timeout: number;
  max_retries: number;
  temperature: number;
}

// ── Root config ─────────────────────────────────────────────────────

export interface Pdf2SkillConfig {
  /** Proxy backend base URL ('' means same-origin, e.g. behind a reverse proxy). */
  backendBaseUrl: string;
  providers: Record<string, ProviderConfig>;
  routers: RoutersConfig;
  mineru: MineruConfig;
  pdf: PdfConfig;
  llm: LlmConfig;
}

// ── Run input / options / result ────────────────────────────────────

export type RunInput =
  | { kind: 'pdf'; file: File | Blob; name: string }
  | { kind: 'markdown'; file: File | Blob; name: string };

export interface RunOptions {
  fromStage?: ResettableStage;
  restart?: boolean;
  /**
   * User-specified output name (kebab-case; see `validateSkillName`). Used as
   * the output directory name AND the SKILL.md frontmatter `name`. When
   * omitted, a name is derived from the input filename.
   */
  outputName?: string;
  /** External abort signal (equivalent to calling Pdf2Skill.abort()). */
  signal?: AbortSignal;
}

export interface RunResult {
  jobId: string;
  skillName: string;
  skillMd: string;
  referenceCount: number;
}

/** A ready-to-install skill zip (SKILL.md + references at the archive root). */
export interface SkillZipResult {
  blob: Blob;
  skillName: string;
}

// ── Jobs / storage ──────────────────────────────────────────────────

export interface JobMeta {
  jobId: string;
  inputName: string;
  completedStages: StageName[];
  updatedAt: number;
}

export interface JobSummary extends JobMeta {
  /** Stage 1 checkpoint exists and its markdown file is still present. */
  resumable: boolean;
}

export interface OutputTreeNode {
  name: string;
  type: 'dir' | 'file';
  size?: number;
  count?: number;
  children?: OutputTreeNode[];
}

export type OutputFile =
  | { path: string; type: 'text'; content: string }
  | { path: string; type: 'image'; blob: Blob }
  | { path: string; type: 'binary'; size: number };

// ── Events ──────────────────────────────────────────────────────────

export interface StageEvent {
  stage: StageKey;
  status: 'pending' | 'active' | 'done' | 'failed';
  /** 0-100; MinerU uses extracted/total pages, peeling/skill use loop counts. */
  percent?: number;
  detail?: string;
  /** True when the stage was restored from checkpoint (原「已从 checkpoint 恢复」). */
  resumed?: boolean;
}

export type LogKind = 'stage' | 'run' | 'detail';

export interface LogEvent {
  level: 'info' | 'warn' | 'error';
  message: string;
  /** UI hint: stage divider, normal run message, or verbose detail. */
  kind?: LogKind;
}

export type Pdf2SkillEvents = {
  stage: StageEvent;
  log: LogEvent;
  error: { error: import('./errors').Pdf2SkillError };
  done: RunResult;
};
