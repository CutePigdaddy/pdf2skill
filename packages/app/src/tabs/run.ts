import { Pdf2Skill, isPdf2SkillError, validateSkillName, slugifySkillName } from '@pdf2skill/sdk';
import type { JobSummary, LogKind, ResettableStage, StageEvent } from '@pdf2skill/sdk';
import { $, escapeHtml, errInfoOf, showToast } from '../ui/dom';
import { syncSelect } from '../ui/dropdown';

const STAGE_KEYS = ['mineru', 'chunking', 'peeling', 'skill'] as const;

const RESTART_MODE_TO_STAGE: Record<string, ResettableStage | undefined> = {
  'from-pdf': 'pdf_conversion',
  'from-chunk': 'llm_chunking',
  'from-tree': 'tree_merging',
};

interface RestartOption {
  value: string;
  label: string;
}

const RESTART_OPTIONS: RestartOption[] = [
  { value: 'continue', label: '继续运行（断点续跑）' },
  { value: 'from-pdf', label: '从 PDF 解析重跑' },
  { value: 'from-chunk', label: '从 Chunking 重跑（保留 Peeling 缓存）' },
  { value: 'from-tree', label: '从 Peeling 重跑（复用已完成分块）' },
  { value: 'restart', label: '全部清空重跑' },
];

/** Modes that only need an existing job checkpoint (no source bytes). */
const RESUME_MODES = new Set(['continue', 'from-chunk', 'from-tree']);

let pdf2skill: Pdf2Skill;
let selectedFile: File | null = null;
let running = false;
/** Last name auto-generated from a filename; used to detect it's safe to replace. */
let autoOutputName = '';
let resumeJobId = '';
let jobSummaries: JobSummary[] = [];
const MAX_LOG_LINES = 500;
let logPinned = true;

// ── Stage progress ────────────────────────────────────────────────

const stageStatus: Record<string, string> = {};

function renderStage(e: StageEvent): void {
  $('pipeline-progress').classList.add('visible');
  const i = STAGE_KEYS.indexOf(e.stage as (typeof STAGE_KEYS)[number]);
  if (i === -1) return;
  stageStatus[e.stage] = e.status;

  const stepEl = $('step-' + e.stage);
  stepEl.classList.remove('pending', 'active', 'done', 'failed');
  stepEl.classList.add(e.status);
  if (e.status === 'active' || e.status === 'done') {
    const detailEl = $('step-' + e.stage + '-detail');
    const parts: string[] = [];
    if (e.resumed) parts.push('已从 checkpoint 恢复');
    if (e.percent !== undefined) parts.push(`${e.percent}%`);
    if (e.detail) parts.push(e.detail);
    detailEl.textContent = parts.join(' · ');
  }

  const connEl = document.getElementById('conn-' + (i + 1));
  if (connEl) {
    const prev = stageStatus[STAGE_KEYS[i]];
    const next = stageStatus[STAGE_KEYS[i + 1]];
    connEl.classList.toggle('done', !!(next && (prev === 'done' || prev === 'failed')));
  }
}

function resetPipelineProgress(): void {
  $('pipeline-progress').classList.remove('visible');
  STAGE_KEYS.forEach((key, i) => {
    const stepEl = $('step-' + key);
    stepEl.classList.remove('active', 'done', 'failed');
    stepEl.classList.add('pending');
    $('step-' + key + '-detail').textContent = '';
    stageStatus[key] = 'pending';
    const connEl = document.getElementById('conn-' + (i + 1));
    if (connEl) connEl.classList.remove('done');
  });
}

// ── Logs & errors ─────────────────────────────────────────────────

const LOG_PLACEHOLDER = `
  <div class="log-placeholder">
    <div class="log-placeholder-icon"><i class="ph ph-terminal-window"></i></div>
    选择文件并点击「开始运行」
  </div>`;

function resetLogBox(): void {
  $('log-box').innerHTML = LOG_PLACEHOLDER;
  logPinned = true;
}

function isLogNearBottom(box: HTMLElement): boolean {
  return box.scrollHeight - box.scrollTop - box.clientHeight < 32;
}

