import { afterEach, describe, expect, it, vi } from 'vitest';
import JSZip from 'jszip';
import { LocalMinerUClient } from '../src/mineru/localMineru';
import { defaultConfig } from '../src/config';
import { Pdf2SkillError } from '../src/errors';
import { fileExists, getDir } from '../src/storage/opfs';
import { mockOpfsRoot } from './helpers/mockOpfs';

async function buildZip(): Promise<Uint8Array<ArrayBuffer>> {
  const zip = new JSZip();
  zip.file('book/auto/book.md', '# Title\n\nHello');
  zip.file('book/auto/images/fig1.png', new Uint8Array([1, 2, 3]));
  return new Uint8Array(await zip.generateAsync({ type: 'arraybuffer' }));
}

function clientWithFetch(fetchImpl: typeof fetch): LocalMinerUClient {
  vi.stubGlobal('fetch', fetchImpl);
  const config = defaultConfig();
  config.mineru.local.base_url = 'http://127.0.0.1:7860';
  return new LocalMinerUClient(config);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('LocalMinerUClient.process', () => {
  it('reads the binary success body exactly once', async () => {
    const zipBytes = await buildZip();
    // A real Response throws "Body is unusable" on a second read, so this
    // guards against the json()-then-arrayBuffer() regression.
    const client = clientWithFetch(async () => new Response(zipBytes, { status: 200 }));
    const root = mockOpfsRoot();

    const md = await client.process(new Uint8Array([1]), 'book.pdf', root);

    expect(md).toContain('# Title');
    expect(await fileExists(root, 'book_mineru_output.zip')).toBe(true);
    const imgDir = await getDir(root, 'images');
    expect(imgDir && (await fileExists(imgDir, 'fig1.png'))).toBe(true);
  });

  it('maps the structured error envelope on non-ok responses', async () => {
    const client = clientWithFetch(
      async () =>
        new Response(
          JSON.stringify({
            detail: { error: { code: 'MINERU_LOCAL_CONN', message: '连不上', advice: '检查服务' } },
          }),
          { status: 502, headers: { 'content-type': 'application/json' } },
        ),
    );
    const root = mockOpfsRoot();

    const err = await client.process(new Uint8Array([1]), 'book.pdf', root).catch((e) => e);

    expect(err).toBeInstanceOf(Pdf2SkillError);
    expect((err as Pdf2SkillError).code).toBe('MINERU_LOCAL_CONN');
    expect((err as Pdf2SkillError).advice).toBe('检查服务');
  });
});
