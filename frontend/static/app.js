/* ════════════════════════════════════════════════════════════════
   PDF2Skill 前端逻辑
   ════════════════════════════════════════════════════════════════ */

// ── Globals ──────────────────────────────────────────────────────
let configData = null;        // raw GET /api/config response
let state = null;             // configData.settings
let providerKeys = {};        // configData.providers_masked (key status per provider)
let mineruKeyStatus = {};     // configData.mineru_key_status
let currentJobId = null;
let logPollTimer = null;
let mineruMode = 'local';
const modelCache = new Map(); // provider name -> string[] (session cache)

// Pipeline stages: routing card + display + settings.yaml keys
const STAGES = [
  { key: 'chunk', selId: 'chunk-provider', stageKey: 'chunking', routerKey: 'chunking_provider', displayId: 'chunk-model-display' },
  { key: 'peel', selId: 'peel-provider', stageKey: 'peeling', routerKey: 'peeling_provider', displayId: 'peel-model-display' },
  { key: 'skill', selId: 'skill-provider', stageKey: 'skill_engine', routerKey: 'skill_engine_provider', displayId: 'skill-model-display' },
];
const MODEL_KEYS = ['chunking_model', 'peeling_model', 'skill_engine_model'];

// ── Small helpers ────────────────────────────────────────────────
function escapeHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = '✓ ' + msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2000);
}

/* Fetch wrapper: on HTTP error, parses the server's {code,message,advice}
   body (if any) and attaches it as e.errInfo for structured display. */
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!res.ok) {
    const e = new Error('HTTP ' + res.status);
    try {
      const body = JSON.parse(await res.text());
      e.errInfo = body.detail?.error || body.error || { code: 'HTTP_' + res.status, message: '请求失败 (HTTP ' + res.status + ')', advice: '' };
    } catch {
      e.errInfo = { code: 'HTTP_' + res.status, message: '请求失败 (HTTP ' + res.status + ')', advice: '' };
    }
    throw e;
  }
  return res.json();
}

function errInfoOf(e) {
  return e && e.errInfo ? e.errInfo : { code: 'UNKNOWN', message: (e && e.message) || String(e), advice: '' };
}

// ── Tabs & navigation ────────────────────────────────────────────
function switchTab(tabId) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabId));
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('active', p.id === 'panel-' + tabId));
  if (tabId === 'outputs') loadOutputs().catch(() => {});
}

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => switchTab(tab.dataset.tab));
});