/** Keep the log tail visible when the Run tab comes back into view. */
export function scrollLogToLatest(): void {
  const box = $('log-box');
  if (logPinned) box.scrollTop = box.scrollHeight;
}

function appendLog(
  level: 'info' | 'warn' | 'error',
  message: string,
  kind: LogKind = 'run',
): void {
  const box = $('log-box');
  box.querySelector('.log-placeholder')?.remove();

  const fullText = message.replace(/\s+/g, ' ').trim();
  let text = kind === 'stage' ? fullText.replace(/^---\s*|\s*---$/g, '') : fullText;
  if (text.length > 360) text = text.slice(0, 357) + '…';

  const row = document.createElement('div');
  const collapsible = kind === 'detail' && level === 'info';
  row.className = `log-line log-${level} log-${kind}${collapsible ? ' is-detail' : ''}`;

  const time = document.createElement('span');
  time.className = 'log-time';
  time.textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  const badge = document.createElement('span');
  badge.className = 'log-level';
  badge.textContent = kind === 'stage' ? 'STAGE' : level.toUpperCase();
  const msg = document.createElement('span');
  msg.className = 'log-msg';
  msg.textContent = text;
  if (fullText !== text) msg.title = fullText;

  row.append(time, badge, msg);
  box.appendChild(row);
  while (box.children.length > MAX_LOG_LINES) box.firstElementChild?.remove();
  if (logPinned && box.offsetParent !== null) box.scrollTop = box.scrollHeight;
}

function showPipelineError(code: string, message: string, advice: string): void {
  $('mineru-error-code').textContent = code ? `错误码: ${code} · ${message}` : message;
  $('mineru-error-advice').textContent = advice || '请稍后重试或检查配置';
  $('mineru-error').classList.add('active');
}

function hidePipelineError(): void {
  $('mineru-error').classList.remove('active');
}

function showUploadError(message: string, advice: string): void {
  $('upload-error-text').textContent = message;
  $('upload-error-advice').textContent = advice || '';
  $('upload-error').style.display = 'block';
}

// ── Output name ───────────────────────────────────────────────────

const OUTPUT_NAME_HINT =
  '命名规范：仅小写字母、数字和中划线（-），≤64 字符，不能以 - 开头/结尾或连续 --。该名称将作为 SKILL.md 的 name 字段。';

function outputNameInput(): HTMLInputElement {
  return $('output-name') as HTMLInputElement;
}

/** Live-validate the output name field. Empty means auto-generate (valid). */
function validateOutputNameField(): boolean {
  const hint = $('output-name-hint');
  const value = outputNameInput().value.trim();
  if (!value) {
    hint.textContent = OUTPUT_NAME_HINT;
    hint.className = 'field-hint';
    return true;
  }
  const issues = validateSkillName(value);
  if (issues.length > 0) {
    hint.textContent = `✗ ${issues[0]}。${OUTPUT_NAME_HINT}`;
    hint.className = 'field-hint invalid';
    return false;
  }
  hint.textContent = '✓ 符合命名规范';
  hint.className = 'field-hint valid';
  return true;
}

// ── Existing-job resume ───────────────────────────────────────────

function resumeJobSelect(): HTMLSelectElement {
  return $('resume-job-select') as HTMLSelectElement;
}

function setSelectDisabled(sel: HTMLSelectElement, disabled: boolean): void {
  sel.disabled = disabled;
  const trigger = sel.nextElementSibling?.querySelector<HTMLButtonElement>('.dd-trigger');
  if (trigger) trigger.disabled = disabled;
}

function setRestartOptions(allowSourceStages: boolean): void {
  const sel = document.getElementById('restart-mode') as HTMLSelectElement;
  const previous = sel.value;
  const options = allowSourceStages
    ? RESTART_OPTIONS
    : RESTART_OPTIONS.filter((o) => RESUME_MODES.has(o.value));
  sel.innerHTML = options
    .map((o) => `<option value="${o.value}">${escapeHtml(o.label)}</option>`)
    .join('');
  sel.value = options.some((o) => o.value === previous) ? previous : 'continue';
  syncSelect(sel);
}

