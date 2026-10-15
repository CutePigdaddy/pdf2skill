import { Pdf2Skill, validateConfig } from '@pdf2skill/sdk';
import type { ProviderConfig } from '@pdf2skill/sdk';
import { fetchModels, getKeys, getProviders, setKey, syncProviders } from '../api/backend';
import { $, checked, escapeHtml, errInfoOf, setChecked, setVal, showToast, val } from '../ui/dom';

const STAGES = [
  { selId: 'chunk-provider', stageKey: 'chunking', routerKey: 'chunking' as const, displayId: 'chunk-model-display' },
  { selId: 'peel-provider', stageKey: 'peeling', routerKey: 'peeling' as const, displayId: 'peel-model-display' },
  { selId: 'skill-provider', stageKey: 'skill_engine', routerKey: 'skillEngine' as const, displayId: 'skill-model-display' },
];
const MODEL_KEYS = ['chunking_model', 'peeling_model', 'skill_engine_model'] as const;

let mineruMode: 'local' | 'remote' = 'local';
const modelCache = new Map<string, string[]>();
let keyStatus: Record<string, { api_key_env: string; api_key_set: boolean; api_key_preview: string }> = {};

let pdf2skill: Pdf2Skill;

function sdk(): Pdf2Skill {
  return pdf2skill;
}

// ── Error banner ──────────────────────────────────────────────────

function showSaveError(e: unknown): void {
  const info = errInfoOf(e);
  const box = $('config-error');
  box.innerHTML =
    `<div class="error-title">⚠ ${escapeHtml(info.message)}</div>` +
    (info.advice ? `<div class="error-advice">💡 ${escapeHtml(info.advice)}</div>` : '');
  box.classList.add('active');
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function hideSaveError(): void {
  const box = $('config-error');
  box.classList.remove('active');
  box.innerHTML = '';
}

// ── Provider rows ─────────────────────────────────────────────────

function renderProviderRows(): void {
  const container = $('provider-rows');
  const providers = sdk().getConfig().providers;
  container.innerHTML = Object.keys(providers)
    .map((name) => {
      const cfg = providers[name];
      const info = keyStatus[name] || {};
      const badge = info.api_key_set
        ? `<span class="key-badge key-set">✓ ${escapeHtml(info.api_key_preview || '已设置')}</span>`
        : `<span class="key-badge key-missing">✗ 未设置 Key</span>`;
      const modelFields = MODEL_KEYS.map(
        (k) =>
          `<div class="pf-field"><label>${escapeHtml(k.replace('_model', ''))} 模型</label>` +
          `<input type="text" data-field="${k}" list="dl-${escapeHtml(name)}-${k}" value="${escapeHtml(cfg[k] || '')}" placeholder="未配置">` +
          `<datalist id="dl-${escapeHtml(name)}-${k}"></datalist></div>`,
      ).join('');
      return `
    <div class="provider-row" data-provider="${escapeHtml(name)}">
      <div class="provider-head">
        <span class="provider-name">${escapeHtml(name)}</span>
        ${badge}
        <div class="provider-head-actions">
          <button class="btn btn-ghost btn-sm" data-action="models" data-name="${escapeHtml(name)}">获取模型列表</button>
          <button class="btn btn-danger btn-sm" data-action="delete" data-name="${escapeHtml(name)}">删除</button>
        </div>
      </div>
      <div class="provider-fields">
        <div class="pf-field pf-wide"><label>base_url</label><input type="text" data-field="base_url" value="${escapeHtml(cfg.base_url || '')}" placeholder="https://api.example.com/v1"></div>
        <div class="pf-field pf-wide"><label>API Key 环境变量名</label><input type="text" data-field="api_key_env" value="${escapeHtml(cfg.api_key_env || '')}" placeholder="EXAMPLE_API_KEY"></div>
        <div class="pf-models">${modelFields}</div>
      </div>
      <div class="model-error" id="model-error-${escapeHtml(name)}" style="display:none"></div>
    </div>`;
    })
    .join('');

  container.querySelectorAll<HTMLButtonElement>('button[data-action]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const name = btn.dataset.name!;
      if (btn.dataset.action === 'models') void fetchModelsFor(name);
      else void deleteProvider(name);
    });
  });

  for (const [name, models] of modelCache) renderDatalists(name, models);
}