/* Jump from the tutorial to a panel section. Must stay global (inline onclick). */
function goTo(tabId, anchorId) {
  switchTab(tabId);
  if (anchorId) {
    const el = document.getElementById(anchorId);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// ── Error banner (config panel) ──────────────────────────────────
function showSaveError(e) {
  const info = errInfoOf(e);
  const box = document.getElementById('config-error');
  box.innerHTML =
    `<div class="error-title">⚠ ${escapeHtml(info.message)}</div>` +
    (info.advice ? `<div class="error-advice">💡 ${escapeHtml(info.advice)}</div>` : '');
  box.classList.add('active');
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function hideSaveError() {
  const box = document.getElementById('config-error');
  box.classList.remove('active');
  box.innerHTML = '';
}

// ── Provider rows ────────────────────────────────────────────────
function renderProviderRows() {
  const container = document.getElementById('provider-rows');
  const providers = state?.llm?.providers || {};
  container.innerHTML = Object.keys(providers).map(name => {
    const cfg = providers[name];
    const keyInfo = providerKeys[name] || {};
    const badge = keyInfo.api_key_set
      ? `<span class="key-badge key-set">✓ ${escapeHtml(keyInfo.api_key_preview || '已设置')}</span>`
      : `<span class="key-badge key-missing">✗ 未设置 Key</span>`;
    const modelFields = MODEL_KEYS.map(k =>
      `<div class="pf-field"><label>${escapeHtml(k.replace('_model', ''))} 模型</label>` +
      `<input type="text" data-field="${k}" list="dl-${name}-${k}" value="${escapeHtml(cfg[k] || '')}" placeholder="未配置">` +
      `<datalist id="dl-${name}-${k}"></datalist></div>`
    ).join('');
    return `
    <div class="provider-row" data-provider="${escapeHtml(name)}">
      <div class="provider-head">
        <span class="provider-name">${escapeHtml(name)}</span>
        ${badge}
        <div class="provider-head-actions">
          <button class="btn btn-ghost btn-sm" onclick="fetchModels('${escapeHtml(name)}')">获取模型列表</button>
          <button class="btn btn-danger btn-sm" onclick="deleteProvider('${escapeHtml(name)}')">删除</button>
        </div>
      </div>
      <div class="provider-fields">
        <div class="pf-field pf-wide"><label>base_url</label><input type="text" data-field="base_url" value="${escapeHtml(cfg.base_url || '')}" placeholder="https://api.example.com/v1"></div>
        <div class="pf-field pf-wide"><label>API Key 环境变量名</label><input type="text" data-field="api_key_env" value="${escapeHtml(cfg.api_key_env || '')}" placeholder="EXAMPLE_API_KEY"></div>
        <div class="pf-models">${modelFields}</div>
      </div>
      <div class="model-error" id="model-error-${escapeHtml(name)}" style="display:none"></div>
    </div>`;
  }).join('');

  // Re-apply cached model lists (rows are re-rendered after loadConfig)
  for (const [name, models] of modelCache) renderDatalists(name, models);
}

function renderDatalists(providerName, models) {
  for (const k of MODEL_KEYS) {
    const dl = document.getElementById(`dl-${providerName}-${k}`);
    if (dl) dl.innerHTML = models.map(m => `<option value="${escapeHtml(m)}"></option>`).join('');
  }
}

/* Current model for a provider+stage: unsaved row edit first, saved state fallback. */
function providerModel(providerName, stageKey) {
  if (!providerName) return '';
  const rowInput = document.querySelector(`.provider-row[data-provider="${providerName}"] input[data-field="${stageKey}_model"]`);
  if (rowInput && rowInput.value.trim() !== '') return rowInput.value.trim();
  return state?.llm?.providers?.[providerName]?.[stageKey + '_model'] || '';
}

function updateStageDisplay(s) {
  const p = document.getElementById(s.selId).value;
  const model = providerModel(p, s.stageKey);
  document.getElementById(s.displayId).textContent = model || '未配置模型';
}

// ── Model list fetch ─────────────────────────────────────────────
async function fetchModels(providerName) {
  const row = document.querySelector(`.provider-row[data-provider="${providerName}"]`);
  if (!row) return;
  const btn = row.querySelector('.provider-head-actions .btn-ghost');
  const errBox = document.getElementById(`model-error-${providerName}`);

  btn.classList.add('loading');
  btn.disabled = true;
  errBox.style.display = 'none';

  try {
    const data = await api(`/api/models?provider=${encodeURIComponent(providerName)}`);
    if (!data.ok) {
      errBox.innerHTML =
        `<div class="model-error-title">${escapeHtml(data.error.message)}</div>` +
        (data.error.advice ? `<div class="model-error-advice">💡 ${escapeHtml(data.error.advice)}</div>` : '');
      errBox.style.display = 'block';
      return;
    }
    modelCache.set(providerName, data.models);
    renderDatalists(providerName, data.models);
    showToast(`已获取 ${data.count} 个模型`);
  } catch (e) {
    const info = errInfoOf(e);
    errBox.innerHTML =
      `<div class="model-error-title">${escapeHtml(info.message)}</div>` +
      (info.advice ? `<div class="model-error-advice">💡 ${escapeHtml(info.advice)}</div>` : '');
    errBox.style.display = 'block';
  } finally {
    btn.classList.remove('loading');
    btn.disabled = false;
  }
}

// ── Provider add / delete ────────────────────────────────────────
function showAddProviderForm() {
  document.getElementById('provider-form').classList.add('active');
  document.getElementById('btn-add-provider').style.display = 'none';
  document.getElementById('new-provider-name').focus();
}

function hideAddProviderForm() {
  document.getElementById('provider-form').classList.remove('active');
  document.getElementById('btn-add-provider').style.display = '';
  ['new-provider-name', 'new-provider-env', 'new-provider-base-url',
   'new-provider-chunking', 'new-provider-peeling', 'new-provider-skill'].forEach(id => {
    document.getElementById(id).value = '';
  });
  document.getElementById('new-provider-error').classList.remove('active');
}

function showFormError(msg) {
  const el = document.getElementById('new-provider-error');
  el.textContent = msg;
  el.classList.add('active');
}

async function saveNewProvider() {
  hideSaveError();
  const name = document.getElementById('new-provider-name').value.trim();
  const envVar = document.getElementById('new-provider-env').value.trim();
  const base = document.getElementById('new-provider-base-url').value.trim();

  if (!/^[a-z0-9][a-z0-9_-]*$/.test(name)) {
    return showFormError('名称不合法：只能包含小写字母、数字、下划线和连字符，且以字母或数字开头');
  }
  if (state?.llm?.providers && name in state.llm.providers) {
    return showFormError(`Provider '${name}' 已存在，可直接在下方编辑`);
  }
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(envVar)) {
    return showFormError('API Key 环境变量名不合法：只能包含字母、数字、下划线，且以字母或下划线开头');
  }
  if (!base.startsWith('http://') && !base.startsWith('https://')) {
    return showFormError('base_url 必须以 http:// 或 https:// 开头');
  }

  const extraProvider = {
    [name]: {
      base_url: base,
      api_key_env: envVar,
      chunking_model: document.getElementById('new-provider-chunking').value.trim(),
      peeling_model: document.getElementById('new-provider-peeling').value.trim(),
      skill_engine_model: document.getElementById('new-provider-skill').value.trim(),
    },
  };
  const ok = await saveConfig({ extraProvider });
  if (ok) hideAddProviderForm();
}

async function deleteProvider(name) {
  if (!confirm(`确定删除 Provider '${name}'？删除后不可恢复。`)) return;
  const ok = await saveConfig({ deleteProviders: [name] });
  if (ok) showToast(`已删除 Provider ${name}`);
}

// ── Config load / save ───────────────────────────────────────────
async function loadConfig() {
  const data = await api('/api/config');
  configData = data;
  state = data.settings;
  providerKeys = data.providers_masked || {};
  mineruKeyStatus = data.mineru_key_status || {};

  // Stage routing selects
  STAGES.forEach(s => {
    const sel = document.getElementById(s.selId);
    sel.innerHTML = Object.keys(state.llm?.providers || {})
      .map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('');
    const routed = state.llm?.routers?.[s.routerKey];
    sel.value = routed && state.llm.providers?.[routed] ? routed : (sel.options.length ? sel.options[0].value : '');
    sel.onchange = () => updateStageDisplay(s);
    updateStageDisplay(s);
  });

  // PDF params
  const pdf = state.pdf || {};
  document.getElementById('request-interval').value = pdf.request_interval ?? 1.0;
  document.getElementById('pdf-page-limit').value = pdf.page_limit ?? 200;
  document.getElementById('pdf-merge-threshold').value = pdf.chunk_merge_threshold ?? 5000;
  document.getElementById('pdf-min-threshold').value = pdf.chunk_min_threshold ?? 1000;

  // MinerU config
  const mu = state.mineru || {};
  mineruMode = mu.api_mode || 'local';
  setMinerUMode(mineruMode);
  document.getElementById('mu-local-url').value = mu.local?.base_url || 'http://localhost:7860';
  document.getElementById('mu-local-backend').value = mu.local?.backend || 'vlm-auto-engine';
  document.getElementById('mu-local-parse').value = mu.local?.parse_method || 'auto';
  document.getElementById('mu-local-formula').checked = mu.local?.formula_enable !== false;
  document.getElementById('mu-local-table').checked = mu.local?.table_enable !== false;
  document.getElementById('mu-remote-model').value = mu.remote?.model_version || 'vlm';
  document.getElementById('mu-remote-upload').value = mu.remote?.upload_mode || 'file';
  document.getElementById('mu-remote-pages').value = mu.remote?.page_ranges || '';
  document.getElementById('mu-remote-ocr').checked = mu.remote?.is_ocr === true;
  document.getElementById('mu-remote-formula').checked = mu.remote?.enable_formula !== false;
  document.getElementById('mu-remote-table').checked = mu.remote?.enable_table !== false;

  const muLang = mu.language || 'ch';
  document.getElementById('mu-language').value = muLang;
  document.getElementById('mu-language-remote').value = muLang;

  const extraFmts = mu.remote?.extra_formats || [];
  document.getElementById('mu-extra-docx').checked = extraFmts.includes('docx');
  document.getElementById('mu-extra-html').checked = extraFmts.includes('html');
  document.getElementById('mu-extra-latex').checked = extraFmts.includes('latex');

  // MinerU API key badge
  const muKeyBadge = document.getElementById('mu-key-badge');
  if (mineruKeyStatus.api_key_set) {
    muKeyBadge.className = 'key-badge key-set';
    muKeyBadge.textContent = '✓ ' + (mineruKeyStatus.api_key_preview || '已设置');
  } else {
    muKeyBadge.className = 'key-badge key-missing';
    muKeyBadge.textContent = '✗ 未设置';
  }

  // API keys section
  const section = document.getElementById('apikey-section');
  section.innerHTML = '';
  for (const [name, info] of Object.entries(providerKeys)) {
    const envVar = info.api_key_env || `${name.toUpperCase()}_API_KEY`;
    const set = info.api_key_set;
    const preview = info.api_key_preview || '';
    section.innerHTML += `
      <div class="key-row">
        <strong>${escapeHtml(name)}</strong>
        <span class="key-badge ${set ? 'key-set' : 'key-missing'}">${set ? '✓ ' + escapeHtml(preview) : '✗ 未设置'}</span>
        <input type="password" id="key-${escapeHtml(name)}" placeholder="输入 ${escapeHtml(envVar)}" style="max-width:240px">
        <button class="btn btn-sm btn-primary" onclick="setKey('${escapeHtml(envVar)}','key-${escapeHtml(name)}')">设置</button>
      </div>`;
  }

  renderProviderRows();
}

/* Save everything (providers + routers + pdf + mineru).
   opts.extraProvider / opts.deleteProviders merge into the same commit. */
async function saveConfig(opts = {}) {
  hideSaveError();

  // Gather providers from the management rows
  const providers = {};
  document.querySelectorAll('.provider-row').forEach(row => {
    const name = row.dataset.provider;
    providers[name] = {};
    row.querySelectorAll('input[data-field]').forEach(inp => {
      providers[name][inp.dataset.field] = inp.value.trim();
    });
  });

  if (opts.extraProvider) Object.assign(providers, opts.extraProvider);

  // Client-side validation (mirrors backend rules)
  for (const [name, cfg] of Object.entries(providers)) {
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(name)) {
      return showSaveError({ errInfo: { code: 'INVALID_PROVIDER_NAME', message: `Provider 名称 '${name}' 不合法`, advice: '名称只能包含小写字母、数字、下划线和连字符，且以字母或数字开头' } });
    }
    if (!(cfg.base_url || '').startsWith('http://') && !(cfg.base_url || '').startsWith('https://')) {
      return showSaveError({ errInfo: { code: 'INVALID_BASE_URL', message: `Provider '${name}' 的 base_url 必须以 http:// 或 https:// 开头`, advice: '示例: https://api.example.com/v1' } });
    }
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(cfg.api_key_env || '')) {
      return showSaveError({ errInfo: { code: 'INVALID_ENV_VAR', message: `Provider '${name}' 的 API Key 环境变量名不合法`, advice: '只能包含字母、数字、下划线，且以字母或下划线开头' } });
    }
  }

  // Stage routers
  const routers = {};
  STAGES.forEach(s => { routers[s.routerKey] = document.getElementById(s.selId).value; });

  // PDF params
  const pdf = {
    request_interval: parseFloat(document.getElementById('request-interval').value) || 1.0,
    page_limit: parseInt(document.getElementById('pdf-page-limit').value) || 200,
    chunk_merge_threshold: parseInt(document.getElementById('pdf-merge-threshold').value) || 5000,
    chunk_min_threshold: parseInt(document.getElementById('pdf-min-threshold').value) || 1000,
  };

  // MinerU config
  const extraFormats = [];
  if (document.getElementById('mu-extra-docx').checked) extraFormats.push('docx');
  if (document.getElementById('mu-extra-html').checked) extraFormats.push('html');
  if (document.getElementById('mu-extra-latex').checked) extraFormats.push('latex');
  const mineru = {
    api_mode: mineruMode,
    language: mineruMode === 'local' ? document.getElementById('mu-language').value : document.getElementById('mu-language-remote').value,
    local: {
      base_url: document.getElementById('mu-local-url').value,
      backend: document.getElementById('mu-local-backend').value,
      parse_method: document.getElementById('mu-local-parse').value,
      formula_enable: document.getElementById('mu-local-formula').checked,
      table_enable: document.getElementById('mu-local-table').checked,
    },
    remote: {
      model_version: document.getElementById('mu-remote-model').value,
      upload_mode: document.getElementById('mu-remote-upload').value,
      page_ranges: document.getElementById('mu-remote-pages').value,
      is_ocr: document.getElementById('mu-remote-ocr').checked,
      enable_formula: document.getElementById('mu-remote-formula').checked,
      enable_table: document.getElementById('mu-remote-table').checked,
      extra_formats: extraFormats,
    },
  };

  const body = { providers, routers, pdf, mineru };
  if (opts.deleteProviders && opts.deleteProviders.length) {
    body.delete_providers = opts.deleteProviders;
  }

  try {
    await api('/api/config', { method: 'POST', body: JSON.stringify(body) });
    showToast('配置已保存');
    await loadConfig();
    return true;
  } catch (e) {
    showSaveError(e);
    return false;
  }
}

