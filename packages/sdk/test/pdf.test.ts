import { describe, expect, it } from 'vitest';
import { PDFDocument } from 'pdf-lib';
import { validatePdf, validatePdfStructure, sanitizeFilename } from '../src/pdf/validator';
import { splitPdfIfNeeded } from '../src/pdf/splitter';

async function makePdf(pages: number): Promise<Uint8Array> {
  const doc = await PDFDocument.create();
  for (let i = 0; i < pages; i++) doc.addPage([300, 400]);
  return new Uint8Array(await doc.save());
}

describe('validatePdf (fast checks)', () => {
  it('passes a valid minimal PDF', async () => {
    const bytes = await makePdf(1);
    expect(validatePdf(bytes, 'book.pdf')).toBeNull();
    expect(await validatePdfStructure(bytes)).toBeNull();
  });

  it('rejects wrong extension', () => {
    expect(validatePdf(new Uint8Array([1]), 'book.txt')?.code).toBe('FORMAT_EXTENSION');
  });

  it('rejects missing filename', () => {
    expect(validatePdf(new Uint8Array([1]), '')?.code).toBe('NAME_MISSING');
  });

  it('rejects path traversal in filename', () => {
    expect(validatePdf(new Uint8Array([1]), '../evil.pdf')?.code).toBe('NAME_INVALID');
  });

  it('rejects empty file', () => {
    expect(validatePdf(new Uint8Array(0), 'book.pdf')?.code).toBe('SIZE_EMPTY');
  });

  it('rejects bad magic bytes', () => {
    const bytes = new TextEncoder().encode('not a pdf at all');
    expect(validatePdf(bytes, 'book.pdf')?.code).toBe('FORMAT_INVALID');
  });

  it('rejects corrupt PDF structure', async () => {
    const bytes = new TextEncoder().encode('%PDF-1.4 totally broken body');
    expect((await validatePdfStructure(bytes))?.code).toBe('FORMAT_CORRUPT');
  });
});

describe('sanitizeFilename', () => {
  it('strips path components', () => {
    expect(sanitizeFilename('C:\\Users\\书 名.pdf')).toBe('书_名.pdf');
  });
  it('replaces dangerous chars', () => {
    expect(sanitizeFilename('a<b>c?.pdf')).toBe('a_b_c_.pdf');
  });
});

describe('splitPdfIfNeeded', () => {
  it('returns the original single part when under the limit', async () => {
    const bytes = await makePdf(3);
    const parts = await splitPdfIfNeeded(bytes, 'book.pdf', 200);
    expect(parts).toHaveLength(1);
    expect(parts[0].name).toBe('book.pdf');
    expect(parts[0].bytes).toBe(bytes);
  });

  it('splits an over-limit PDF into part files', async () => {
    const bytes = await makePdf(7);
    const parts = await splitPdfIfNeeded(bytes, 'book.pdf', 3);
    expect(parts.map((p) => p.name)).toEqual(['book_part01.pdf', 'book_part02.pdf', 'book_part03.pdf']);
    for (const part of parts) {
      const doc = await PDFDocument.load(part.bytes);
      expect(doc.getPageCount()).toBeLessThanOrEqual(3);
    }
    const total = await Promise.all(
      parts.map(async (p) => (await PDFDocument.load(p.bytes)).getPageCount()),
    );
    expect(total.reduce((a, b) => a + b, 0)).toBe(7);
  });

  it('caps pages per split at 200 even with a higher limit', async () => {
    const bytes = await makePdf(3);
    const parts = await splitPdfIfNeeded(bytes, 'book.pdf', 500);
    expect(parts).toHaveLength(1); // 3 pages < 500, no split despite cap logic
  });
});