function renderDatalists(providerName: string, models: string[]): void {
  for (const k of MODEL_KEYS) {
    const dl = document.getElementById(`dl-${providerName}-${k}`);
    if (dl) dl.innerHTML = models.map((m) => `<option value="${escapeHtml(m)}"></option>`).join('');
  }
}

function providerModel(providerName: string, stageKey: string): string {
  if (!providerName) return '';
  const rowInput = document.querySelector<HTMLInputElement>(
    `.provider-row[data-provider="${providerName}"] input[data-field="${stageKey}_model"]`,
  );
  if (rowInput && rowInput.value.trim() !== '') return rowInput.value.trim();
  return (
    (sdk().getConfig().providers[providerName] as unknown as Record<string, string> | undefined)?.[
      stageKey + '_model'
    ] || ''
  );
}

function updateStageDisplay(
  s: (typeof STAGES)[number],
): void {
  const p = (document.getElementById(s.selId) as HTMLSelectElement).value;
  $(s.displayId).textContent = providerModel(p, s.stageKey) || '未配置模型';
}

// ── Model list fetch ──────────────────────────────────────────────

async function fetchModelsFor(providerName: string): Promise<void> {
  const row = document.querySelector(`.provider-row[data-provider="${providerName}"]`);
  if (!row) return;
  const btn = row.querySelector<HTMLButtonElement>('.provider-head-actions .btn-ghost');
  const errBox = document.getElementById(`model-error-${providerName}`);
  if (!btn) return;

  btn.classList.add('loading');
  btn.disabled = true;
  if (errBox) errBox.style.display = 'none';

  try {
    const data = await fetchModels(providerName);
    if (!data.ok) {
      const info = data.error!;
      if (errBox) {
        errBox.innerHTML =
          `<div class="model-error-title">${escapeHtml(info.message)}</div>` +
          (info.advice ? `<div class="model-error-advice">💡 ${escapeHtml(info.advice)}</div>` : '');
        errBox.style.display = 'block';
      }
      return;
    }
    modelCache.set(providerName, data.models || []);
    renderDatalists(providerName, data.models || []);
    showToast(`已获取 ${data.count} 个模型`);
  } catch (e) {
    const info = errInfoOf(e);
    if (errBox) {
      errBox.innerHTML =
        `<div class="model-error-title">${escapeHtml(info.message)}</div>` +
        (info.advice ? `<div class="model-error-advice">💡 ${escapeHtml(info.advice)}</div>` : '');
      errBox.style.display = 'block';
    }
  } finally {
    btn.classList.remove('loading');
    btn.disabled = false;
  }
}

// ── Provider add / delete ─────────────────────────────────────────

function showFormError(msg: string): void {
  const el = $('new-provider-error');
  el.textContent = msg;
  el.classList.add('active');
}

function hideAddProviderForm(): void {
  $('provider-form').classList.remove('active');
  $('btn-add-provider').style.display = '';
  for (const id of [
    'new-provider-name',
    'new-provider-env',
    'new-provider-base-url',
    'new-provider-chunking',
    'new-provider-peeling',
    'new-provider-skill',
  ]) {
    setVal(id, '');
  }
  $('new-provider-error').classList.remove('active');
}

async function saveNewProvider(): Promise<void> {
  hideSaveError();
  const name = val('new-provider-name').trim();
  const envVar = val('new-provider-env').trim();
  const base = val('new-provider-base-url').trim();

  if (!/^[a-z0-9][a-z0-9_-]*$/.test(name)) {
    return showFormError('名称不合法：只能包含小写字母、数字、下划线和连字符，且以字母或数字开头');
  }
  if (name in sdk().getConfig().providers) {
    return showFormError(`Provider '${name}' 已存在，可直接在下方编辑`);
  }
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(envVar)) {
    return showFormError('API Key 环境变量名不合法：只能包含字母、数字、下划线，且以字母或下划线开头');
  }
  if (!base.startsWith('http://') && !base.startsWith('https://')) {
    return showFormError('base_url 必须以 http:// 或 https:// 开头');
  }

  const cfg = sdk().getConfig();
  cfg.providers[name] = {
    base_url: base,
    api_key_env: envVar,
    chunking_model: val('new-provider-chunking').trim(),
    peeling_model: val('new-provider-peeling').trim(),
    skill_engine_model: val('new-provider-skill').trim(),
  };
  sdk().configure({ providers: cfg.providers });
  hideAddProviderForm();
  await loadConfigUI();
  showToast('Provider 已保存（记得点「保存配置」或「同步到服务端」）');
}

