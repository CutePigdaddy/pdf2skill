import type { ResettableStage } from '../types';
import { fileExists, readText, writeTextAtomic } from './opfs';

export const CHECKPOINT_VERSION = 1;

export interface CheckpointState {
  version: number;
  completed_stages: string[];
  data: Record<string, unknown>;
}

/**
 * Port of utils/checkpoint.py::CheckpointManager. Persisted as
 * ``.checkpoint.json`` inside the job's OPFS directory, same JSON shape.
 */
export class CheckpointManager {
  static readonly STAGE_ORDER = ['pdf_conversion', 'llm_chunking', 'tree_merging'] as const;

  readonly dir: FileSystemDirectoryHandle;
  readonly fileName = '.checkpoint.json';
  state: CheckpointState;

  constructor(dir: FileSystemDirectoryHandle) {
    this.dir = dir;
    this.state = this.emptyState();
  }

  private emptyState(): CheckpointState {
    return { version: CHECKPOINT_VERSION, completed_stages: [], data: {} };
  }

  /** Load from OPFS (must be called after construction before use). */
  async load(): Promise<void> {
    if (!(await fileExists(this.dir, this.fileName))) return;
    try {
      const raw = await readText(this.dir, this.fileName);
      const data = JSON.parse(raw) as CheckpointState;
      if (data.version !== CHECKPOINT_VERSION) {
        console.warn(
          `[pdf2skill] Checkpoint version mismatch (expected ${CHECKPOINT_VERSION}, got ${data.version}). Resetting.`,
        );
        return;
      }
      this.state = data;
    } catch (e) {
      console.warn(`[pdf2skill] Failed to load checkpoint: ${e}. Resetting.`);
      this.state = this.emptyState();
    }
  }

  async save(): Promise<void> {
    this.state.version = CHECKPOINT_VERSION;
    await writeTextAtomic(this.dir, this.fileName, JSON.stringify(this.state, null, 2));
  }

  async markStageCompleted(stageName: string, stageData?: Record<string, unknown>): Promise<void> {
    if (!this.state.completed_stages.includes(stageName)) {
      this.state.completed_stages.push(stageName);
    }
    if (stageData) {
      this.state.data[stageName] = stageData;
    }
    await this.save();
  }

  isStageCompleted(stageName: string): boolean {
    return this.state.completed_stages.includes(stageName);
  }

  getStageData<T = unknown>(stageName: string): T | undefined {
    return this.state.data[stageName] as T | undefined;
  }

  /** Remove stageName and all later stages (re-run from a specific stage). */
  async resetFromStage(stageName: ResettableStage): Promise<void> {
    if (!CheckpointManager.STAGE_ORDER.includes(stageName)) {
      throw new Error(
        `Unknown stage: ${stageName}. Valid: ${CheckpointManager.STAGE_ORDER.join(', ')}`,
      );
    }
    const idx = CheckpointManager.STAGE_ORDER.indexOf(stageName);
    const toRemove = CheckpointManager.STAGE_ORDER.slice(idx);
    for (const s of toRemove) {
      this.state.completed_stages = this.state.completed_stages.filter((x) => x !== s);
      delete this.state.data[s];
    }
    await this.save();
  }

  async clearAll(): Promise<void> {
    this.state = this.emptyState();
    await this.save();
  }
}
