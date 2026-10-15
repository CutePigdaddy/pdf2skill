import { LlmClient, Pdf2SkillError, defaultConfig, mergeConfig } from '../src/index';

const config = mergeConfig(defaultConfig(), {
  backendBaseUrl: 'http://127.0.0.1:8502',
  routers: { chunking: 'mocktest', peeling: 'mocktest', skillEngine: 'mocktest' },
  providers: {
    mocktest: { base_url: 'http://127.0.0.1:8599/v1', api_key_env: 'MOCKTEST_API_KEY', chunking_model: 'm' },
  },
  pdf: { request_interval: 0 },
});

const client = new LlmClient('chunking', config);
const content = await client.chat('ping', { isJson: false });
console.log('chat ok:', JSON.stringify(content));

// JSON mode + parser round-trip
const parsed = client.parseJson("```json\n{'splits': [1, 2], 'atomic': True}\n```");
console.log('parse ok:', JSON.stringify(parsed));

// Error envelope unwrap: unknown provider
try {
  const bad = new LlmClient('peeling', mergeConfig(defaultConfig(), {
    routers: { chunking: 'nope', peeling: 'nope', skillEngine: 'nope' },
  }));
  await bad.chat('x');
  console.error('should have thrown');
  process.exit(1);
} catch (e) {
  const err = e as Pdf2SkillError;
  console.log('error ok:', err.code, '|', err.message.slice(0, 30));
}

// Backend-level error: provider not registered server-side
try {
  const unknownServer = new LlmClient('chunking', mergeConfig(defaultConfig(), {
    backendBaseUrl: 'http://127.0.0.1:8502',
    routers: { chunking: 'ghost', peeling: 'ghost', skillEngine: 'ghost' },
    providers: { ghost: { base_url: 'http://127.0.0.1:8599/v1', api_key_env: 'G', chunking_model: 'm' } },
    pdf: { request_interval: 0 },
  }));
  await unknownServer.chat('x');
  console.error('should have thrown');
  process.exit(1);
} catch (e) {
  const err = e as Pdf2SkillError;
  console.log('backend error ok:', err.code);
}
console.log('ALL OK');
