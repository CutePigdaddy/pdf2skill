// Copies root prompts/*.txt into src/prompts/ so prompts stay single-sourced
// from the Python project. Run via `npm run sync-prompts` (predev/prebuild/pretest).
import { copyFileSync, mkdirSync, readdirSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const srcDir = join(here, '..', '..', '..', 'prompts');
const destDir = join(here, '..', 'src', 'prompts');

if (!existsSync(srcDir)) {
  console.error(`[sync-prompts] source prompts dir not found: ${srcDir}`);
  process.exit(1);
}
mkdirSync(destDir, { recursive: true });

const files = readdirSync(srcDir).filter((f) => f.endsWith('.txt'));
for (const f of files) {
  copyFileSync(join(srcDir, f), join(destDir, f));
}
console.log(`[sync-prompts] copied ${files.length} prompt files: ${files.join(', ')}`);