async function setKey(envVar, inputId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const val = input.value;
  if (!val) return;
  try {
    await api('/api/apikey', { method: 'POST', body: JSON.stringify({ env_var: envVar, value: val }) });
    input.value = '';
    modelCache.clear(); // key changed -> cached model lists may no longer apply
    showToast('Key 已设置');
    await loadConfig();
  } catch (e) {
    showSaveError(e);
  }
}

// ── Inputs ───────────────────────────────────────────────────────
async function loadInputs() {
  const data = await api('/api/inputs');
  const sel = document.getElementById('input-file');
  sel.innerHTML = '<option value="">— 选择文件 —</option>';
  for (const f of data.files) {
    const size = (f.size / 1024 / 1024).toFixed(1);
    sel.innerHTML += `<option value="${escapeHtml(f.relative)}">${escapeHtml(f.name)} (${size}MB)</option>`;
  }
  const btnDelete = document.getElementById('btn-delete-input');
  sel.onchange = () => {
    const opt = sel.value;
    btnDelete.style.display = opt ? 'inline-flex' : 'none';
    if (opt) {
      document.getElementById('input-mode').value = opt.toLowerCase().endsWith('.md') ? 'markdown' : 'pdf';
      const stem = opt.replace(/\.[^.]+$/, '');
      document.getElementById('output-dir').value = 'outputs/' + stem;
    }
    loadCheckpoint();
  };
}