function applyResumeSelection(jobId: string | null): void {
  resumeJobId = jobId ?? '';
  const isResume = !!resumeJobId;

  const dropzone = $('dropzone');
  dropzone.classList.toggle('disabled', isResume);
  dropzone.setAttribute('aria-disabled', String(isResume));
  dropzone.tabIndex = isResume ? -1 : 0;
  const upload = $('upload-input') as HTMLInputElement;
  upload.disabled = isResume;
  $('upload-error').style.display = 'none';
  if (isResume) {
    // Keep the File in memory: switching back to "不使用已有任务" restores it.
    $('selected-file').style.display = 'none';
  } else if (selectedFile) {
    $('selected-file').style.display = '';
  }

  setSelectDisabled(document.getElementById('input-mode') as HTMLSelectElement, isResume);
  outputNameInput().disabled = isResume;
  setRestartOptions(!isResume);
  $('btn-start').innerHTML = isResume
    ? '<i class="ph ph-play"></i>继续任务'
    : '<i class="ph ph-play"></i>开始运行';

  const hint = $('resume-job-hint');
  if (isResume) {
    const job = jobSummaries.find((j) => j.jobId === resumeJobId);
    hint.textContent = job
      ? `将从 ${job.jobId} 的 checkpoint 继续；已完成 ${job.completedStages.length}/4 阶段。要上传新文件，请先切回“不使用已有任务”。`
      : '将从该任务的 checkpoint 继续。要上传新文件，请先切回“不使用已有任务”。';
  } else {
    hint.textContent = jobSummaries.length
      ? '选择后可跳过上传，直接从该任务的 checkpoint 继续。只列出已完成 Stage 1 的任务。'
      : '当前浏览器没有可继续的任务。任务按访问地址（origin）隔离；若之前在 localhost:5173 或 localhost:8501 跑过，请用同一地址打开，或先运行一次任务。';
  }
}

export async function refreshRunJobs(): Promise<void> {
  const sel = resumeJobSelect();
  try {
    jobSummaries = (await pdf2skill.listJobs()).filter((j) => j.resumable);
  } catch (e) {
    console.warn('[pdf2skill] listJobs failed', e);
    jobSummaries = [];
  }

  sel.innerHTML =
    '<option value="">不使用已有任务</option>' +
    jobSummaries
      .map((j) => {
        const date = j.updatedAt ? new Date(j.updatedAt).toLocaleString() : '未知时间';
        return `<option value="${escapeHtml(j.jobId)}">${escapeHtml(j.inputName)}（${j.completedStages.length}/4 阶段，${date}）</option>`;
      })
      .join('');

  const keep =
    resumeJobId && jobSummaries.some((j) => j.jobId === resumeJobId) ? resumeJobId : '';
  sel.value = keep;
  syncSelect(sel);
  applyResumeSelection(keep || null);
}

/** Select a job from another tab and switch this panel into resume mode. */
export async function resumeJob(jobId: string): Promise<void> {
  await refreshRunJobs();
  if (!jobSummaries.some((j) => j.jobId === jobId)) {
    showToast('该任务缺少 Stage 1 输入，无法继续；请重新上传原文件', 'warn');
    return;
  }
  resumeJobSelect().value = jobId;
  syncSelect(resumeJobSelect());
  applyResumeSelection(jobId);
}

// ── Disk → OPFS import ────────────────────────────────────────────

interface DiskJobSummary {
  jobId: string;
  inputName: string;
  completedStages: string[];
  updatedAt: number;
  resumable: boolean;
  mdFile: string;
}

let importing = false;

function setImportStatus(text: string): void {
  $('import-status').textContent = text;
}