async function deleteProvider(name: string): Promise<void> {
  if (!confirm(`确定删除 Provider '${name}'？删除后不可恢复。`)) return;
  const cfg = sdk().getConfig();
  if (Object.keys(cfg.providers).length <= 1) {
    return showSaveError({
      errInfo: { code: 'LAST_PROVIDER', message: `无法删除最后一个 Provider '${name}'`, advice: '至少保留一个 Provider' },
    });
  }
  for (const [stage, p] of Object.entries(cfg.routers)) {
    if (p === name) {
      return showSaveError({
        errInfo: {
          code: 'PROVIDER_IN_USE',
          message: `Provider '${name}' 正被阶段 ${stage} 使用，无法删除`,
          advice: '请先在阶段卡片中将对应阶段切换到其他 Provider',
        },
      });
    }
  }
  delete cfg.providers[name];
  sdk().configure({ providers: cfg.providers });
  await loadConfigUI();
  showToast(`已删除 Provider ${name}`);
}

// ── MinerU mode ───────────────────────────────────────────────────

function setMinerUMode(mode: 'local' | 'remote'): void {
  mineruMode = mode;
  $('mode-local').classList.toggle('active', mode === 'local');
  $('mode-remote').classList.toggle('active', mode === 'remote');
  $('mineru-local').classList.toggle('active', mode === 'local');
  $('mineru-remote').classList.toggle('active', mode === 'remote');
}

// ── Load / save ───────────────────────────────────────────────────

