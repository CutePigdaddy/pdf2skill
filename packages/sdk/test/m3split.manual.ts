/**
 * M3 split verification: generate a 450-page synthetic PDF, split it with the
 * TS splitter (pdf-lib), and write parts to %TEMP%/m3split/ts/ for the Python
 * side to diff against its own PyPDF2 split.
 * Run: npx vite-node test/m3split.manual.ts
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { PDFDocument, StandardFonts, rgb } from 'pdf-lib';
import { splitPdfIfNeeded } from '../src/index';

const OUT_DIR = resolve(__dirname, '../../../.m3split');
const BIG_PDF = resolve(OUT_DIR, 'big.pdf');

async function main() {
  mkdirSync(OUT_DIR, { recursive: true });
  mkdirSync(resolve(OUT_DIR, 'ts'), { recursive: true });
  const doc = await PDFDocument.create();
  const font = await doc.embedFont(StandardFonts.Helvetica);
  for (let i = 1; i <= 450; i++) {
    const page = doc.addPage([400, 300]);
    page.drawText(`Page ${i} of 450`, { x: 40, y: 260, size: 20, font, color: rgb(0, 0, 0) });
    const filler = `filler-${i} `.repeat(8).trim();
    page.drawText(filler, { x: 40, y: 230, size: 10, font });
  }
  const bigBytes = await doc.save();
  writeFileSync(BIG_PDF, bigBytes);
  console.log(`[gen] big.pdf: ${bigBytes.length} bytes, 450 pages`);

  const parts = await splitPdfIfNeeded(new Uint8Array(bigBytes), 'big.pdf', 200);
  console.log(`[split] ${parts.length} parts`);
  for (const p of parts) {
    writeFileSync(resolve(OUT_DIR, 'ts', p.name), p.bytes);
    console.log(`[split] ${p.name}: ${p.bytes.length} bytes`);
  }
  console.log('[m3split] TS side: DONE');
}

main().catch((e) => {
  console.error('[m3split] FAILED:', e);
  process.exit(1);
});