function formatMb(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

async function responseError(resp: Response): Promise<string> {
  try {
    const body = await resp.json();
    return body?.error?.message || `HTTP ${resp.status}`;
  } catch {
    return `HTTP ${resp.status}`;
  }
}

async function fetchDiskJobs(): Promise<DiskJobSummary[]> {
  const resp = await fetch('/api/jobs');
  if (resp.status === 404) {
    throw new Error('服务端没有 /api/jobs 接口；如果是刚更新的代码，请重启 python frontend/server.py');
  }
  if (!resp.ok) throw new Error(await responseError(resp));
  const body = await resp.json();
  if (!body?.ok || !Array.isArray(body.jobs)) throw new Error('服务端返回格式异常');
  return body.jobs as DiskJobSummary[];
}

async function readBundle(
  resp: Response,
  onProgress: (done: number, total: number) => void,
): Promise<Uint8Array> {
  const total = Number(resp.headers.get('content-length') || 0);
  if (!resp.body) return new Uint8Array(await resp.arrayBuffer());

  const reader = resp.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (!value) continue;
    chunks.push(value);
    received += value.byteLength;
    onProgress(received, total);
  }
  const bytes = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

async function importDiskJob(job: DiskJobSummary, button: HTMLButtonElement): Promise<void> {
  if (importing) {
    button.disabled = false;
    return;
  }
  importing = true;
  try {
    setImportStatus(`正在下载 ${job.jobId}…`);
    const resp = await fetch(`/api/jobs/${encodeURIComponent(job.jobId)}/bundle`);
    if (!resp.ok) {
      setImportStatus(`导入失败：${await responseError(resp)}`);
      return;
    }

    const totalBytes = Number(resp.headers.get('content-length') || 0);
    if (
      totalBytes > 256 * 1024 * 1024 &&
      !window.confirm(
        `任务包约 ${formatMb(totalBytes)}，导入会占用较多浏览器内存与存储空间。仍要继续吗？`,
      )
    ) {
      setImportStatus('已取消');
      return;
    }

    const zipBytes = await readBundle(resp, (done, total) => {
      setImportStatus(
        total ? `下载中 ${formatMb(done)} / ${formatMb(total)}` : `下载中 ${formatMb(done)}`,
      );
    });

    setImportStatus('正在写入浏览器 OPFS…');
    let summary: JobSummary;
    try {
      summary = await pdf2skill.importJob(job.jobId, zipBytes, {
        inputName: job.inputName,
        onProgress: (done, total) => setImportStatus(`导入中 ${done}/${total} 个文件`),
      });
    } catch (e) {
      if (isPdf2SkillError(e) && e.code === 'JOB_EXISTS') {
        setImportStatus('浏览器中已有同名任务；请先在「输出」页删除后再导入');
        return;
      }
      throw e;
    }

    setImportStatus('');
    ($('import-dialog') as HTMLDialogElement).close();
    showToast(`已导入 ${job.jobId}（${summary.completedStages.length}/4 阶段）`);
    await refreshRunJobs();
    resumeJobSelect().value = job.jobId;
    syncSelect(resumeJobSelect());
    applyResumeSelection(job.jobId);
  } catch (e) {
    const info = errInfoOf(e);
    setImportStatus(`导入失败：${info.message}${info.advice ? `（${info.advice}）` : ''}`);
  } finally {
    importing = false;
    button.disabled = false;
  }
}

function renderDiskJobs(jobs: DiskJobSummary[]): void {
  const box = $('import-job-list');
  if (jobs.length === 0) {
    box.innerHTML =
      '<div class="import-empty">服务端 outputs/ 中没有可导入的任务（需要已完成 Stage 1 的 checkpoint）。</div>';
    return;
  }

  box.innerHTML = jobs
    .map((job) => {
      const date = job.updatedAt ? new Date(job.updatedAt).toLocaleString() : '未知时间';
      return `<div class="import-job-row">
        <div>
          <div class="import-job-name">${escapeHtml(job.jobId)}</div>
          <div class="import-job-meta">${job.completedStages.length}/4 阶段 · ${escapeHtml(job.mdFile)} · ${escapeHtml(date)}</div>
        </div>
        <button class="btn btn-secondary btn-sm" type="button" data-import-job="${escapeHtml(job.jobId)}">导入</button>
      </div>`;
    })
    .join('');

  box.querySelectorAll<HTMLButtonElement>('[data-import-job]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const job = jobs.find((j) => j.jobId === btn.dataset.importJob);
      if (!job || btn.disabled) return;
      btn.disabled = true;
      void importDiskJob(job, btn);
    });
  });
}

