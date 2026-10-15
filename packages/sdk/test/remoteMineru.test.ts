import { afterEach, describe, expect, it, vi } from 'vitest';
import JSZip from 'jszip';
import { PDFDocument } from 'pdf-lib';
import { RemoteMinerUClient } from '../src/mineru/mineruClient';
import { defaultConfig } from '../src/config';
import { dirExists, fileExists, getDir, readBytes } from '../src/storage/opfs';
import { mockOpfsRoot } from './helpers/mockOpfs';

async function makePdf(pageCount: number): Promise<Uint8Array<ArrayBuffer>> {
  const doc = await PDFDocument.create();
  for (let i = 0; i < pageCount; i++) doc.addPage([300, 400]);
  return new Uint8Array(await doc.save());
}

async function makeZip(partIndex: number, withImage: boolean): Promise<Uint8Array<ArrayBuffer>> {
  const zip = new JSZip();
  zip.file('book/auto/book.md', `Content of part ${partIndex}`);
  if (withImage) zip.file('book/auto/images/fig1.png', new Uint8Array([1, 2, 3]));
  return new Uint8Array(await zip.generateAsync({ type: 'arraybuffer' }));
}

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

/** Mock the proxy backend's four MinerU routes; returns captured upload filenames. */
function stubFetch(): { uploadedNames: string[] } {
  const uploadedNames: string[] = [];
  let batchSeq = 0;
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    if (url.endsWith('/api/mineru/file-urls/batch')) {
      batchSeq += 1;
      const body = JSON.parse(String(init?.body)) as { files: { name: string }[] };
      uploadedNames.push(body.files[0].name);
      return jsonResponse({
        code: 0,
        data: { batch_id: `batch-${batchSeq}`, file_urls: [`https://upload.example/part-${batchSeq}`] },
      });
    }
    if (url.includes('/api/mineru/upload')) return jsonResponse({ ok: true });
    if (url.includes('/api/mineru/extract-results/batch/')) {
      const n = url.split('/batch/')[1].replace('batch-', '');
      return jsonResponse({
        code: 0,
        data: { extract_result: [{ state: 'done', full_zip_url: `https://download.example/part-${n}.zip` }] },
      });
    }
    if (url.includes('/api/mineru/download')) {
      const target = new URL(url, 'http://x').searchParams.get('url') ?? '';
      const n = Number(target.match(/part-(\d+)\.zip/)?.[1] ?? 0);
      return new Response(await makeZip(n, n === 1), { status: 200 });
    }
    throw new Error(`unexpected fetch: ${url}`);
  });
  return { uploadedNames };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('RemoteMinerUClient.process', () => {
  it('splits an over-limit PDF, writes parts to splits/, and joins with part separators', async () => {
    const config = defaultConfig();
    config.pdf.page_limit = 2;
    const client = new RemoteMinerUClient(config);
    const root = mockOpfsRoot();
    const { uploadedNames } = stubFetch();

    const md = await client.process(await makePdf(3), 'book.pdf', root);

    expect(md).toBe('Content of part 1\n\n---\n\n# Part 2\n\nContent of part 2');
    expect(uploadedNames).toEqual(['book_part01.pdf', 'book_part02.pdf']);

    // Python: splits are written to output_dir/splits/ when over the limit
    expect(await dirExists(root, 'splits')).toBe(true);
    const splitsDir = await getDir(root, 'splits');
    expect(splitsDir).toBeTruthy();
    const part1 = await PDFDocument.load(await readBytes(splitsDir!, 'book_part01.pdf'));
    const part2 = await PDFDocument.load(await readBytes(splitsDir!, 'book_part02.pdf'));
    expect(part1.getPageCount()).toBe(2);
    expect(part2.getPageCount()).toBe(1);

    // Images from part 1's zip land in jobRoot/images
    const imgDir = await getDir(root, 'images');
    expect(imgDir && (await fileExists(imgDir, 'fig1.png'))).toBe(true);
  });

  it('keeps an under-limit PDF whole and writes no splits/', async () => {
    const config = defaultConfig();
    config.pdf.page_limit = 5;
    const client = new RemoteMinerUClient(config);
    const root = mockOpfsRoot();
    const { uploadedNames } = stubFetch();

    const md = await client.process(await makePdf(3), 'book.pdf', root);

    expect(md).toBe('Content of part 1');
    expect(uploadedNames).toEqual(['book.pdf']);
    expect(await dirExists(root, 'splits')).toBe(false);
  });
});
