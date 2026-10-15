/**
 * M3 verification: full Stage-1 flow in Node (validate -> upload -> poll -> extract -> full.md).
 * Run: npx vite-node test/m3.manual.ts  (requires proxy_server.py on 127.0.0.1:8502)
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  validatePdfFull,
  sanitizeFilename,
  RemoteMinerUClient,
  readText,
  writeText,
  defaultConfig,
  type Pdf2SkillConfig,
} from '../src/index';
import { mockOpfsRoot, type MockDirHandle } from './helpers/mockOpfs';

const PDF_PATH = resolve(__dirname, '../../../inputs/test.pdf');

const config: Pdf2SkillConfig = {
  ...defaultConfig(),
  backendBaseUrl: 'http://127.0.0.1:8502',
  mineru: { ...defaultConfig().mineru, api_mode: 'remote', language: 'en' },
};

async function main() {
  const bytes = new Uint8Array(readFileSync(PDF_PATH));
  console.log(`[m3] loaded test.pdf (${bytes.length} bytes)`);

  const err = await validatePdfFull(bytes, 'test.pdf');
  if (err) throw new Error(`validation failed: ${JSON.stringify(err)}`);
  console.log('[m3] validation: PASS');

  const safeName = sanitizeFilename('test.pdf');
  console.log(`[m3] sanitized name: ${safeName}`);

  const jobRoot = mockOpfsRoot() as unknown as MockDirHandle;

  const client = new RemoteMinerUClient(config);
  const fullText = await client.process(bytes, 'test.pdf', jobRoot as never);
  console.log('[m3] full.md length:', fullText.length);
  console.log('[m3] full.md head:', JSON.stringify(fullText.slice(0, 120)));

  await writeText(jobRoot as never, 'full.md', fullText);
  const fullMd = await readText(jobRoot as never, 'full.md');
  if (fullMd !== fullText) throw new Error('full.md roundtrip mismatch');
  console.log('[m3] M3 FULL FLOW: PASS');
}

main().catch((e) => {
  console.error('[m3] FAILED:', e);
  process.exit(1);
});
