import { afterEach, describe, expect, it, vi } from 'vitest';
import { LlmClient } from '../src/llm/llmClient';
import { defaultConfig } from '../src/config';
import type { Pdf2SkillConfig } from '../src/types';

function configWith(efforts: {
  chunking?: string;
  peeling?: string;
  skill_engine?: string;
}): Pdf2SkillConfig {
  const cfg = defaultConfig();
  cfg.providers = {
    test: {
      base_url: 'https://api.example.test/v1',
      api_key_env: 'TEST_API_KEY',
      chunking_model: 'chunk-model',
      peeling_model: 'peel-model',
      skill_engine_model: 'skill-model',
      chunking_reasoning_effort: efforts.chunking,
      peeling_reasoning_effort: efforts.peeling,
      skill_engine_reasoning_effort: efforts.skill_engine,
    },
  };
  cfg.routers = { chunking: 'test', peeling: 'test', skillEngine: 'test' };
  return cfg;
}

function mockFetch(): Record<string, unknown>[] {
  const payloads: Record<string, unknown>[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init: RequestInit) => {
      payloads.push(JSON.parse(String(init.body)) as Record<string, unknown>);
      return new Response(
        JSON.stringify({ ok: true, body: { choices: [{ message: { content: 'reply' } }] } }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    }),
  );
  return payloads;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('LlmClient reasoning_effort', () => {
  it('sends the stage-specific effort', async () => {
    const payloads = mockFetch();
    const client = new LlmClient(
      'peeling',
      configWith({ chunking: 'high', peeling: 'low', skill_engine: 'max' }),
    );

    await client.chat('hello');

    expect(payloads[0]).toMatchObject({ model: 'peel-model', reasoning_effort: 'low' });
  });

  it('omits the field when the stage effort is empty', async () => {
    const payloads = mockFetch();
    const client = new LlmClient('chunking', configWith({ peeling: 'high' }));

    await client.chat('hello');

    expect(payloads[0]).not.toHaveProperty('reasoning_effort');
  });

  it('trims surrounding whitespace before sending', async () => {
    const payloads = mockFetch();
    const client = new LlmClient('skill_engine', configWith({ skill_engine: '  high  ' }));

    await client.chat('hello');

    expect(payloads[0].reasoning_effort).toBe('high');
  });
});