export async function loadConfigUI(): Promise<void> {
  const cfg = sdk().getConfig();

  // Stage routing selects
  for (const s of STAGES) {
    const sel = document.getElementById(s.selId) as HTMLSelectElement;
    sel.innerHTML = Object.keys(cfg.providers)
      .map((n) => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`)
      .join('');
    const routed = cfg.routers[s.routerKey];
    sel.value = routed && cfg.providers[routed] ? routed : (sel.options.length ? (sel.options[0] as HTMLOptionElement).value : '');
    sel.onchange = () => updateStageDisplay(s);
    updateStageDisplay(s);
  }

  // PDF params
  setVal('request-interval', String(cfg.pdf.request_interval));
  setVal('pdf-page-limit', String(cfg.pdf.page_limit));
  setVal('pdf-merge-threshold', String(cfg.pdf.chunk_merge_threshold));
  setVal('pdf-min-threshold', String(cfg.pdf.chunk_min_threshold));

  // MinerU config
  setMinerUMode(cfg.mineru.api_mode);
  setVal('mu-local-url', cfg.mineru.local.base_url);
  setVal('mu-local-backend', cfg.mineru.local.backend);
  setVal('mu-local-parse', cfg.mineru.local.parse_method);
  setChecked('mu-local-formula', cfg.mineru.local.formula_enable);
  setChecked('mu-local-table', cfg.mineru.local.table_enable);
  setVal('mu-remote-model', cfg.mineru.remote.model_version);
  setVal('mu-remote-pages', cfg.mineru.remote.page_ranges);
  setChecked('mu-remote-ocr', cfg.mineru.remote.is_ocr);
  setChecked('mu-remote-formula', cfg.mineru.remote.enable_formula);
  setChecked('mu-remote-table', cfg.mineru.remote.enable_table);
  setVal('mu-language', cfg.mineru.language);
  setVal('mu-language-remote', cfg.mineru.language);

  renderProviderRows();
  await loadKeyStatus();
}

async function loadKeyStatus(): Promise<void> {
  try {
    const data = await getKeys();
    keyStatus = data.providers || {};

    // MinerU key badge
    const muKeyBadge = $('mu-key-badge');
    if (data.mineru?.api_key_set) {
      muKeyBadge.className = 'key-badge key-set';
      muKeyBadge.textContent = '✓ ' + (data.mineru.api_key_preview || '已设置');
    } else {
      muKeyBadge.className = 'key-badge key-missing';
      muKeyBadge.textContent = '✗ 未设置';
    }

    // API keys section
    const section = $('apikey-section');
    section.innerHTML = '';
    const cfg = sdk().getConfig();
    for (const name of Object.keys(cfg.providers)) {
      const info = keyStatus[name];
      const envVar = info?.api_key_env || cfg.providers[name].api_key_env || `${name.toUpperCase()}_API_KEY`;
      const set = !!info?.api_key_set;
      const preview = info?.api_key_preview || '';
      const row = document.createElement('div');
      row.className = 'key-row';
      row.innerHTML = `
        <strong>${escapeHtml(name)}</strong>
        <span class="key-badge ${set ? 'key-set' : 'key-missing'}">${set ? '✓ ' + escapeHtml(preview) : '✗ 未设置'}</span>
        <input type="password" placeholder="输入 ${escapeHtml(envVar)}" style="max-width:240px">
        <button class="btn btn-sm btn-primary">设置</button>`;
      const input = row.querySelector('input')!;
      row.querySelector('button')!.addEventListener('click', () => {
        void applySetKey(envVar, input);
      });
      section.appendChild(row);
    }
    renderProviderRows();
  } catch {
    // Backend offline: keys section shows a hint.
    $('apikey-section').innerHTML =
      '<div class="key-row"><span class="key-badge key-missing">代理后端未连接 — Key 状态不可用</span></div>';
  }
}

async function applySetKey(envVar: string, input: HTMLInputElement): Promise<void> {
  if (!input.value) return;
  try {
    await setKey(envVar, input.value);
    input.value = '';
    modelCache.clear();
    showToast('Key 已设置');
    await loadKeyStatus();
  } catch (e) {
    showSaveError(e);
  }
}

function gatherProvidersFromUI(): Record<string, ProviderConfig> {
  const providers: Record<string, Record<string, string>> = {};
  document.querySelectorAll<HTMLElement>('.provider-row').forEach((row) => {
    const name = row.dataset.provider!;
    providers[name] = {};
    row.querySelectorAll<HTMLInputElement>('input[data-field]').forEach((inp) => {
      providers[name][inp.dataset.field!] = inp.value.trim();
    });
  });
  return providers as unknown as Record<string, ProviderConfig>;
}

export async function saveConfigUI(): Promise<boolean> {
  hideSaveError();

  const providers = gatherProvidersFromUI();

  // Client-side validation (same rules as the backend)
  for (const [name, cfg] of Object.entries(providers)) {
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(name)) {
      showSaveError({
        errInfo: {
          code: 'INVALID_PROVIDER_NAME',
          message: `Provider 名称 '${name}' 不合法`,
          advice: '名称只能包含小写字母、数字、下划线和连字符，且以字母或数字开头',
        },
      });
      return false;
    }
    if (!(cfg.base_url || '').startsWith('http://') && !(cfg.base_url || '').startsWith('https://')) {
      showSaveError({
        errInfo: {
          code: 'INVALID_BASE_URL',
          message: `Provider '${name}' 的 base_url 必须以 http:// 或 https:// 开头`,
          advice: '示例: https://api.example.com/v1',
        },
      });
      return false;
    }
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(cfg.api_key_env || '')) {
      showSaveError({
        errInfo: {
          code: 'INVALID_ENV_VAR',
          message: `Provider '${name}' 的 API Key 环境变量名不合法`,
          advice: '只能包含字母、数字、下划线，且以字母或下划线开头',
        },
      });
      return false;
    }
  }

  const routers = {
    chunking: (document.getElementById('chunk-provider') as HTMLSelectElement).value,
    peeling: (document.getElementById('peel-provider') as HTMLSelectElement).value,
    skillEngine: (document.getElementById('skill-provider') as HTMLSelectElement).value,
  };

  const pdf = {
    request_interval: parseFloat(val('request-interval')) || 0.3,
    page_limit: parseInt(val('pdf-page-limit'), 10) || 200,
    chunk_merge_threshold: parseInt(val('pdf-merge-threshold'), 10) || 5000,
    chunk_min_threshold: parseInt(val('pdf-min-threshold'), 10) || 1000,
  };

  const mineru = {
    api_mode: mineruMode,
    language: mineruMode === 'local' ? val('mu-language') : val('mu-language-remote'),
    local: {
      base_url: val('mu-local-url'),
      backend: val('mu-local-backend'),
      parse_method: val('mu-local-parse'),
      formula_enable: checked('mu-local-formula'),
      table_enable: checked('mu-local-table'),
    },
    remote: {
      model_version: val('mu-remote-model'),
      page_ranges: val('mu-remote-pages'),
      is_ocr: checked('mu-remote-ocr'),
      enable_formula: checked('mu-remote-formula'),
      enable_table: checked('mu-remote-table'),
      extra_formats: [],
    },
  };

  const cfg = sdk().getConfig();
  cfg.providers = providers;
  cfg.routers = routers;
  Object.assign(cfg.pdf, pdf);
  Object.assign(cfg.mineru, mineru);

  const issues = validateConfig(cfg);
  if (issues.length > 0) {
    showSaveError({ errInfo: issues[0] });
    return false;
  }

  sdk().configure(cfg);
  showToast('配置已保存（浏览器本地）');
  await loadConfigUI();
  return true;
}