async function openImportDialog(): Promise<void> {
  const dialog = $('import-dialog') as HTMLDialogElement;
  $('import-job-list').innerHTML = '';
  setImportStatus('正在读取服务端 outputs/…');
  if (!dialog.open) dialog.showModal();

  try {
    const jobs = await fetchDiskJobs();
    renderDiskJobs(jobs);
    setImportStatus(jobs.length ? `共 ${jobs.length} 个可导入任务` : '');
  } catch (e) {
    const info = errInfoOf(e);
    $('import-job-list').innerHTML = `<div class="import-empty">读取失败：${escapeHtml(info.message)}</div>`;
    setImportStatus(info.advice || '请确认服务端已启动，且 /api/jobs 可访问');
  }
}

// ── File selection ────────────────────────────────────────────────

function selectFile(file: File): void {
  const ext = file.name.toLowerCase().split('.').pop() || '';
  if (ext !== 'pdf' && ext !== 'md' && ext !== 'markdown') {
    showUploadError('仅支持 .pdf 或 .md 文件', '请选择 PDF 或 Markdown 文件');
    return;
  }
  if (ext === 'pdf') {
    if (file.size === 0) {
      showUploadError('文件为空', '请选择有效的 PDF 文件');
      return;
    }
    if (file.size > 200 * 1024 * 1024) {
      showUploadError(
        `文件大小超过 200MB（当前 ${(file.size / 1024 / 1024).toFixed(1)}MB）`,
        '请拆分文件后重新选择',
      );
      return;
    }
  }
  if (resumeJobId) {
    resumeJobSelect().value = '';
    syncSelect(resumeJobSelect());
    applyResumeSelection(null);
  }
  $('upload-error').style.display = 'none';
  selectedFile = file;
  // Clearing the inline style restores the stylesheet's flex layout.
  $('selected-file').style.display = '';
  $('selected-file-name').textContent = file.name;
  $('selected-file-size').textContent = `${(file.size / 1024).toFixed(1)}KB`;
  const modeSelect = document.getElementById('input-mode') as HTMLSelectElement;
  modeSelect.value = ext === 'pdf' ? 'pdf' : 'markdown';
  syncSelect(modeSelect);

  // Refresh an auto-generated output name when a different file is selected:
  // the name doubles as the job id, so a stale value would resume the previous
  // file's checkpoint instead of processing the new one. A hand-edited name is
  // preserved. Non-ASCII stems stay empty so the pipeline auto-generates one.
  const nameInput = outputNameInput();
  if (!nameInput.value.trim() || nameInput.value === autoOutputName) {
    autoOutputName = slugifySkillName(file.name.replace(/\.[^.]*$/, ''));
    nameInput.value = autoOutputName;
  }
  validateOutputNameField();
}

function setupDropzone(): void {
  const zone = $('dropzone');
  const input = $('upload-input') as HTMLInputElement;
  zone.addEventListener('click', () => input.click());
  // The dropzone is a div with role=button; give keyboard users the same path.
  zone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      input.click();
    }
  });
  input.addEventListener('change', () => {
    if (input.files && input.files.length > 0) selectFile(input.files[0]);
  });
  zone.addEventListener('dragover', (e) => {
    e.preventDefault();
    zone.classList.add('drag-over');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    if (e.dataTransfer && e.dataTransfer.files.length > 0) selectFile(e.dataTransfer.files[0]);
  });
}

// ── Run / stop ────────────────────────────────────────────────────

function setRunning(isRunning: boolean): void {
  running = isRunning;
  $('panel-run').setAttribute('aria-busy', String(isRunning));
  ($('btn-start') as HTMLButtonElement).disabled = isRunning;
  $('btn-stop').style.display = isRunning ? 'inline-flex' : 'none';
}

function setStatus(label: string, cls: string, dot = false): void {
  $('run-status').innerHTML = `<span class="status ${cls}">${dot ? '<span class="status-dot"></span>' : ''}${escapeHtml(label)}</span>`;
}