// ── Upload ───────────────────────────────────────────────────────
function setupUploadZone() {
  const zone = document.getElementById('dropzone');
  if (!zone) return;
  const input = document.getElementById('upload-input');
  zone.addEventListener('click', () => input.click());
  input.addEventListener('change', () => { if (input.files.length > 0) uploadFile(input.files[0]); });
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault(); zone.classList.remove('drag-over');
    if (e.dataTransfer.files.length > 0) uploadFile(e.dataTransfer.files[0]);
  });
}

function uploadFile(file) {
  if (!file.name.toLowerCase().endsWith('.pdf')) { showUploadError('FORMAT_EXTENSION', '文件扩展名不是 .pdf', '请选择 .pdf 后缀的文件'); return; }
  if (file.size === 0) { showUploadError('SIZE_EMPTY', '文件为空', '请上传有效的 PDF 文件'); return; }
  if (file.size > 200 * 1024 * 1024) { showUploadError('SIZE_EXCEED', `文件大小超过 200MB（当前 ${(file.size / 1024 / 1024).toFixed(1)}MB）`, '请拆分文件后重新上传'); return; }

  const prog = document.getElementById('upload-progress');
  const fill = document.getElementById('upload-progress-fill');
  const detail = document.getElementById('upload-progress-detail');
  prog.style.display = 'block'; fill.style.width = '0%'; detail.textContent = '上传中...';
  hideUploadError();

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) { const pct = Math.round((e.loaded / e.total) * 100); fill.style.width = pct + '%'; detail.textContent = `上传中 ${pct}%`; }
  };
  xhr.onload = () => {
    if (xhr.status === 200) {
      fill.style.width = '100%'; detail.textContent = '上传完成';
      setTimeout(() => { prog.style.display = 'none'; }, 1500);
      const result = JSON.parse(xhr.responseText);
      loadInputs().then(() => {
        const sel = document.getElementById('input-file');
        sel.value = result.file.relative;
        sel.dispatchEvent(new Event('change'));
      }).catch((e) => showUploadError('REFRESH_FAILED', '上传成功但刷新列表失败', errInfoOf(e).message));
      showToast('文件上传成功');
    } else {
      prog.style.display = 'none';
      try { const err = JSON.parse(xhr.responseText); const d = err.detail || err; showUploadError(d.error?.code || 'UNKNOWN', d.error?.message || '上传失败', d.error?.advice || ''); }
      catch { showUploadError('UNKNOWN', '上传失败', '请稍后重试'); }
    }
  };
  xhr.onerror = () => { prog.style.display = 'none'; showUploadError('NETWORK', '网络错误', '请检查网络连接后重试'); };
  const formData = new FormData(); formData.append('file', file); xhr.send(formData);
}

