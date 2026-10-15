import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import bookSummaryPrompt from '../src/prompts/book_summary.txt?raw';
import chunkStrategyPrompt from '../src/prompts/chunk_strategy.txt?raw';
import skillNamePrompt from '../src/prompts/skill_name.txt?raw';
import skillParadigmPrompt from '../src/prompts/skill_paradigm.txt?raw';

/**
 * The root prompts/ directory is the single source of truth shared with the
 * Python project; scripts/sync-prompts.mjs copies it into src/prompts/. This
 * test fails if the copy drifts (e.g. vitest run without the pretest sync)
 * and verifies that the raw imports actually used by the stages resolve to
 * the Python bytes.
 */

const rootPromptsDir = fileURLToPath(new URL('../../../prompts/', import.meta.url));
const sdkPromptsDir = fileURLToPath(new URL('../src/prompts/', import.meta.url));

const USED_PROMPTS: Record<string, string> = {
  'book_summary.txt': bookSummaryPrompt,
  'chunk_strategy.txt': chunkStrategyPrompt,
  'skill_name.txt': skillNamePrompt,
  'skill_paradigm.txt': skillParadigmPrompt,
};

const txtFiles = (dir: string): string[] =>
  readdirSync(dir)
    .filter((f) => f.endsWith('.txt'))
    .sort();

describe('prompt files are byte-identical to the Python project', () => {
  it('root and SDK prompt directories contain the same files', () => {
    expect(txtFiles(sdkPromptsDir)).toEqual(txtFiles(rootPromptsDir));
  });

  for (const f of txtFiles(rootPromptsDir)) {
    it(`${f} bytes match`, () => {
      const root = readFileSync(join(rootPromptsDir, f));
      const sdk = readFileSync(join(sdkPromptsDir, f));
      expect(sdk.equals(root)).toBe(true);
    });
  }

  for (const [f, bundled] of Object.entries(USED_PROMPTS)) {
    it(`${f} raw import equals the Python source`, () => {
      expect(bundled).toBe(readFileSync(join(rootPromptsDir, f), 'utf-8'));
    });
  }
});
