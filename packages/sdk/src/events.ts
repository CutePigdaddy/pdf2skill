import type { Pdf2SkillEvents } from './types';

type Handler<K extends keyof Pdf2SkillEvents> = (event: Pdf2SkillEvents[K]) => void;

/** Minimal typed event emitter. `on` returns an unsubscribe function. */
export class Emitter {
  private handlers: { [K in keyof Pdf2SkillEvents]?: Set<Handler<K>> } = {};

  on<K extends keyof Pdf2SkillEvents>(event: K, handler: Handler<K>): () => void {
    const set = (this.handlers[event] as Set<Handler<K>>) ?? new Set();
    set.add(handler);
    this.handlers[event] = set as never;
    return () => set.delete(handler);
  }

  emit<K extends keyof Pdf2SkillEvents>(event: K, payload: Pdf2SkillEvents[K]): void {
    const set = this.handlers[event] as Set<Handler<K>> | undefined;
    if (!set) return;
    for (const h of [...set]) {
      try {
        h(payload);
      } catch (e) {
        console.error('[pdf2skill] event handler error', e);
      }
    }
  }

  removeAll(): void {
    this.handlers = {};
  }
}