function showUploadError(code, message, advice) {
  document.getElementById('upload-error-title').textContent = message;
  document.getElementById('upload-error-advice').textContent = advice || '';
  document.getElementById('upload-error').style.display = 'block';
}
function hideUploadError() { document.getElementById('upload-error').style.display = 'none'; }

async function deleteInput() {
  const sel = document.getElementById('input-file');
  const val = sel.value;
  if (!val) return;
  if (!confirm(`确定删除 inputs/${val}？`)) return;
  try {
    await api(`/api/inputs/${encodeURIComponent(val)}`, { method: 'DELETE' });
    await loadInputs();
    document.getElementById('btn-delete-input').style.display = 'none';
    showToast('文件已删除');
  } catch (e) {
    const info = errInfoOf(e);
    alert('删除失败: ' + info.message + (info.advice ? '\n' + info.advice : ''));
  }
}

// ── Pipeline ─────────────────────────────────────────────────────
// 4-stage flow served by backend /api/status -> stages array.
const STAGE_KEYS = ['mineru', 'chunking', 'peeling', 'skill'];

function renderStages(stages) {
  if (!stages || !stages.length) return;
  const progressEl = document.getElementById('pipeline-progress');
  progressEl.classList.add('visible');

  const statusOf = {};
  const detailOf = {};
  stages.forEach(s => {
    statusOf[s.key] = s.status || 'pending';
    detailOf[s.key] = s.detail || '';
  });

  for (let i = 0; i < STAGE_KEYS.length; i++) {
    const key = STAGE_KEYS[i];
    const stepEl = document.getElementById('step-' + key);
    const status = statusOf[key] || 'pending';
    stepEl.classList.remove('pending', 'active', 'done', 'failed');
    stepEl.classList.add(status);
    const detailEl = document.getElementById('step-' + key + '-detail');
    if (detailEl) detailEl.textContent = detailOf[key] || '';

    // connector (conn-{i+1}) is "done" when both adjacent stages are done
    const connEl = document.getElementById('conn-' + (i + 1));
    if (connEl) {
      const prev = STAGE_KEYS[i];
      const next = STAGE_KEYS[i + 1];
      const prevOk = prev && (statusOf[prev] === 'done' || statusOf[prev] === 'failed');
      const nextOk = next && (statusOf[next] === 'done' || statusOf[next] === 'failed');
      connEl.classList.toggle('done', !!(prevOk && nextOk));
    }
  }
}

