import type { ChatOptions } from '../llm/llmClient';

/**
 * Minimal LLM surface the pipeline stages depend on. `LlmClient` satisfies
 * this structurally; tests inject a mock for record/replay.
 */
export interface LlmTransport {
  chat(prompt: string, options?: ChatOptions): Promise<string>;
  parseJson(text: string): Record<string, unknown>;
}
