import type { Pdf2SkillConfig, ProviderConfig } from './types';

// Defaults aligned with config/settings.yaml + Python code fallbacks.
const DEFAULT_CONFIG: Pdf2SkillConfig = {
  backendBaseUrl: '',
  providers: {},
  routers: { chunking: '', peeling: '', skillEngine: '' },
  mineru: {
    api_mode: 'remote',
    language: 'ch',
    remote: {
      model_version: 'vlm',
      is_ocr: false,
      enable_formula: true,
      enable_table: true,
      page_ranges: '',
      extra_formats: [],
    },
    local: {
      base_url: 'http://localhost:7860',
      backend: 'vlm-auto-engine',
      parse_method: 'auto',
      formula_enable: true,
      table_enable: true,
    },
  },
  pdf: {
    page_limit: 200,
    chunk_max_tokens: 10000,
    chunk_max_iterations: 5,
    chunk_anchor_length: 30,
    chunk_min_threshold: 1000,
    chunk_merge_threshold: 5000,
    request_interval: 0.3,
  },
  llm: { timeout: 300, max_retries: 3, temperature: 0.3 },
};

const STORAGE_KEY = 'pdf2skill.config';

export function defaultConfig(): Pdf2SkillConfig {
  return structuredClone(DEFAULT_CONFIG);
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Dynamic key read for plain-data merges (typed objects have no index signature). */
function getProp(obj: object, key: string): unknown {
  return Reflect.get(obj, key);
}

/**
 * Deep-merge a partial config onto the defaults (arrays and scalars replaced).
 * Generic so nested config sections merge through the same code path.
 */
export function mergeConfig<T extends object>(base: T, patch: unknown): T {
  if (!isPlainObject(patch)) return base;
  const out: T = Object.assign({}, base);
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined) continue;
    if (k === 'providers' && isPlainObject(v)) {
      // The provider registry is replace-only: deletions (remove provider)
      // and import-from-server must be expressible, which key-merge forbids.
      Object.assign(out, { providers: v });
      continue;
    }
    const cur = getProp(base, k);
    if (isPlainObject(v) && isPlainObject(cur)) {
      Object.assign(out, { [k]: mergeConfig(cur, v) });
    } else {
      Object.assign(out, { [k]: v });
    }
  }
  return out;
}

export function loadStoredConfig(): Pdf2SkillConfig | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return mergeConfig(defaultConfig(), JSON.parse(raw));
  } catch {
    return null;
  }
}

export function saveStoredConfig(config: Pdf2SkillConfig): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
}

const PROVIDER_NAME_RE = /^[a-z0-9][a-z0-9_-]*$/;
const ENV_VAR_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

export type ConfigIssue = { code: string; message: string; advice: string };

/** Validate providers/routers with the same rules as the Python backend. */
export function validateConfig(config: Pdf2SkillConfig): ConfigIssue[] {
  const issues: ConfigIssue[] = [];
  const names = Object.keys(config.providers);
  for (const [name, cfg] of Object.entries(config.providers)) {
    if (!PROVIDER_NAME_RE.test(name)) {
      issues.push({
        code: 'INVALID_PROVIDER_NAME',
        message: `Provider 名称 '${name}' 不合法`,
        advice: '名称只能包含小写字母、数字、下划线和连字符，且以字母或数字开头',
      });
    }
    if (!cfg.base_url || !/^https?:\/\//.test(cfg.base_url)) {
      issues.push({
        code: 'INVALID_BASE_URL',
        message: `Provider '${name}' 的 base_url 必须以 http:// 或 https:// 开头`,
        advice: '示例: https://api.example.com/v1',
      });
    }
    if (!ENV_VAR_RE.test(cfg.api_key_env || '')) {
      issues.push({
        code: 'INVALID_ENV_VAR',
        message: `Provider '${name}' 的 api_key_env 不合法`,
        advice: '环境变量名只能包含字母、数字、下划线，且以字母或下划线开头',
      });
    }
  }
  const routerEntries: [string, string][] = [
    ['chunking', config.routers.chunking],
    ['peeling', config.routers.peeling],
    ['skillEngine', config.routers.skillEngine],
  ];
  for (const [stage, name] of routerEntries) {
    if (name && !(name in config.providers)) {
      issues.push({
        code: 'ROUTER_UNKNOWN_PROVIDER',
        message: `阶段 ${stage} 引用了不存在的 Provider '${name}'`,
        advice: '请选择配置中已有的 Provider',
      });
    }
  }
  if (names.length === 0) {
    issues.push({
      code: 'NO_PROVIDER',
      message: '尚未配置任何 Provider',
      advice: '请在「配置」面板添加 Provider，或从服务端导入 settings.yaml',
    });
  }
  return issues;
}

export type { Pdf2SkillConfig, ProviderConfig };