function resetPipelineProgress() {
  const progressEl = document.getElementById('pipeline-progress');
  progressEl.classList.remove('visible');
  STAGE_KEYS.forEach((key, i) => {
    const stepEl = document.getElementById('step-' + key);
    stepEl.classList.remove('active', 'done', 'failed');
    stepEl.classList.add('pending');
    const detailEl = document.getElementById('step-' + key + '-detail');
    if (detailEl) detailEl.textContent = '';
    const connEl = document.getElementById('conn-' + (i + 1));
    if (connEl) connEl.classList.remove('done');
  });
}

// Restart-mode select value -> from_stage (empty for continue/restart)
const RESTART_MODE_TO_STAGE = {
  'from-pdf': 'pdf_conversion',
  'from-chunk': 'llm_chunking',
  'from-tree': 'tree_merging',
};
// stage key -> short English label for the checkpoint badge display
// (consistent with the 4-stage progress dots: MinerU/Chunking/Peeling)
const CKPT_STAGE_LABELS = {
  pdf_conversion: 'MinerU',
  llm_chunking: 'Chunking',
  tree_merging: 'Peeling',
};

async function loadCheckpoint() {
  const dirEl = document.getElementById('output-dir');
  const statusEl = document.getElementById('checkpoint-status');
  if (!dirEl || !statusEl) return;
  const outputDir = dirEl.value.trim();
  if (!outputDir) {
    statusEl.innerHTML = '<span class="ckpt-empty">— 无历史记录 —</span>';
    return;
  }
  try {
    const data = await api('/api/checkpoint?output_dir=' + encodeURIComponent(outputDir));
    if (!data.exists || !data.completed_stages?.length) {
      statusEl.innerHTML = '<span class="ckpt-empty">— 无历史记录 —</span>';
      return;
    }
    const badges = data.completed_stages
      .map(s => `<span class="ckpt-badge ckpt-done">✓ ${escapeHtml(CKPT_STAGE_LABELS[s] || s)}</span>`)
      .join('');
    statusEl.innerHTML = badges;
  } catch (e) {
    statusEl.innerHTML = '<span class="ckpt-empty">— 历史记录读取失败 —</span>';
  }
}

