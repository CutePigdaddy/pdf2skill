import type {
  MineruConfig,
  PdfConfig,
  ProviderConfig,
  RoutersConfig,
} from '@pdf2skill/sdk';
import type { ErrorInfo } from '../ui/dom';

/** Thin proxy backend client (LLM keys / models / providers / health). */
async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const e = new Error('HTTP ' + res.status) as Error & { errInfo: ErrorInfo };
    try {
      const body = JSON.parse(await res.text()) as {
        detail?: { error?: ErrorInfo } | string;
        error?: ErrorInfo;
      };
      const detail = body.detail;
      e.errInfo =
        (typeof detail === 'object' && detail.error) ||
        body.error || {
          code: 'HTTP_' + res.status,
          message: `请求失败 (HTTP ${res.status})`,
          advice: '',
        };
    } catch {
      e.errInfo = { code: 'HTTP_' + res.status, message: `请求失败 (HTTP ${res.status})`, advice: '' };
    }
    throw e;
  }
  return res.json() as Promise<T>;
}

export async function checkHealth(): Promise<boolean> {
  try {
    await api<{ ok: boolean }>('/api/health');
    return true;
  } catch {
    return false;
  }
}

export interface ModelListResult {
  ok: boolean;
  models?: string[];
  count?: number;
  error?: ErrorInfo;
}

export function fetchModels(provider: string): Promise<ModelListResult> {
  return api<ModelListResult>(`/api/models?provider=${encodeURIComponent(provider)}`);
}

export interface KeyStatus {
  api_key_env: string;
  api_key_set: boolean;
  api_key_preview: string;
}

export interface KeysResult {
  ok: boolean;
  providers: Record<string, KeyStatus>;
  mineru: { api_key_set: boolean; api_key_preview: string };
}

export function getKeys(): Promise<KeysResult> {
  return api<KeysResult>('/api/keys');
}

export function setKey(envVar: string, value: string): Promise<{ ok: boolean }> {
  return api('/api/keys', { method: 'POST', body: JSON.stringify({ env_var: envVar, value }) });
}

export interface ServerConfigResult {
  ok: boolean;
  providers: Record<string, ProviderConfig>;
  /** Web keys (chunking/…) plus any CLI-only *_provider aliases. */
  routers: Record<string, string>;
  pdf: PdfConfig;
  mineru: MineruConfig;
}

export function getServerConfig(): Promise<ServerConfigResult> {
  return api<ServerConfigResult>('/api/config');
}

export function saveServerConfig(
  providers: Record<string, ProviderConfig>,
  routers: RoutersConfig,
  pdf: PdfConfig,
  mineru: MineruConfig,
  deleteProviders: string[] = [],
): Promise<{ ok: boolean }> {
  return api('/api/config', {
    method: 'POST',
    body: JSON.stringify({
      providers,
      routers,
      pdf,
      mineru,
      delete_providers: deleteProviders,
    }),
  });
}