async function importFromServer(): Promise<void> {
  hideSaveError();
  try {
    const data = await getProviders();
    if (!data.ok || !data.providers || Object.keys(data.providers).length === 0) {
      showSaveError({
        errInfo: { code: 'NO_PROVIDER', message: '服务端 settings.yaml 中没有 Provider', advice: '请先在服务端配置 llm.providers' },
      });
      return;
    }
    const routers = {
      chunking: data.routers?.chunking_provider || data.routers?.chunking || '',
      peeling: data.routers?.peeling_provider || data.routers?.peeling || '',
      skillEngine: data.routers?.skill_engine_provider || data.routers?.skillEngine || '',
    };
    sdk().configure({
      providers: data.providers as unknown as Record<string, ProviderConfig>,
      routers,
    });
    await loadConfigUI();
    showToast(`已导入 ${Object.keys(data.providers).length} 个 Provider`);
  } catch (e) {
    showSaveError(e);
  }
}

async function syncToServer(): Promise<void> {
  hideSaveError();
  const providers = gatherProvidersFromUI();
  const routers = {
    chunking: (document.getElementById('chunk-provider') as HTMLSelectElement).value,
    peeling: (document.getElementById('peel-provider') as HTMLSelectElement).value,
    skillEngine: (document.getElementById('skill-provider') as HTMLSelectElement).value,
  };
  try {
    await syncProviders(providers as unknown as Record<string, Record<string, string>>, routers);
    showToast('已同步到服务端 settings.yaml');
  } catch (e) {
    showSaveError(e);
  }
}

// ── Wire up ───────────────────────────────────────────────────────

export function setupConfigTab(sdkInstance: Pdf2Skill): void {
  pdf2skill = sdkInstance;
  $('btn-add-provider').addEventListener('click', () => {
    $('provider-form').classList.add('active');
    $('btn-add-provider').style.display = 'none';
    $('new-provider-name').focus();
  });
  $('btn-cancel-new-provider').addEventListener('click', hideAddProviderForm);
  $('btn-save-new-provider').addEventListener('click', () => void saveNewProvider());
  $('btn-save-config').addEventListener('click', () => void saveConfigUI());
  $('btn-import-providers').addEventListener('click', () => void importFromServer());
  $('btn-sync-providers').addEventListener('click', () => void syncToServer());
  $('mode-local').addEventListener('click', () => setMinerUMode('local'));
  $('mode-remote').addEventListener('click', () => setMinerUMode('remote'));
  $('btn-set-mu-key').addEventListener('click', () => {
    void applySetKey('MINERU_API_KEY', $('mu-api-key') as HTMLInputElement);
  });
}