async function startPipeline() {
  const inputFile = document.getElementById('input-file').value;
  if (!inputFile) { alert('请选择输入文件'); return; }

  const restartMode = document.getElementById('restart-mode').value;
  const body = {
    input_file: inputFile,
    mode: document.getElementById('input-mode').value,
    output_dir: document.getElementById('output-dir').value,
    chunk_provider: document.getElementById('chunk-provider').value,
    peel_provider: document.getElementById('peel-provider').value,
    skill_provider: document.getElementById('skill-provider').value,
    chunk_model: providerModel(document.getElementById('chunk-provider').value, 'chunking'),
    peel_model: providerModel(document.getElementById('peel-provider').value, 'peeling'),
    skill_model: providerModel(document.getElementById('skill-provider').value, 'skill_engine'),
    request_interval: document.getElementById('request-interval').value,
    restart: restartMode === 'restart',
    from_stage: RESTART_MODE_TO_STAGE[restartMode] || '',
  };

  try {
    const res = await api('/api/run', { method: 'POST', body: JSON.stringify(body) });
    currentJobId = res.job_id;
    document.getElementById('btn-start').disabled = true;
    document.getElementById('btn-stop').style.display = 'inline-flex';
    document.getElementById('run-status').innerHTML = '<span class="status status-running"><span class="status-dot"></span>运行中</span>';
    document.getElementById('log-box').textContent = '';
    document.getElementById('mineru-error').classList.remove('active');
    resetPipelineProgress();
    pollLog();
  } catch (e) {
    const info = errInfoOf(e);
    alert('启动失败: ' + info.message + (info.advice ? '\n' + info.advice : ''));
  }
}

async function stopPipeline() {
  if (!currentJobId) return;
  try {
    await api('/api/stop', { method: 'POST', body: JSON.stringify({ job_id: currentJobId }) });
  } catch (e) {
    const info = errInfoOf(e);
    alert('停止失败: ' + info.message);
  }
  finishJob('stopped');
}

async function pollLog() {
  if (!currentJobId) return;
  try {
    const data = await api(`/api/status?job_id=${currentJobId}`);
    const logBox = document.getElementById('log-box');
    const logText = data.log || '';

    const placeholder = logBox.querySelector('.log-placeholder');
    if (placeholder) placeholder.remove();

    logBox.textContent = logText || '(无日志)';
    logBox.scrollTop = logBox.scrollHeight;

    // Structured 4-stage progress comes from the backend (data.stages)
    if (data.stages) {
      renderStages(data.stages);
    }
    if (data.mineru_error) {
      showMinerUError(data.mineru_error);
    }

    if (data.status === 'running') {
      logPollTimer = setTimeout(pollLog, 2000);
    } else {
      finishJob(data.status, data.mineru_error);
    }
  } catch (e) {
    logPollTimer = setTimeout(pollLog, 3000);
  }
}

function finishJob(status, mineruError) {
  clearTimeout(logPollTimer);
  document.getElementById('btn-start').disabled = false;
  document.getElementById('btn-stop').style.display = 'none';

  if (mineruError) {
    showMinerUError(mineruError);
  }

  // Stage dots already reflect the terminal state via the last renderStages()
  // call (backend converges status to done/failed/stopped before returning).

  const cls = status === 'done' ? 'status-done' : status === 'failed' ? 'status-failed' : 'status-running';
  const label = status === 'done' ? '完成' : status === 'failed' ? '失败' : status;
  const dot = status === 'done' ? '' : status === 'failed' ? '' : '<span class="status-dot"></span>';
  document.getElementById('run-status').innerHTML = `<span class="status ${cls}">${dot}${label}</span>`;
}

