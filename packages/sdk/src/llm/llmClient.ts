import type { Pdf2SkillConfig } from '../types';
import { Pdf2SkillError, type StageKey } from '../errors';
import { parseJsonResponse } from './jsonParser';

export type LlmStage = 'chunking' | 'peeling' | 'skill_engine';

const MAX_TOKENS_BY_STAGE: Record<LlmStage, number> = {
  chunking: 20000,
  peeling: 16000,
  skill_engine: 16000,
};

const JSON_SYSTEM_PROMPT =
  'You are a helpful assistant that MUST output only valid JSON. Do not include markdown code block markers in your raw response unless explicitly requested.';

export interface ChatOptions {
  isJson?: boolean;
  maxTokens?: number;
  signal?: AbortSignal;
}

/**
 * Port of utils/llm_client.py::LLMClient. Provider/model are resolved from
 * the SDK config (routers + provider registry); the request itself goes
 * through the thin proxy backend, which holds the API key server-side.
 */
export class LlmClient {
  readonly stage: LlmStage;
  readonly provider: string;
  readonly model: string;
  readonly maxTokens: number;

  private readonly config: Pdf2SkillConfig;
  private readonly signal?: AbortSignal;
  private lastRequestAt = 0;

  constructor(stage: LlmStage, config: Pdf2SkillConfig, signal?: AbortSignal) {
    this.stage = stage;
    this.config = config;
    this.signal = signal;

    const routerKey =
      stage === 'chunking' ? 'chunking' : stage === 'peeling' ? 'peeling' : 'skillEngine';
    this.provider = config.routers[routerKey];
    const providerCfg = config.providers[this.provider];
    if (!providerCfg) {
      throw new Pdf2SkillError(
        'UNKNOWN_PROVIDER',
        `Provider '${this.provider}' 未在配置中注册`,
        '请在「配置」面板添加 Provider 或从服务端导入 settings.yaml',
      );
    }
    const modelKey =
      stage === 'chunking'
        ? 'chunking_model'
        : stage === 'peeling'
          ? 'peeling_model'
          : 'skill_engine_model';
    this.model = providerCfg[modelKey] || '';
    if (!this.model) {
      throw new Pdf2SkillError(
        'NO_MODEL',
        `Provider '${this.provider}' 未配置 ${modelKey}`,
        '请在「配置」面板为该阶段选择模型',
      );
    }
    this.maxTokens = MAX_TOKENS_BY_STAGE[stage];
  }

  private stageLabel(): StageKey {
    return this.stage === 'chunking' ? 'chunking' : this.stage === 'peeling' ? 'peeling' : 'skill';
  }

  private apiUrl(path: string): string {
    return `${this.config.backendBaseUrl}${path}`;
  }

  private async throttle(): Promise<void> {
    const interval = this.config.pdf.request_interval || 0;
    if (interval <= 0) return;
    const elapsed = Date.now() - this.lastRequestAt;
    if (elapsed < interval * 1000) {
      await new Promise((r) => setTimeout(r, interval * 1000 - elapsed));
    }
    this.lastRequestAt = Date.now();
  }

  /** Send a prompt and return the assistant's response text. */
  async chat(prompt: string, options: ChatOptions = {}): Promise<string> {
    await this.throttle();

    const messages: { role: string; content: string }[] = [];
    if (options.isJson) messages.push({ role: 'system', content: JSON_SYSTEM_PROMPT });
    messages.push({ role: 'user', content: prompt });

    const payload: Record<string, unknown> = {
      provider: this.provider,
      model: this.model,
      messages,
      temperature: this.config.llm.temperature,
      max_tokens: options.maxTokens ?? this.maxTokens,
    };
    if (options.isJson) payload.response_format = { type: 'json_object' };

    let resp: Response;
    try {
      resp = await fetch(this.apiUrl('/api/llm/chat'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: options.signal ?? this.signal,
      });
    } catch (e) {
      if ((e as Error).name === 'AbortError') throw e;
      throw new Pdf2SkillError(
        'LLM_CONNECT',
        `无法连接代理后端 (${this.apiUrl('/api/llm/chat')})`,
        '请确认代理服务已启动（python frontend/proxy_server.py）且地址正确',
        this.stageLabel(),
        e,
      );
    }

    let body: unknown;
    try {
      body = await resp.json();
    } catch {
      throw new Pdf2SkillError(
        'LLM_PARSE',
        `代理后端返回了非 JSON 响应 (HTTP ${resp.status})`,
        '请检查代理服务版本与地址',
        this.stageLabel(),
      );
    }

    // Error envelope: {ok:false, error:{...}} directly or wrapped in {detail:...}
    const envelope = (body as { detail?: unknown })?.detail ?? body;
    const errObj = (envelope as { ok?: boolean; error?: { code: string; message: string; advice?: string } });
    if (errObj && errObj.ok === false && errObj.error) {
      throw new Pdf2SkillError(
        errObj.error.code,
        errObj.error.message,
        errObj.error.advice || '',
        this.stageLabel(),
      );
    }

    const data = (envelope as { ok?: boolean; body?: Record<string, unknown> })?.body;
    if (!data) {
      throw new Pdf2SkillError(
        'LLM_PARSE',
        '代理后端响应格式异常 — 缺少 body 字段',
        '请检查代理服务版本',
        this.stageLabel(),
      );
    }

    let content: unknown;
    try {
      content = (data.choices as { message: { content: unknown } }[])[0].message.content;
    } catch {
      if ('error' in data) {
        const err = (data as { error: { message?: string } }).error;
        throw new Pdf2SkillError(
          'LLM_PARSE',
          `LLM API 返回错误: ${err?.message ?? String(err)}`,
          '',
          this.stageLabel(),
        );
      }
      throw new Pdf2SkillError(
        'LLM_PARSE',
        'LLM 响应格式异常 — 缺少 choices[0].message.content',
        '',
        this.stageLabel(),
      );
    }

    if (content === null || content === undefined) {
      throw new Pdf2SkillError(
        'LLM_PARSE',
        'LLM 返回了空内容 (content=null)',
        `模型 '${this.model}' 可能不支持该请求格式，或 max_tokens 过小`,
        this.stageLabel(),
      );
    }

    return String(content);
  }

  /** Extract a JSON object from an LLM response string. */
  parseJson(text: string): Record<string, unknown> {
    return parseJsonResponse(text);
  }
}
