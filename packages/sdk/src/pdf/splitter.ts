import { PDFDocument } from 'pdf-lib';
import { Pdf2SkillError } from '../errors';

export interface SplitPart {
  name: string;
  bytes: Uint8Array;
}

/**
 * Port of RemoteMinerUProcessor._split_pdf_if_needed. Returns a single
 * element wrapping the original bytes when under the page limit, otherwise
 * parts of at most min(pageLimit, 200) pages, named {stem}_partNN.pdf.
 */
export async function splitPdfIfNeeded(
  pdfBytes: Uint8Array,
  filename: string,
  pageLimit: number,
): Promise<SplitPart[]> {
  let doc: PDFDocument;
  try {
    doc = await PDFDocument.load(pdfBytes, { ignoreEncryption: true });
  } catch (e) {
    throw new Pdf2SkillError(
      'PDF_SPLIT_ERROR',
      'PDF 拆分失败：无法加载 PDF',
      '请确认文件完整且未加密',
      'mineru',
      e,
    );
  }

  const totalPages = doc.getPageCount();
  const stem = filename.replace(/\.pdf$/i, '');
  if (totalPages <= pageLimit) {
    return [{ name: filename, bytes: pdfBytes }];
  }

  const pagesPerSplit = Math.min(pageLimit, 200);
  const numSplits = Math.ceil(totalPages / pagesPerSplit);
  const parts: SplitPart[] = [];

  for (let i = 0; i < numSplits; i++) {
    const start = i * pagesPerSplit;
    const end = Math.min((i + 1) * pagesPerSplit, totalPages);
    let out: PDFDocument;
    try {
      out = await PDFDocument.create();
      const copied = await out.copyPages(doc, range(start, end));
      for (const page of copied) out.addPage(page);
    } catch (e) {
      throw new Pdf2SkillError(
        'PDF_SPLIT_ERROR',
        `PDF 拆分失败：第 ${start + 1}-${end} 页复制出错`,
        '请尝试重新导出 PDF 或手动拆分',
        'mineru',
        e,
      );
    }
    const bytes = new Uint8Array(await out.save());
    parts.push({ name: `${stem}_part${String(i + 1).padStart(2, '0')}.pdf`, bytes });
  }
  return parts;
}

function range(start: number, end: number): number[] {
  const arr: number[] = [];
  for (let i = start; i < end; i++) arr.push(i);
  return arr;
}