// ── Outputs ──────────────────────────────────────────────────────
async function loadOutputs() {
  const data = await api('/api/outputs');
  const treeEl = document.getElementById('output-tree');
  treeEl.innerHTML = '';
  if (!data.tree?.children?.length) {
    treeEl.innerHTML = `
      <div class="outputs-empty">
        <div class="empty-icon">📂</div>
        <div class="empty-title">暂无输出</div>
        <div class="empty-desc">运行 Pipeline 后，输出文件将显示在这里</div>
      </div>`;
    return;
  }
  renderTree(data.tree, treeEl, '');
}

function renderTree(node, parentEl, pathPrefix) {
  if (!node.children) return;
  for (const child of node.children) {
    const childPath = pathPrefix ? pathPrefix + '/' + child.name.replace('/', '') : child.name.replace('/', '');
    const div = document.createElement('div');

    if (child.type === 'dir') {
      const item = document.createElement('div');
      item.className = 'tree-item';
      const label = child.name;
      const countLabel = child.count != null ? `<span class="tree-size">${child.count} files</span>` : '';
      item.innerHTML = `<span class="tree-icon">📁</span><span class="tree-name">${escapeHtml(label)}</span>${countLabel}`;
      div.appendChild(item);

      const childrenDiv = document.createElement('div');
      childrenDiv.className = 'tree-children';
      childrenDiv.style.display = 'none';
      div.appendChild(childrenDiv);

      item.addEventListener('click', () => {
        const open = childrenDiv.style.display !== 'none';
        childrenDiv.style.display = open ? 'none' : 'block';
        if (!open && !childrenDiv.hasChildNodes()) {
          renderTree(child, childrenDiv, childPath);
        }
      });
    } else {
      const item = document.createElement('div');
      item.className = 'tree-item';
      item.innerHTML = `<span class="tree-icon">📄</span><span class="tree-name">${escapeHtml(child.name)}</span><span class="tree-size">${child.size ? (child.size / 1024).toFixed(1) + 'KB' : ''}</span>`;
      item.addEventListener('click', () => previewFile(childPath));
      div.appendChild(item);
    }
    parentEl.appendChild(div);
  }
}

async function previewFile(path) {
  const viewer = document.getElementById('file-viewer');
  try {
    const res = await fetch(`/api/outputs/file?path=${encodeURIComponent(path)}`);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const ct = res.headers.get('content-type') || '';
    if (ct.startsWith('image/')) {
      viewer.style.display = 'block';
      viewer.innerHTML = `<img src="${res.url}" style="max-width:100%">`;
    } else {
      const data = await res.json();
      viewer.style.display = 'block';
      if (data.type === 'text') {
        viewer.textContent = data.content;
      } else {
        viewer.textContent = `(Binary file, ${data.size} bytes)`;
      }
    }
  } catch (e) {
    viewer.style.display = 'block';
    viewer.textContent = 'Error: ' + e.message;
  }
}

// ── MinerU helpers ────────────────────────────────────────────────
function setMinerUMode(mode) {
  mineruMode = mode;
  document.getElementById('mode-local').classList.toggle('active', mode === 'local');
  document.getElementById('mode-remote').classList.toggle('active', mode === 'remote');
  document.getElementById('mineru-local').classList.toggle('active', mode === 'local');
  document.getElementById('mineru-remote').classList.toggle('active', mode === 'remote');
}

function showMinerUError(err) {
  const card = document.getElementById('mineru-error');
  const codeEl = document.getElementById('mineru-error-code');
  const adviceEl = document.getElementById('mineru-error-advice');

  codeEl.textContent = err.err_code ? `错误码: ${err.err_code} · ${err.message}` : err.message || '未知错误';
  adviceEl.textContent = err.advice || '请稍后重试或检查配置';
  card.classList.add('active');
}

// ── Init ─────────────────────────────────────────────────────────
async function init() {
  setupUploadZone();
  const outputDirEl = document.getElementById('output-dir');
  if (outputDirEl) outputDirEl.addEventListener('change', loadCheckpoint);
  try {
    await loadConfig();
  } catch (e) {
    showSaveError(e);
    showToast('配置加载失败，请检查后端服务');
  }
  try {
    await loadInputs();
  } catch (e) {
    const sel = document.getElementById('input-file');
    sel.innerHTML = '<option value="">— 加载失败，请刷新重试 —</option>';
  }
  loadCheckpoint();
}

init();