async function startPipeline(): Promise<void> {
  const resumeId = resumeJobId;
  if (!resumeId && !selectedFile) {
    showToast('请先选择输入文件或已有任务', 'warn');
    return;
  }

  // The output name becomes the SKILL.md `name`; enforce the kebab-case spec
  // before starting so users get an actionable message instead of a broken skill.
  const outputName = outputNameInput().value.trim();
  if (!resumeId && !validateOutputNameField()) {
    showToast('输出名称不符合命名规范，请修改后再运行', 'warn');
    return;
  }

  const mode = (document.getElementById('input-mode') as HTMLSelectElement).value as 'pdf' | 'markdown';
  const restartMode = (document.getElementById('restart-mode') as HTMLSelectElement).value;
  if (resumeId && !RESUME_MODES.has(restartMode)) {
    showToast('继续已有任务时不能从 PDF 重跑或全部清空；请重新上传原文件', 'warn');
    return;
  }

  // Log box reset
  resetLogBox();
  hidePipelineError();
  resetPipelineProgress();
  setRunning(true);
  setStatus('运行中', 'status-running', true);

  try {
    const result = resumeId
      ? await pdf2skill.resume(resumeId, {
          fromStage: RESTART_MODE_TO_STAGE[restartMode],
        })
      : await pdf2skill.run(
          { kind: mode, file: selectedFile!, name: selectedFile!.name },
          {
            outputName: outputName || undefined,
            restart: restartMode === 'restart',
            fromStage: RESTART_MODE_TO_STAGE[restartMode],
          },
        );
    setStatus('完成', 'status-done');
    showToast(`技能库已生成: ${result.skillName}（${result.referenceCount} 个引用文件）`);
  } catch (e) {
    if (isPdf2SkillError(e) && e.code === 'ABORTED') {
      setStatus('已停止', 'status-failed');
      appendLog(
        'warn',
        resumeId
          ? '任务已中止：已完成阶段的 checkpoint 已保存，可从该任务继续'
          : '任务已中止：已完成阶段的 checkpoint 已保存，重新运行同一文件可断点续跑',
      );
    } else {
      const info = errInfoOf(e);
      setStatus('失败', 'status-failed');
      showPipelineError(info.code, info.message, info.advice);
      // Mark the failing stage dot as failed if known
      if (isPdf2SkillError(e) && e.stage) {
        const stepEl = document.getElementById('step-' + e.stage);
        if (stepEl) {
          stepEl.classList.remove('pending', 'active', 'done');
          stepEl.classList.add('failed');
        }
      }
    }
  } finally {
    setRunning(false);
    void refreshRunJobs();
  }
}

// ── Wire up ───────────────────────────────────────────────────────

export function setupRunTab(sdkInstance: Pdf2Skill): void {
  pdf2skill = sdkInstance;
  setupDropzone();
  outputNameInput().addEventListener('input', validateOutputNameField);
  $('btn-start').addEventListener('click', () => void startPipeline());
  $('btn-stop').addEventListener('click', () => pdf2skill.abort());
  resumeJobSelect().addEventListener('change', (e) => {
    applyResumeSelection((e.target as HTMLSelectElement).value || null);
  });
  setRestartOptions(true);
  void refreshRunJobs();

  const box = $('log-box');
  const detailToggle = $('log-detail-toggle') as HTMLInputElement;
  box.addEventListener('scroll', () => {
    logPinned = isLogNearBottom(box);
  });
  detailToggle.addEventListener('change', () => {
    box.classList.toggle('show-detail', detailToggle.checked);
    if (detailToggle.checked) scrollLogToLatest();
  });
  $('btn-clear-log').addEventListener('click', resetLogBox);

  $('btn-import-job').addEventListener('click', () => void openImportDialog());
  $('btn-import-close').addEventListener('click', () => {
    if (!importing) ($('import-dialog') as HTMLDialogElement).close();
  });
  $('import-dialog').addEventListener('cancel', (e) => {
    if (importing) e.preventDefault();
  });

  pdf2skill.on('stage', renderStage);
  pdf2skill.on('log', (e) => appendLog(e.level, e.message, e.kind ?? 'run'));
  pdf2skill.on('error', (e) => appendLog('error', `[${e.error.code}] ${e.error.message}`));

  window.addEventListener('beforeunload', (e) => {
    if (running) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
}
